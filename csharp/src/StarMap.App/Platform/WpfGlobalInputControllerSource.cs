using StarMap.Core.Input;

namespace StarMap.App.Platform;

/// <summary>OS boundary only. The concrete adapter preserves the production source's serial,
/// bounded notifications; no UI or business callback is moved into the native hook.</summary>
internal interface IWpfGlobalInputSource : IAsyncDisposable
{
    event Action<GlobalInputNotification>? ObservationReceived;
    double NowSeconds { get; }
    Task<GlobalInputStartResult> StartAsync();
    Task<GlobalInputSnapshot> ConfigureAsync(GlobalInputBindings bindings);
    Task<GlobalInputStopResult> StopAsync(TimeSpan? timeout = null);
    void CancelModifierTaps();
    void SetPointerTarget(GlobalInputPointerTarget? target);
}

internal sealed class WpfNativeGlobalInputSource(GlobalInputBindings bindings) : IWpfGlobalInputSource
{
    readonly WindowsGlobalInput native = new(bindings);
    public event Action<GlobalInputNotification>? ObservationReceived
    { add => native.ObservationReceived += value; remove => native.ObservationReceived -= value; }
    public double NowSeconds => WindowsGlobalInput.MonotonicSeconds;
    public Task<GlobalInputStartResult> StartAsync() => native.StartAsync();
    public Task<GlobalInputSnapshot> ConfigureAsync(GlobalInputBindings next) => native.ConfigureAsync(next);
    public Task<GlobalInputStopResult> StopAsync(TimeSpan? timeout = null) => native.StopAsync(timeout);
    public void CancelModifierTaps() => native.CancelModifierTaps();
    public void SetPointerTarget(GlobalInputPointerTarget? target) => native.SetPointerTarget(target);
    public ValueTask DisposeAsync() => native.DisposeAsync();
}
