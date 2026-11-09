using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using StarMap.Core.Floors;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Core.Settings;
using StarMap.Runtime;

namespace StarMap.App;

/// <summary>Persistent UI consumer of the real session/runtime, with no capture or round on construction.</summary>
internal sealed class RuntimePage : UserControl
{
    readonly ICapturePort capture;
    readonly Action<string> status;
    readonly TextBlock summary = new() { Text = "选择结构资源并连接窗口后，可手动开始本局。", TextWrapping = TextWrapping.Wrap, Margin = new(0, 4, 0, 12) };
    readonly TextBlock detail = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 4, 0, 8) };
    readonly TextBlock resourceCaption = new() { Text = "本局运行资源尚未绑定。", TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 8, 0, 8) };
    readonly TextBlock roundState = ValueText("等待开始"), mapIdentity = ValueText("尚未确认"), currentFloor = ValueText("尚未确认");
    readonly TextBlock candidateCaption = new() { Text = "读取游戏地图后，候选会显示在这里。", TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 4, 0, 8) };
    readonly ListBox candidates = new() { MinHeight = 60, MaxHeight = 170, Margin = new(0, 4, 0, 8), Visibility = Visibility.Collapsed };
    readonly ComboBox manualMap = new() { MinWidth = 240, MaxWidth = 620, HorizontalAlignment = HorizontalAlignment.Stretch };
    readonly ComboBox floor = new() { MinWidth = 130, HorizontalAlignment = HorizontalAlignment.Left, DisplayMemberPath = "Label", SelectedValuePath = "Id" };
    readonly Button start, end, acquire, cancel, previous, next, confirm, pick, applyFloor, toggleVisibility;
    readonly DispatcherTimer renderTimer;
    SessionService? session;
    LiveRuntime? runtime;
    CancellationTokenSource? pendingCapture;
    string resourceRoot = "";
    string[] availableMaps = [];
    JsonElement? floorSettings;
    TaskCompletionSource? configurationCompleted;
    SessionService? proposedSession;
    LiveRuntime? proposedRuntime;
    bool closed, rendering, configuring, loadingResources;
    bool alignedVisible = true;
    string? resourceLease;
    readonly OrderedMapInput mapInput = new();
    (long Ticket, long Input)? mapRead;
    internal Func<Task>? PrepareInput { get; set; }
    Func<Task>? toggleVisibilityRequested;
    internal Func<Task>? ToggleVisibilityRequested
    {
        get => toggleVisibilityRequested;
        set { toggleVisibilityRequested = value; UpdateVisibilityControl(); }
    }

    internal RuntimePage(ICapturePort capture, Action<string> status)
    {
        this.capture = capture; this.status = status;
        foreach (var text in new[] { detail, resourceCaption, candidateCaption })
            text.SetResourceReference(TextBlock.ForegroundProperty, "StarMap.Muted");
        var panel = new StackPanel { Name = "RuntimeOverview" };
        panel.Children.Add(new TextBlock { Text = "本局", FontSize = 21, FontWeight = FontWeights.SemiBold });
        panel.Children.Add(summary);
        var overview = new Grid { Margin = new(0, 0, 0, 12) };
        for (int column = 0; column < 3; column++) overview.ColumnDefinitions.Add(new());
        AddState(overview, 0, "状态", roundState); AddState(overview, 1, "地图身份", mapIdentity); AddState(overview, 2, "当前楼层", currentFloor);
        panel.Children.Add(overview);
        var actions = new WrapPanel();
        start = Add(actions, "开始本局", async (_, _) => { if (PrepareInput is not null) await PrepareInput(); Dispatch("start_round"); });
        start.SetResourceReference(StyleProperty, "StarMap.PrimaryButton");
        end = Add(actions, "结束本局", (_, _) => Dispatch("end_round"));
        acquire = Add(actions, "3 秒后读取地图", async (_, _) => await CaptureAfterDelay());
        cancel = Add(actions, "取消识别", (_, _) => CancelWork());
        toggleVisibility = Add(actions, "隐藏路线", async (_, _) => { if (ToggleVisibilityRequested is { } toggle) await toggle(); });
        toggleVisibility.Name = "RuntimeToggleVisibility";
        panel.Children.Add(actions);
        panel.Children.Add(detail);
        panel.Children.Add(new Separator { Margin = new(0, 8, 0, 16) });
        var candidateArea = new StackPanel { Name = "RuntimeCandidates" };
        candidateArea.Children.Add(new TextBlock { Text = "地图候选", FontSize = 16, FontWeight = FontWeights.SemiBold });
        candidateArea.Children.Add(candidateCaption); candidateArea.Children.Add(candidates);
        candidates.SelectionChanged += (_, _) =>
        {
            if (!rendering && candidates.SelectedItem is CandidateRow row)
                Dispatch("select_candidate", new { index = row.Index });
        };
        var choices = new WrapPanel();
        previous = Add(choices, "上一候选", (_, _) => Dispatch("previous_candidate"));
        next = Add(choices, "下一候选", (_, _) => Dispatch("next_candidate"));
        confirm = Add(choices, "确认所选地图", (_, _) => Dispatch("confirm_route")); candidateArea.Children.Add(choices);
        panel.Children.Add(candidateArea);
        var manual = new StackPanel { Margin = new(0, 8, 0, 8) };
        manual.Children.Add(new Label { Content = "地图", Target = manualMap, Padding = new(0), Margin = new(0, 0, 0, 8) });
        manual.Children.Add(manualMap);
        var manualActions = new WrapPanel { Margin = new(0, 8, 0, 0) };
        pick = Add(manualActions, "确认手动选图", (_, _) =>
        {
            if (manualMap.SelectedItem is string mapId) Dispatch("confirm_route", new { mapId });
        });
        manual.Children.Add(manualActions);
        panel.Children.Add(new Expander { Name = "RuntimeManualSelection", Header = "手动选择地图", Content = manual, HorizontalContentAlignment = HorizontalAlignment.Stretch, Margin = new(0, 4, 0, 12) });
        var floorActions = new WrapPanel { Margin = new(0, 4, 0, 0) };
        floorActions.Children.Add(new Label { Content = "楼层", Target = floor, Padding = new(0), VerticalAlignment = VerticalAlignment.Center, Margin = new(0, 0, 12, 8) });
        floor.Margin = new(0, 0, 12, 8); floorActions.Children.Add(floor);
        applyFloor = Add(floorActions, "应用楼层", (_, _) =>
        {
            if (floor.SelectedValue is string floorId) Dispatch("set_floor", new { floor = floorId });
        });
        panel.Children.Add(floorActions);
        var diagnostics = new StackPanel();
        diagnostics.Children.Add(resourceCaption);
        var guidance = new TextBlock { Text = "只读取你已打开的地图，不发送游戏按键。确认地图会立即保留选择；结构未对齐时显示等待对齐。隐藏路线时，识别仍会继续。", TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 8) };
        guidance.SetResourceReference(TextBlock.ForegroundProperty, "StarMap.Muted"); diagnostics.Children.Add(guidance);
        panel.Children.Add(new Expander { Name = "RuntimeDiagnostics", Header = "运行资源与说明", Content = diagnostics, HorizontalContentAlignment = HorizontalAlignment.Stretch, Margin = new(0, 12, 0, 0) });
        Content = panel;
        // Read the runtime's bounded immutable view only. RefreshRuntime would
        // treat an in-flight producer's new frame/map as an external invalidation.
        renderTimer = new(TimeSpan.FromMilliseconds(125), DispatcherPriority.Background, (_, _) => Render(), Dispatcher);
        renderTimer.Stop(); Render();
        IsVisibleChanged += (_, _) =>
        {
            if (IsVisible && !closed) { Render(); renderTimer.Start(); }
            else renderTimer.Stop();
        };
    }

    internal LiveRuntimeState? State => runtime?.ReadState();
    internal bool AlignedVisible => runtime?.ReadState().AlignedVisible ?? alignedVisible;
    internal void SetAlignedVisibility(bool visible)
    {
        if (closed) return;
        alignedVisible = visible;
        runtime?.SetAlignedVisibility(visible); proposedRuntime?.SetAlignedVisibility(visible);
        UpdateVisibilityControl();
        Render();
    }
    void UpdateVisibilityControl()
    {
        toggleVisibility.IsEnabled = !closed && ToggleVisibilityRequested is not null;
        toggleVisibility.Content = AlignedVisible ? "隐藏路线" : "显示路线";
    }
    internal RuntimeRenderPacket? ReadRender() => runtime?.TryReadRender(out var packet) == true ? packet : null;
    internal string? ResourceRoot => runtime?.ReadState().ResourcesBound == true ? resourceRoot : null;
    internal SessionView? SessionView => session?.Read();
    internal bool CanReplaceResources => !configuring && !loadingResources && pendingCapture is null
        && (session is null || (!session.Read().WorkerActive && runtime?.ReadState().RoundActive != true));

    internal bool BeginResourceLoad()
    {
        if (closed || !CanReplaceResources) return false;
        if (session is not null && (resourceLease = session.AcquireResourceLease()) is null) return false;
        loadingResources = true; Render(); return true;
    }
    internal void EndResourceLoad()
    {
        if (resourceLease is { } lease) session?.ReleaseResourceLease(lease);
        resourceLease = null; loadingResources = false; Render();
    }

    internal async Task<bool> Configure(string root, SettingsDocument settings, bool reloadResources = false)
    {
        if (closed) return false;
        SetAlignedVisibility(settings.SheetVisibilityEnabled);
        if (configuring) { status("另一项运行设置正在应用，本次设置尚未应用，请稍后重试。"); return false; }
        var desired = new SessionSelection(settings.GameSeason, settings.GameMode, settings.RouteProfileId);
        var controls = JsonSerializer.SerializeToElement(new JsonObject { ["floorControlMode"] = settings.FloorControlMode,
            ["floorControlRegion"] = settings.FloorControlRegion });
        bool replace = reloadResources || runtime is null || root != resourceRoot || floorSettings is null || !JsonElement.DeepEquals(floorSettings.Value, controls);
        if (replace && !CanReplaceResources)
        { status("设置已保存；请先结束本局并等待识别停止，再点击“保存并应用到测试版”。"); return false; }
        configuring = true;
        var completed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        configurationCompleted = completed; Render();
        try
        {
            if (replace)
            {
                // Bind the proposed resource/settings context before replacing
                // the idle current runtime. A failed bind leaves it available.
                var nextRuntime = new LiveRuntime(capture, root, new() { FloorSettings = controls, SheetVisibilityEnabled = alignedVisible });
                var nextSession = new SessionService(new SynchronizationContextSessionDispatcher(new DispatcherSynchronizationContext(Dispatcher)), nextRuntime);
                proposedRuntime = nextRuntime; proposedSession = nextSession;
                bool proposedAccepted = nextSession.Reconfigure(settings.GameMode, settings.RouteProfileId, settings.GameSeason);
                await nextSession.DrainAsync();
                if (closed || !proposedAccepted || !nextRuntime.ReadState().ResourcesBound || nextRuntime.ReadState().Context.Selection != desired)
                {
                    string refusal = nextSession.Read().LastReceipt?.Reason ?? nextRuntime.ReadState().Reason;
                    nextSession.Shutdown(); nextRuntime.Shutdown(); await nextSession.DrainAsync();
                    if (!closed) status("运行资源未切换，仍保留原来的资源与设置。原因：" + refusal);
                    return false;
                }
                await CloseRuntime();
                if (closed) { nextSession.Shutdown(); nextRuntime.Shutdown(); return false; }
                nextRuntime.SetAlignedVisibility(alignedVisible);
                runtime = nextRuntime; session = nextSession;
                resourceRoot = root; floorSettings = controls; session.Changed += Render;
                session.ResultConsumed += ReconcileMapInput;
                mapInput.Reset(); mapRead = null;
            }
            else
            {
                if (!session!.Reconfigure(settings.GameMode, settings.RouteProfileId, settings.GameSeason))
                {
                    status("设置已保存，但尚未应用到本局：" + session.Read().Message);
                    return false;
                }
                await session.DrainAsync();
            }
            if (closed) return false;
            if (IsVisible) renderTimer.Start();
            var applied = runtime!.ReadState();
            bool succeeded = applied.ResourcesBound && applied.Context.Selection == desired;
            if (!succeeded) status($"设置已保存，运行设置未切换；当前仍为 {applied.Context.Season} / {applied.Context.Mode}。请检查所选模式的资源后重试应用。");
            return succeeded;
        }
        finally { proposedSession = null; proposedRuntime = null; configuring = false; completed.TrySetResult(); Render(); }
    }

    internal SessionDispatchResult? Dispatch(string action, object? payload = null)
    {
        if (closed || configuring || loadingResources || session is null) return null;
        if (action == "toggle_round") action = runtime?.ReadState().RoundActive == true ? "end_round" : "start_round";
        if (action is "end_round" or "stop_round" or "reset_round") CancelDelay();
        var result = session.Dispatch(action, payload is null ? null : JsonSerializer.SerializeToElement(payload));
        if (result.Disposition is SessionDispatchDisposition.Enqueued or SessionDispatchDisposition.Coalesced)
        {
            if (action == "capture_now") mapRead = (session.Read().Sequence.LastIssuedSequence, mapInput.Sequence);
            if (action is "end_round" or "stop_round" or "reset_round") { mapInput.Reset(); mapRead = null; }
        }
        if (result.Disposition is SessionDispatchDisposition.Unavailable or SessionDispatchDisposition.ResourceLocked)
            status("未执行：" + session.Read().Message);
        Render(); return result;
    }

    internal void BindingChanged()
        => SuspendInput("window-binding-changed");

    internal void SuspendInput(string reason)
    {
        if (closed) return;
        mapInput.Reset(); mapRead = null;
        CancelDelay(); session?.CancelPending(reason);
        runtime?.InvalidateCapture(reason); session?.RefreshRuntime(); Render();
    }

    internal void HandleShortcut(string action, double? createdAtSeconds = null)
    {
        if (action is "map" or "escape")
        {
            var edge = action == "map" ? mapInput.Press(createdAtSeconds: createdAtSeconds) : mapInput.Close(createdAtSeconds: createdAtSeconds);
            // A pass-through edge already changed the game's physical phase.
            // Outside/preparing/ending a round it cannot start or cancel round work.
            if (configuring || loadingResources || runtime?.ReadState().RoundActive != true || session is null
                || session.Read().PendingAction is "start_round" or "end_round" or "stop_round" or "reset_round" or "toggle_round") return;
            CancelDelay(); session.CancelPending("new-map-input"); runtime.InvalidateCapture("new-map-input"); session.RefreshRuntime();
            var payload = new { inputSequence = edge.Sequence, openIntent = edge.OpenIntent, inputCreatedAt = edge.CreatedAtSeconds, inputSource = edge.Source };
            Dispatch(edge.OpenIntent ? "capture_now" : "close_map", payload);
            return;
        }
        var command = action switch { "start-round" => "start_round", "toggle-round" => "toggle_round",
            "end-round" => "end_round", "candidate-prev" => "previous_candidate", "candidate-next" => "next_candidate",
            "confirm-round" => "confirm_route", "cycle-floor" => "cycle_floor", _ => null };
        if (command is not null) Dispatch(command);
    }

    void ReconcileMapInput(SessionCompletion completion)
    {
        if (closed || mapRead is not { } read || completion.Ticket.Sequence != read.Ticket || completion.Disposition != "Accepted") return;
        var observed = runtime?.ReadState();
        // A timeout/black transition does not prove that the game map closed.
        // Only current positive visual evidence reconciles this exact input edge.
        if (observed is { MapOpen: true, Context.FrameId: not null }) mapInput.Reconcile(true, read.Input);
    }

    async Task CaptureAfterDelay()
    {
        if (pendingCapture is not null || session is null || closed) return;
        var scheduledSession = session;
        var scheduledRound = runtime?.ReadState().Context.RoundId;
        using var cancellation = new CancellationTokenSource(); pendingCapture = cancellation; Render();
        try
        {
            for (int count = 3; count > 0; count--)
            { status($"请手动切回已连接的游戏窗口，{count} 秒后读取地图。"); await Task.Delay(1000, cancellation.Token); }
            cancellation.Token.ThrowIfCancellationRequested();
            if (ReferenceEquals(session, scheduledSession) && runtime?.ReadState() is { RoundActive: true } current
                && current.Context.RoundId == scheduledRound) Dispatch("capture_now");
        }
        catch (OperationCanceledException) { if (!closed) status("已取消本次读取。"); }
        finally { if (ReferenceEquals(pendingCapture, cancellation)) pendingCapture = null; Render(); }
    }

    void CancelDelay() => pendingCapture?.Cancel();
    void CancelWork()
    {
        CancelDelay(); session?.CancelPending(); runtime?.InvalidateCapture("cancelled");
        session?.RefreshRuntime(); status("已取消，正在结束的任务不会写回旧画面。"); Render();
    }

    void Render()
    {
        if (closed || !IsVisible) return;
        var view = session?.Read(); var state = runtime?.ReadState();
        resourceCaption.Text = state is { ResourcesBound: true } ? "本局运行资源：" + resourceRoot : "本局运行资源尚未绑定。";
        bool active = state?.RoundActive == true;
        bool working = view?.WorkerActive == true || pendingCapture is not null || configuring || loadingResources;
        bool available = state?.ResourcesBound == true;
        bool Supports(string action) => view?.Runtime.AvailableCommands.Contains(action) == true;
        UpdateVisibilityControl();
        start.IsEnabled = available && !active && !working && Supports("start_round");
        end.IsEnabled = active && !configuring && !loadingResources && Supports("end_round");
        acquire.IsEnabled = active && capture.IsBound && !working && Supports("capture_now");
        cancel.IsEnabled = !configuring && !loadingResources && working;
        previous.IsEnabled = active && !working && Supports("previous_candidate");
        next.IsEnabled = active && !working && Supports("next_candidate");
        confirm.IsEnabled = active && !working && Supports("confirm_route");
        pick.IsEnabled = active && !working && view?.Runtime.ManualMapPickAvailable == true;
        applyFloor.IsEnabled = active && !working && Supports("set_floor");
        if (state is null) return;
        summary.Text = $"{state.Context.Season} · {ModeLabel(state.Context.Mode)}";
        roundState.Text = !available ? "资源未就绪" : configuring || loadingResources ? "正在准备" : !active ? "等待开始" : working ? "正在处理" : "本局进行中";
        mapIdentity.Text = state.IdentityKind == "group" ? $"已确认相似组 · {state.IdentityMemberIds.Count} 张地图"
            : state.IdentityLocked ? $"已锁定 {state.MapDisplayName ?? state.Context.MapId}" : state.Candidates.Count > 0 ? $"{state.Candidates.Count} 个候选待确认" : "尚未确认";
        currentFloor.Text = state.Context.FloorId is null ? "尚未确认" : FloorLabel(state.Context.FloorId);
        candidateCaption.Text = state.IdentityKind == "group" ? $"当前显示参考：{state.MapDisplayName ?? state.Context.MapId}。组内成员仍待区分，可手动选图。"
            : state.IdentityLocked ? (state.NativeGeometryVerified ? "地图身份已锁定，结构已对齐。" : "地图身份已锁定，等待结构对齐。")
            : state.Candidates.Count > 0 ? "选择候选后确认地图；也可以展开手动选图。" : "读取游戏地图后，候选会显示在这里。";
        candidates.Visibility = state.Candidates.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        detail.Text = configuring || loadingResources ? "正在读取资源或应用设置，请稍候。"
            : working ? "正在识别，可随时取消。" : Reason(state.Reason);
        rendering = true;
        try
        {
            var rows = state.Candidates.Select((row, i) => new CandidateRow(i,
                row.IdentityKind == "group" ? $"相似地图组 · {row.MemberIds.Count} 张" : row.Name ?? row.MapId,
                row.IdentityVerified)).ToArray();
            if (!candidates.Items.Cast<CandidateRow>().SequenceEqual(rows)) candidates.ItemsSource = rows;
            candidates.SelectedIndex = state.SelectedCandidateIndex;
            var contract = FloorContract.Find(state.Context.Season, state.Context.Mode);
            var floorRows = new[] { new FloorRow("auto", "自动观察") }.Concat(contract?.ObservedValues.Select(value =>
                new FloorRow(contract.IdFor(value)!, FloorLabel(contract.IdFor(value)))) ?? []).ToArray();
            if (!floor.Items.Cast<FloorRow>().SequenceEqual(floorRows)) { floor.ItemsSource = floorRows; floor.SelectedIndex = 0; }
            if (!manualMap.Items.Cast<string>().SequenceEqual(availableMaps))
            {
                string? selected = manualMap.SelectedItem as string;
                manualMap.ItemsSource = availableMaps;
                if (selected is not null && availableMaps.Contains(selected)) manualMap.SelectedItem = selected;
            }
        }
        finally { rendering = false; }
    }

    internal void SetMaps(IEnumerable<string> mapIds)
    {
        var maps = mapIds.Distinct().Order().ToArray();
        if (availableMaps.SequenceEqual(maps)) return;
        availableMaps = maps; Render();
    }
    internal async Task DrainAsync() { if (session is not null) await session.DrainAsync(); }
    async Task CloseRuntime()
    {
        if (session is null) return;
        var previousSession = session; var previousRuntime = runtime!;
        session = null; runtime = null;
        previousSession.Changed -= Render; previousSession.ResultConsumed -= ReconcileMapInput;
        previousSession.Shutdown(); previousRuntime.Shutdown(); await previousSession.DrainAsync();
    }
    internal async Task CloseAsync()
    {
        if (closed) return;
        closed = true; renderTimer.Stop(); CancelDelay();
        proposedSession?.Shutdown(); proposedRuntime?.Shutdown();
        await CloseRuntime();
        if (configurationCompleted is { } completed) await completed.Task;
    }
    static string FloorLabel(string? value) => value is null ? "楼层待确认" : value == "B1" ? "地下 1 层" : value.StartsWith('-') ? $"地下 {value[1..]} 层" : value + "F";
    static string ModeLabel(string value) => value switch { "nightmare" => "噩梦", "difficult" => "困难", "difficulty2" => "普通（历史）", _ => value };
    static string Reason(string reason) => reason switch {
        "context-bound" => "资源已就绪，开始本局不会自动开图或采集。", "round-started" => "请在游戏中打开地图，再读取画面。",
        "entry-neighborhood-candidates" or "candidates-awaiting-confirmation" => "画面尚不能唯一确认地图，可选择候选或手动选图。", "window-binding-changed" => "窗口连接已改变，请重新读取。",
        "map-closed" => "地图已关闭，已确认的地图选择保留。", "cancelled" => "识别已取消。",
        "map-open-preparation-deadline" => "未在准备时间内读到完整地图，请打开地图并重试。", _ => string.IsNullOrEmpty(reason) ? "" : "当前结果：" + reason };
    static Button Add(Panel panel, string title, RoutedEventHandler action)
    {
        var button = new Button { Content = title, IsEnabled = false, Padding = new(12, 8, 12, 8), Margin = new(0, 0, 8, 8) };
        button.Click += action; panel.Children.Add(button); return button;
    }
    static TextBlock ValueText(string value) => new() { Text = value, FontSize = 14, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Margin = new(0, 7, 0, 0) };
    static void AddState(Grid grid, int column, string label, TextBlock value)
    {
        var group = new StackPanel();
        var caption = new TextBlock { Text = label, FontSize = 11 };
        caption.SetResourceReference(TextBlock.ForegroundProperty, "StarMap.Muted");
        group.Children.Add(caption); group.Children.Add(value);
        var surface = new Border { Child = group, Padding = new(12), CornerRadius = new(8), Margin = new(column == 0 ? 0 : 4, 0, column == 2 ? 0 : 4, 0) };
        surface.SetResourceReference(Border.BackgroundProperty, "StarMap.Canvas");
        Grid.SetColumn(surface, column); grid.Children.Add(surface);
    }
    sealed record CandidateRow(int Index, string MapId, bool Verified)
    { public override string ToString() => MapId + (Verified ? " · 已通过身份验证" : " · 待确认"); }
    sealed record FloorRow(string Id, string Label);
}
