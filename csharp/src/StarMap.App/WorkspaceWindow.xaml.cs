using System.IO;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Win32;
using StarMap.Core.Resources;
using StarMap.Core.Settings;

namespace StarMap.App;

public partial class WorkspaceWindow : Window
{
    readonly ExperimentalDataRoot dataRoot;
    readonly Dictionary<string, FrameworkElement> pages = [];
    readonly ListBox resourceList = new() { MinWidth = 230 };
    readonly Image preview = new() { Stretch = Stretch.Uniform };
    readonly TextBlock resourceDetails = new() { TextWrapping = TextWrapping.Wrap };
    readonly TextBlock resourceSummary = new() { TextWrapping = TextWrapping.Wrap };
    readonly TextBox resourcePath = new() { IsReadOnly = true, MinWidth = 220 };
    readonly ComboBox mode = new() { ItemsSource = new[] { new ModeChoice("nightmare", "噩梦", true), new ModeChoice("difficult", "困难", true), new ModeChoice("difficulty2", "普通（历史设置，不用于新识别）", false) }, DisplayMemberPath = "Label", SelectedValuePath = "Key" };
    readonly ComboBox season = new() { ItemsSource = new[] { "s0", "s1" } };
    readonly TextBox hotkey = new();
    readonly Slider sheetOpacity = new() { Minimum = 0, Maximum = 1, TickFrequency = .05 };
    readonly Slider referenceOpacity = new() { Minimum = 0, Maximum = 1, TickFrequency = .05 };
    readonly TextBlock settingsState = new() { TextWrapping = TextWrapping.Wrap };
    SettingsDocument? settings;
    ResourceCatalog? catalog;
    List<ResourceRow> resourceRows = [];
    int resourceGeneration, previewGeneration;
    bool ready, closed;
    ConnectionPage? connectionPage;
    bool settingsBusy;
    bool loadingSettings;
    readonly HashSet<string> editedSettings = [];
    readonly SemaphoreSlim previewGate = new(1, 1);
    CancellationTokenSource? previewCancellation;

    public WorkspaceWindow(string experimentalDataPath, string? initialResourcePath)
    {
        dataRoot = new ExperimentalDataRoot(experimentalDataPath);
        InitializeComponent();
        pages["home"] = BuildHome();
        pages["gallery"] = BuildGallery();
        pages["settings"] = BuildSettings();
        pages["help"] = BuildHelp();
        pages["packages"] = Pending("图包管理", "导入、在线图包、版本恢复尚未接入此测试版。当前可在地图资源页读取现有图包，不会修改原图包。");
        var customPage = new CustomGalleryPage(dataRoot);
        pages["custom"] = customPage;
        pages["plaza"] = Pending("路线广场", "账号、路线广场和投稿尚未接入。此版本没有登录或上传功能。");
        ready = true;
        ShowPage("home");
        Loaded += async (_, _) =>
        {
            await LoadSettings();
            await customPage.LoadAsync();
            if (!string.IsNullOrWhiteSpace(initialResourcePath)) await LoadResources(initialResourcePath);
        };
        Closed += (_, _) => { closed = true; resourceGeneration++; previewGeneration++; previewCancellation?.Cancel(); connectionPage?.Dispose(); };
    }

    void Navigate(object sender, SelectionChangedEventArgs e)
    {
        if (ready && Navigation.SelectedItem is ListBoxItem { Tag: string key }) ShowPage(key);
    }

    void ShowPage(string key)
    {
        if (!pages.TryGetValue(key, out var page)) return;
        Heading.Text = key switch { "home" => "主页面", "gallery" => "地图资源", "packages" => "图包管理", "custom" => "自定义路线", "plaza" => "路线广场", "settings" => "设置", _ => "帮助与公告" };
        PageDescription.Text = key switch
        {
            "home" => "连接窗口，检查资源与当前状态。只有你明确操作后才采集画面。",
            "gallery" => "读取原图包，按模式、作者和楼层查看。预览不会参与地图识别。",
            "settings" => "设置只保存到本测试版的数据目录。可导入原版设置的副本。",
            "help" => "查看此版本的可用范围与仍待验证的功能。",
            _ => "完整迁移正在进行，页面存在不代表功能已接通。"
        };
        PageContent.Content = page;
    }

