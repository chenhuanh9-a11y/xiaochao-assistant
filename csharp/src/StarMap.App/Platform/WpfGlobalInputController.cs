using System.Windows.Threading;
using StarMap.Core.Input;

namespace StarMap.App.Platform;

/// <summary>Read on the UI thread, immediately before consumption. BindingGeneration is the
/// existing bound-window owner's generation; it must change even when a replacement reuses a HWND.
/// PickerKeys contains only the keys of the currently visible picker.</summary>
public sealed record WpfGlobalInputContext(GlobalInputScope Scope,
    IReadOnlyList<string>? PickerKeys = null, long BindingGeneration = 0,
    GlobalInputPointerTarget? PointerTarget = null, long LocalPanelWindow = 0);

/// <summary>Business consumers use Dispatch and Pointer. Notification retains source metadata;
/// Notification.Pointer alone is not permission to perform a pointer action.</summary>
public sealed record WpfGlobalInputDelivery(GlobalInputNotification Notification,
    GlobalInputDispatch Dispatch, GlobalInputPointerEvent? Pointer = null);

/// <summary>
/// UI owner for the existing pass-through input policy. Construction installs nothing.
/// Every source notification occupies at most one pending UI operation; its serial source
/// subscriber waits for completion, so the source's bounded channel supplies backpressure.
/// Stop cancels only pending operations. A running callback must return before Stop completes.
/// All public operations, the context factory and business callback belong to this dispatcher.
/// Never synchronously wait for Start/Configure on the UI thread: their ordered Started
/// notification is the sole authority that resets held state, not the returned snapshot.
/// </summary>
public sealed class WpfGlobalInputController : IAsyncDisposable
{
    readonly Dispatcher ui;
    readonly IWpfGlobalInputSource source;
    readonly Func<WpfGlobalInputContext> readContext;
    readonly Action<WpfGlobalInputDelivery> deliver;
    readonly GlobalInputDispatcher policy;
    GlobalInputBindings bindings, activeBindings;
    CancellationTokenSource? deliveryCancellation;
    TaskCompletionSource<GlobalInputSnapshot>? starting;
    PendingConfiguration? configuring;
    Task<GlobalInputStartResult>? startTask;
    Task<GlobalInputStopResult>? stopTask;
    WpfGlobalInputContext? previousContext;
    GlobalInputPointerEvent? gesture;
    long generation;
    double gameEventsAfter, scopeEventsAfter;
    bool listeningRequested, ready, closed;
    string? lastDeliveryError;

    sealed record PendingConfiguration(GlobalInputBindings Bindings, long AfterGeneration,
        TaskCompletionSource<GlobalInputSnapshot> Applied);

    public WpfGlobalInputController(Dispatcher dispatcher, GlobalInputBindings bindings,
        Func<WpfGlobalInputContext> scopeFactory, Action<WpfGlobalInputDelivery> dispatch)
        : this(dispatcher, bindings, scopeFactory, dispatch, new WpfNativeGlobalInputSource(bindings)) { }

    // Only the OS producer/installation boundary is replaced in offline tests.
    internal WpfGlobalInputController(Dispatcher dispatcher, GlobalInputBindings bindings,
        Func<WpfGlobalInputContext> scopeFactory, Action<WpfGlobalInputDelivery> dispatch,
        IWpfGlobalInputSource source)
    {
        ui = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
        ui.VerifyAccess();
        this.bindings = activeBindings = bindings ?? throw new ArgumentNullException(nameof(bindings));
        readContext = scopeFactory ?? throw new ArgumentNullException(nameof(scopeFactory));
        deliver = dispatch ?? throw new ArgumentNullException(nameof(dispatch));
        this.source = source ?? throw new ArgumentNullException(nameof(source));
        policy = new(bindings);
        policy.Reset(bindings, new(0, Array.Empty<int>()));
        source.ObservationReceived += Observe;
    }

