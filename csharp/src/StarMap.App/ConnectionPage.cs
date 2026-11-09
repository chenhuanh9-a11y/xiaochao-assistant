using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using StarMap.App.Platform;

namespace StarMap.App;

/// <summary>Manual binding and one-shot preview. It neither starts a round nor installs input observers.</summary>
internal sealed class ConnectionPage : UserControl, IDisposable
{
    readonly WindowsClientCapture capture = new();
    readonly ComboBox windows = new() { MinWidth = 310, MaxWidth = 720, HorizontalAlignment = HorizontalAlignment.Left };
    readonly TextBlock connection = new() { Text = "尚未连接窗口。", TextWrapping = TextWrapping.Wrap };
    readonly Image preview = new() { Stretch = Stretch.Uniform, MaxHeight = 230 };
    readonly Button captureButton = new() { Content = "3 秒后采集一次", IsEnabled = false };
    readonly Button cancelButton = new() { Content = "取消采集", IsEnabled = false };
    readonly Action<string> status;
    readonly CancellationTokenSource lifetime = new();
    CancellationTokenSource? pending;
    BoundWindow? binding;
    int generation;
    bool disposed;

    internal ConnectionPage(Action<string> status)
    {
        this.status = status;
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "游戏窗口", FontSize = 19, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 14) });
        panel.Children.Add(connection);
        var choose = new DockPanel { Margin = new Thickness(0, 14, 0, 12) };
        var refresh = MakeButton("刷新窗口", Refresh);
        DockPanel.SetDock(refresh, Dock.Right); choose.Children.Add(refresh); choose.Children.Add(windows); panel.Children.Add(choose);
        var actions = new WrapPanel();
        actions.Children.Add(MakeButton("连接所选窗口", Bind));
        actions.Children.Add(MakeButton("断开", (_, _) => Disconnect()));
        captureButton.Margin = new Thickness(0, 0, 10, 8); captureButton.Padding = new Thickness(14, 8, 14, 8); captureButton.Click += Capture;
        cancelButton.Margin = new Thickness(0, 0, 10, 8); cancelButton.Padding = new Thickness(14, 8, 14, 8); cancelButton.Click += (_, _) => { pending?.Cancel(); ClearPreview(); };
        actions.Children.Add(captureButton); actions.Children.Add(cancelButton); panel.Children.Add(actions);
        panel.Children.Add(new TextBlock { Text = "采集前请手动切回所选窗口。只采集它的客户区，不切换前台、不发送按键；当前预览不会执行识别。", Foreground = Brushes.SlateGray, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 14) });
        panel.Children.Add(preview); Content = panel;
        windows.SelectionChanged += (_, _) => Disconnect();
    }

    async void Refresh(object sender, RoutedEventArgs e)
    {
        var button = (Button)sender; button.IsEnabled = false;
        var token = lifetime.Token;
        try
        {
            var result = await Task.Run(() => WindowsClientCapture.EnumerateCandidates(cancellationToken: token), token);
            if (disposed) return;
            windows.ItemsSource = result.Items;
            connection.Text = $"找到 {result.Items.Count} 个窗口，请明确选择。" + (result.Skipped == 0 ? "" : $" {result.Skipped} 项无法检查。") + (result.Truncated ? " 列表已达到上限。" : "");
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is Win32Exception or InvalidOperationException or ClientCaptureException) { connection.Text = "窗口列表不可用：" + error.Message; }
        finally { if (!disposed) button.IsEnabled = true; }
    }

    void Bind(object sender, RoutedEventArgs e)
    {
        if (windows.SelectedItem is not WindowCandidate selected) { connection.Text = "请先刷新并选择窗口。"; return; }
        Disconnect();
        try
        {
            binding = capture.Bind(selected);
            captureButton.IsEnabled = true;
            connection.Text = $"已连接：{binding.Identity.Title} · {binding.GeometryAtBinding.Width}×{binding.GeometryAtBinding.Height}";
            status("窗口已绑定；尚未采集、注册热键或开始本局。");
        }
        catch (Exception error) when (error is ClientCaptureException or Win32Exception or InvalidOperationException or ArgumentException) { connection.Text = "没有连接：" + error.Message; }
    }

    async void Capture(object sender, RoutedEventArgs e)
    {
        if (binding is not { } target || pending is not null) return;
        int request = ++generation;
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token); pending = cancellation;
        captureButton.IsEnabled = false; cancelButton.IsEnabled = true; preview.Source = null;
        try
        {
            for (int count = 3; count > 0; count--) { status($"请手动切回所选窗口，{count} 秒后采集。可随时取消。"); await Task.Delay(1000, cancellation.Token); }
            var frame = await capture.CaptureAsync(target, cancellation.Token);
            if (disposed || cancellation.IsCancellationRequested || request != generation || !ReferenceEquals(binding, target)) return;
            capture.ValidateForCommit(frame);
            preview.Source = frame.Image;
            status($"已读取 {frame.Metadata.After.Width}×{frame.Metadata.After.Height} 客户区画面。这是单次 GDI 预览，尚未识别。" );
        }
        catch (OperationCanceledException) { if (!disposed) status("已取消采集，旧预览已清除。"); }
        catch (Exception error) when (error is ClientCaptureException or Win32Exception or InvalidOperationException or ArgumentException)
        { if (!disposed) { ClearPreview(); status("没有显示画面：" + error.Message); } }
        finally
        {
            if (ReferenceEquals(pending, cancellation)) pending = null;
            if (!disposed) { cancelButton.IsEnabled = false; captureButton.IsEnabled = binding is not null; }
        }
    }

    void ClearPreview() { generation++; preview.Source = null; }
    void Disconnect()
    {
        pending?.Cancel(); capture.Unbind(); binding = null; ClearPreview(); captureButton.IsEnabled = false;
        connection.Text = "尚未连接窗口。";
    }
    static Button MakeButton(string text, RoutedEventHandler handler)
    {
        var button = new Button { Content = text, Padding = new Thickness(14, 8, 14, 8), Margin = new Thickness(0, 0, 10, 8) };
        button.Click += handler; return button;
    }
    public void Dispose()
    {
        if (disposed) return;
        disposed = true; lifetime.Cancel(); pending?.Cancel(); capture.Unbind(); capture.Dispose(); lifetime.Dispose();
    }
}
