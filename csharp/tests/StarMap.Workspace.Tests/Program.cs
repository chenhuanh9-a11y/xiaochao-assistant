using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.App;
using StarMap.Core.Resources;
using StarMap.Core.Settings;

internal static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        int option = Array.IndexOf(args, "--output");
        if (option < 0 || option + 1 == args.Length) throw new ArgumentException("--output is required");
        string output = Path.GetFullPath(args[option + 1]);
        if (Directory.Exists(output) || File.Exists(output)) throw new IOException("Preserve existing evidence; supply a new output directory.");
        Directory.CreateDirectory(output);
        var checks = new List<object>();
        var app = new App();
        app.InitializeComponent();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        WorkspaceWindow? shell = null;
        int exit = 0;
        void Check(bool result, string name)
        {
            checks.Add(new { name, passed = result }); Console.WriteLine((result ? "ok " : "FAIL ") + name);
            if (!result) throw new InvalidDataException(name);
        }
        try
        {
            byte[] region = [0,0,0,0,0, 0,1,1,1,0, 0,1,1,1,0, 0,1,1,1,0, 0,0,0,0,0];
            byte[] expectedPreview = [250,250,250,250,250, 250,40,40,40,250, 250,40,225,40,250, 250,40,40,40,250, 250,250,250,250,250];
            Check(ResourcePreview.Outline(region, 5, 5, default).SequenceEqual(expectedPreview), "filled-region-renders-pale-interior-and-actual-boundary");
            using (var cancelled = new CancellationTokenSource())
            {
                cancelled.Cancel(); bool rejected = false;
                try { ResourcePreview.Outline(region, 5, 5, cancelled.Token); } catch (OperationCanceledException) { rejected = true; }
                Check(rejected, "cancelled-mask-preview-does-not-render");
            }
            string tall = Path.Combine(output, "tall-preview.png");
            SavePng(BitmapSource.Create(32, 2400, 96, 96, PixelFormats.Gray8, null, new byte[32 * 2400], 32), tall);
            var resized = ResourcePreview.LoadImage(tall, default);
            Check(resized.PixelHeight == 1600 && resized.PixelWidth <= 1600, "real-image-decoder-bounds-preview-dimensions");
            string large = Path.Combine(output, "oversized-canvas.png");
            SavePng(BitmapSource.Create(10000, 7000, 96, 96, PixelFormats.BlackWhite, null, new byte[1250 * 7000], 1250), large);
            bool largeRejected = false;
            try { ResourcePreview.LoadImage(large, default); } catch (InvalidDataException) { largeRejected = true; }
            Check(largeRejected, "small-compressed-image-with-oversized-canvas-rejected");
            string broken = Path.Combine(output, "broken-preview.png");
            File.WriteAllBytes(broken, [137,80,78,71,13,10,26,10,0,0]);
            bool brokenRejected = false;
            try { ResourcePreview.LoadImage(broken, default); }
            catch (Exception error) when (error is IOException or FormatException or NotSupportedException) { brokenRejected = true; }
            Check(brokenRejected, "broken-image-is-an-explicit-preview-failure");
            shell = new WorkspaceWindow(Path.Combine(output, "isolated-data"), null);
            Check(shell.Icon is not null, "real-window-constructor-loads-icon-and-baml");
            var navigation = (ListBox)shell.FindName("Navigation");
            var content = (ContentControl)shell.FindName("PageContent");
            var home = content.Content;
            foreach (var item in navigation.Items.Cast<ListBoxItem>()) navigation.SelectedItem = item;
            navigation.SelectedIndex = 0;
            Check(ReferenceEquals(home, content.Content), "navigation-retains-home-instance");
            Check(!shell.IsVisible && !Directory.Exists(Path.Combine(output, "isolated-data")), "construction-does-not-show-or-create-player-file");
            int resourcesOption = Array.IndexOf(args, "--resources");
            if (resourcesOption >= 0)
            {
                Await(InvokeTask(shell, "LoadResources", Path.GetFullPath(args[resourcesOption + 1])));
                var list = Field<ListBox>(shell, "resourceList");
                list.SelectedItem = list.Items.Cast<object>().Single(row => row.GetType().GetProperty("Entry")!.GetValue(row)
                    is ResourceCatalogEntry { Mode: "nightmare", Season: "s0", MapId: "nightmare-03", Floor: "2" });
                Await(InvokeTask(shell, "PreviewResource"));
                var preview = Field<Image>(shell, "preview");
                Check(preview.Source is BitmapSource, "selected-real-floor-produces-preview-bitmap");
                var bitmap = (BitmapSource)preview.Source;
                SavePng(bitmap, Path.Combine(output, "preview-source.png"));
                navigation.SelectedIndex = 1;
                shell.Measure(new Size(1120, 760)); shell.Arrange(new Rect(0, 0, 1120, 760)); shell.UpdateLayout();
                var page = (FrameworkElement)content.Content;
                page.Measure(new Size(800, 600)); page.Arrange(new Rect(0, 0, 800, 600)); page.UpdateLayout();
                var rendered = new RenderTargetBitmap(800, 600, 96, 96, PixelFormats.Pbgra32);
                rendered.Render(page);
                SavePng(rendered, Path.Combine(output, "gallery-software-render.png"));
                string fixture = Path.Combine(output, "preview-failure-fixture");
                Directory.CreateDirectory(Path.Combine(fixture, "references"));
                var pack = JsonNode.Parse(File.ReadAllBytes(Path.Combine(Path.GetFullPath(args[resourcesOption + 1]), "references", "nightmare-map-pack.json")))!;
                var map = pack["maps"]!.AsArray().Single(row => row!["id"]!.GetValue<string>() == "nightmare-03")!;
                map["layers"]!["2"] = "fixture-preview.png";
                File.WriteAllText(Path.Combine(fixture, "references", "nightmare-map-pack.json"), pack.ToJsonString());
                string fixtureImage = Path.Combine(fixture, "fixture-preview.png");
                File.Copy(broken, fixtureImage);
                var failureWindow = new WorkspaceWindow(Path.Combine(output, "preview-failure-data"), null);
                try
                {
                    Await(InvokeTask(failureWindow, "LoadResources", fixture));
                    Await(InvokeTask(failureWindow, "PreviewResource"));
                    Check(Field<Image>(failureWindow, "preview").Source is null
                        && Field<TextBlock>(failureWindow, "resourceDetails").Text.Contains("无法预览"), "missing-typed-structure-is-an-explicit-preview-failure");
                    var choices = Field<ListBox>(failureWindow, "resourceList");
                    choices.SelectedItem = choices.Items.Cast<object>().Single(row => row.GetType().GetProperty("Entry")!.GetValue(row)
                        is ResourceCatalogEntry { MapId: "nightmare-03", Floor: "2" });
                    Await(InvokeTask(failureWindow, "PreviewResource"));
                    Check(Field<Image>(failureWindow, "preview").Source is null
                        && Field<TextBlock>(failureWindow, "resourceDetails").Text.Contains("无法预览"), "catalog-selection-handles-real-broken-image-without-dispatcher-failure");
                    File.Copy(tall, fixtureImage, overwrite: true);
                    using (var locked = new FileStream(fixtureImage, FileMode.Open, FileAccess.Read, FileShare.None))
                    {
                        Await(InvokeTask(failureWindow, "PreviewResource"));
                        Check(Field<Image>(failureWindow, "preview").Source is null
                            && Field<TextBlock>(failureWindow, "resourceDetails").Text.Contains("无法预览"), "catalog-preview-handles-real-file-access-failure");
                    }
                    Await(InvokeTask(failureWindow, "PreviewResource"));
                    Check(Field<Image>(failureWindow, "preview").Source is BitmapSource, "valid-preview-recovers-after-data-and-access-failures");
                }
                finally { failureWindow.Close(); }
            }
            Await(InvokeTask(shell, "LoadSettings"));
            var settings = Field<SettingsDocument>(shell, "settings");
            var saveGate = Field<SemaphoreSlim>(settings, "_saveGate");
            var hotkey = Field<TextBox>(shell, "hotkey");
            var opacity = Field<Slider>(shell, "sheetOpacity");
            hotkey.Text = "Ctrl+G";
            saveGate.Wait();
            Task firstSave;
            try
            {
                firstSave = InvokeTask(shell, "SaveSettings");
                Check(!firstSave.IsCompleted, "save-is-paused-at-real-document-serialization-gate");
                hotkey.Text = "Alt+G";
                opacity.Value = .7;
            }
            finally { saveGate.Release(); }
            Await(firstSave);
            string settingsPath = Path.Combine(output, "isolated-data", "ui-settings.json");
            using (var first = JsonDocument.Parse(File.ReadAllBytes(settingsPath)))
                Check(first.RootElement.GetProperty("hotkey").GetString() == "Ctrl+G"
                    && !first.RootElement.TryGetProperty("sheetOpacity", out _), "first-save-persists-only-captured-controls");
            Check(Field<HashSet<string>>(shell, "editedSettings").SetEquals(["hotkey", "sheetOpacity"]), "edits-to-same-and-new-field-during-save-remain-dirty");
            Await(InvokeTask(shell, "SaveSettings"));
            using (var second = JsonDocument.Parse(File.ReadAllBytes(settingsPath)))
                Check(second.RootElement.GetProperty("hotkey").GetString() == "Alt+G"
                    && second.RootElement.GetProperty("sheetOpacity").GetDouble() == .7, "second-save-persists-newer-controls-through-real-io");
            Check(Field<HashSet<string>>(shell, "editedSettings").Count == 0, "successful-second-save-clears-captured-edits");
            string importSource = Path.Combine(output, "selected-settings.json");
            File.WriteAllText(importSource, "{\"hotkey\":\"Ctrl+H\",\"futureField\":[1,2]}");
            var importWindow = new WorkspaceWindow(Path.Combine(output, "import-data"), null);
            try
            {
                Await(InvokeTask(importWindow, "LoadSettings"));
                var importing = InvokeTask(importWindow, "ImportSelectedSettings", importSource);
                string[] editorFields = ["mode", "season", "hotkey", "sheetOpacity", "referenceOpacity"];
                Check(!importing.IsCompleted && editorFields.All(key => !Field<Control>(importWindow, key).IsEnabled), "import-pending-disables-all-settings-editors");
                Await(importing);
                Check(editorFields.All(key => Field<Control>(importWindow, key).IsEnabled)
                    && Field<TextBox>(importWindow, "hotkey").Text == "Ctrl+H", "import-restores-editors-with-verified-copy");
                Check(File.ReadAllBytes(importSource).SequenceEqual(File.ReadAllBytes(Path.Combine(output, "import-data", "ui-settings.json"))), "ui-import-preserves-original-and-unknown-bytes");
            }
            finally { importWindow.Close(); }
        }
        catch (Exception error) { exit = 1; checks.Add(new { name = "window-construction", passed = false, error = error.ToString() }); Console.Error.WriteLine(error); }
        finally
        {
            shell?.Close();
            File.WriteAllText(Path.Combine(output, "result.json"), JsonSerializer.Serialize(new { exit, checks, scope = "Real WPF construction/navigation and settings save/control/serialization/IO race. Only the existing document serialization semaphore is held to arrange the interleaving. No Show, UI automation, HWND capture or GUI acceptance." }, new JsonSerializerOptions { WriteIndented = true }));
            app.Shutdown(exit);
        }
        return exit;
    }

    static T Field<T>(object owner, string name) => (T)(owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)
        ?? throw new InvalidOperationException($"Missing field {name}."));

    static Task InvokeTask(object owner, string name, params object[] args) => (Task)owner.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(owner, args)!;

    static void SavePng(BitmapSource image, string path)
    {
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image));
        using var file = File.Create(path); encoder.Save(file);
    }

    static void Await(Task task)
    {
        var frame = new DispatcherFrame();
        _ = task.ContinueWith(_ => Dispatcher.CurrentDispatcher.BeginInvoke(() => frame.Continue = false),
            CancellationToken.None, TaskContinuationOptions.None, TaskScheduler.FromCurrentSynchronizationContext());
        Dispatcher.PushFrame(frame);
        task.GetAwaiter().GetResult();
    }
}
