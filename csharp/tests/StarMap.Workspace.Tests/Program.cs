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
using StarMap.App.Platform;
using StarMap.Core.Input;
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
            var navigationItems = navigation.Items.Cast<ListBoxItem>().ToArray();
            Check(navigationItems.Select(item => item.Tag as string).SequenceEqual(new[] { "home", "gallery", "custom", "plaza", "settings", "help" })
                && !navigationItems.Single(item => item.Tag as string == "plaza").IsEnabled,
                "qt-page-responsibilities-retained-and-unconnected-plaza-cannot-open-empty-page");
            Check(home is ScrollViewer { Content: StackPanel homePanel }
                && homePanel.Children[0] is Border { Child: RuntimePage },
                "home-starts-with-current-round-before-connection-and-resource-configuration");
            var connectionShortcut = (Button)shell.FindName("HomeConnectionSettings");
            var preparation = Field<Expander>(shell, "homePreparation");
            Check(connectionShortcut.Visibility == Visibility.Visible && !((ScrollViewer)home).IsAncestorOf(connectionShortcut)
                && ((StackPanel)preparation.Content).Children[0] == Field<ConnectionPage>(shell, "connectionPage"),
                "home-connection-shortcut-stays-outside-scroll-and-owns-the-existing-connection-page");
            var homeScroll = (ScrollViewer)home;
            homeScroll.Measure(new Size(800, 420)); homeScroll.Arrange(new Rect(0, 0, 800, 420)); homeScroll.UpdateLayout();
            var connection = Field<ConnectionPage>(shell, "connectionPage");
            var viewport = VisualDescendants<System.Windows.Controls.ScrollContentPresenter>(homeScroll).First();
            var connectionScrollEvidence = new List<object>();
            Rect? ConnectionBounds()
            {
                try { return connection.TransformToAncestor(viewport).TransformBounds(new Rect(connection.RenderSize)); }
                catch (InvalidOperationException) { return null; }
            }
            bool ConnectionInsideViewport() => ConnectionBounds() is { } bounds && connection.ActualHeight > 0
                && Math.Abs(bounds.Height - connection.ActualHeight) < .5 && bounds.Top >= -.5
                && bounds.Bottom <= viewport.ActualHeight + .5 && homeScroll.VerticalOffset > 0;
            object ConnectionScrollEvidence(string phase)
            {
                var bounds = ConnectionBounds();
                return new { phase, homeScroll.VerticalOffset, scrollViewportHeight = homeScroll.ViewportHeight, homeScroll.ExtentHeight, physicalViewportHeight = viewport.ActualHeight,
                    preparation.IsExpanded, connection.ActualHeight, scaleY = (preparation.Template.FindName("ContentSiteScaleTransform", preparation) as ScaleTransform)?.ScaleY,
                    target = bounds is { } value ? new { value.X, value.Y, value.Width, value.Height, value.Bottom } : null };
            }
            try
            {
                connectionScrollEvidence.Add(ConnectionScrollEvidence("before-click"));
                Check(homeScroll.VerticalOffset == 0 && homeScroll.ScrollableHeight > 0 && viewport.ActualHeight > 0,
                    "connection-navigation-regression-starts-in-the-real-scrollable-home-viewport");
                connectionShortcut.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Check(preparation.IsExpanded && !Field<WindowsRuntimeCapturePort>(shell, "runtimeCapture").IsBound
                    && !Field<WpfGlobalInputController>(shell, "inputController").ListeningRequested,
                    "home-connection-shortcut-only-expands-settings-without-binding-or-starting-input");
                var revealed = WaitForLayout(ConnectionInsideViewport); Await(revealed);
                connectionScrollEvidence.Add(ConnectionScrollEvidence("after-expand-and-reveal"));
                Check(revealed.Result, "home-connection-shortcut-scrolls-the-complete-connection-page-into-the-actual-viewport");
                homeScroll.ScrollToTop(); homeScroll.UpdateLayout();
                Dispatcher.CurrentDispatcher.Invoke(new Action(() => { }), DispatcherPriority.ContextIdle);
                connectionScrollEvidence.Add(ConnectionScrollEvidence("already-expanded-reset-to-top"));
                Check(homeScroll.VerticalOffset == 0, "connection-navigation-repeated-click-starts-after-a-real-scroll-reset");
                connectionShortcut.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                var revealedAgain = WaitForLayout(ConnectionInsideViewport); Await(revealedAgain);
                connectionScrollEvidence.Add(ConnectionScrollEvidence("already-expanded-revealed-again"));
                Check(revealedAgain.Result, "home-connection-shortcut-reveals-the-same-page-again-when-already-expanded");
            }
            finally
            {
                connectionScrollEvidence.Add(ConnectionScrollEvidence("finally"));
                File.WriteAllText(Path.Combine(output, "connection-navigation.json"), JsonSerializer.Serialize(new {
                    scope = "Actual home ScrollViewer, Material Expander and header click handler measured without Show, native input or game binding",
                    observations = connectionScrollEvidence
                }, new JsonSerializerOptions { WriteIndented = true }));
            }
            preparation.IsExpanded = false;
            var settingsPage = (Grid)Field<Dictionary<string, FrameworkElement>>(shell, "pages")["settings"];
            var settingsScroll = settingsPage.Children.OfType<ScrollViewer>().Single();
            Check(settingsPage.Children.OfType<Border>().Single(area => area.Child is StackPanel { Name: "SettingsActionBar" }) is { } settingsBar
                && Grid.GetRow(settingsBar) == 1 && Grid.GetRow(settingsScroll) == 0
                && !settingsScroll.IsAncestorOf(Field<Button>(shell, "saveSettingsButton"))
                && !settingsScroll.IsAncestorOf(Field<Button>(shell, "importSettingsButton")),
                "settings-existing-actions-and-feedback-have-a-fixed-row-outside-the-editor-scroll");
            var runtimePanel = (StackPanel)Field<RuntimePage>(shell, "runtimePage").Content;
            Check(runtimePanel.Children.OfType<Expander>().Single(item => item.Name == "RuntimeDiagnostics").IsExpanded == false
                && runtimePanel.Children.OfType<Expander>().Single(item => item.Name == "RuntimeManualSelection").IsExpanded == false,
                "runtime-diagnostics-and-manual-picker-are-disclosed-without-replacing-runtime-instance");
            foreach (var item in navigationItems.Where(item => item.IsEnabled)) navigation.SelectedItem = item;
            navigation.SelectedIndex = 0;
            Check(ReferenceEquals(home, content.Content), "navigation-retains-home-instance");
            Check(!shell.IsVisible && !Directory.Exists(Path.Combine(output, "isolated-data")), "construction-does-not-show-or-create-player-file");
            Check(!Field<WpfGlobalInputController>(shell, "inputController").ListeningRequested,
                "real-window-construction-does-not-start-global-observer");
            Await(VisibilityWorkspaceRegression.Run(output, Check));
            int resourcesOption = Array.IndexOf(args, "--resources");
            int replayOption = Array.IndexOf(args, "--original-replay");
            if (resourcesOption >= 0 && replayOption >= 0)
            {
                Await(GroupChoiceWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), output, Check));
                Await(MapRecoveryWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), output, Check));
                Await(PassiveRefreshWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), output, Check));
                Await(ManualLearningWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), output, Check));
                Await(FloorClickWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), output, Check));
                Await(GameMapDragWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption+1]),Path.GetFullPath(args[replayOption+1]),output,Check));
                int closeOption=Array.IndexOf(args,"--close-oracle");
                if(closeOption>=0)Await(MapCloseWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption+1]),Path.GetFullPath(args[replayOption+1]),Path.GetFullPath(args[closeOption+1]),output,Check));
                Await(CurrentPoseWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), Check));
                Await(UnifiedSceneWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), output, Check));
                Await(StructureIndexWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]), output, Check));
            }
            int steadyOption = Array.IndexOf(args, "--steady-oracle");
            if (resourcesOption >= 0 && replayOption >= 0 && steadyOption >= 0)
            {
                Await(SteadyWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]),
                    Path.GetFullPath(args[steadyOption + 1]), output, Check));
                int hostOption = Array.IndexOf(args, "--host-path-oracle");
                if (hostOption < 0) throw new ArgumentException("--host-path-oracle is required with --steady-oracle");
                Await(S1ExplicitDomainWorkspaceRegression.Run(Path.GetFullPath(args[resourcesOption + 1]), Path.GetFullPath(args[replayOption + 1]),
                    Path.GetFullPath(args[hostOption + 1]), output, Check));
            }
            int captureOption = Array.IndexOf(args, "--capture-record");
            if (resourcesOption >= 0 && captureOption >= 0)
                Await(RuntimeWorkspaceRegression.Run(Path.GetFullPath(args[captureOption + 1]), Path.GetFullPath(args[resourcesOption + 1]), output, Check));
            if (resourcesOption >= 0)
            {
                Await(InvokeTask(shell, "LoadResources", Path.GetFullPath(args[resourcesOption + 1])));
                var list = Field<ListBox>(shell, "resourceList");
                list.SelectedItem = list.Items.Cast<object>().Single(row => row.GetType().GetProperty("Entry")!.GetValue(row)
                    is ResourceCatalogEntry { Mode: "nightmare", Season: "s0", MapId: "nightmare-03", Floor: "2" });
                var selectedRow = list.SelectedItem!;
                var selectedEntry = (ResourceCatalogEntry)selectedRow.GetType().GetProperty("Entry")!.GetValue(selectedRow)!;
                string RowProperty(string name) => (string)selectedRow.GetType().GetProperty(name)!.GetValue(selectedRow)!;
                var rowTemplate = (StackPanel)list.ItemTemplate.LoadContent();
                rowTemplate.DataContext = selectedRow;
                var detachedTexts = rowTemplate.Children.OfType<TextBlock>().ToArray();
                foreach (var text in detachedTexts) { text.GetBindingExpression(TextBlock.TextProperty)?.UpdateTarget(); text.GetBindingExpression(FrameworkElement.ToolTipProperty)?.UpdateTarget(); }
                object TextEvidence(TextBlock text) => new {
                    text.Text, toolTip = text.ToolTip as string, trimming = text.TextTrimming.ToString(),
                    textPath = text.GetBindingExpression(TextBlock.TextProperty)?.ParentBinding.Path?.Path,
                    textStatus = text.GetBindingExpression(TextBlock.TextProperty)?.Status.ToString(),
                    tooltipStatus = text.GetBindingExpression(FrameworkElement.ToolTipProperty)?.Status.ToString(),
                    textDataItemIsSelectedRow = ReferenceEquals(text.GetBindingExpression(TextBlock.TextProperty)?.DataItem, selectedRow),
                    tooltipDataItemIsSelectedRow = ReferenceEquals(text.GetBindingExpression(FrameworkElement.ToolTipProperty)?.DataItem, selectedRow)
                };
                var detachedBeforeDispatcher = detachedTexts.Select(TextEvidence).ToArray();
                // Exercise the production ItemsControl -> item container -> DataTemplate path.
                // UpdateTarget cannot attach an as-yet unattached binding to a tree context.
                navigation.SelectedIndex = 1;
                shell.Measure(new Size(1120, 760)); shell.Arrange(new Rect(0, 0, 1120, 760)); shell.UpdateLayout();
                var galleryForBindings = (FrameworkElement)content.Content;
                galleryForBindings.Measure(new Size(800, 600)); galleryForBindings.Arrange(new Rect(0, 0, 800, 600)); galleryForBindings.UpdateLayout();
                list.ScrollIntoView(selectedRow); list.UpdateLayout();
                Dispatcher.CurrentDispatcher.Invoke(new Action(() => { }), DispatcherPriority.ContextIdle);
                list.UpdateLayout();
                var rowContainer = list.ItemContainerGenerator.ContainerFromItem(selectedRow) as ListBoxItem;
                TextBlock[] rowTexts = rowContainer is null ? [] : VisualDescendants<TextBlock>(rowContainer)
                    .Where(text => text.GetBindingExpression(TextBlock.TextProperty)?.ParentBinding.Path?.Path is "DisplayLabel" or "ContextLabel").ToArray();
                File.WriteAllText(Path.Combine(output, "gallery-row-bindings.json"), JsonSerializer.Serialize(new {
                    expected = new { selectedEntry.DisplayName, contextLabel = "噩梦 · S0 · 2F", selectedEntry.MapId, selectedEntry.Profile },
                    model = new { displayLabel = RowProperty("DisplayLabel"), contextLabel = RowProperty("ContextLabel"), technicalDetails = RowProperty("TechnicalDetails") },
                    detachedBeforeDispatcher, detachedAfterDispatcher = detachedTexts.Select(TextEvidence).ToArray(),
                    containerGenerated = rowContainer is not null,
                    containerItemIsSelectedRow = rowContainer is not null && ReferenceEquals(list.ItemContainerGenerator.ItemFromContainer(rowContainer), selectedRow),
                    realized = rowTexts.Select(TextEvidence).ToArray()
                }, new JsonSerializerOptions { WriteIndented = true }));
                Check(rowContainer is not null && ReferenceEquals(list.ItemContainerGenerator.ItemFromContainer(rowContainer), selectedRow)
                    && rowTexts.Length == 2 && rowTexts[0].Text == selectedEntry.DisplayName && rowTexts[1].Text == "噩梦 · S0 · 2F"
                    && RowProperty("TechnicalDetails").Contains(selectedEntry.MapId) && RowProperty("TechnicalDetails").Contains(selectedEntry.Profile)
                    && selectedRow.ToString()!.Contains(selectedEntry.Profile)
                    && rowTexts.All(text => text.TextTrimming == TextTrimming.CharacterEllipsis && (string)text.ToolTip == RowProperty("TechnicalDetails")
                        && ReferenceEquals(text.GetBindingExpression(TextBlock.TextProperty)?.DataItem, selectedRow)
                        && ReferenceEquals(text.GetBindingExpression(FrameworkElement.ToolTipProperty)?.DataItem, selectedRow))
                    && ScrollViewer.GetHorizontalScrollBarVisibility(list) == ScrollBarVisibility.Disabled,
                    "gallery-rows-use-trusted-name-and-readable-context-with-complete-ids-in-tooltip-and-search");
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
                    Check(Field<RuntimePage>(failureWindow, "runtimePage").ResourceRoot is null
                        && Field<TextBlock>(failureWindow, "resourceSummary").Text.Contains("运行资源未切换"),
                        "partial-gallery-remains-inspectable-without-claiming-runtime-resource-binding");
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
            if (resourcesOption >= 0)
            {
                var loading = InvokeTask(shell, "LoadResources", Path.GetFullPath(args[resourcesOption + 1]));
                Check(!loading.IsCompleted && !Field<TextBox>(shell, "hotkey").IsEnabled,
                    "resource-load-publishes-exclusive-loading-state-before-io");
                Await(InvokeTask(shell, "SaveSettings"));
                Check(Field<TextBlock>(shell, "settingsState").Text.Contains("尚未保存或应用")
                    && !File.Exists(Path.Combine(output, "isolated-data", "ui-settings.json")),
                    "settings-save-during-resource-load-reports-unapplied-and-preserves-unsaved-file");
                Await(loading);
                Check(Field<TextBox>(shell, "hotkey").IsEnabled, "resource-load-restores-settings-editors-after-binding");
            }
            var settings = Field<SettingsDocument>(shell, "settings");
            var saveGate = Field<SemaphoreSlim>(settings, "_saveGate");
            var hotkey = Field<TextBox>(shell, "hotkey");
            var opacity = Field<Slider>(shell, "sheetOpacity");
            hotkey.Text = "Ctrl+G";
            Check(Field<TextBlock>(shell, "settingsDirtyState").Text == "有未保存的更改",
                "fixed-settings-status-derives-dirty-state-from-the-existing-edit-writer");
            saveGate.Wait();
            Task firstSave;
            try
            {
                firstSave = InvokeTask(shell, "SaveSettings");
                Check(!firstSave.IsCompleted, "save-is-paused-at-real-document-serialization-gate");
                Check(!Field<Button>(shell, "saveSettingsButton").IsEnabled && !Field<Button>(shell, "importSettingsButton").IsEnabled
                    && hotkey.IsEnabled, "settings-toolbar-disables-duplicate-actions-without-blocking-new-draft-edits-during-save");
                int currentGeneration = Field<int>(shell, "resourceGeneration");
                Await(InvokeTask(shell, "LoadResources", Path.Combine(output, "must-not-bind-during-save")));
                Check(Field<int>(shell, "resourceGeneration") == currentGeneration,
                    "resource-change-during-real-pending-save-does-not-start-another-configuration");
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
            Check(Field<TextBlock>(shell, "settingsDirtyState").Text == "没有未保存的更改"
                && Field<Button>(shell, "saveSettingsButton").IsEnabled && Field<Button>(shell, "importSettingsButton").IsEnabled,
                "fixed-settings-toolbar-reconciles-after-real-save-completion");
            string importSource = Path.Combine(output, "selected-settings.json");
            File.WriteAllText(importSource, "{\"hotkey\":\"Ctrl+H\",\"futureField\":[1,2]}");
            var importWindow = new WorkspaceWindow(Path.Combine(output, "import-data"), null);
            try
            {
                Await(InvokeTask(importWindow, "LoadSettings"));
                var importing = InvokeTask(importWindow, "ImportSelectedSettings", importSource);
                string[] editorFields = ["mode", "season", "hotkey", "sheetOpacity", "referenceOpacity"];
                Check(!importing.IsCompleted && editorFields.All(key => !Field<Control>(importWindow, key).IsEnabled)
                    && Field<TextBox[]>(importWindow, "actionHotkeys").All(editor => !editor.IsEnabled)
                    && !Field<Button>(importWindow, "clearPickerKey").IsEnabled,
                    "import-pending-disables-all-settings-editors");
                Await(importing);
                Check(editorFields.All(key => Field<Control>(importWindow, key).IsEnabled)
                    && Field<TextBox>(importWindow, "hotkey").Text == "Ctrl+H", "import-restores-editors-with-verified-copy");
                Check(File.ReadAllBytes(importSource).SequenceEqual(File.ReadAllBytes(Path.Combine(output, "import-data", "ui-settings.json"))), "ui-import-preserves-original-and-unknown-bytes");
                var badActions = new JsonArray(Enumerable.Range(0, 10).Select(_ => (JsonNode?)JsonValue.Create("")).ToArray());
                badActions[9] = null;
                string invalidSource = Path.Combine(output, "invalid-picker-source.json");
                File.WriteAllText(invalidSource, new JsonObject { ["hotkey"] = "G", ["actionHotkeys"] = badActions }.ToJsonString());
                var invalidWindow = new WorkspaceWindow(Path.Combine(output, "invalid-import-data"), null);
                try
                {
                    Await(InvokeTask(invalidWindow, "LoadSettings"));
                    Await(InvokeTask(invalidWindow, "ImportSelectedSettings", invalidSource));
                    Check(Field<TextBox[]>(invalidWindow, "actionHotkeys")[9].Text == "null"
                        && Field<Expander>(invalidWindow, "advancedHotkeysDisclosure").IsExpanded
                        && !Field<WpfGlobalInputController>(invalidWindow, "inputController").ListeningRequested
                        && File.ReadAllBytes(invalidSource).SequenceEqual(File.ReadAllBytes(Path.Combine(output, "invalid-import-data", "ui-settings.json"))),
                        "invalid-raw-picker-tail-remains-visible-and-preserved-with-observer-off");
                    Field<Button>(invalidWindow, "clearPickerKey").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    Await(InvokeTask(invalidWindow, "SaveSettings"));
                    using var repaired = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(output, "invalid-import-data", "ui-settings.json")));
                    Check(repaired.RootElement.GetProperty("actionHotkeys")[9].GetString() == ""
                        && !Field<WpfGlobalInputController>(invalidWindow, "inputController").ListeningRequested,
                        "explicit-clear-picker-control-repairs-raw-tail-without-starting-observer");
                }
                finally { invalidWindow.Close(); }
                var invalidMapWindow = new WorkspaceWindow(Path.Combine(output, "invalid-map-key-data"), null);
                try
                {
                    string invalidMapSource = Path.Combine(output, "invalid-map-key-source.json");
                    File.WriteAllText(invalidMapSource, "{\"hotkey\":null}");
                    Await(InvokeTask(invalidMapWindow, "LoadSettings"));
                    Await(InvokeTask(invalidMapWindow, "ImportSelectedSettings", invalidMapSource));
                    Check(Field<TextBox>(invalidMapWindow, "hotkey").Text == "null"
                        && !Field<WpfGlobalInputController>(invalidMapWindow, "inputController").ListeningRequested,
                        "invalid-raw-map-key-is-visible-with-observer-off");
                    Field<TextBox>(invalidMapWindow, "hotkey").Text = "H";
                    Await(InvokeTask(invalidMapWindow, "SaveSettings"));
                    using var repaired = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(output, "invalid-map-key-data", "ui-settings.json")));
                    Check(repaired.RootElement.GetProperty("hotkey").GetString() == "H"
                        && !Field<WpfGlobalInputController>(invalidMapWindow, "inputController").ListeningRequested,
                        "explicit-map-key-repair-applies-without-starting-observer");
                }
                finally { invalidMapWindow.Close(); }
            }
            finally { importWindow.Close(); }
            var errorWindow = new WorkspaceWindow(Path.Combine(output, "input-configure-error-data"), null);
            try
            {
                Await(InvokeTask(errorWindow, "LoadSettings"));
                Await(Field<WpfGlobalInputController>(errorWindow, "inputController").DisposeAsync().AsTask());
                var bindings = GlobalInputBindings.FromSettings(Field<SettingsDocument>(errorWindow, "settings"));
                var source = new FixtureInputSource(bindings);
                var host = new WpfGlobalInputController(Dispatcher.CurrentDispatcher, bindings, () => new(new()), _ => { }, source);
                errorWindow.GetType().GetField("inputController", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(errorWindow, host);
                Await(host.StartAsync());
                Check(host.IsReady, "input-configure-failure-fixture-uses-real-host-ready-order-with-only-os-source-replaced");
                source.ConfigurationFailure = new System.ComponentModel.Win32Exception(5, "fixture PostThreadMessageW failure");
                Field<TextBox>(errorWindow, "hotkey").Text = "H";
                Await(InvokeTask(errorWindow, "SaveSettings"));
                Check(!host.ListeningRequested && source.Drained.IsCompletedSuccessfully
                    && Field<TextBlock>(errorWindow, "inputState").Text.Contains("配置未应用")
                    && JsonNode.Parse(File.ReadAllBytes(Path.Combine(output, "input-configure-error-data", "ui-settings.json")))!["hotkey"]!.GetValue<string>() == "H",
                    "native-configure-win32-failure-preserves-saved-settings-stops-observer-and-reports-error");
                source.ConfigurationFailure = null;
                Field<TextBox>(errorWindow, "hotkey").Text = "J";
                Await(InvokeTask(errorWindow, "SaveSettings"));
                Check(!host.ListeningRequested && host.ActiveBindings.Items.Single(item => item.Action == "map").VirtualKey == 'J',
                    "input-configure-recovers-on-next-explicit-save-without-restarting-observer");
            }
            finally { errorWindow.Close(); }
        }
        catch (Exception error) { exit = 1; checks.Add(new { name = "window-construction", passed = false, error = error.ToString() }); Console.Error.WriteLine(error); }
        finally
        {
            shell?.Close();
            File.WriteAllText(Path.Combine(output, "result.json"), JsonSerializer.Serialize(new { exit, checks, scope = "Real WPF construction/navigation, Runtime/Session confirmation and settings save/control/serialization/IO race. The picker loads real resources through SetMaps in a shown offscreen nonactivating window; clicks are routed programmatically. Capture/foreground use recorded fixtures, no native input/capture/game or visual acceptance. The existing document serialization semaphore arranges IO interleaving." }, new JsonSerializerOptions { WriteIndented = true }));
            app.Shutdown(exit);
        }
        return exit;
    }

    static T Field<T>(object owner, string name) => (T)(owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)
        ?? throw new InvalidOperationException($"Missing field {name}."));

    static Task InvokeTask(object owner, string name, params object[] args) => (Task)owner.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(owner, args)!;

    static IEnumerable<T> VisualDescendants<T>(DependencyObject parent) where T : DependencyObject
    {
        for (int index = 0; index < VisualTreeHelper.GetChildrenCount(parent); index++)
        {
            var child = VisualTreeHelper.GetChild(parent, index);
            if (child is T match) yield return match;
            foreach (var descendant in VisualDescendants<T>(child)) yield return descendant;
        }
    }

    static Task<bool> WaitForLayout(Func<bool> condition)
    {
        var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        long deadline = Environment.TickCount64 + 3000;
        var timer = new DispatcherTimer(DispatcherPriority.Background) { Interval = TimeSpan.FromMilliseconds(16) };
        timer.Tick += (_, _) =>
        {
            try
            {
                bool ready = condition();
                if (ready || Environment.TickCount64 >= deadline) { timer.Stop(); completion.TrySetResult(ready); }
            }
            catch (Exception error) { timer.Stop(); completion.TrySetException(error); }
        };
        timer.Start(); return completion.Task;
    }

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
