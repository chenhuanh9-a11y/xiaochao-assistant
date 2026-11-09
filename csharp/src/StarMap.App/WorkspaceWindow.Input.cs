using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Settings;

namespace StarMap.App;

public partial class WorkspaceWindow
{
    WpfGlobalInputController? inputController;
    Task? inputOperation;
    bool inputWanted, inputSettingsPending;
    readonly TextBlock inputState = new() { Text = "快捷键未启用。", TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray };
    readonly Button enableInput = new() { Content = "启用快捷键", Padding = new(13, 7, 13, 7), Margin = new(0, 0, 10, 8) };
    readonly Button disableInput = new() { Content = "停用快捷键", Padding = new(13, 7, 13, 7), Margin = new(0, 0, 10, 8), IsEnabled = false };

    FrameworkElement BuildInputPanel()
    {
        var panel = new StackPanel { Margin = new(0, 16, 0, 0) };
        var controls = new WrapPanel(); controls.Children.Add(enableInput); controls.Children.Add(disableInput);
        panel.Children.Add(controls); panel.Children.Add(inputState);
        enableInput.Click += async (_, _) => await EnableInput();
        disableInput.Click += async (_, _) => await DisableInput();
        return panel;
    }

    void InitializeInput()
    {
        inputController = new(Dispatcher, GlobalInputBindings.FromSettings(SettingsDocument.CreateNew()), ReadInputContext, ConsumeInput);
        // Original startRound prepares even without a game/input permission.
        // An explicit start also attempts the observer when a window is bound.
        if (runtimePage is not null) runtimePage.PrepareInput = async () => { if (ValidInputBinding()) await EnableInput(); };
        GotKeyboardFocus += (_, _) => RefreshInputScope();
        LostKeyboardFocus += (_, _) => RefreshInputScope();
        Activated += (_, _) => RefreshInputScope(); Deactivated += (_, _) => RefreshInputScope();
        PreviewKeyDown += LocalPanelKey;
        UpdateInputControls();
    }

    WpfGlobalInputContext ReadInputContext()
    {
        var binding = runtimeCapture.Binding;
        if (!inputWanted || closing || closed) return new(new(), BindingGeneration: binding?.Generation ?? 0);
        bool valid = ValidInputBinding();
        var scope = WindowsGlobalInput.ReadScope(binding?.Identity.Handle.ToInt64() ?? 0, valid, managementEnabled: true);
        bool ownText = scope.ForegroundIsAssistant && Keyboard.FocusedElement is FrameworkElement { IsVisible: true } focus
            && (focus is TextBoxBase or PasswordBox || focus is ComboBox { IsEditable: true });
        bool editing = scope.ForegroundIsAssistant && (hotkey.IsKeyboardFocusWithin || actionHotkeys.Any(editor => editor.IsKeyboardFocusWithin));
        return new(scope with { GameWindowExists = valid, TextEntryActive = ownText, EditingBindings = editing },
            BindingGeneration: binding?.Generation ?? 0, LocalPanelWindow: new WindowInteropHelper(this).Handle.ToInt64());
    }

    bool ValidInputBinding()
    {
        if (runtimeCapture.Binding is not { } binding) return false;
        try { CaptureNative.ValidateIdentity(binding.Identity, requireForeground: false); return true; }
        catch (Exception error) when (error is ClientCaptureException or Win32Exception or InvalidOperationException or ArgumentException) { return false; }
    }

    void RefreshInputScope()
    {
        if (closed || closing) return;
        inputController?.RefreshScope(); UpdateInputControls();
    }

    async Task<bool> EnableInput()
    {
        if (closed || closing || inputController is null) return false;
        if (inputOperation is { IsCompleted: false }) { inputState.Text = "快捷键状态正在更新，请稍候。"; return false; }
        if (!ValidInputBinding()) { inputState.Text = "请先连接可用的游戏窗口，再启用快捷键。"; return false; }
        await ApplyInputSettings();
        if (inputController is null || inputOperation is { IsCompleted: false }) return false;
        try
        {
            // Validate again: a failed settings projection must never enable old defaults.
            _ = GlobalInputBindings.FromSettings(settings ?? SettingsDocument.CreateNew());
            if (inputController.IsReady) return true;
            inputWanted = true;
            var start = inputController.StartAsync(); inputOperation = start; UpdateInputControls();
            var result = await start;
            if (closed || closing || !inputWanted) return false;
            inputWanted = result.Started;
            inputState.Text = result.Started ? "快捷键已启用，原按键继续交给游戏。" : "快捷键未启用：" + result.Error;
            return result.Started;
        }
        catch (Exception error) when (error is ArgumentException or InvalidOperationException or Win32Exception)
        { inputWanted = false; inputState.Text = "快捷键未启用：" + error.Message; return false; }
        finally
        {
            inputOperation = null; UpdateInputControls();
            if (inputSettingsPending && !closed && !closing) await ApplyInputSettings();
        }
    }

