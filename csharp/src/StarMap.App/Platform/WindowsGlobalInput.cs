using System.Collections.Concurrent;
using System.Diagnostics;
using System.Runtime.InteropServices;
using StarMap.Core.Input;
using N = StarMap.App.Platform.WindowsGlobalInputNative;

namespace StarMap.App.Platform;

public sealed record GlobalInputStartResult(bool Started, long Generation, bool KeyboardAvailable,
    bool MouseAvailable, GlobalInputSnapshot? Snapshot, string? Error);
public sealed record GlobalInputStopResult(bool Stopped, string? Error);

/// <summary>
/// Explicitly started pass-through WH_KEYBOARD_LL + Raw Input mouse source.
/// Constructor, configuration and the main App never install an observer. StartAsync owns a
/// dedicated message thread; notifications run on a separate serial dispatcher, never in a hook.
/// Subscribers must marshal to their owner/UI thread and revalidate scope at consumption.
/// No RegisterHotKey, mouse hook, NOLEGACY, activation, capture or injected input is used.
/// </summary>
public sealed class WindowsGlobalInput : IDisposable, IAsyncDisposable
{
    readonly object gate = new();
    static readonly object rawOwnerGate = new();
    static WindowsGlobalInput? rawOwner;
    GlobalInputBindings bindings;
    Run? current;
    bool disposed, disposing;
    long generation, pointerGeneration;
    GlobalInputPointerTarget? pointerTarget;
    string? lastSubscriberError;

    sealed record ConfigureCommand(GlobalInputBindings Bindings, TaskCompletionSource<GlobalInputSnapshot> Completion);
    sealed class Run(GlobalInputBindings bindings)
    {
        internal GlobalInputBindings Bindings = bindings;
        internal readonly GlobalInputState State = new();
        internal readonly ConcurrentQueue<ConfigureCommand> Commands = new();
        internal readonly WindowsGlobalInputDelivery Events = new();
        internal readonly TaskCompletionSource<GlobalInputStartResult> Ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal readonly TaskCompletionSource Completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal Thread Thread = null!;
        internal Task Dispatch = Task.CompletedTask;
        internal volatile bool StopRequested;
        internal volatile bool CancelTapsRequested;
        internal volatile uint ThreadId;
        internal nint Hook, MouseWindow;
        internal N.HookProc? Callback;
        internal bool MouseRegistered;
        internal bool MouseFailureReported;
        internal string? Error;
        internal GlobalInputPointerEvent? Gesture;
        internal int StartX, StartY;
        internal long PointerSequence;
        internal GlobalInputPointerTarget? GestureTarget;
    }

    public WindowsGlobalInput(GlobalInputBindings bindings) => this.bindings = bindings ?? throw new ArgumentNullException(nameof(bindings));
    public event Action<GlobalInputNotification>? ObservationReceived;
    public bool IsRunning { get { lock (gate) return current is { StopRequested: false } run && !run.Completion.Task.IsCompleted; } }
    public string? LastSubscriberError => Volatile.Read(ref lastSubscriberError);
    public static double MonotonicSeconds => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;

