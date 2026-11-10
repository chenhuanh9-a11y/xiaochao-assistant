using System.ComponentModel;
using System.IO;
using System.Windows;
using Microsoft.Win32;
using StarMap.Bridge;

namespace StarMap.Desktop;

public partial class MainWindow : Window
{
    private readonly StartupOptions options;
    private IWorkerConnection? worker;
    private Task? connectionTask;
    private readonly Func<string, string, IWorkerConnection> createWorker;
    private readonly string frameRoot;
    private readonly CaptureExclusionOwner exclusion;
    private readonly AlignedOverlayWindow overlay;
    private NativeCaptureService? nativeCapture;
    private string? selectedRoot;
    private bool initialized;
    private bool connecting, closing, closeReady;
    public GalleryViewModel Gallery { get; } = new();
    public RuntimeViewModel Runtime { get; } = new();

    public MainWindow(StartupOptions options, Func<string, string, IWorkerConnection>? workerFactory = null)
    {
        this.options = options;
        exclusion = new(Dispatcher, () => closing);
        var dataRoot = options.DataRoot ?? Path.Combine(options.SourceRoot, "work", "hybrid-desktop", Guid.NewGuid().ToString("N"));
        frameRoot = Path.Combine(dataRoot, "frames");
        createWorker = workerFactory ?? ((root, installation) =>
        {
            Directory.CreateDirectory(frameRoot);
            if (LiveModeSwitch.IsChecked == true)
            {
                nativeCapture = new(exclusion.CaptureAsync);
                Runtime.NativeContextIsCurrent = nativeCapture.IsRenderCurrent;
                return HybridWorker.Start(options.Python, Path.Combine(options.SourceRoot, "hybrid", "python", "runtime_worker.py"),
                    root, Path.Combine(dataRoot, "live"), frameRoot, installation, captureService: nativeCapture);
            }
            return HybridWorker.Start(options.Python, Path.Combine(options.SourceRoot, "hybrid", "python", "runtime_worker.py"),
                root, dataRoot, frameRoot, installation);
        });
        InitializeComponent();
        exclusion.Register(this);
        overlay = new(Runtime, render => nativeCapture?.IsRenderCurrent(render) == true);
        exclusion.Register(overlay);
        DataContext = Gallery;
        Gallery.IsVisible = false;
        HomePage.DataContext = Runtime;
        Runtime.IsVisible = true;
        Runtime.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName is nameof(RuntimeViewModel.HasRound) or nameof(RuntimeViewModel.Busy) or nameof(RuntimeViewModel.ServiceUnavailable)) UpdateResourceActions();
        };
        Gallery.PropertyChanged += (_, e) => { if (e.PropertyName == nameof(GalleryViewModel.Busy)) UpdateResourceActions(); };
        initialized = true;
        LiveModeSwitch.IsChecked = options.LiveCapture;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (options.ResourceRoot is not null) await ConnectAsync(options.ResourceRoot);
    }

    private async void OnOpenResources(object sender, RoutedEventArgs e)
    {
        var picker = new OpenFolderDialog { Title = "选择包含 references、assets 的本机资源目录", Multiselect = false };
        if (picker.ShowDialog(this) == true) await ConnectAsync(picker.FolderName);
    }

    public Task ConnectAsync(string selectedRoot)
    {
        if (connecting || closing || (Runtime.HasRound && !Runtime.ServiceUnavailable)) return Task.CompletedTask;
        return connectionTask = ConnectCoreAsync(selectedRoot);
    }

    private async Task ConnectCoreAsync(string selectedRoot)
    {
        if (connecting || closing) return;
        connecting = true;
        OpenResourcesButton.IsEnabled = false;
        RefreshButton.IsEnabled = false;
        try
        {
            Gallery.Detach();
            Runtime.Detach();
            if (worker is not null)
            {
                var previous = worker;
                worker = null;
                try { await previous.DisposeAsync(); }
                catch (IOException) when (previous.HasExited) { /* A crashed worker cannot block an explicit new connection. */ }
            }
            if (nativeCapture is not null) await nativeCapture.DisposeAsync();
            await exclusion.DrainAsync();
            nativeCapture = null; Runtime.NativeContextIsCurrent = null;
            if (closing) return;
            var installation = Path.TrimEndingDirectorySeparator(Path.GetFullPath(selectedRoot));
            if (Path.GetFileName(installation).Equals("_internal", StringComparison.OrdinalIgnoreCase))
                installation = Directory.GetParent(installation)!.FullName;
            var root = Directory.Exists(Path.Combine(installation, "_internal"))
                ? Path.Combine(installation, "_internal") : installation;
            worker = createWorker(root, installation);
            this.selectedRoot = root;
            ResourcePathLabel.Text = root;
            ResourcePathLabel.ToolTip = root;
            await Gallery.AttachAsync(worker);
            if (!closing) await Runtime.AttachAsync(worker, frameRoot);
            if (!closing && Runtime.IsLive) Runtime.SetGameWindows(GameWindowCatalog.Read());
        }
        catch (Exception error)
        {
            if (!closing) MessageBox.Show(this, error.Message, "资源服务未连接", MessageBoxButton.OK, MessageBoxImage.Error);
        }
        finally
        {
            connecting = false;
            if (!closing) UpdateResourceActions();
        }
    }

    private async void OnRefresh(object sender, RoutedEventArgs e) => await Gallery.ReloadAsync();
    private void UpdateResourceActions()
    {
        var choose = !closing && !connecting && !Runtime.Busy && (!Runtime.HasRound || Runtime.ServiceUnavailable);
        var refresh = !closing && !connecting && !Runtime.Busy && !Gallery.Busy;
        if (OpenResourcesButton.IsEnabled != choose) OpenResourcesButton.IsEnabled = choose;
        if (RefreshButton.IsEnabled != refresh) RefreshButton.IsEnabled = refresh;
        if (LiveModeSwitch.IsEnabled != choose) LiveModeSwitch.IsEnabled = choose;
    }

    private async void OnCaptureModeChanged(object sender, RoutedEventArgs e)
    {
        if (!initialized || closing || selectedRoot is null) return;
        if (Runtime.HasRound && !Runtime.ServiceUnavailable)
        {
            LiveModeSwitch.IsChecked = worker?.EvidenceKind == "live-client-capture";
            return;
        }
        await ConnectAsync(selectedRoot);
    }
    private void OnRefreshWindows(object? sender, EventArgs e)
    {
        if (Runtime.CanChangeContext) Runtime.SetGameWindows(GameWindowCatalog.Read());
    }
    private async void OnBindWindow(object? sender, EventArgs e)
    {
        if (!Runtime.CanBindWindow || nativeCapture is null || Runtime.SelectedGameWindow is not { } selected) return;
        try
        {
            nativeCapture.SelectTarget(selected.Hwnd);
            await Runtime.BindCaptureAsync(selected.Hwnd);
        }
        catch (Exception error) { MessageBox.Show(this, error.Message, "游戏窗口未绑定", MessageBoxButton.OK, MessageBoxImage.Information); }
    }

    private async void OnOpenFrame(object? sender, EventArgs e)
    {
        var picker = new OpenFileDialog { Title = "选择保存的游戏客户区画面", Filter = "PNG 图片|*.png", Multiselect = false };
        if (picker.ShowDialog(this) == true) await LoadFrameAsync(picker.FileName);
    }

    public Task LoadFrameAsync(string path, object? captureContext = null) => Runtime.LoadFrameAsync(path, captureContext);
    public void ShowHome()
    {
        if (HomePage.Visibility == Visibility.Visible) return;
        GalleryPage.Visibility = Visibility.Collapsed;
        Gallery.IsVisible = false;
        HomePage.Visibility = Visibility.Visible;
        Runtime.IsVisible = true;
    }
    public void ShowGallery()
    {
        if (GalleryPage.Visibility == Visibility.Visible) return;
        Runtime.IsVisible = false;
        HomePage.Visibility = Visibility.Collapsed;
        GalleryPage.Visibility = Visibility.Visible;
        Gallery.IsVisible = true;
    }
    private void OnHomeNavigation(object sender, RoutedEventArgs e) => ShowHome();
    private void OnGalleryNavigation(object sender, RoutedEventArgs e) => ShowGallery();

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (closeReady) return;
        e.Cancel = true;
        if (closing) return;
        closing = true;
        IsEnabled = false;
        Gallery.BeginClose();
        Runtime.BeginClose();
        try
        {
            try { if (worker is not null) await worker.DisposeAsync(); }
            finally
            {
                try { if (connectionTask is not null) await connectionTask; }
                finally
                {
                    await Runtime.DrainCopiesAsync();
                    if (nativeCapture is not null) await nativeCapture.DisposeAsync();
                    await exclusion.DrainAsync();
                }
            }
        }
        catch (Exception error) { MessageBox.Show(this, error.Message, "资源服务退出异常", MessageBoxButton.OK, MessageBoxImage.Warning); }
        finally { overlay.Dispose(); closeReady = true; Close(); }
    }
}