    async Task ApplyInputSettings()
    {
        if (closed || closing || inputController is null) return;
        if (inputOperation is { IsCompleted: false }) { inputSettingsPending = true; inputState.Text = "设置已保存，快捷键配置等待当前操作完成。"; return; }
        inputSettingsPending = false;
        try
        {
            var next = GlobalInputBindings.FromSettings(settings ?? SettingsDocument.CreateNew());
            if (inputController.ActiveBindings.Items.SequenceEqual(next.Items)) return;
            var configure = inputController.ConfigureAsync(next); inputOperation = configure; UpdateInputControls();
            await configure;
            if (!closed && !closing) inputState.Text = inputController.IsReady ? "新快捷键已生效。" : "快捷键配置已准备好，尚未启用监听。";
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is ArgumentException or InvalidOperationException or Win32Exception)
        {
            await DisableInput();
            if (!closed && !closing) inputState.Text = "快捷键配置未应用：" + error.Message;
        }
        finally
        {
            inputOperation = null; UpdateInputControls();
            if (inputSettingsPending && !closed && !closing) await ApplyInputSettings();
        }
    }

    async Task DisableInput()
    {
        if (inputController is null) return;
        inputWanted = false; runtimePage?.SuspendInput("input-stopped");
        var stopped = await inputController.StopAsync();
        if (!closed && !closing) inputState.Text = stopped.Stopped ? "快捷键已关闭。" : "快捷键仍在退出，请稍候重试：" + stopped.Error;
        UpdateInputControls();
    }

    async Task<bool> CloseInput()
    {
        if (inputController is null) return true;
        inputWanted = false; UpdateInputControls();
        var stopped = await inputController.StopAsync();
        if (!stopped.Stopped) { inputState.Text = "输入监听仍在退出，请稍候再关闭：" + stopped.Error; return false; }
        await inputController.DisposeAsync(); inputController = null;
        return true;
    }

    void ConsumeInput(WpfGlobalInputDelivery delivery)
    {
        if (closed || closing) return;
        switch (delivery.Notification.Kind)
        {
            case GlobalInputNotificationKind.Started: inputState.Text = "快捷键已启用，原按键继续交给游戏。"; UpdateInputControls(); break;
            case GlobalInputNotificationKind.Warning: inputState.Text = "快捷键提示：" + delivery.Notification.Message; break;
            case GlobalInputNotificationKind.Failed:
            case GlobalInputNotificationKind.Stopped:
                inputWanted = false; runtimePage?.SuspendInput("input-stopped");
                inputState.Text = "快捷键已停止：" + delivery.Notification.Message; UpdateInputControls(); break;
        }
        foreach (var action in delivery.Dispatch.Actions)
        {
            if (action == "toggle-panel") { TogglePanel(); continue; }
            if (action == "toggle-sheet-visibility") { Status.Text = "悬浮路线显示仍在迁移，当前尚未启用。"; continue; }
            if (action == "manual-map-picker") { Status.Text = "手动选图快捷面板仍在迁移，请在主页面确认地图。"; continue; }
            bool starts = action == "start-round" || action == "toggle-round" && runtimePage?.State?.RoundActive != true;
            if (starts && (settingsBusy || resourcesBusy)) { Status.Text = "请等待设置完成，再开始本局。"; continue; }
            runtimePage?.HandleShortcut(action, delivery.Notification.Key?.CreatedAtSeconds);
        }
    }

    void LocalPanelKey(object sender, KeyEventArgs e)
    {
        if (closed || closing || e.IsRepeat || inputController?.IsReady != true) return;
        var context = ReadInputContext();
        if (!context.Scope.ForegroundIsAssistant || context.Scope.TextEntryActive || context.Scope.EditingBindings) return;
        int vk = KeyInterop.VirtualKeyFromKey(e.Key == Key.System ? e.SystemKey : e.Key);
        if (!GlobalInputDispatcher.HasApplicationLocalPanelOwner(vk) || Keyboard.Modifiers.HasFlag(ModifierKeys.Windows)) return;
        var held = (Keyboard.Modifiers.HasFlag(ModifierKeys.Control) ? GlobalInputModifiers.Ctrl : 0)
            | (Keyboard.Modifiers.HasFlag(ModifierKeys.Alt) ? GlobalInputModifiers.Alt : 0)
            | (Keyboard.Modifiers.HasFlag(ModifierKeys.Shift) ? GlobalInputModifiers.Shift : 0);
        if (inputController.ActiveBindings.Items.Any(binding => binding.Action == "toggle-panel" && binding.VirtualKey == vk && binding.Modifiers == held)) TogglePanel();
        // Original key always continues to WPF/Windows.
    }

    void TogglePanel() { if (IsVisible) Hide(); else Show(); }

    void UpdateInputControls()
    {
        enableInput.IsEnabled = !closed && !closing && inputOperation is not { IsCompleted: false } && inputController?.IsReady != true;
        disableInput.IsEnabled = !closed && !closing && (inputWanted || inputController?.ListeningRequested == true);
    }
}