    public bool ListeningRequested { get { ui.VerifyAccess(); return listeningRequested; } }
    public bool IsReady { get { ui.VerifyAccess(); return ready && configuring is null && listeningRequested && !closed; } }
    /// <summary>Immutable binding projection already applied to policy on this UI owner.
    /// A listening configuration switches here at ordered Started, before ConfigureAsync
    /// may return. This is not listening permission; consumers must also check IsReady.</summary>
    public GlobalInputBindings ActiveBindings { get { ui.VerifyAccess(); return activeBindings; } }
    public long Generation { get { ui.VerifyAccess(); return generation; } }
    public string? LastDeliveryError => Volatile.Read(ref lastDeliveryError);

    public Task<GlobalInputStartResult> StartAsync()
    {
        VerifyOpen();
        if (stopTask is { IsCompleted: false }) throw new InvalidOperationException("Input is still stopping.");
        if (configuring is not null) throw new InvalidOperationException("Input is still being configured.");
        if (listeningRequested && startTask is not null) return startTask;
        deliveryCancellation?.Dispose();
        deliveryCancellation = new();
        listeningRequested = true; ready = false;
        starting = new(TaskCreationOptions.RunContinuationsAsynchronously);
        try { RefreshScope(); }
        catch { listeningRequested = false; deliveryCancellation.Cancel(); throw; }
        startTask = AwaitStart(source.StartAsync(), starting.Task, deliveryCancellation, deliveryCancellation.Token);
        return startTask;
    }

    async Task<GlobalInputStartResult> AwaitStart(Task<GlobalInputStartResult> native,
        Task<GlobalInputSnapshot> applied, CancellationTokenSource lifetime, CancellationToken token)
    {
        var result = await native.ConfigureAwait(false);
        if (!result.Started)
        {
            // A rejected restart need not produce a new terminal notification.
            void RevokeFailedStart()
            {
                if (!closed && ReferenceEquals(lifetime, deliveryCancellation))
                    listeningRequested = ready = false;
            }
            try
            {
                if (ui.CheckAccess()) RevokeFailedStart();
                else await ui.InvokeAsync(RevokeFailedStart, DispatcherPriority.Input, token).Task.ConfigureAwait(false);
            }
            catch (OperationCanceledException) { }
            return result;
        }
        try { await applied.WaitAsync(token).ConfigureAwait(false); return result; }
        catch (OperationCanceledException)
        { return result with { Started = false, Error = "Input stopped before its UI subscriber was ready." }; }
    }

    public Task<GlobalInputSnapshot> ConfigureAsync(GlobalInputBindings next)
    {
        VerifyOpen(); ArgumentNullException.ThrowIfNull(next);
        if (stopTask is { IsCompleted: false } || startTask is { IsCompleted: false } || configuring is not null)
            throw new InvalidOperationException("Finish the current input lifecycle operation before configuring.");
        CancelModifierTaps();
        // Keep desired bindings even if native configuration is followed by overflow/Stop
        // before Started reaches us. WindowsGlobalInput retains the same desired bindings
        // for its next run; only the policy's active projection waits for ordered Started.
        bindings = next;
        if (!listeningRequested)
        {
            // An idle source has no queued run or notifications. Its result is safe to seed here.
            return ConfigureIdle(next);
        }
        var pending = new PendingConfiguration(next, generation,
            new(TaskCreationOptions.RunContinuationsAsynchronously));
        configuring = pending;
        return AwaitConfiguration(source.ConfigureAsync(next), pending.Applied.Task, deliveryCancellation!.Token);
    }

    async Task<GlobalInputSnapshot> ConfigureIdle(GlobalInputBindings next)
    {
        // The native source completes this synchronously when idle. Do not move policy ownership.
        var operation = source.ConfigureAsync(next);
        if (!operation.IsCompleted) throw new InvalidOperationException("Idle input configuration must complete synchronously.");
        var snapshot = await operation;
        generation = snapshot.Generation;
        policy.Reset(next, snapshot); activeBindings = next;
        return snapshot;
    }