    public Task<GlobalInputStartResult> StartAsync()
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed || disposing, this);
            if (!OperatingSystem.IsWindows()) return Task.FromResult(new GlobalInputStartResult(false, generation, false, false, null, "Native input requires Windows."));
            if (current is { } existing && (!existing.Completion.Task.IsCompleted || !existing.Dispatch.IsCompleted || existing.Thread.IsAlive))
                return existing.StopRequested
                    ? Task.FromResult(new GlobalInputStartResult(false, existing.State.Generation, false, false, null, "Previous observer is still stopping."))
                    : existing.Ready.Task;
            var run = new Run(bindings);
            current = run;
            run.Dispatch = Task.Run(() => DispatchAsync(run));
            run.Thread = new Thread(() => MessageLoop(run)) { Name = "starmap-pass-through-input", IsBackground = false };
            try { run.Thread.Start(); }
            catch (Exception error)
            {
                run.StopRequested = true; run.Error = error.Message;
                run.Ready.TrySetResult(new(false, generation, false, false, null, run.Error));
                run.Events.Complete(generation, run.Error); run.Completion.TrySetResult();
            }
            return run.Ready.Task;
        }
    }

    /// <summary>Live reconfiguration is serialized on the native owner thread and invalidates queued old edges.</summary>
    public Task<GlobalInputSnapshot> ConfigureAsync(GlobalInputBindings next)
    {
        ArgumentNullException.ThrowIfNull(next);
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed || disposing, this);
            bindings = next;
            if (current is not { } run || run.Completion.Task.IsCompleted)
                return Task.FromResult(new GlobalInputSnapshot(Interlocked.Increment(ref generation), Array.Empty<int>()));
            if (run.StopRequested) throw new InvalidOperationException("The observer is stopping.");
            var command = new ConfigureCommand(next, new(TaskCreationOptions.RunContinuationsAsynchronously));
            run.Commands.Enqueue(command);
            if (run.ThreadId != 0 && !N.PostThreadMessageW(run.ThreadId, N.Command, 0, 0))
            {
                command.Completion.TrySetException(N.Error("Post configuration command"));
                RequestStop(run);
            }
            return command.Completion.Task;
        }
    }

    /// <summary>
    /// Explicit cached context only. Same-window refresh affects the next press; an active drag keeps
    /// its original immutable context. Set null on session/floor/resource invalidation. Does not start listening.
    /// </summary>
    public void SetPointerTarget(GlobalInputPointerTarget? target)
    {
        if (target is not null && (target.Window <= 0 || string.IsNullOrEmpty(target.ContextId)
            || !double.IsFinite(target.ObservationCreatedAtSeconds))) throw new ArgumentException("Invalid pointer target.", nameof(target));
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed || disposing, this);
            if (pointerTarget == target) return;
            var targetChanged = pointerTarget?.Window != target?.Window;
            pointerTarget = target;
            if (!targetChanged) return;
            pointerGeneration++;
            if (current is { ThreadId: not 0 } run && !run.StopRequested)
                N.PostThreadMessageW(run.ThreadId, N.Command, 0, 0);
        }
    }

    /// <summary>Call together with dispatcher.CancelModifierTaps when text entry/editing or listening intent changes.</summary>
    public void CancelModifierTaps()
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed || disposing, this);
            if (current is not { } run || run.StopRequested) return;
            run.CancelTapsRequested = true;
            if (run.ThreadId != 0) N.PostThreadMessageW(run.ThreadId, N.Command, 0, 0);
        }
    }

    public async Task<GlobalInputStopResult> StopAsync(TimeSpan? timeout = null)
    {
        var budget = timeout ?? TimeSpan.FromSeconds(2);
        if (budget <= TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(timeout));
        var elapsed = Stopwatch.StartNew();
        Run? run;
        lock (gate) { run = current; if (run is null) return new(true, null); RequestStop(run); }
        try
        {
            await Task.WhenAll(run.Completion.Task, run.Dispatch).WaitAsync(budget).ConfigureAwait(false);
            var remaining = budget - elapsed.Elapsed;
            if (run.Thread.IsAlive && !run.Thread.Join(remaining > TimeSpan.Zero ? remaining : TimeSpan.Zero))
                return new(false, "Observer cleanup finished but its owner thread has not exited.");
            return new(true, run.Error);
        }
        catch (TimeoutException) { return new(false, "Observer is still stopping; no thread was terminated and restart remains blocked."); }
    }

    static void RequestStop(Run run)
    {
        run.StopRequested = true;
        if (run.ThreadId != 0 && !N.PostThreadMessageW(run.ThreadId, N.Quit, 0, 0))
            run.Error ??= N.Error("Post observer stop").Message;
    }

    void Queue(Run run, GlobalInputNotification item)
    {
        if (run.Events.TryPost(item)) return;
        // A stalled subscriber must not accumulate unbounded input or invent a fresh edge later.
        run.Error = "Input notification queue overflowed; observer stopped and held state must be reseeded.";
        RequestStop(run);
    }

    Task DispatchAsync(Run run) => run.Events.DispatchAsync(Publish);

    void Publish(GlobalInputNotification item)
    {
        if (ObservationReceived is not { } subscribers) return;
        foreach (Action<GlobalInputNotification> subscriber in subscribers.GetInvocationList())
            try { subscriber(item); }
            catch (Exception error) { Volatile.Write(ref lastSubscriberError, $"{error.GetType().Name}: {error.Message}"); }
    }

    GlobalInputSnapshot ResetState(Run run)
    {
        var initiallyHeld = Enumerable.Range(1, 255).Where(key => !GlobalInputState.IsBareModifier(key) && N.GetAsyncKeyState(key) < 0).ToArray();
        return run.State.Reset(run.Bindings, Interlocked.Increment(ref generation), initiallyHeld);
    }

    void MessageLoop(Run run)
    {
        try
        {
            N.PeekMessageW(out _, 0, 0, 0, 0); // Create the queue before publishing its thread ID.
            run.ThreadId = N.GetCurrentThreadId();
            if (run.StopRequested) return;
            var snapshot = ResetState(run);
            run.Callback = (code, message, pointer) =>
            {
                try
                {
                    if (code >= 0 && !run.StopRequested && message is 0x100 or 0x101 or 0x104 or 0x105)
                    {
                        ApplyTapCancellation(run);
                        var key = Marshal.PtrToStructure<N.Keyboard>(pointer);
                        var edge = run.State.Transition((int)key.VirtualKey, (int)key.Scan, key.Flags,
                            message is 0x100 or 0x104, (long)N.GetForegroundWindow(), MonotonicSeconds,
                            other => N.GetAsyncKeyState(other) < 0);
                        if (edge is not null) Queue(run, new(GlobalInputNotificationKind.Key, edge.Generation, Key: edge));
                    }
                }
                catch (Exception error) { run.Error = $"Keyboard callback failed: {error.Message}"; RequestStop(run); }
                // Every path, including negative codes, stop, parse failure and queue overflow, passes through.
                return N.CallNextHookEx(0, code, message, pointer);
            };
            run.Hook = N.SetWindowsHookExW(N.KeyboardLowLevel, run.Callback, N.GetModuleHandleW(null), 0);
            if (run.Hook == 0) throw N.Error("Install pass-through keyboard hook");
            try { StartMouse(run); run.State.MouseObservationAvailable = true; }
            catch (Exception error) { Queue(run, new(GlobalInputNotificationKind.Warning, run.State.Generation, Code: "raw-mouse-unavailable", Message: error.Message)); }
            Queue(run, new(GlobalInputNotificationKind.Started, snapshot.Generation, Snapshot: snapshot));
            run.Ready.TrySetResult(new(true, snapshot.Generation, true, run.MouseRegistered, snapshot, null));
            while (!run.StopRequested)
            {
                DrainCommands(run);
                var result = N.GetMessageW(out var message, 0, 0, 0);
                if (result == -1) throw N.Error("Read observer message");
                if (result == 0) break;
                if (message.Id == N.Command) { DrainCommands(run); continue; }
                if (message.Window == run.MouseWindow && message.Id == N.Input)
                {
                    try { ReceiveMouse(run, message); }
                    catch (Exception error)
                    {
                        run.State.CancelModifierTaps();
                        CancelPointer(run, "raw-mouse-observation-failed");
                        if (!run.MouseFailureReported)
                        {
                            run.MouseFailureReported = true;
                            Queue(run, new(GlobalInputNotificationKind.Warning, run.State.Generation, Code: "raw-mouse-packet-failed", Message: error.Message));
                        }
                    }
                    finally { N.DefWindowProcW(message.Window, message.Id, message.WParam, message.LParam); }
                    continue;
                }
                N.TranslateMessage(in message); N.DispatchMessageW(in message);
            }
        }
        catch (Exception error) { run.Error = $"{error.GetType().Name}: {error.Message}"; }
        finally
        {
            run.StopRequested = true;
            // Delivery owns final cancellation from the last gesture actually published. It must
            // not compete with ordinary events for a slot in a possibly saturated channel.
            try { run.State.CancelModifierTaps(); run.Gesture = null; run.GestureTarget = null; }
            catch (Exception error) { run.Error = JoinError(run.Error, "Cancel input state: " + error.Message); }
            try { StopMouse(run); }
            catch (Exception error) { run.Error = JoinError(run.Error, "Mouse cleanup: " + error.Message); }
            try
            {
                if (run.Hook != 0 && !N.UnhookWindowsHookEx(run.Hook)) run.Error = JoinError(run.Error, N.Error("Unregister keyboard hook").Message);
            }
            catch (Exception error) { run.Error = JoinError(run.Error, "Keyboard cleanup: " + error.Message); }
            run.Hook = 0; run.ThreadId = 0;
            while (run.Commands.TryDequeue(out var command)) command.Completion.TrySetException(new InvalidOperationException("Observer stopped before configuration."));
            run.Ready.TrySetResult(new(false, run.State.Generation, false, false, null, run.Error ?? "Observer stopped before installation."));
            run.Events.Complete(run.State.Generation, run.Error);
            // Keep the delegate rooted until after unhook/owner cleanup. No forced thread termination.
            GC.KeepAlive(run.Callback);
            run.Completion.TrySetResult();
        }
    }

    void DrainCommands(Run run)
    {
        ApplyTapCancellation(run);
        while (run.Commands.TryDequeue(out var command))
        {
            try
            {
                run.Bindings = command.Bindings;
                var snapshot = ResetState(run);
                CancelPointer(run, "input-bindings-changed");
                Queue(run, new(GlobalInputNotificationKind.Started, snapshot.Generation, Snapshot: snapshot));
                command.Completion.TrySetResult(snapshot);
            }
            catch (Exception error) { command.Completion.TrySetException(error); throw; }
        }
        GlobalInputPointerTarget? target; long pointerVersion;
        lock (gate) { target = pointerTarget; pointerVersion = pointerGeneration; }
        if (run.Gesture is not null && (run.GestureTarget?.Window != target?.Window
            || run.Gesture.PointerGeneration != pointerVersion))
            CancelPointer(run, "pointer-target-changed");
    }

    static void ApplyTapCancellation(Run run)
    {
        if (!run.CancelTapsRequested) return;
        run.CancelTapsRequested = false;
        run.State.CancelModifierTaps();
    }

    static string JoinError(string? previous, string next) => previous is null ? next : previous + "; " + next;

    public void Dispose()
    {
        lock (gate) { if (disposed) return; disposing = true; }
        var result = StopAsync().GetAwaiter().GetResult();
        if (!result.Stopped) throw new InvalidOperationException(result.Error);
        lock (gate) disposed = true;
        GC.SuppressFinalize(this);
    }
    public async ValueTask DisposeAsync()
    {
        lock (gate) { if (disposed) return; disposing = true; }
        var result = await StopAsync().ConfigureAwait(false);
        if (!result.Stopped) throw new InvalidOperationException(result.Error);
        lock (gate) disposed = true;
        GC.SuppressFinalize(this);
    }

    /// <summary>Optional dispatch-time fact read. Never activates or validates a capture binding.</summary>
    public static GlobalInputScope ReadScope(long gameWindow, bool gameEnabled, bool managementEnabled,
        bool textEntryActive = false, bool editingBindings = false, double gameEventsAfterSeconds = 0)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
        var foreground = N.GetForegroundWindow();
        var assistant = foreground != 0 && N.GetWindowThreadProcessId(foreground, out var process) != 0 && process == Environment.ProcessId;
        return new(gameWindow, gameWindow != 0 && N.IsWindow((nint)gameWindow), (long)foreground, assistant,
            gameEnabled, managementEnabled, textEntryActive, editingBindings, gameEventsAfterSeconds);
    }

    void StartMouse(Run run)
    {
        lock (rawOwnerGate)
        {
            if (rawOwner is not null) throw new InvalidOperationException("Mouse raw input already has an owner in this process.");
            if (N.GetAwarenessFromDpiAwarenessContext(N.GetThreadDpiAwarenessContext()) != 2)
                throw new InvalidOperationException("Mouse observation requires the application's per-monitor DPI context.");
            uint count = 0;
            var deviceSize = (uint)Marshal.SizeOf<N.RawDevice>();
            if (N.GetRegisteredRawInputDevices(null, ref count, deviceSize) == uint.MaxValue) throw N.Error("Inspect raw-input owner");
            if (count > 1024) throw new InvalidOperationException("Unexpected raw-input registration count.");
            if (count != 0)
            {
                var rows = new N.RawDevice[count];
                var read = N.GetRegisteredRawInputDevices(rows, ref count, deviceSize);
                if (read == uint.MaxValue) throw N.Error("Read raw-input owner");
                if (read > rows.Length || rows.Take((int)read).Any(row => row.Page == 1 && row.Usage == 2))
                    throw new InvalidOperationException("Mouse raw input already has an owner in this process.");
            }
            run.MouseWindow = N.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, (nint)(-3), 0, 0, 0);
            if (run.MouseWindow == 0) throw N.Error("Create mouse message-only window");
            if (!N.RegisterRawInputDevices([new() { Page = 1, Usage = 2, Flags = N.InputSink, Target = run.MouseWindow }], 1, deviceSize))
                throw N.Error("Register mouse INPUTSINK");
            run.MouseRegistered = true; rawOwner = this;
        }
    }

    void StopMouse(Run run)
    {
        lock (rawOwnerGate)
        {
            if (run.MouseRegistered)
            {
                if (!N.RegisterRawInputDevices([new() { Page = 1, Usage = 2, Flags = N.Remove }], 1, (uint)Marshal.SizeOf<N.RawDevice>()))
                    run.Error = JoinError(run.Error, N.Error("Remove mouse INPUTSINK").Message);
                else { run.MouseRegistered = false; if (rawOwner == this) rawOwner = null; }
            }
            if (run.MouseWindow != 0)
            {
                if (!N.DestroyWindow(run.MouseWindow)) run.Error = JoinError(run.Error, N.Error("Destroy mouse message-only window").Message);
                else run.MouseWindow = 0;
            }
            // A failed unregister retains ownership and blocks replacement; it is not reported as successful cleanup.
        }
    }

    void ReceiveMouse(Run run, N.Message message)
    {
        uint size = 0, headerSize = (uint)Marshal.SizeOf<N.RawHeader>();
        if (N.GetRawInputData(message.LParam, N.ReadInput, 0, ref size, headerSize) == uint.MaxValue) throw N.Error("Size raw mouse packet");
        if (size < headerSize + Marshal.SizeOf<N.RawMouse>() || size > 65536) return;
        var buffer = Marshal.AllocHGlobal((int)size);
        try
        {
            var read = N.GetRawInputData(message.LParam, N.ReadInput, buffer, ref size, headerSize);
            if (read == uint.MaxValue) throw N.Error("Read raw mouse packet");
            if (read < headerSize + Marshal.SizeOf<N.RawMouse>()) return;
            var header = Marshal.PtrToStructure<N.RawHeader>(buffer);
            if (header.Kind != 0 || header.Size != read) return;
            var mouse = Marshal.PtrToStructure<N.RawMouse>(buffer + (int)headerSize);
            var foreground = (long)N.GetForegroundWindow();
            ApplyTapCancellation(run);
            run.State.ObserveMouseActivity(mouse.ButtonFlags);
            var ageMilliseconds = unchecked((uint)N.GetTickCount64() - message.Time);
            var expired = ageMilliseconds > GlobalInputDispatcher.MaximumEventAgeSeconds * 1000;
            var created = MonotonicSeconds - ageMilliseconds / 1000d;
            if (expired) CancelPointer(run, "raw-mouse-input-expired");
            if (run.State.WantsMouseButtons)
                foreach (var (vk, down) in GlobalInputMouse.ButtonTransitions(mouse.ButtonFlags))
                {
                    if (expired && down) continue; // Stale releases still clear both native and subscriber held state.
                    var edge = run.State.Transition(vk, 0, 0, down, foreground, created);
                    if (edge is not null) Queue(run, new(GlobalInputNotificationKind.Key, edge.Generation, Key: edge));
                }
            if (expired) return;
            foreach (var phase in GlobalInputMouse.PointerPhases(mouse.ButtonFlags, mouse.Flags, mouse.LastX, mouse.LastY))
                ObservePointer(run, phase, message.Position.X, message.Position.Y, foreground, created);
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    void ObservePointer(Run run, string phase, int x, int y, long foreground, double created)
    {
        GlobalInputPointerTarget? target; long pointerVersion;
        lock (gate) { target = pointerTarget; pointerVersion = pointerGeneration; }
        if (target is null || target.Window != foreground || run.StopRequested)
        { CancelPointer(run, "pointer-context-unavailable"); return; }
        var previous = run.Gesture;
        if (phase != "down" && previous is null) return;
        if (phase == "down" && created < target.ObservationCreatedAtSeconds)
        { CancelPointer(run, "pointer-press-before-current-frame"); return; }
        if (phase != "down" && (run.GestureTarget?.Window != target.Window || previous?.PointerGeneration != pointerVersion))
        { CancelPointer(run, "pointer-target-changed"); return; }
        var sequence = ++run.PointerSequence;
        if (phase == "down") { run.StartX = x; run.StartY = y; previous = null; run.GestureTarget = target; }
        var pressTarget = run.GestureTarget!;
        var item = new GlobalInputPointerEvent(phase, x, y, created, foreground, target.Window, run.State.Generation,
            pointerVersion, ContextId: pressTarget.ContextId, Sequence: sequence, GestureId: previous?.GestureId ?? sequence,
            DeltaX: previous is null ? 0 : x - previous.X, DeltaY: previous is null ? 0 : y - previous.Y,
            TotalDeltaX: x - run.StartX, TotalDeltaY: y - run.StartY);
        run.Gesture = phase == "up" ? null : item;
        Queue(run, new(GlobalInputNotificationKind.Pointer, item.Generation, Pointer: item));
    }

    void CancelPointer(Run run, string reason)
    {
        if (run.Gesture is not { } gesture) return;
        run.Gesture = null; run.GestureTarget = null;
        var cancel = gesture with { Phase = "cancel", Reason = reason };
        Queue(run, new(GlobalInputNotificationKind.Pointer, gesture.Generation, Pointer: cancel));
    }
}
