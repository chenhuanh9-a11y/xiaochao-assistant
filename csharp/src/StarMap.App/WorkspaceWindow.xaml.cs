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
using StarMap.App.Platform;

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
    readonly TextBox[] actionHotkeys = Enumerable.Range(0, SettingsDocument.ActionOrder.Length).Select(_ => new TextBox()).ToArray();
    readonly Button clearPickerKey = new() { Content = "清空手动选图键", Padding = new(13, 7, 13, 7), Margin = new(0, 10, 0, 0), HorizontalAlignment = HorizontalAlignment.Left };
    readonly Slider sheetOpacity = new() { Minimum = 0, Maximum = 1, TickFrequency = .05 };
    readonly Slider referenceOpacity = new() { Minimum = 0, Maximum = 1, TickFrequency = .05 };
    readonly TextBlock settingsState = new() { TextWrapping = TextWrapping.Wrap };
    SettingsDocument? settings;
    ResourceCatalog? catalog;
    List<ResourceRow> resourceRows = [];
    int resourceGeneration, previewGeneration;
    bool ready, closed;
    ConnectionPage? connectionPage;
    RuntimePage? runtimePage;
    readonly WindowsRuntimeCapturePort runtimeCapture = new();
    RuntimeOverlayHost? runtimeOverlay;
    Task? visibilitySave;
    bool visibilityReady;
    bool closing, closeReady;
    bool settingsBusy;
    bool resourcesBusy;
    bool loadingSettings;
    readonly HashSet<string> editedSettings = [];
    readonly SemaphoreSlim previewGate = new(1, 1);
    CancellationTokenSource? previewCancellation;

    public WorkspaceWindow(string experimentalDataPath, string? initialResourcePath)
    {
        dataRoot = new ExperimentalDataRoot(experimentalDataPath);
        InitializeComponent();
        InitializePageFocus();
        pages["home"] = BuildHome();
        runtimeOverlay = new(Dispatcher, () => runtimePage?.ReadRender(), runtimeCapture,
            message => Status.Text = message, () => settings?.SheetOpacity ?? .55,
            () => runtimePage?.AlignedVisible ?? settings?.SheetVisibilityEnabled ?? true,
            () => runtimePage?.ReadPendingGroupMaterial(), packet => runtimePage?.AcceptGroupMaterialReady(packet) == true,
            (packet, reason) => runtimePage?.RejectGroupMaterial(packet, reason));
        pages["gallery"] = BuildGallery();
        pages["settings"] = BuildSettings();
        pages["help"] = BuildHelp();
        var customPage = new CustomGalleryPage(dataRoot);
        pages["custom"] = customPage;
        InitializeInput();
        ready = true;
        ShowPage("home");
        Loaded += async (_, _) =>
        {
            await LoadSettings();
            await customPage.LoadAsync();
            if (!string.IsNullOrWhiteSpace(initialResourcePath)) await LoadResources(initialResourcePath);
        };
        Closing += async (_, e) =>
        {
            if (closeReady) return;
            e.Cancel = true;
            if (closing) return;
            closing = true;
            if (runtimePage is not null) runtimePage.ToggleVisibilityRequested = null;
            if (!await CloseInput())
            {
                closing = false; UpdateInputControls();
                if (visibilityReady && runtimePage is not null) runtimePage.ToggleVisibilityRequested = ToggleAlignedVisibility;
                return;
            }
            if (visibilitySave is { } savingVisibility) await savingVisibility;
            closed = true; resourceGeneration++; previewGeneration++;
            previewCancellation?.Cancel(); connectionPage?.Dispose();
            if (runtimePage is not null) await runtimePage.CloseAsync();
            runtimeOverlay?.Dispose();
            runtimeCapture.Dispose(); closeReady = true;
            // A drained session can complete synchronously inside Closing.
            // Re-enter Close only after WPF has left its current close callback.
            _ = Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(Close));
        };
    }

    void Navigate(object sender, SelectionChangedEventArgs e)
    {
        if (ready && Navigation.SelectedItem is ListBoxItem { Tag: string key }) ShowPage(key);
    }

    void ShowPage(string key)
    {
        if (!pages.TryGetValue(key, out var page)) return;
        if (ReferenceEquals(PageContent.Content, page)) return;
        RememberPageFocus();
        HomeConnectionSettings.Visibility = key == "home" ? Visibility.Visible : Visibility.Collapsed;
        Heading.Text = key switch { "home" => "主页面", "gallery" => "地图资源", "packages" => "图包管理", "custom" => "自定义路线", "plaza" => "路线广场", "settings" => "设置", _ => "帮助与公告" };
        PageDescription.Text = key switch
        {
            "home" => "先连接游戏窗口，再开始本局；地图与楼层可在此确认。",
            "gallery" => "读取原图包，按模式、作者和楼层查看。预览不会参与地图识别。",
            "settings" => "设置只保存到本测试版的数据目录。可导入原版设置的副本。",
            "help" => "查看此版本的可用范围与仍待验证的功能。",
            _ => "完整迁移正在进行，页面存在不代表功能已接通。"
        };
        PageContent.Content = page;
        RestorePageFocus(key);
    }

    FrameworkElement BuildHome()
    {
        var panel = new StackPanel();
        runtimePage = new RuntimePage(runtimeCapture, message => Status.Text = message);
        connectionPage = new ConnectionPage(message => Status.Text = message, runtimeCapture);
        connectionPage.BindingChanged += runtimePage.BindingChanged;
        connectionPage.BindingChanged += RefreshInputScope;
        panel.Children.Add(Surface(runtimePage));

        var preparation = new StackPanel { Margin = new(0, 12, 0, 4) };
        preparation.Children.Add(connectionPage);
        preparation.Children.Add(SectionHeading("游戏内快捷键", new(0, 20, 0, 0)));
        preparation.Children.Add(BuildInputPanel());
        homePreparation = Disclosure("游戏连接与快捷键", preparation, "HomePreparation");
        homePreparation.Margin = new(0, 16, 0, 0);
        homePreparation.Unloaded += (_, _) => CancelConnectionReveal();

        var resources = new StackPanel();
        resources.Children.Add(SectionHeading("当前资源", new(0, 0, 0, 8)));
        resources.Children.Add(resourceSummary);
        var links = new WrapPanel { Margin = new(0, 12, 0, 0) };
        links.Children.Add(TextButton("地图资源", (_, _) => SelectPage("gallery")));
        links.Children.Add(TextButton("快捷键与显示设置", (_, _) => SelectPage("settings")));
        resources.Children.Add(links);
        var resourceArea = Surface(resources); resourceArea.Margin = new(0, 16, 0, 0);
        panel.Children.Add(resourceArea);
        panel.Children.Add(homePreparation);
        resourceSummary.Text = "尚未选择图包。选择现有资源目录后可查看真实地图和楼层。";
        homeScroll = PageScroll(panel); homeScroll.CanContentScroll = false;
        return homeScroll;
    }

    FrameworkElement BuildGallery()
    {
        var root = new Grid();
        resourceList.MinWidth = 0;
        resourceList.ItemTemplate = (DataTemplate)FindResource("ResourceRowTemplate");
        var itemStyle = new Style(typeof(ListBoxItem), (Style)FindResource("MaterialDesignListBoxItem"));
        itemStyle.Setters.Add(new Setter(HorizontalContentAlignmentProperty, HorizontalAlignment.Stretch));
        itemStyle.Setters.Add(new Setter(ToolTipProperty, new Binding("TechnicalDetails")));
        resourceList.ItemContainerStyle = itemStyle;
        ScrollViewer.SetHorizontalScrollBarVisibility(resourceList, ScrollBarVisibility.Disabled);
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition());
        var pathRow = new DockPanel { Margin = new Thickness(0, 0, 0, 12) };
        var choose = Button("选择现有资源目录", async (_, _) => await ChooseResources());
        DockPanel.SetDock(choose, Dock.Right); pathRow.Children.Add(choose); pathRow.Children.Add(resourcePath); root.Children.Add(pathRow);
        var search = new TextBox { ToolTip = "搜索地图、模式、作者或楼层", Margin = new Thickness(0, 0, 0, 12) };
        MaterialDesignThemes.Wpf.HintAssist.SetHint(search, "搜索地图、模式、作者或楼层");
        search.TextChanged += (_, _) =>
        {
            var text = search.Text.Trim();
            resourceList.ItemsSource = resourceRows.Where(row => row.ToString().Contains(text, StringComparison.OrdinalIgnoreCase)).ToArray();
        };
        Grid.SetRow(search, 1); root.Children.Add(search);
        var packageNotice = Disclosure("图包导入与在线下载 · 尚未接通", BodyText("导入、在线图包、版本恢复尚未接入此测试版。当前只能选择现有资源目录并查看；不会修改原图包。"), "GalleryPackageAvailability");
        packageNotice.Margin = new(0, 0, 0, 12); Grid.SetRow(packageNotice, 2); root.Children.Add(packageNotice);
        var body = new Grid(); body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(230) }); body.ColumnDefinitions.Add(new ColumnDefinition());
        resourceList.SelectionChanged += async (_, _) => await PreviewResource();
        resourceList.Margin = new Thickness(0, 0, 18, 0); body.Children.Add(resourceList);
        var detail = new Grid(); detail.RowDefinitions.Add(new RowDefinition()); detail.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        detail.Children.Add(Surface(preview));
        resourceDetails.Margin = new Thickness(0, 14, 0, 0); Grid.SetRow(resourceDetails, 1); detail.Children.Add(resourceDetails);
        Grid.SetColumn(detail, 1); body.Children.Add(detail); Grid.SetRow(body, 3); root.Children.Add(body);
        return root;
    }

    async Task ChooseResources()
    {
        var dialog = new OpenFolderDialog { Title = "选择原星图资源目录" };
        if (dialog.ShowDialog(this) == true) await LoadResources(dialog.FolderName);
    }

    async Task LoadResources(string path)
    {
        if (settingsBusy || resourcesBusy) { Status.Text = "设置正在保存或图包正在载入，请等待完成后再选择目录。"; return; }
        if (runtimePage is not null && !runtimePage.BeginResourceLoad())
        { Status.Text = "请先结束本局并等待识别停止，再更换资源目录。"; return; }
        resourcesBusy = true; SetSettingsEditing(false);
        int generation = ++resourceGeneration; Status.Text = "正在读取图包索引…";
        try
        {
            await Dispatcher.Yield(DispatcherPriority.Background);
            var loaded = await Task.Run(() => ResourceCatalog.Load(path));
            if (closed || generation != resourceGeneration) return;
            runtimePage?.EndResourceLoad();
            bool runtimeBound = runtimePage is not null && await runtimePage.Configure(loaded.RootPath, settings ?? SettingsDocument.CreateNew(), reloadResources: true);
            if (closed || generation != resourceGeneration) return;
            catalog = loaded; resourceRows = loaded.Entries.Select(entry => new ResourceRow(entry)).ToList();
            resourcePath.Text = loaded.RootPath; resourceList.ItemsSource = resourceRows;
            resourceSummary.Text = $"图库已读取 {loaded.Entries.Count} 项图层；资源问题 {loaded.Issues.Count} 项。未修改图包。"
                + (runtimeBound ? " 本局运行资源已切换到该目录。" : " 运行资源未切换；本局仍使用页面标明的运行目录。");
            Status.Text = resourceSummary.Text;
            if (resourceRows.Count != 0) resourceList.SelectedIndex = 0;
            if (runtimeBound && runtimePage?.State is { } applied) runtimePage.SetMaps(loaded.Entries.Where(entry =>
                entry.Mode == applied.Context.Mode && entry.Season == applied.Context.Season).Select(entry => entry.MapId));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.Text.Json.JsonException or ArgumentException)
        {
            if (closed || generation != resourceGeneration) return;
            Status.Text = "图包未载入：" + error.Message;
        }
        finally { resourcesBusy = false; runtimePage?.EndResourceLoad(); SetSettingsEditing(!settingsBusy); }
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
        var panel = new StackPanel();
        var modeStyle = new Style(typeof(ComboBoxItem));
        var legacy = new DataTrigger { Binding = new Binding("Selectable"), Value = false };
        legacy.Setters.Add(new Setter(IsEnabledProperty, false)); modeStyle.Triggers.Add(legacy); mode.ItemContainerStyle = modeStyle;
        mode.SelectionChanged += (_, _) => MarkEdited("gameMode"); season.SelectionChanged += (_, _) => MarkEdited("gameSeason");
        hotkey.TextChanged += (_, _) => MarkEdited("hotkey"); sheetOpacity.ValueChanged += (_, _) => MarkEdited("sheetOpacity"); referenceOpacity.ValueChanged += (_, _) => MarkEdited("referenceOpacity");
        var context = new StackPanel();
        context.Children.Add(SectionHeading("模式与赛季", new(0, 0, 0, 4)));
        var modeField = new StackPanel(); var seasonField = new StackPanel();
        AddField(modeField, "游戏模式", mode); AddField(seasonField, "赛季", season);
        context.Children.Add(ResponsiveColumns(modeField, seasonField));
        panel.Children.Add(Surface(context));
        var shortcuts = new StackPanel();
        shortcuts.Children.Add(SectionHeading("快捷键", new(0, 0, 0, 4)));
        AddField(shortcuts, "游戏地图键", hotkey);
        string[] actionLabels = ["显示或隐藏主面板", "上一候选", "下一候选", "确认地图", "切换楼层", "结束本局", "显示或隐藏贴合图", "开始本局", "开始或结束本局", "手动选图"];
        for (int i = 0; i < actionHotkeys.Length; i++)
            actionHotkeys[i].TextChanged += (_, _) => MarkEdited("actionHotkeys");
        foreach (int i in new[] { 0, 8, 6 }) AddField(shortcuts, actionLabels[i], actionHotkeys[i]);
        var advanced = new StackPanel();
        foreach (int i in new[] { 1, 2, 3, 4, 5, 7, 9 }) AddField(advanced, actionLabels[i], actionHotkeys[i]);
        shortcuts.Children.Add(BodyText("快捷键留空可解绑。错误原值会显示在对应输入框，修改并保存后才生效。", new(0, 12, 0, 8)));
        clearPickerKey.Click += (_, _) => { actionHotkeys[9].Text = ""; MarkEdited("actionHotkeys"); };
        advanced.Children.Add(clearPickerKey);
        advancedHotkeysDisclosure = Disclosure("高级快捷键", advanced, "AdvancedHotkeys");
        shortcuts.Children.Add(advancedHotkeysDisclosure);
        var display = new StackPanel();
        display.Children.Add(SectionHeading("小抄显示", new(0, 0, 0, 4)));
        AddField(display, "贴合透明度", sheetOpacity); AddField(display, "定点透明度", referenceOpacity);
        display.Children.Add(BodyText("显示参数保留在测试版设置中。悬浮路线显示的实际能力以本版本已接通范围为准。", new(0, 16, 0, 0)));
        var options = ResponsiveColumns(Surface(shortcuts), Surface(display)); options.Margin = new(0, 16, 0, 0); panel.Children.Add(options);
        var actions = new WrapPanel { Margin = new(0, 12, 0, 0) };
        saveSettingsButton = Button("保存并应用到测试版", async (_, _) => await SaveSettings()); saveSettingsButton.SetResourceReference(StyleProperty, "StarMap.PrimaryButton");
        importSettingsButton = Button("导入原版设置副本", async (_, _) => await ImportSettings());
        actions.Children.Add(saveSettingsButton); actions.Children.Add(importSettingsButton);
        var footer = new StackPanel { Name = "SettingsActionBar" };
        footer.Children.Add(settingsDirtyState);
        footer.Children.Add(new ScrollViewer { Content = settingsState, MaxHeight = 66, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        footer.Children.Add(actions);
        var actionArea = new Border { Child = footer, BorderThickness = new(0, 1, 0, 0), Padding = new(0, 12, 0, 0), Margin = new(0, 12, 0, 0) };
        actionArea.SetResourceReference(Border.BorderBrushProperty, "StarMap.Outline");
        var root = new Grid(); root.RowDefinitions.Add(new()); root.RowDefinitions.Add(new() { Height = GridLength.Auto });
        root.Children.Add(PageScroll(panel)); Grid.SetRow(actionArea, 1); root.Children.Add(actionArea);
        UpdateSettingsToolbar();
        return root;
    }

    async Task LoadSettings()
    {
        visibilityReady = false;
        if (runtimePage is not null) runtimePage.ToggleVisibilityRequested = null;
        var loaded = await SettingsDocument.LoadAsync(dataRoot);
        if (closed) return;
        visibilityReady = true;
        if (runtimePage is not null) runtimePage.ToggleVisibilityRequested = ToggleAlignedVisibility;
        if (!loaded.Succeeded || loaded.Document is null) { settingsState.Text = "设置未载入：" + loaded.Error?.Message; return; }
        settings = loaded.Document; DisplaySettings(); await ApplyInputSettings();
    }

    void DisplaySettings()
    {
        if (settings is null) return;
        visibilityReady = true;
        if (runtimePage is not null) runtimePage.ToggleVisibilityRequested = ToggleAlignedVisibility;
        loadingSettings = true;
        try
        {
            mode.SelectedValue = settings.GameMode; season.SelectedItem = settings.GameSeason;
            var rawValues = settings.Values;
            hotkey.Text = rawValues.TryGetPropertyValue("hotkey", out var rawKey)
                ? rawKey is JsonValue keyValue && keyValue.TryGetValue<string>(out var keyText) ? keyText : rawKey?.ToJsonString() ?? "null"
                : settings.Hotkey;
            var rawActions = rawValues["actionHotkeys"] as JsonArray;
            for (int i = 0; i < actionHotkeys.Length; i++)
            {
                bool hasRaw = rawActions is not null && i < rawActions.Count;
                var raw = hasRaw ? rawActions![i] : null;
                actionHotkeys[i].Text = raw is JsonValue value && value.TryGetValue<string>(out var text)
                    ? text : hasRaw ? raw?.ToJsonString() ?? "null" : settings.ActionHotkeys[i];
            }
            sheetOpacity.Value = settings.SheetOpacity; referenceOpacity.Value = settings.ReferenceOpacity;
            editedSettings.Clear();
        }
        finally { loadingSettings = false; }
        var issues = settings.GetValidationIssues();
        if (issues.Keys.Any(key => key.Contains("actionHotkeys", StringComparison.Ordinal)) && advancedHotkeysDisclosure is not null)
            advancedHotkeysDisclosure.IsExpanded = true;
        settingsState.Text = "当前为测试版设置。原版设置与未知字段会保留。"
            + (issues.Count == 0 ? "" : "\n待修复：" + string.Join("；", issues.Values));
        UpdateSettingsToolbar();
        runtimePage?.SetAlignedVisibility(settings.SheetVisibilityEnabled);
        runtimeOverlay?.RefreshVisibility();
    }

    void MarkEdited(string key) { if (!loadingSettings && settings is not null) { editedSettings.Add(key); UpdateSettingsToolbar(); } }

    JsonNode? ReadSettingControl(string key) => key switch
    {
        "gameMode" => JsonValue.Create(mode.SelectedValue?.ToString()), "gameSeason" => JsonValue.Create(season.SelectedItem?.ToString()),
        "hotkey" => JsonValue.Create(hotkey.Text), "sheetOpacity" => JsonValue.Create(sheetOpacity.Value), "referenceOpacity" => JsonValue.Create(referenceOpacity.Value),
        "actionHotkeys" => new JsonArray(actionHotkeys.Select(editor => (JsonNode?)JsonValue.Create(editor.Text)).ToArray()),
        "sheetVisibilityEnabled" => JsonValue.Create(settings?.SheetVisibilityEnabled ?? true),
        _ => throw new InvalidOperationException("Unknown edited setting.")
    };

    async Task SaveSettings()
    {
        if (resourcesBusy) { settingsState.Text = "图包正在载入，本次更改尚未保存或应用，请完成后重试。"; return; }
        if (settings is null || settingsBusy) return;
        settingsBusy = true; UpdateSettingsToolbar();
        try
        {
            if (editedSettings.Count == 0) { settingsState.Text = "设置没有更改，正在应用已保存设置。"; await ConfigureRuntime(); await ApplyInputSettings(); return; }
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
                await ConfigureRuntime();
                await ApplyInputSettings();
            }
            else settingsState.Text = "没有保存：" + saved.Error?.Message;
        }
        catch (Exception error) when (error is IOException or ArgumentException or InvalidOperationException or UnauthorizedAccessException) { settingsState.Text = "没有保存：" + error.Message; }
        finally { settingsBusy = false; UpdateSettingsToolbar(); }
    }

    async Task ImportSettings()
    {
        if (resourcesBusy) { settingsState.Text = "图包正在载入，请完成后再导入设置。"; return; }
        if (settingsBusy) return;
        var dialog = new OpenFileDialog { Title = "选择原版 ui-settings.json", Filter = "设置文件 (*.json)|*.json" };
        if (dialog.ShowDialog(this) != true) return;
        await ImportSelectedSettings(dialog.FileName);
    }

    async Task ImportSelectedSettings(string path)
    {
        if (resourcesBusy) { settingsState.Text = "图包正在载入，尚未导入设置，请完成后重试。"; return; }
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
            await ConfigureRuntime();
            await ApplyInputSettings();
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { settingsState.Text = "没有导入：" + error.Message; }
        finally { settingsBusy = false; SetSettingsEditing(!resourcesBusy); }
    }

    void SetSettingsEditing(bool enabled)
    {
        foreach (var control in new Control[] { mode, season, hotkey, sheetOpacity, referenceOpacity }) control.IsEnabled = enabled;
        foreach (var editor in actionHotkeys) editor.IsEnabled = enabled;
        clearPickerKey.IsEnabled = enabled;
        UpdateSettingsToolbar();
    }

    async Task ConfigureRuntime()
    {
        if (closed || catalog is null || runtimePage is null) return;
        var current = settings ?? SettingsDocument.CreateNew();
        if (await runtimePage.Configure(catalog.RootPath, current))
        {
            var applied = runtimePage.State!.Context;
            runtimePage.SetMaps(catalog.Entries.Where(entry => entry.Mode == applied.Mode && entry.Season == applied.Season).Select(entry => entry.MapId));
        }
    }

    static FrameworkElement BuildHelp()
    {
        var panel = new StackPanel();
        panel.Children.Add(HeadingText("当前测试版", 20, new Thickness(0, 0, 0, 16)));
        panel.Children.Add(new TextBlock { Text = "这是完整迁移的进行中版本。资源、设置、当前楼层观察、S0 入口与登记相似组、S1 结构候选、组内成员选择、当前帧贴合和显示素材使用 C#，不启动 Python。稳态跟踪、手动选图浮层、完整作者路线交互、账号与在线服务仍在迁移。\n\n窗口绑定、开始本局和采集只有你明确操作才执行。不会自动打开游戏地图或发送按键。\n\n原始资源不会被修改；缺失楼层或坐标信息会显示问题。安装、实机与回退的验证状态单独记录。", TextWrapping = TextWrapping.Wrap, MaxWidth = 650, HorizontalAlignment = HorizontalAlignment.Left, LineHeight = 25 });
        return panel;
    }

    static TextBlock HeadingText(string text, double size, Thickness margin) => new() { Text = text, FontSize = size, FontWeight = FontWeights.SemiBold, Margin = margin };
    static Button Button(string text, RoutedEventHandler action)
    {
        var button = new Button { Content = text, Padding = new Thickness(16, 8, 16, 8), Margin = new Thickness(0, 0, 8, 8), HorizontalAlignment = HorizontalAlignment.Left };
        button.Click += action; return button;
    }
    static void AddField(Panel panel, string label, FrameworkElement control)
    {
        var caption = new Label { Content = label, Target = control, Padding = new(0), Margin = new Thickness(0, 14, 0, 7) };
        panel.Children.Add(caption); control.MinWidth = 0; control.HorizontalAlignment = HorizontalAlignment.Stretch; panel.Children.Add(control);
    }
    sealed record ResourceRow(ResourceCatalogEntry Entry)
    {
        public string DisplayLabel => Entry.DisplayName;
        public string ContextLabel => $"{ResourceModeLabel(Entry.Mode)} · {Entry.Season.ToUpperInvariant()} · {ResourceFloorLabel(Entry.Floor)}";
        public string TechnicalDetails => $"{Entry.DisplayName}\n地图 ID：{Entry.MapId}\n路线配置 ID：{Entry.Profile}\n{Entry.Mode} · {Entry.Season} · {Entry.Floor}";
        public override string ToString() => $"{DisplayLabel}\n{ContextLabel}\n{TechnicalDetails}";
        static string ResourceModeLabel(string value) => value switch { "nightmare" => "噩梦", "difficult" => "困难", "difficulty2" => "普通（历史）", _ => value };
        static string ResourceFloorLabel(string value) => value == "B1" ? "地下 1 层"
            : value.StartsWith('-') ? $"地下 {value[1..]} 层" : int.TryParse(value, out _) ? value + "F" : value;
    }
    sealed record ModeChoice(string Key, string Label, bool Selectable);
}
