using StarMap.App.Platform;
using StarMap.Core.Input;

/// <summary>Replaces native installation and OS event production only. The production edge state,
/// bounded channel, source subscriber, WPF Dispatcher and business-policy owner are real.</summary>
internal sealed class FixtureInputSource(GlobalInputBindings initial) : IWpfGlobalInputSource
{
    readonly GlobalInputState state = new() { MouseObservationAvailable = true };
    readonly HashSet<int> physicalLevels = [];
    GlobalInputBindings bindings = initial;
    WindowsGlobalInputDelivery? channel;
    Task drain = Task.CompletedTask;
    long generation;
    TaskCompletionSource<GlobalInputStartResult>? nativeStart;
    TaskCompletionSource<GlobalInputSnapshot>? nativeConfiguration;
    GlobalInputStartResult? startResult;
    GlobalInputSnapshot? configurationResult;
    int inSubscriber;
    int subscriberKind = -1;

    public event Action<GlobalInputNotification>? ObservationReceived;
    public double NowSeconds { get; set; } = 100;
    public int Starts { get; private set; }
    public int TapCancellations { get; private set; }
    public GlobalInputPointerTarget? PointerTarget { get; private set; }
    public bool DelayStartResult { get; set; }
    public bool DelayConfigurationResult { get; set; }
    public Exception? ConfigurationFailure { get; set; }
    public bool InSubscriber => Volatile.Read(ref inSubscriber) != 0;
    public bool InKeySubscriber => Volatile.Read(ref subscriberKind) == (int)GlobalInputNotificationKind.Key;
    public long Generation => generation;
    public Task Drained => drain;

    public Task<GlobalInputStartResult> StartAsync()
    {
        if (!drain.IsCompleted) throw new InvalidOperationException("Previous fixture source is still draining.");
        Starts++;
        channel = new();
        var active = channel;
        drain = Task.Run(() => active.DispatchAsync(item =>
        {
            Volatile.Write(ref inSubscriber, 1);
            Volatile.Write(ref subscriberKind, (int)item.Kind);
            try { ObservationReceived?.Invoke(item); }
            finally { Volatile.Write(ref subscriberKind, -1); Volatile.Write(ref inSubscriber, 0); }
        }));
        var snapshot = state.Reset(bindings, ++generation, physicalLevels);
        Post(new(GlobalInputNotificationKind.Started, generation, Snapshot: snapshot));
        startResult = new(true, generation, true, true, snapshot, null);
        nativeStart = new(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!DelayStartResult) nativeStart.SetResult(startResult);
        return nativeStart.Task;
    }

    public void ReleaseStartResult() => nativeStart!.TrySetResult(startResult!);

    public Task<GlobalInputSnapshot> ConfigureAsync(GlobalInputBindings next)
    {
        bindings = next;
        if (ConfigurationFailure is { } failure) return Task.FromException<GlobalInputSnapshot>(failure);
        var snapshot = state.Reset(bindings, ++generation, physicalLevels);
        if (channel is null || drain.IsCompleted) return Task.FromResult(snapshot);
        if (!TryPost(new(GlobalInputNotificationKind.Started, generation, Snapshot: snapshot)))
            channel.Complete(generation, "fixture configuration notification overflow");
        configurationResult = snapshot;
        nativeConfiguration = new(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!DelayConfigurationResult) nativeConfiguration.SetResult(snapshot);
        return nativeConfiguration.Task;
    }

    public void ReleaseConfigurationResult() => nativeConfiguration!.TrySetResult(configurationResult!);

    public GlobalInputKeyEvent? Key(int vk, bool down, long foreground = 42,
        double? created = null, int scan = 0, uint flags = 0)
    {
        var edge = state.Transition(vk, scan, flags, down, foreground, created ?? NowSeconds, physicalLevels.Contains);
        var physical = GlobalInputState.PhysicalKey(vk, scan, flags);
        if (down) physicalLevels.Add(physical); else physicalLevels.Remove(physical);
        if (edge is not null) Post(new(GlobalInputNotificationKind.Key, edge.Generation, Key: edge));
        return edge;
    }

    public bool TryPost(GlobalInputNotification item) => channel!.TryPost(item);
    public void Post(GlobalInputNotification item)
    {
        if (!TryPost(item)) throw new InvalidOperationException("Fixture filled the real bounded channel unexpectedly.");
    }
    public void End(string? error = null) => channel!.Complete(generation, error);
    public void CancelModifierTaps() { TapCancellations++; state.CancelModifierTaps(); }
    public void SetPointerTarget(GlobalInputPointerTarget? target) => PointerTarget = target;
    public async Task<GlobalInputStopResult> StopAsync(TimeSpan? timeout = null)
    {
        if (channel is null) return new(true, null);
        End();
        await drain.WaitAsync(timeout ?? TimeSpan.FromSeconds(5)).ConfigureAwait(false);
        return new(true, null);
    }
    public async ValueTask DisposeAsync() { await StopAsync(); }
}
