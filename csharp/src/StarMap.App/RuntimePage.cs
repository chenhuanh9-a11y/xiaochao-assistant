using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using StarMap.Core.Floors;
using StarMap.Core.Session;
using StarMap.Core.Settings;
using StarMap.Runtime;

namespace StarMap.App;

/// <summary>Persistent UI consumer of the real session/runtime, with no capture or round on construction.</summary>
internal sealed class RuntimePage : UserControl
{
    readonly ICapturePort capture;
    readonly Action<string> status;
    readonly TextBlock summary = new() { Text = "选择结构资源并连接窗口后，可手动开始本局。", TextWrapping = TextWrapping.Wrap };
    readonly TextBlock detail = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 8, 0, 12) };
    readonly TextBlock resourceCaption = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 0, 0, 12) };
    readonly ListBox candidates = new() { MinHeight = 65, MaxHeight = 170, Margin = new(0, 8, 0, 10) };
    readonly ComboBox manualMap = new() { MinWidth = 270, MaxWidth = 620, HorizontalAlignment = HorizontalAlignment.Left };
    readonly ComboBox floor = new() { MinWidth = 130, HorizontalAlignment = HorizontalAlignment.Left, DisplayMemberPath = "Label", SelectedValuePath = "Id" };
    readonly Button start, end, acquire, cancel, previous, next, confirm, pick, applyFloor;
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
    string? resourceLease;

    internal RuntimePage(ICapturePort capture, Action<string> status)
    {
        this.capture = capture; this.status = status;
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "本局", FontSize = 19, FontWeight = FontWeights.SemiBold, Margin = new(0, 0, 0, 12) });
        panel.Children.Add(summary); panel.Children.Add(detail); panel.Children.Add(resourceCaption);
        var actions = new WrapPanel();
        start = Add(actions, "开始本局", (_, _) => Dispatch("start_round"));
        end = Add(actions, "结束本局", (_, _) => { CancelDelay(); Dispatch("end_round"); });
        acquire = Add(actions, "3 秒后读取地图", async (_, _) => await CaptureAfterDelay());
        cancel = Add(actions, "取消识别", (_, _) => CancelWork()); panel.Children.Add(actions);
        panel.Children.Add(candidates);
        candidates.SelectionChanged += (_, _) =>
        {
            if (!rendering && candidates.SelectedItem is CandidateRow row)
                Dispatch("select_candidate", new { index = row.Index });
        };
        var choices = new WrapPanel();
        previous = Add(choices, "上一候选", (_, _) => Dispatch("previous_candidate"));
        next = Add(choices, "下一候选", (_, _) => Dispatch("next_candidate"));
        confirm = Add(choices, "确认所选地图", (_, _) => Dispatch("confirm_route")); panel.Children.Add(choices);
        panel.Children.Add(new TextBlock { Text = "手动选图", Margin = new(0, 10, 0, 6) });
        panel.Children.Add(manualMap);
        var manualActions = new WrapPanel { Margin = new(0, 8, 0, 0) };
        pick = Add(manualActions, "确认手动选图", (_, _) =>
        {
            if (manualMap.SelectedItem is string mapId) Dispatch("confirm_route", new { mapId });
        });
        manualActions.Children.Add(floor);
        applyFloor = Add(manualActions, "应用楼层", (_, _) =>
        {
            if (floor.SelectedValue is string floorId) Dispatch("set_floor", new { floor = floorId });
        }); panel.Children.Add(manualActions);
        panel.Children.Add(new TextBlock { Text = "只读取你已打开的地图，不发送游戏按键。确认地图会立即保留选择；结构未对齐时显示等待对齐。悬浮路线显示仍在迁移。", TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 12, 0, 0) });
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
                var nextRuntime = new LiveRuntime(capture, root, new() { FloorSettings = controls });
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
                runtime = nextRuntime; session = nextSession;
                resourceRoot = root; floorSettings = controls; session.Changed += Render;
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
        var result = session.Dispatch(action, payload is null ? null : JsonSerializer.SerializeToElement(payload));
        if (result.Disposition is SessionDispatchDisposition.Unavailable or SessionDispatchDisposition.ResourceLocked)
            status("未执行：" + session.Read().Message);
        Render(); return result;
    }

    internal void BindingChanged()
    {
        if (closed) return;
        CancelDelay(); session?.CancelPending("window-binding-changed");
        runtime?.InvalidateCapture("window-binding-changed"); session?.RefreshRuntime(); Render();
    }

    internal void HandleShortcut(string action)
    {
        var command = action switch { "map" => "map", "start-round" => "start_round", "toggle-round" => "toggle_round",
            "end-round" => "end_round", "candidate-prev" => "previous_candidate", "candidate-next" => "next_candidate",
            "confirm-round" => "confirm_route", "cycle-floor" => "cycle_floor", "escape" => "close_map", _ => null };
        if (command is not null) Dispatch(command);
    }

    async Task CaptureAfterDelay()
    {
        if (pendingCapture is not null || session is null || closed) return;
        using var cancellation = new CancellationTokenSource(); pendingCapture = cancellation; Render();
        try
        {
            for (int count = 3; count > 0; count--)
            { status($"请手动切回已连接的游戏窗口，{count} 秒后读取地图。"); await Task.Delay(1000, cancellation.Token); }
            cancellation.Token.ThrowIfCancellationRequested(); Dispatch("capture_now");
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
        start.IsEnabled = available && capture.IsBound && !active && !working && Supports("start_round");
        end.IsEnabled = active && !configuring && !loadingResources && Supports("end_round");
        acquire.IsEnabled = active && capture.IsBound && !working && Supports("capture_now");
        cancel.IsEnabled = !configuring && !loadingResources && working;
        previous.IsEnabled = active && !working && Supports("previous_candidate");
        next.IsEnabled = active && !working && Supports("next_candidate");
        confirm.IsEnabled = active && !working && Supports("confirm_route");
        pick.IsEnabled = active && !working && view?.Runtime.ManualMapPickAvailable == true;
        applyFloor.IsEnabled = active && !working && Supports("set_floor");
        if (state is null) return;
        summary.Text = $"{state.Context.Season} · {ModeLabel(state.Context.Mode)} · " + (!available ? "运行资源尚未绑定。" : !active ? "本局尚未开始。" : state.IdentityLocked
            ? $"已锁定 {state.Context.MapId} · {FloorLabel(state.Context.FloorId)} · " + (state.NativeGeometryVerified ? "结构已对齐" : "等待结构对齐")
            : state.Candidates.Count > 0 ? $"当前 {FloorLabel(state.Context.FloorId)}，有 {state.Candidates.Count} 个候选，请确认地图。" : "本局已开始，等待读取游戏地图。");
        detail.Text = state.Context.Season == "s1" ? "此赛季的完整识别流程仍在迁移，当前不能读取并对齐地图。"
            : configuring || loadingResources ? "正在读取资源或应用设置，请稍候。"
            : working ? "正在识别，可随时取消。" : Reason(state.Reason);
        rendering = true;
        try
        {
            var rows = state.Candidates.Select((row, i) => new CandidateRow(i, row.MapId, row.IdentityVerified)).ToArray();
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
        previousSession.Changed -= Render; previousSession.Shutdown(); previousRuntime.Shutdown(); await previousSession.DrainAsync();
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
        var button = new Button { Content = title, IsEnabled = false, Padding = new(13, 7, 13, 7), Margin = new(0, 0, 9, 8) };
        button.Click += action; panel.Children.Add(button); return button;
    }
    sealed record CandidateRow(int Index, string MapId, bool Verified)
    { public override string ToString() => MapId + (Verified ? " · 已通过身份验证" : " · 待确认"); }
    sealed record FloorRow(string Id, string Label);
}
