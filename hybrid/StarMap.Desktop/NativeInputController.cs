using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Threading;
using StarMap.Bridge;
using StarMap.Windows;

namespace StarMap.Desktop;

/// <summary>UI owner consuming the bounded native stream; no business in the hook.</summary>
public sealed class NativeInputController(Window owner, RuntimeViewModel runtime,
    Func<WindowSnapshot?> currentTarget) : IAsyncDisposable
{
    private readonly PassThroughKeyboardSource source = new();
    private readonly CancellationTokenSource stopping = new();
    private NativeInputPolicy? policy;
    private Task? consumer;
    private Task? closeTask;
    private bool closed;
    private long boundHwnd;
    public bool Stopped { get; private set; }

    public async Task StartAsync(IWorkerConnection worker)
    {
        var response = await worker.InvokeAsync("input_preferences", cancellation: stopping.Token);
        if (closed) return;
        if (!RuntimeViewModel.Flag(response, "available"))
        { runtime.InputStatus = RuntimeViewModel.String(response, "error", "快捷键设置不可用。"); return; }
        var all = response.GetProperty("bindings").EnumerateArray().ToArray();
        var supported = all.Where(row => RuntimeViewModel.Flag(row, "keyboardSupported")
            && RuntimeViewModel.String(row, "action") != "manual-map-picker").ToArray();
        var bindings = supported.Select(row => new KeyboardShortcut(row.GetProperty("action").GetString()!,
            row.GetProperty("name").GetString()!, row.GetProperty("vk").GetInt32(),
            row.GetProperty("modifiers").EnumerateArray().Select(value => value.GetInt32()).ToArray())).ToArray();
        policy = new(bindings);
        source.UpdateSubscriptions(bindings.Select(binding => binding.VirtualKey).Concat(new[] { 0x5b, 0x5c }));
        KeyboardStartInfo started;
        try { started = await source.StartAsync().WaitAsync(TimeSpan.FromSeconds(2), stopping.Token); }
        catch { await DisposeAsync(); throw; }
        policy.BeginSource(started.Generation, started.InitialHeldKeys);
        if (closed) return;
        boundHwnd = runtime.BoundHwnd;
        runtime.MapInputObserved += Reconcile;
        runtime.PropertyChanged += OnRuntimeChanged;
        var unsupported = all.Except(supported).Select(row => RuntimeViewModel.String(row, "name")).ToArray();
        runtime.InputStatus = unsupported.Length == 0 ? "全局快捷键已连接；游戏地图键保持穿透，请先开始本局。"
            : "全局键盘快捷键已连接；尚不支持的绑定：" + string.Join("、", unsupported) + "（鼠标、单独修饰键或手动选图）。";
        consumer = ConsumeAsync();
    }

    private void Reconcile(long sequence, bool open) => policy?.Reconcile(sequence, open);
    private void OnRuntimeChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        if (e.PropertyName != nameof(RuntimeViewModel.BoundHwnd) || boundHwnd == runtime.BoundHwnd) return;
        boundHwnd = runtime.BoundHwnd;
        policy?.ResetMap();
    }

    private async Task ConsumeAsync()
    {
        try
        {
            await foreach (var edge in source.ReadAllAsync(stopping.Token).ConfigureAwait(false))
            {
                // At most one queued UI wake-up. While UI is occupied the
                // source's bounded channel remains the sole pending mailbox.
                await owner.Dispatcher.InvokeAsync(() => Process(edge), DispatcherPriority.Input, stopping.Token);
            }
            if (!closed && source.FailureMessage is { Length: > 0 } failure) throw new IOException(failure);
        }
        catch (OperationCanceledException) when (closed) { }
        catch (Exception error)
        {
            if (!closed) await owner.Dispatcher.InvokeAsync(() =>
                runtime.InputStatus = "全局快捷键已停止，请重新连接资源目录：" + error.Message);
        }
    }

    private void Process(KeyboardEdge edge)
    {
        if (closed || policy is null) return;
        var foreground = GetForegroundWindow();
        WindowSnapshot? target;
        try { target = currentTarget(); }
        catch (Exception) { target = null; } // Lost game never disables management shortcuts.
        var game = target is not null && target.Identity.Hwnd.ToInt64() == runtime.BoundHwnd
            && !target.Minimized && target.Visible && edge.ForegroundHwnd == target.Identity.Hwnd
            && target.ForegroundHwnd == edge.ForegroundHwnd
            && foreground == edge.ForegroundHwnd;
        var textEntry = ComponentDispatcher.IsThreadModal || owner.IsActive &&
            (Keyboard.FocusedElement is TextBoxBase or PasswordBox
             || Keyboard.FocusedElement is ComboBox { IsEditable: true });
        var intent = policy.Accept(new(edge.VirtualKey, edge.PhysicalVirtualKey, edge.IsDown, edge.WasDown,
            edge.Timestamp, edge.ForegroundHwnd, edge.Generation), Stopwatch.GetTimestamp(), game, textEntry);
        if (intent is null || foreground != edge.ForegroundHwnd) return;
        if (intent.Action == "toggle-panel")
        {
            if (owner.IsVisible) owner.Hide();
            else { owner.Show(); if (owner.WindowState == WindowState.Minimized) owner.WindowState = WindowState.Normal; owner.Activate(); }
            return;
        }
        _ = DispatchAsync(intent);
    }

    private async Task DispatchAsync(ShortcutIntent intent)
    {
        try
        {
            if (intent.Action is "map" or "escape")
                await runtime.HandleMapInputAsync(intent.Sequence, intent.OpenIntent, runtime.BoundHwnd, intent.AgeSeconds);
            else await runtime.HandleShortcutAsync(intent.Action);
        }
        catch (Exception error) { if (!closed) runtime.InputStatus = "快捷键操作未完成：" + error.Message; }
    }

    public ValueTask DisposeAsync() => new(closeTask ??= CloseCoreAsync());

    private async Task CloseCoreAsync()
    {
        if (closed) return;
        closed = true;
        runtime.MapInputObserved -= Reconcile;
        runtime.PropertyChanged -= OnRuntimeChanged;
        stopping.Cancel();
        var result = await source.CloseAsync();
        Stopped = result.Stopped;
        if (consumer is not null) await consumer.WaitAsync(TimeSpan.FromSeconds(2));
        if (!result.Stopped || result.FailureMessage is { Length: > 0 })
            throw new IOException("全局快捷键未能正常停止：" + result.FailureMessage);
        stopping.Dispose();
    }

    [DllImport("user32.dll")] private static extern nint GetForegroundWindow();
}
