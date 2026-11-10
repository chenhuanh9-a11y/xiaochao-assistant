using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.Bridge;
using StarMap.Desktop;

internal static class Program
{
    private static int checks;
    private static void Check(bool value, string description)
    {
        if (!value) throw new InvalidOperationException(description);
        Console.WriteLine("PASS " + description);
        checks++;
    }

    [STAThread]
    private static int Main(string[] args)
    {
        var nativePresentationOnly = args.Length == 1 && args[0] == "--native-presentation-only";
        if (!nativePresentationOnly && (args.Length is not (3 or 4 or 5) || (args.Length == 5 && args[4] != "--runtime-only")))
        {
            Console.Error.WriteLine("usage: StarMap.IntegrationTests SOURCE_ROOT PYTHON RESOURCE_ROOT [CURRENT_FRAME_INDEX [--runtime-only]]");
            return 2;
        }
        var exitCode = 1;
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.Resources.MergedDictionaries.Add(new ResourceDictionary
        {
            Source = new Uri("pack://application:,,,/StarMap.Hybrid;component/Theme.xaml")
        });
        app.Startup += async (_, _) =>
        {
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext(app.Dispatcher));
            try
            {
                if (!nativePresentationOnly)
                {
                    if (args.Length != 5) await RunAsync(args[0], args[1], args[2]);
                    if (args.Length >= 4) await RuntimeWorkspaceChecks.RunAsync(args[0], args[1], args[2], args[3]);
                }
                await NativePresentationChecks.RunAsync();
                exitCode = 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); }
            finally { app.Shutdown(); }
        };
        app.Run();
        return exitCode;
    }

    private static async Task RunAsync(string sourceRoot, string python, string resourceRoot)
    {
        var script = Path.Combine(sourceRoot, "hybrid", "python", "worker.py");
        await using var worker = PythonWorker.Start(python, script, resourceRoot);
        var hello = await worker.HelloAsync();
        Check(hello.Contexts.Any(c => c.Season == "s0" && c.Mode == "nightmare"), "real Python handshake carries production contexts");
        var catalog = await worker.CatalogAsync("s0", "nightmare");
        Check(catalog.Rows.Length > 0, "production loader returns fixture rows over real pipes");
        var row = catalog.Rows.First(r => r.Available);
        var preview = await worker.PreviewAsync(catalog.CatalogId, row.Key);
        Check(Convert.FromBase64String(preview.PngBase64).AsSpan(0, 8).SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }), "actual PNG bytes cross Python to C#");
        using (var cancelled = new CancellationTokenSource())
        {
            cancelled.Cancel();
            try { await worker.CatalogAsync("s0", "nightmare", cancelled.Token); throw new Exception("cancel was ignored"); }
            catch (OperationCanceledException) { Check(true, "cancelled queued request is not submitted"); }
        }
        Check((await worker.HelloAsync()).Contexts.Length == hello.Contexts.Length, "cancel does not desynchronize later replies");
        var second = await worker.CatalogAsync("s0", "nightmare");
        try { await worker.PreviewAsync(catalog.CatalogId, row.Key); throw new Exception("old snapshot accepted"); }
        catch (WorkerException) { Check(true, "old resource snapshot rejected after refresh"); }
        Check((await worker.PreviewAsync(second.CatalogId, row.Key)).Key == row.Key, "current preview recovers after old snapshot error");

        var gallery = new GalleryViewModel();
        await gallery.AttachAsync(worker);
        if (gallery.SelectedRow?.Available != true)
        {
            Check(gallery.Preview is null, "missing default author image does not borrow structure pixels");
            // The real resource archive intentionally omits author art. Exercise the actual
            // public selection action, rather than claiming those images exist.
            SelectAvailableProfile(gallery);
        }
        await UntilAsync(() => gallery.Preview is not null);
        Check(gallery.SelectedRow is not null && gallery.Preview!.IsFrozen, "UI owner consumes a frozen decoded production PNG");
        var rows = gallery.Rows;
        var originalSelection = gallery.SelectedRow;
        gallery.Context = gallery.Context;
        gallery.Profile = gallery.Profile;
        gallery.Floor = gallery.Floor;
        Check(ReferenceEquals(rows, gallery.Rows) && ReferenceEquals(originalSelection, gallery.SelectedRow), "duplicate selection keeps list and item instances");
        gallery.Search = "no-such-map-8eab";
        Check(gallery.SelectedRow is null && gallery.Preview is null, "empty filter revokes displayed pixels");
        gallery.Search = "";
        await UntilAsync(() => gallery.Preview is not null);
        gallery.Context = gallery.Contexts.First(c => c.Season == "s0" && c.Mode == "difficult");
        Check(gallery.Preview is null && gallery.Rows is null, "context change clears former resource pixels immediately");
        await UntilAsync(() => !gallery.Busy);
        gallery.Context = gallery.Contexts.First(c => c.Season == "s0" && c.Mode == "nightmare");
        await UntilAsync(() => !gallery.Busy && gallery.Preview is not null);
        Check(gallery.SelectedRow?.Key == originalSelection?.Key, "return to context restores prior logical selection");
        await gallery.ReloadAsync();
        gallery.BeginClose();
        await worker.CloseAsync();
        Check(worker.HasExited && gallery.Preview is null, "close drains own Python process and revokes pending pixels");
        await Task.Delay(100);
        Check(gallery.Preview is null, "late completion cannot repaint closed gallery");
        await CheckLateCancellationAsync(sourceRoot, python, resourceRoot);
        await CheckWindowRetirementAsync(sourceRoot, python, resourceRoot);
        Console.WriteLine($"{checks} checks passed; real pipes and fixture resources, no native/game input.");
    }

    private static async Task CheckWindowRetirementAsync(string sourceRoot, string python, string resourceRoot)
    {
        var owned = new List<PythonWorker>();
        var proxy = Path.Combine(sourceRoot, "hybrid", "StarMap.IntegrationTests", "delayed_worker.py");
        var window = new MainWindow(new(sourceRoot, python, null), (root, installation) =>
        {
            var worker = PythonWorker.Start(python, proxy, root, installation);
            owned.Add(worker);
            return worker;
        });
        window.Show();
        window.ShowGallery();
        await window.ConnectAsync(resourceRoot);
        if (window.Gallery.SelectedRow?.Available != true)
            SelectAvailableProfile(window.Gallery);
        await UntilAsync(() => window.Gallery.Preview is not null);
        await SaveWindowRendersAsync(window, sourceRoot);
        await window.Gallery.ReloadAsync();
        var reconnect = window.ConnectAsync(resourceRoot);
        Check(!reconnect.IsCompleted, "actual MainWindow reconnect waits for its retiring worker");
        window.Close();
        Check(window.IsVisible, "X during reconnect defers window close while old worker drains");
        await reconnect;
        await UntilAsync(() => !window.IsVisible);
        Check(owned.Count == 1 && owned.All(w => w.HasExited), "close during retirement starts no new worker and leaves no own process");

        string? actualRoot = null, actualInstallation = null;
        var temporary = Path.Combine(Path.GetTempPath(), "starmap-hybrid-lock-" + Guid.NewGuid().ToString("N"));
        var internalRoot = Path.Combine(temporary, "_internal");
        Directory.CreateDirectory(internalRoot);
        var selectedInternal = new MainWindow(new(sourceRoot, python, null), (root, installation) =>
        {
            actualRoot = root;
            actualInstallation = installation;
            return PythonWorker.Start(python, Path.Combine(sourceRoot, "hybrid", "python", "worker.py"), root, installation);
        });
        selectedInternal.Show();
        selectedInternal.ShowGallery();
        await selectedInternal.ConnectAsync(internalRoot);
        Check(actualRoot == internalRoot && actualInstallation == temporary, "selecting _internal keeps parent installation lock identity");
        selectedInternal.Close();
        await UntilAsync(() => !selectedInternal.IsVisible);
        // No recursive cleanup: the small temporary directory contains the normal resource lock.

        var recoveryWorkers = new List<PythonWorker>();
        var recovery = new MainWindow(new(sourceRoot, python, null), (root, installation) =>
        {
            var script = recoveryWorkers.Count == 0 ? "exit_worker.py" : "delayed_worker.py";
            var worker = PythonWorker.Start(python, Path.Combine(sourceRoot, "hybrid", "StarMap.IntegrationTests", script), root, installation);
            recoveryWorkers.Add(worker);
            return worker;
        });
        recovery.Show();
        recovery.ShowGallery();
        await recovery.ConnectAsync(resourceRoot);
        await recovery.ConnectAsync(resourceRoot);
        Check(recoveryWorkers.Count == 2 && recoveryWorkers[0].HasExited && recovery.Gallery.Rows is not null,
            "one reconnect action recovers from a real nonzero worker exit");
        recovery.Close();
        await UntilAsync(() => !recovery.IsVisible);
        Check(recoveryWorkers.All(w => w.HasExited), "recovered MainWindow closes all owned workers");
    }

    private static void SelectAvailableProfile(GalleryViewModel gallery)
    {
        foreach (var profile in gallery.Profiles)
        {
            gallery.Profile = profile;
            if (gallery.Rows!.Cast<BrowseRow>().Any(r => r.Available)) return;
        }
        throw new InvalidOperationException("No profile contains an available resource image.");
    }

    private static async Task SaveWindowRendersAsync(MainWindow window, string sourceRoot)
    {
        var output = Path.Combine(sourceRoot, "work", "hybrid-gallery-renders", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(output);
        foreach (var size in new[] { (Width: 1080, Height: 760), (Width: 760, Height: 560) })
        {
            window.Width = size.Width;
            window.Height = size.Height;
            window.UpdateLayout();
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            var content = (FrameworkElement)window.Content;
            var dpi = VisualTreeHelper.GetDpi(window);
            var image = new RenderTargetBitmap((int)Math.Ceiling(window.ActualWidth * dpi.DpiScaleX),
                (int)Math.Ceiling(window.ActualHeight * dpi.DpiScaleY), dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
            image.Render(window);
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(image));
            using var stream = File.Create(Path.Combine(output, $"gallery-{size.Width}x{size.Height}.png"));
            encoder.Save(stream);
            var picker = (FrameworkElement)window.FindName("OpenResourcesButton");
            var bounds = picker.TransformToAncestor(window).TransformBounds(new Rect(picker.RenderSize));
            Check(content.IsArrangeValid && bounds.Right <= window.ActualWidth && bounds.Bottom <= window.ActualHeight,
                $"resource picker remains inside actual {size.Width}x{size.Height} WPF layout");
        }
        Console.WriteLine("WPF RenderTargetBitmap evidence (not desktop capture): " + output);
    }

    private static async Task CheckLateCancellationAsync(string sourceRoot, string python, string resourceRoot)
    {
        var proxy = Path.Combine(sourceRoot, "hybrid", "StarMap.IntegrationTests", "delayed_worker.py");
        await using var worker = PythonWorker.Start(python, proxy, resourceRoot);
        var catalog = await worker.CatalogAsync("s0", "nightmare");
        var row = catalog.Rows.First(r => r.Available);
        using var cancellation = new CancellationTokenSource();
        var pending = worker.PreviewAsync(catalog.CatalogId, row.Key, cancellation.Token);
        // Calling the real async RPC acquires the uncontended semaphore and submits before
        // it awaits stdout. The proxy delays the actual production preview response.
        await Task.Delay(40);
        cancellation.Cancel();
        try { await pending; throw new Exception("late cancellation was ignored"); }
        catch (OperationCanceledException) { Check(true, "in-flight cancelled production preview is drained and rejected"); }
        Check((await worker.PreviewAsync(catalog.CatalogId, row.Key)).Key == row.Key,
            "next real response stays correlated after late cancellation");
        var gallery = new GalleryViewModel();
        await gallery.AttachAsync(worker);
        var current = gallery.SelectedRow;
        if (current?.Available != true)
            SelectAvailableProfile(gallery);
        gallery.BeginClose();
        await worker.CloseAsync();
        Check(gallery.Preview is null && worker.HasExited, "close during delayed real preview drains worker without repaint");
    }

    private static async Task UntilAsync(Func<bool> done)
    {
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (!done())
        {
            if (DateTime.UtcNow > deadline) throw new TimeoutException("UI consumer did not reach expected state.");
            await Task.Delay(20);
        }
    }
}