    FrameworkElement BuildHome()
    {
        var panel = new StackPanel();
        connectionPage = new ConnectionPage(message => Status.Text = message);
        panel.Children.Add(connectionPage);
        panel.Children.Add(HeadingText("资源与设置", 18, new Thickness(0, 28, 0, 12)));
        panel.Children.Add(resourceSummary);
        var choose = Button("选择原结构资源目录", async (_, _) => await ChooseResources());
        choose.Margin = new Thickness(0, 16, 0, 0);
        panel.Children.Add(choose);
        panel.Children.Add(new TextBlock { Text = "当前已接入资源查看、实验设置和窗口绑定。完整识别、悬浮显示与其他页面仍在迁移。", TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new Thickness(0, 22, 0, 0) });
        resourceSummary.Text = "尚未选择图包。选择现有资源目录后可查看真实地图和楼层。";
        return new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    }

    FrameworkElement BuildGallery()
    {
        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition());
        var pathRow = new DockPanel { Margin = new Thickness(0, 0, 0, 12) };
        var choose = Button("选择目录", async (_, _) => await ChooseResources());
        DockPanel.SetDock(choose, Dock.Right); pathRow.Children.Add(choose); pathRow.Children.Add(resourcePath); root.Children.Add(pathRow);
        var search = new TextBox { ToolTip = "搜索地图、模式、作者或楼层", Margin = new Thickness(0, 0, 0, 12) };
        search.TextChanged += (_, _) =>
        {
            var text = search.Text.Trim();
            resourceList.ItemsSource = resourceRows.Where(row => row.ToString().Contains(text, StringComparison.OrdinalIgnoreCase)).ToArray();
        };
        Grid.SetRow(search, 1); root.Children.Add(search);
        var body = new Grid(); body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(270) }); body.ColumnDefinitions.Add(new ColumnDefinition());
        resourceList.SelectionChanged += async (_, _) => await PreviewResource();
        resourceList.Margin = new Thickness(0, 0, 18, 0); body.Children.Add(resourceList);
        var detail = new Grid(); detail.RowDefinitions.Add(new RowDefinition()); detail.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        detail.Children.Add(new Border { Background = Brushes.White, Padding = new Thickness(14), Child = preview });
        resourceDetails.Margin = new Thickness(0, 14, 0, 0); Grid.SetRow(resourceDetails, 1); detail.Children.Add(resourceDetails);
        Grid.SetColumn(detail, 1); body.Children.Add(detail); Grid.SetRow(body, 2); root.Children.Add(body);
        return root;
    }

    async Task ChooseResources()
    {
        var dialog = new OpenFolderDialog { Title = "选择原星图资源目录" };
        if (dialog.ShowDialog(this) == true) await LoadResources(dialog.FolderName);
    }

    async Task LoadResources(string path)
    {
        int generation = ++resourceGeneration; Status.Text = "正在读取图包索引…";
        try
        {
            var loaded = await Task.Run(() => ResourceCatalog.Load(path));
            if (closed || generation != resourceGeneration) return;
            catalog = loaded; resourceRows = loaded.Entries.Select(entry => new ResourceRow(entry)).ToList();
            resourcePath.Text = loaded.RootPath; resourceList.ItemsSource = resourceRows;
            resourceSummary.Text = $"已读取 {loaded.Entries.Count} 项图层；资源问题 {loaded.Issues.Count} 项。未修改图包。";
            Status.Text = resourceSummary.Text;
            if (resourceRows.Count != 0) resourceList.SelectedIndex = 0;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.Text.Json.JsonException or ArgumentException)
        {
            if (closed || generation != resourceGeneration) return;
            Status.Text = "图包未载入：" + error.Message;
        }
    }

    async Task PreviewResource()
    {
        int generation = ++previewGeneration;
        previewCancellation?.Cancel();
        if (catalog is null || resourceList.SelectedItem is not ResourceRow row) { preview.Source = null; return; }
        using var cancellation = new CancellationTokenSource();
        previewCancellation = cancellation;
        bool acquired = false;
        var selectedCatalog = catalog; var entry = row.Entry; preview.Source = null;
        resourceDetails.Text = $"{entry.DisplayName} · {entry.MapId} / {entry.Floor}\n{entry.Mode} · {entry.Season} · {entry.Profile}\n结构：{entry.StructureStatus}";
        try
        {
            await previewGate.WaitAsync(cancellation.Token);
            acquired = true;
            var rendered = await Task.Run(() =>
            {
                if (entry.PreviewPath is not null && entry.PreviewIsFloorSpecific)
                {
                    return (Bitmap: ResourcePreview.LoadImage(entry.PreviewPath, cancellation.Token), Caption: "本楼层资源预览；不是识别输出。");
                }
                cancellation.Token.ThrowIfCancellationRequested();
                var loaded = selectedCatalog.LoadSharedStructure(entry);
                if (!loaded.Success || loaded.Value is null) throw new InvalidDataException(loaded.Error?.Message ?? "该楼层缺少可预览结构。");
                cancellation.Token.ThrowIfCancellationRequested();
                var mask = loaded.Value.CleanMask ?? loaded.Value.ValidationMask;
                var source = ResourcePreview.FromMask(mask, cancellation.Token);
                return (Bitmap: source, Caption: loaded.Value.CleanMask is null ? "旧格式验证区域轮廓；不是完整楼层图或识别输出。" : "缺少完整楼层图，显示二值区域轮廓；不是识别输出。");
            }, cancellation.Token);
            if (closed || cancellation.IsCancellationRequested || generation != previewGeneration) return;
            preview.Source = rendered.Bitmap;
            resourceDetails.Text += "\n" + rendered.Caption;
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is IOException or InvalidDataException or UnauthorizedAccessException or FormatException or InvalidOperationException or NotSupportedException or ArgumentException or OverflowException)
        {
            if (!closed && generation == previewGeneration) resourceDetails.Text += "\n无法预览：" + error.Message;
        }
        finally
        {
            if (acquired) previewGate.Release();
            if (ReferenceEquals(previewCancellation, cancellation)) previewCancellation = null;
        }
    }

    FrameworkElement BuildSettings()
    {
        var panel = new StackPanel { MaxWidth = 600, HorizontalAlignment = HorizontalAlignment.Left };
        var modeStyle = new Style(typeof(ComboBoxItem));
        var legacy = new DataTrigger { Binding = new Binding("Selectable"), Value = false };
        legacy.Setters.Add(new Setter(IsEnabledProperty, false)); modeStyle.Triggers.Add(legacy); mode.ItemContainerStyle = modeStyle;
        mode.SelectionChanged += (_, _) => MarkEdited("gameMode"); season.SelectionChanged += (_, _) => MarkEdited("gameSeason");
        hotkey.TextChanged += (_, _) => MarkEdited("hotkey"); sheetOpacity.ValueChanged += (_, _) => MarkEdited("sheetOpacity"); referenceOpacity.ValueChanged += (_, _) => MarkEdited("referenceOpacity");
        AddField(panel, "游戏模式", mode); AddField(panel, "赛季", season); AddField(panel, "地图键", hotkey);
        AddField(panel, "贴合透明度", sheetOpacity); AddField(panel, "定点透明度", referenceOpacity);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 20, 0, 16) };
        actions.Children.Add(Button("保存到测试版", async (_, _) => await SaveSettings()));
        actions.Children.Add(Button("导入原版设置副本", async (_, _) => await ImportSettings())); panel.Children.Add(actions); panel.Children.Add(settingsState);
        return new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    }

    async Task LoadSettings()
    {
        var loaded = await SettingsDocument.LoadAsync(dataRoot);
        if (closed) return;
        if (!loaded.Succeeded || loaded.Document is null) { settingsState.Text = "设置未载入：" + loaded.Error?.Message; return; }
        settings = loaded.Document; DisplaySettings();
    }

    void DisplaySettings()
    {
        if (settings is null) return;
        loadingSettings = true;
        try
        {
            mode.SelectedValue = settings.GameMode; season.SelectedItem = settings.GameSeason; hotkey.Text = settings.Hotkey;
            sheetOpacity.Value = settings.SheetOpacity; referenceOpacity.Value = settings.ReferenceOpacity;
            editedSettings.Clear();
        }
        finally { loadingSettings = false; }
        settingsState.Text = "当前为测试版设置。原版设置与未知字段会保留。";
    }

    void MarkEdited(string key) { if (!loadingSettings && settings is not null) editedSettings.Add(key); }

    JsonNode? ReadSettingControl(string key) => key switch
    {
        "gameMode" => JsonValue.Create(mode.SelectedValue?.ToString()), "gameSeason" => JsonValue.Create(season.SelectedItem?.ToString()),
        "hotkey" => JsonValue.Create(hotkey.Text), "sheetOpacity" => JsonValue.Create(sheetOpacity.Value), "referenceOpacity" => JsonValue.Create(referenceOpacity.Value),
        _ => throw new InvalidOperationException("Unknown edited setting.")
    };

    async Task SaveSettings()
    {
        if (settings is null || settingsBusy) return;
        if (editedSettings.Count == 0) { settingsState.Text = "设置没有更改。"; return; }
        settingsBusy = true;
        try
        {
            var changes = new Dictionary<string, JsonNode?>();
            foreach (var key in editedSettings)
                changes[key] = ReadSettingControl(key);
            settings.SetMany(changes);
            var saved = await settings.SaveAsync(dataRoot);
            if (saved.Succeeded)
            {
                foreach (var (key, value) in changes)
                    if (JsonNode.DeepEquals(value, ReadSettingControl(key))) editedSettings.Remove(key);
                settingsState.Text = editedSettings.Count == 0 ? "已保存到测试版数据目录；没有修改原版设置。" : "本次更改已保存，仍有新更改尚未保存。";
            }
            else settingsState.Text = "没有保存：" + saved.Error?.Message;
        }
        catch (Exception error) when (error is IOException or ArgumentException or InvalidOperationException or UnauthorizedAccessException) { settingsState.Text = "没有保存：" + error.Message; }
        finally { settingsBusy = false; }
    }

    async Task ImportSettings()
    {
        if (settingsBusy) return;
        var dialog = new OpenFileDialog { Title = "选择原版 ui-settings.json", Filter = "设置文件 (*.json)|*.json" };
        if (dialog.ShowDialog(this) != true) return;
        await ImportSelectedSettings(dialog.FileName);
    }

    async Task ImportSelectedSettings(string path)
    {
        if (settingsBusy) return;
        settingsBusy = true;
        SetSettingsEditing(false);
        settingsState.Text = "正在导入设置副本…";
        try
        {
            // Show the disabled/loading state before asynchronous disk work.
            await Dispatcher.Yield(DispatcherPriority.Background);
            var result = await SettingsDocument.ImportAsync(dataRoot, path);
            if (closed) return;
            if (!result.Succeeded || result.Document is null) { settingsState.Text = "没有导入：" + result.Error?.Message; return; }
            settings = result.Document; DisplaySettings(); settingsState.Text = "已复制并回读原版设置。原文件保留。";
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { settingsState.Text = "没有导入：" + error.Message; }
        finally { settingsBusy = false; SetSettingsEditing(true); }
    }

    void SetSettingsEditing(bool enabled)
    {
        foreach (var control in new Control[] { mode, season, hotkey, sheetOpacity, referenceOpacity }) control.IsEnabled = enabled;
    }

    static FrameworkElement BuildHelp()
    {
        var panel = new StackPanel();
        panel.Children.Add(HeadingText("当前测试版", 20, new Thickness(0, 0, 0, 16)));
        panel.Children.Add(new TextBlock { Text = "这是完整迁移的进行中版本。资源与设置使用 C# 直接读取，不启动 Python。识别算法、楼层观察、作者路线、账号与在线服务仍需逐项接入和验收。\n\n窗口绑定与单次采集只有你明确操作才执行。不会自动开始游戏、注入按键或改变正式版。\n\n原始资源不会被修改；缺失楼层或坐标信息会显示问题。安装、实机与回退的验证状态单独记录。", TextWrapping = TextWrapping.Wrap, MaxWidth = 650, HorizontalAlignment = HorizontalAlignment.Left, LineHeight = 25 });
        return panel;
    }

    static FrameworkElement Pending(string heading, string text)
    {
        var panel = new StackPanel(); panel.Children.Add(HeadingText(heading, 20, new Thickness(0, 0, 0, 16))); panel.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, MaxWidth = 600, HorizontalAlignment = HorizontalAlignment.Left }); return panel;
    }
    static TextBlock HeadingText(string text, double size, Thickness margin) => new() { Text = text, FontSize = size, FontWeight = FontWeights.SemiBold, Margin = margin };
    static Button Button(string text, RoutedEventHandler action)
    {
        var button = new Button { Content = text, Padding = new Thickness(15, 8, 15, 8), Margin = new Thickness(0, 0, 10, 0), HorizontalAlignment = HorizontalAlignment.Left };
        button.Click += action; return button;
    }
    static void AddField(Panel panel, string label, FrameworkElement control)
    {
        panel.Children.Add(new TextBlock { Text = label, Margin = new Thickness(0, 14, 0, 7) }); control.MinWidth = 300; panel.Children.Add(control);
    }
    sealed record ResourceRow(ResourceCatalogEntry Entry)
    {
        public override string ToString() => $"{Entry.DisplayName} ({Entry.MapId})\n{Entry.Mode} · {Entry.Season} · {Entry.Floor} · {Entry.Profile}";
    }
    sealed record ModeChoice(string Key, string Label, bool Selectable);
}
