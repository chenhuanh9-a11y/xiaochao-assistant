using System.ComponentModel;
using System.IO;
using System.Windows;
using Microsoft.Win32;
using StarMap.Bridge;

namespace StarMap.Desktop;

public partial class MainWindow : Window
{
    private readonly StartupOptions options;
    private PythonWorker? worker;
    private Task? connectionTask;
    private readonly Func<string, string, PythonWorker> createWorker;
    private bool connecting, closing, closeReady;
    public GalleryViewModel Gallery { get; } = new();

    public MainWindow(StartupOptions options, Func<string, string, PythonWorker>? workerFactory = null)
    {
        this.options = options;
        createWorker = workerFactory ?? ((root, installation) => PythonWorker.Start(options.Python,
            Path.Combine(options.SourceRoot, "hybrid", "python", "worker.py"), root, installation));
        InitializeComponent();
        DataContext = Gallery;
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
        if (connecting || closing) return Task.CompletedTask;
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
            if (worker is not null)
            {
                var previous = worker;
                worker = null;
                try { await previous.DisposeAsync(); }
                catch (IOException) when (previous.HasExited) { /* A crashed worker cannot block an explicit new connection. */ }
            }
            if (closing) return;
            var installation = Path.TrimEndingDirectorySeparator(Path.GetFullPath(selectedRoot));
            if (Path.GetFileName(installation).Equals("_internal", StringComparison.OrdinalIgnoreCase))
                installation = Directory.GetParent(installation)!.FullName;
            var root = Directory.Exists(Path.Combine(installation, "_internal"))
                ? Path.Combine(installation, "_internal") : installation;
            worker = createWorker(root, installation);
            ResourcePathLabel.Text = root;
            ResourcePathLabel.ToolTip = root;
            await Gallery.AttachAsync(worker);
        }
        catch (Exception error)
        {
            if (!closing) MessageBox.Show(this, error.Message, "资源服务未连接", MessageBoxButton.OK, MessageBoxImage.Error);
        }
        finally
        {
            connecting = false;
            if (!closing) OpenResourcesButton.IsEnabled = RefreshButton.IsEnabled = true;
        }
    }

    private async void OnRefresh(object sender, RoutedEventArgs e) => await Gallery.ReloadAsync();

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (closeReady) return;
        e.Cancel = true;
        if (closing) return;
        closing = true;
        IsEnabled = false;
        Gallery.BeginClose();
        try
        {
            try { if (worker is not null) await worker.DisposeAsync(); }
            finally { if (connectionTask is not null) await connectionTask; }
        }
        catch (Exception error) { MessageBox.Show(this, error.Message, "资源服务退出异常", MessageBoxButton.OK, MessageBoxImage.Warning); }
        finally { closeReady = true; Close(); }
    }
}