    static async Task<GlobalInputSnapshot> AwaitConfiguration(Task<GlobalInputSnapshot> native,
        Task<GlobalInputSnapshot> applied, CancellationToken token)
    {
        var result = await native.ConfigureAwait(false);
        var ordered = await applied.WaitAsync(token).ConfigureAwait(false);
        if (result.Generation != ordered.Generation)
            throw new InvalidOperationException("Configuration result did not match its ordered Started notification.");
        return result;
    }

    /// <summary>Call immediately after changing binding, listening intent, text focus, shortcut
    /// editing, picker visibility or pointer context, even when no input edge arrives. This does
    /// not install/reconfigure the observer or start a round. Resume fences queued game presses.</summary>
    public void RefreshScope()
    {
        VerifyOpen();
        ApplyContext(readContext());
    }

    public void CancelModifierTaps()
    {
        VerifyOpen();
        policy.CancelModifierTaps(); source.CancelModifierTaps();
    }

    WpfGlobalInputContext ApplyContext(WpfGlobalInputContext context)
    {
        ArgumentNullException.ThrowIfNull(context);
        ArgumentNullException.ThrowIfNull(context.Scope);
        var old = previousContext;
        var scope = context.Scope;
        var bindingChanged = old is null || old.Scope.GameWindow != scope.GameWindow
            || old.BindingGeneration != context.BindingGeneration;
        var resumed = scope.GameEnabled && scope.GameWindowExists
            && (old is null || !old.Scope.GameEnabled || !old.Scope.GameWindowExists || bindingChanged);
        var uiGuardChanged = old is null || old.Scope.ManagementEnabled != scope.ManagementEnabled
            || old.Scope.TextEntryActive != scope.TextEntryActive || old.Scope.EditingBindings != scope.EditingBindings
            || !(old.PickerKeys ?? []).SequenceEqual(context.PickerKeys ?? [], StringComparer.Ordinal);
        var changed = bindingChanged || old is null || old.Scope.GameEnabled != scope.GameEnabled
            || old.Scope.GameWindowExists != scope.GameWindowExists
            || uiGuardChanged;
        if (changed)
        {
            policy.CancelModifierTaps(); source.CancelModifierTaps();
            // A queued press from an older UI guard/picker must not become a fresh shortcut
            // after typing ends, a picker opens, or listening intent is restored.
        }
        if (uiGuardChanged) scopeEventsAfter = source.NowSeconds;
        if (bindingChanged || resumed) gameEventsAfter = source.NowSeconds;
        previousContext = context with { PickerKeys = (context.PickerKeys ?? []).ToArray() };
        policy.SetPickerKeys(context.PickerKeys ?? []);
        var pointer = listeningRequested && scope.GameEnabled && scope.GameWindowExists
            && !scope.TextEntryActive && !scope.EditingBindings && context.PointerTarget?.Window == scope.GameWindow
            ? context.PointerTarget : null;
        source.SetPointerTarget(pointer);
        if (gesture is { } active && (pointer is null || active.ContextId != pointer.ContextId
            || active.TargetWindow != pointer.Window || bindingChanged))
            CancelGesture("ui-input-context-changed");
        return context with { Scope = scope with {
            GameEnabled = scope.GameEnabled && ready && configuring is null,
            ManagementEnabled = scope.ManagementEnabled && ready && configuring is null,
            GameEventsAfterSeconds = Math.Max(scope.GameEventsAfterSeconds, gameEventsAfter) } };
    }

    void Observe(GlobalInputNotification notification)
    {
        var lifetime = Volatile.Read(ref deliveryCancellation);
        if (lifetime is null || lifetime.IsCancellationRequested) return;
        try
        {
            if (ui.CheckAccess()) throw new InvalidOperationException("Native input notifications require the serial source thread.");
            var token = lifetime.Token;
            var operation = ui.InvokeAsync(() =>
            {
                if (!token.IsCancellationRequested && ReferenceEquals(lifetime, deliveryCancellation) && !closed)
                    Consume(notification);
            }, DispatcherPriority.Input, token);
            // Exactly one outstanding operation from the source's single serial subscriber.
            operation.Task.GetAwaiter().GetResult();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { Volatile.Write(ref lastDeliveryError, $"{error.GetType().Name}: {error.Message}"); }
    }

    void Consume(GlobalInputNotification notification)
    {
        ui.VerifyAccess();
        if (!listeningRequested) return;
        if (notification.Kind == GlobalInputNotificationKind.Started && notification.Snapshot is { } snapshot)
        {
            if (snapshot.Generation < generation) return;
            if (snapshot.Generation == generation && ready) return; // Never reseed after delivered edges.
            if (configuring is { } pending && snapshot.Generation > pending.AfterGeneration)
            {
                bindings = pending.Bindings; configuring = null;
                policy.Reset(bindings, snapshot); activeBindings = bindings; generation = snapshot.Generation; ready = true;
                pending.Applied.TrySetResult(snapshot);
            }
            else
            {
                policy.Reset(bindings, snapshot); activeBindings = bindings; generation = snapshot.Generation; ready = true;
            }
            starting?.TrySetResult(snapshot);
        }
        else if (notification.Generation != generation)
        {
            // Native ResetState can precede installation failure, or a full channel can
            // reject configure's Started. The out-of-band terminal reset remains authoritative
            // for that newer generation. Older-run terminals may never revoke a replacement.
            var terminal = notification.Kind is GlobalInputNotificationKind.Failed or GlobalInputNotificationKind.Stopped;
            if (notification.Generation < generation || (!terminal && (ready
                || notification.Kind != GlobalInputNotificationKind.Warning))) return;
            generation = notification.Generation;
        }

        if (notification.Kind == GlobalInputNotificationKind.Failed)
        {
            ready = false;
            policy.CancelModifierTaps(); source.CancelModifierTaps();
        }

        if (notification.Kind == GlobalInputNotificationKind.Stopped)
        {
            ready = false; listeningRequested = false;
            policy.Reset(bindings, new(generation, Array.Empty<int>())); activeBindings = bindings;
            configuring?.Applied.TrySetCanceled(); configuring = null;
            starting?.TrySetCanceled();
            CancelGesture("input-source-stopped");
            source.SetPointerTarget(null);
        }

        var result = GlobalInputDispatch.Empty;
        GlobalInputPointerEvent? acceptedPointer = null;
        if (notification.Key is { } key)
        {
            // Rejected and stale releases still enter the real policy in source order.
            var current = ReadCurrentContext();
            var stalePicker = key.CreatedAtSeconds < gameEventsAfter && (current.PickerKeys ?? [])
                .Any(name => GlobalInputBindings.PickerKeys.TryGetValue(name, out var vk) && vk == key.VirtualKey);
            var scope = key.CreatedAtSeconds < scopeEventsAfter || stalePicker
                ? current.Scope with { TextEntryActive = true } : current.Scope;
            result = policy.Dispatch(key, scope, source.NowSeconds);
            // WPF can consume this same physical key first and Hide the owner.
            // Its HWND at observation time keeps the local owner even after focus changes.
            if (current.LocalPanelWindow != 0 && key.ForegroundWindow == current.LocalPanelWindow
                && GlobalInputDispatcher.HasApplicationLocalPanelOwner(key.VirtualKey))
                result = result with { Actions = Array.AsReadOnly(result.Actions.Where(action => action != "toggle-panel").ToArray()) };
        }
        if (notification.Pointer is { } pointer)
            acceptedPointer = AcceptPointer(pointer, ReadCurrentContext());
        Publish(new(notification, result, acceptedPointer));
    }

    WpfGlobalInputContext ReadCurrentContext()
    {
        try { return ApplyContext(readContext()); }
        catch (Exception error)
        {
            Volatile.Write(ref lastDeliveryError, $"Scope {error.GetType().Name}: {error.Message}");
            policy.CancelModifierTaps(); source.CancelModifierTaps(); source.SetPointerTarget(null);
            CancelGesture("ui-input-scope-unavailable");
            // Still feed rejected releases/down edges to policy, so a later repeat cannot
            // masquerade as a new press merely because one scope read failed.
            return new(new(TextEntryActive: true));
        }
    }

    void Publish(WpfGlobalInputDelivery delivery)
    {
        try { deliver(delivery); }
        catch (Exception error) { Volatile.Write(ref lastDeliveryError, $"Consumer {error.GetType().Name}: {error.Message}"); }
    }

    GlobalInputPointerEvent? AcceptPointer(GlobalInputPointerEvent pointer, WpfGlobalInputContext context)
    {
        if (pointer.Phase == "cancel")
        {
            if (gesture is not { } active || active.GestureId != pointer.GestureId
                || active.PointerGeneration != pointer.PointerGeneration) return null;
            gesture = null; return pointer;
        }
        var scope = context.Scope;
        var age = source.NowSeconds - pointer.CreatedAtSeconds;
        if (!scope.GameEnabled || !scope.GameWindowExists || scope.TextEntryActive || scope.EditingBindings
            || pointer.TargetWindow != scope.GameWindow || pointer.ForegroundWindow != scope.GameWindow
            || scope.CurrentForegroundWindow != scope.GameWindow || context.PointerTarget is not { } target
            || pointer.ContextId != target.ContextId || pointer.TargetWindow != target.Window
            || pointer.CreatedAtSeconds < Math.Max(scopeEventsAfter, Math.Max(scope.GameEventsAfterSeconds, target.ObservationCreatedAtSeconds))
            || !double.IsFinite(age) || age < 0 || age > GlobalInputDispatcher.MaximumEventAgeSeconds)
        { CancelGesture("ui-pointer-scope-rejected"); return null; }
        if (pointer.Phase != "down" && (gesture is not { } previous || previous.GestureId != pointer.GestureId
            || previous.PointerGeneration != pointer.PointerGeneration || pointer.Sequence <= previous.Sequence)) return null;
        if (pointer.Phase == "down") CancelGesture("ui-pointer-replaced");
        gesture = pointer.Phase == "up" ? null : pointer;
        return pointer;
    }

    void CancelGesture(string reason)
    {
        if (gesture is not { } active) return;
        gesture = null;
        var cancel = active with { Phase = "cancel", Reason = reason };
        Publish(new(new(GlobalInputNotificationKind.Pointer, active.Generation, Pointer: cancel), GlobalInputDispatch.Empty, cancel));
    }

    /// <summary>Revokes UI permission immediately, then awaits native cleanup AND its subscriber.
    /// Cancelling a queued UI operation is not evidence that a running callback has returned.</summary>
    public Task<GlobalInputStopResult> StopAsync(TimeSpan? timeout = null)
    {
        ui.VerifyAccess();
        if (closed) return Task.FromResult(new GlobalInputStopResult(true, null));
        if (stopTask is { IsCompleted: false }) return stopTask;
        listeningRequested = ready = false;
        deliveryCancellation?.Cancel();
        starting?.TrySetCanceled(); configuring?.Applied.TrySetCanceled(); configuring = null;
        policy.Reset(bindings, new(generation, Array.Empty<int>())); activeBindings = bindings;
        source.CancelModifierTaps(); source.SetPointerTarget(null);
        stopTask = source.StopAsync(timeout);
        CancelGesture("ui-input-stopped");
        return stopTask;
    }

    public async ValueTask DisposeAsync()
    {
        ui.VerifyAccess();
        if (closed) return;
        var stopped = await StopAsync();
        if (!stopped.Stopped) throw new InvalidOperationException(stopped.Error);
        // No source callback remains after Stop's serial subscriber completion.
        closed = true;
        source.ObservationReceived -= Observe;
        await source.DisposeAsync();
        deliveryCancellation?.Dispose();
    }

    void VerifyOpen()
    {
        ui.VerifyAccess();
        ObjectDisposedException.ThrowIf(closed, this);
    }
}
