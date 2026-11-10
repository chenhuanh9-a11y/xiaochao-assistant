using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.Bridge;

namespace StarMap.Desktop;

public sealed class RuntimeCandidate(string key, int index, JsonElement value) : INotifyPropertyChanged
{
    public string Key { get; } = key;
    public int Index { get; private set; } = index;
    public JsonElement Data { get; private set; } = value;
    public string Name => RuntimeViewModel.String(Data, "name", Key);
    public string Detail => RuntimeViewModel.String(Data, "reason", "等待当前画面验证");
    public event PropertyChangedEventHandler? PropertyChanged;
    public void Update(int index, JsonElement value)
    {
        Index = index;
        if (Data.GetRawText() == value.GetRawText()) return;
        Data = value;
        PropertyChanged?.Invoke(this, new(nameof(Name)));
        PropertyChanged?.Invoke(this, new(nameof(Detail)));
    }
}

/// <summary>UI-thread state projection. The Python snapshot owns round/identity/pose.</summary>
public sealed class RuntimeViewModel : INotifyPropertyChanged
{
    private IWorkerConnection? connection;
    private CancellationTokenSource request = new();
    private long generation, viewGeneration;
    private bool closing, visible, busy, prepared, serviceUnavailable;
    private BrowseContext? context;
    private JsonElement? snapshot, pendingRender;
    private string? stagedFrame;
    private string frameRoot = "", frameLabel = "尚未选择保存画面", status = "先选择本机资源目录。", detail = "";
    private ImageSource? frameImage, alignedImage;
    private Transform overlayTransform = Transform.Identity;
    private double clientWidth = 1280, clientHeight = 720, imageWidth, imageHeight;
    private int frameWidth, frameHeight;
    private readonly HashSet<Task> copies = [];
    private RuntimeCandidate? selectedCandidate;
    private FloorChoice? selectedFloor;
    private string? cachedFramePath, cachedRenderKey, cachedRenderPng;
    private string? floorSelectionContext, lastPublishedFloor;
    private BitmapSource? cachedFrame, cachedRender;

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
    private bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value; Changed(name); return true;
    }
    public BrowseContext[] Contexts { get; private set; } = [];
    public ObservableCollection<RuntimeCandidate> Candidates { get; } = [];
    public FloorChoice[] Floors { get; private set; } = [];
    public bool Busy { get => busy; private set { if (Set(ref busy, value)) NotifyActions(); } }
    public bool RoundActive => snapshot.HasValue && Flag(snapshot.Value, "roundActive");
    public bool RoundPreparing => snapshot.HasValue && Flag(snapshot.Value, "roundPreparing");
    public bool HasRound => RoundActive || RoundPreparing;
    public bool ServiceUnavailable { get => serviceUnavailable; private set { if (Set(ref serviceUnavailable, value)) NotifyActions(); } }
    public bool HasFrame => stagedFrame is not null;
    public bool CanChangeContext => connection is not null && !ServiceUnavailable && !Busy && !HasRound && !closing;
    public bool CanStart => prepared && HasFrame && !Busy && !HasRound && !closing && Command("start_round");
    public bool CanCapture => !ServiceUnavailable && HasFrame && HasRound && !Busy && !closing && Command("capture_now");
    public bool CanEnd => !ServiceUnavailable && HasRound && !closing;
    public bool CanSelectCandidate => !ServiceUnavailable && HasFrame && HasRound && !Busy && !closing && SelectedCandidate is not null && Command("select_candidate");
    public bool CanSetFloor => !ServiceUnavailable && HasFrame && HasRound && !Busy && !closing && SelectedFloor is not null && Command("set_floor");
    public bool CanConfirm => !ServiceUnavailable && HasFrame && HasRound && !Busy && !closing && Command("confirm_route");
    public bool CanLoadFrame => connection?.SupportsRuntime == true && !ServiceUnavailable && !closing;
    public bool CanToggleSheet => connection?.SupportsRuntime == true && !ServiceUnavailable && !Busy && !closing;
    public JsonElement? Snapshot => snapshot;
    public string Status { get => status; private set => Set(ref status, value); }
    public string Detail { get => detail; private set => Set(ref detail, value); }
    public string FrameLabel { get => frameLabel; private set => Set(ref frameLabel, value); }
    public string RoundStatus => RoundActive ? "本局进行中" : RoundPreparing ? "本局已准备" : "等待开始";
    public string MapName => snapshot.HasValue ? String(snapshot.Value, "mapName", String(snapshot.Value, "mapId", "尚未确认")) : "尚未确认";
    public string FloorLabel => snapshot.HasValue ? FloorName(String(snapshot.Value, "observedFloor", String(snapshot.Value, "floor", ""))) : "尚未确认";
    public ImageSource? FrameImage { get => frameImage; private set => Set(ref frameImage, value); }
    public ImageSource? AlignedImage { get => alignedImage; private set => Set(ref alignedImage, value); }
    public Transform OverlayTransform { get => overlayTransform; private set => Set(ref overlayTransform, value); }
    public double ClientWidth { get => clientWidth; private set => Set(ref clientWidth, value); }
    public double ClientHeight { get => clientHeight; private set => Set(ref clientHeight, value); }
    public double ImageWidth { get => imageWidth; private set => Set(ref imageWidth, value); }
    public double ImageHeight { get => imageHeight; private set => Set(ref imageHeight, value); }
    public bool IsVisible
    {
        get => visible;
        set { if (Set(ref visible, value)) { viewGeneration++; if (value) _ = PublishVisibleAsync(); } }
    }
    public BrowseContext? Context
    {
        get => context;
        set
        {
            if (Equals(context, value) || !CanChangeContext) return;
            context = value; Changed();
            _ = PrepareAsync();
        }
    }
    public RuntimeCandidate? SelectedCandidate
    {
        get => selectedCandidate;
        set { if (Set(ref selectedCandidate, value)) NotifyActions(); }
    }
    public FloorChoice? SelectedFloor
    {
        get => selectedFloor;
        set { if (Set(ref selectedFloor, value)) NotifyActions(); }
    }

    public async Task AttachAsync(IWorkerConnection worker, string ownedFrameRoot)
    {
        Detach();
        connection = worker;
        frameRoot = ownedFrameRoot;
        if (!worker.SupportsRuntime) { Status = "当前连接只提供图库。"; return; }
        var hello = await worker.HelloAsync();
        if (closing || connection != worker) return;
        Contexts = hello.Contexts; Changed(nameof(Contexts));
        context = Contexts.FirstOrDefault(); Changed(nameof(Context));
        await PrepareAsync();
    }

    public Task PrepareAsync()
    {
        if (Context is null || connection is null || closing || HasRound) return Task.CompletedTask;
        prepared = false;
        snapshot = null;
        NotifySnapshot();
        return ExecuteAsync("prepare", new { season = Context.Season, mode = Context.Mode }, "正在准备本局资源…");
    }

    public async Task LoadFrameAsync(string source, object? captureContext = null)
    {
        if (!CanLoadFrame) return;
        var worker = connection!;
        var current = Begin("正在读取保存画面…");
        stagedFrame = null; pendingRender = null; FrameImage = null; AlignedImage = null;
        FrameLabel = "尚未选择保存画面";
        NotifyActions();
        try
        {
            var path = Path.Combine(frameRoot, Guid.NewGuid().ToString("N") + ".png");
            Directory.CreateDirectory(frameRoot);
            var copy = Task.Run(() => File.Copy(source, path, overwrite: false));
            copies.Add(copy);
            try { await copy; }
            finally { copies.Remove(copy); }
            if (!Current(current, worker)) return;
            var result = await worker.InvokeAsync("set_frame", new { path, captureContext }, request.Token);
            if (!Current(current, worker)) return;
            ApplySnapshot(result);
            if (!result.TryGetProperty("frame", out var frame) || !Flag(frame, "success"))
                throw new InvalidDataException(frame.ValueKind == JsonValueKind.Object ? String(frame, "reason", "保存画面不可用。") : "保存画面不可用。");
            stagedFrame = path;
            FrameLabel = Path.GetFileName(source);
            var shape = frame.GetProperty("frameShape");
            ClientHeight = frameHeight = shape[0].GetInt32(); ClientWidth = frameWidth = shape[1].GetInt32();
            Status = "画面已载入，可以开始本局。";
            await PublishVisibleAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { await RecoverAsync(current, worker, error); }
        finally { if (Current(current, worker)) { Busy = false; NotifyActions(); } }
    }

    public Task StartRoundAsync() => !CanStart ? Task.CompletedTask : ExecuteAsync("dispatch", new { action = "start_round" }, "正在开始本局…");
    public Task CaptureAsync() => !CanCapture ? Task.CompletedTask : ExecuteAsync("dispatch", new { action = "capture_now" }, "正在识别与贴合…");
    public Task EndRoundAsync() => !CanEnd ? Task.CompletedTask : ExecuteAsync("dispatch", new { action = "end_round" }, "正在结束本局…");
    public Task CancelAsync() => ExecuteAsync("dispatch", new { action = "close_map" }, "正在取消识别…");
    public Task ConfirmAsync() => !CanConfirm ? Task.CompletedTask : ExecuteAsync("dispatch", new { action = "confirm_route" }, "正在确认路线…");
    public Task SetFloorAsync()
    {
        var floor = SelectedFloor;
        return !CanSetFloor || floor is null ? Task.CompletedTask :
            ExecuteAsync("dispatch", new { action = "set_floor", payload = new { floor = floor.Id } }, "正在核对楼层…");
    }
    public Task ToggleSheetAsync() => !CanToggleSheet ? Task.CompletedTask : ExecuteAsync("dispatch", new { action = "toggle_sheet_visibility" }, "正在更新显示…");
    public Task SelectCandidateAsync()
    {
        var candidate = SelectedCandidate;
        if (!CanSelectCandidate || candidate is null) return Task.CompletedTask;
        var row = candidate.Data;
        var payload = new Dictionary<string, object?> { ["index"] = candidate.Index };
        if (String(row, "selectionSeason") == "s1") payload["candidateMapId"] = String(row, "mapId");
        if (Flag(row, "structureSupported"))
            foreach (var key in new[] { "mapId", "structureFrameId", "structureGeneration", "structureRoundId" })
                if (row.TryGetProperty(key, out var value)) payload[key] = value.Clone();
        return ExecuteAsync("dispatch", new { action = "select_candidate", payload }, "正在核对所选地图…");
    }

    private async Task ExecuteAsync(string operation, object arguments, string message)
    {
        if (connection is null || !connection.SupportsRuntime || closing) return;
        var worker = connection;
        var current = Begin(message);
        try
        {
            var result = await worker.InvokeAsync(operation, arguments, request.Token);
            if (!Current(current, worker)) return;
            if (result.TryGetProperty("capability", out var unavailable) && !Flag(unavailable, "available"))
            {
                prepared = false; snapshot = null;
                Status = "所选模式的资源尚不可用。";
                Detail = String(unavailable, "reason");
                NotifySnapshot();
                return;
            }
            ApplySnapshot(result);
            if (result.TryGetProperty("capability", out var capability))
            {
                prepared = Flag(capability, "available");
                Status = prepared ? "本局资源已就绪。" : "所选模式的资源尚不可用。";
                Detail = String(capability, "reason");
            }
            else if (result.TryGetProperty("receipt", out var receipt))
            {
                var reason = String(receipt, "reason");
                Status = Message(reason, Flag(receipt, "success"));
                Detail = reason;
            }
            await PublishVisibleAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { await RecoverAsync(current, worker, error); }
        finally { if (Current(current, worker)) Busy = false; }
    }

    private async Task RecoverAsync(long current, IWorkerConnection worker, Exception error)
    {
        if (!Current(current, worker)) return;
        Status = "操作未完成，正在同步本局状态…"; Detail = error.Message;
        try
        {
            // An RPC error revokes pixels, not the Python-owned round. Reconcile
            // after its terminal has drained before enabling any new action.
            var latest = await worker.InvokeAsync("snapshot", cancellation: request.Token);
            if (!Current(current, worker)) return;
            ApplySnapshot(latest);
            Status = "操作未完成，可以重试或结束本局。";
            await PublishVisibleAsync();
        }
        catch (Exception syncError)
        {
            if (!Current(current, worker)) return;
            ServiceUnavailable = true; prepared = false;
            Status = "服务状态未能同步，请重新连接资源目录；重新连接会结束当前本局。";
            Detail = error.Message + "；" + syncError.Message;
            NotifySnapshot();
        }
    }

    private long Begin(string message)
    {
        generation++; viewGeneration++;
        request.Cancel(); request.Dispose(); request = new();
        pendingRender = null; AlignedImage = null;
        Busy = true; Status = message; Detail = "";
        return generation;
    }
    private bool Current(long value, IWorkerConnection worker) => !closing && generation == value && connection == worker;
    private void ApplySnapshot(JsonElement result)
    {
        if (!result.TryGetProperty("snapshot", out var state)) throw new InvalidDataException("服务未返回本局状态。");
        if (Context is not null && (String(state, "season") != Context.Season || String(state, "mode") != Context.Mode))
            throw new InvalidDataException("返回状态与当前赛季/模式不一致。");
        snapshot = state.Clone();
        ServiceUnavailable = false;
        pendingRender = result.TryGetProperty("render", out var render) && render.ValueKind == JsonValueKind.Object ? render.Clone() : null;
        NotifySnapshot();
    }
    private void NotifySnapshot()
    {
        if (IsVisible && !snapshot.HasValue) ClearChoices();
        Changed(nameof(Snapshot));
        Changed(nameof(RoundActive)); Changed(nameof(RoundPreparing)); Changed(nameof(HasRound));
        Changed(nameof(RoundStatus)); Changed(nameof(MapName)); Changed(nameof(FloorLabel));
        NotifyActions();
    }
    private void NotifyActions()
    {
        foreach (var name in new[] { nameof(CanChangeContext), nameof(CanStart), nameof(CanCapture), nameof(CanEnd),
            nameof(CanSelectCandidate), nameof(CanSetFloor), nameof(CanConfirm), nameof(CanLoadFrame), nameof(CanToggleSheet), nameof(HasFrame) }) Changed(name);
    }
    private bool Command(string name) => snapshot.HasValue && snapshot.Value.TryGetProperty("availableCommands", out var list)
        && list.ValueKind == JsonValueKind.Array && list.EnumerateArray().Any(item => item.GetString() == name);

    private async Task PublishVisibleAsync()
    {
        if (!IsVisible || closing) return;
        var current = generation; var view = ++viewGeneration;
        if (!snapshot.HasValue) ClearChoices();
        if (snapshot.HasValue)
        {
            var state = snapshot.Value;
            var selected = SelectedCandidate?.Key;
            var rows = state.TryGetProperty("candidateRows", out var array) && array.ValueKind == JsonValueKind.Array ? array.EnumerateArray().ToArray() : [];
            var existing = Candidates.ToDictionary(row => row.Key);
            var ordered = new List<RuntimeCandidate>();
            for (var i = 0; i < rows.Length; i++)
            {
                var key = String(rows[i], "key", String(rows[i], "mapId", i.ToString()));
                if (existing.TryGetValue(key, out var row)) row.Update(i, rows[i].Clone());
                else row = new(key, i, rows[i].Clone());
                ordered.Add(row);
            }
            for (var i = Candidates.Count - 1; i >= 0; i--) if (!ordered.Contains(Candidates[i])) Candidates.RemoveAt(i);
            for (var i = 0; i < ordered.Count; i++)
            {
                var old = Candidates.IndexOf(ordered[i]);
                if (old < 0) Candidates.Insert(i, ordered[i]); else if (old != i) Candidates.Move(old, i);
            }
            SelectedCandidate = Candidates.FirstOrDefault(row => row.Key == selected)
                ?? Candidates.ElementAtOrDefault(state.TryGetProperty("selectedCandidateIndex", out var index) ? index.GetInt32() : -1);
            var nextFloors = state.TryGetProperty("availableFloors", out var floors) && floors.ValueKind == JsonValueKind.Array
                ? floors.EnumerateArray().Select(value => new FloorChoice(value.GetString()!, FloorName(value.GetString()!))).ToArray() : [];
            if (!Floors.SequenceEqual(nextFloors)) { Floors = nextFloors; Changed(nameof(Floors)); }
            var actualFloor = String(state, "floor");
            var actualContext = string.Join("/", String(state, "season"), String(state, "mode"), String(state, "mapId"), state.GetProperty("roundId").GetRawText());
            if (floorSelectionContext != actualContext || lastPublishedFloor != actualFloor || !Floors.Contains(SelectedFloor))
                SelectedFloor = Floors.FirstOrDefault(row => row.Id == actualFloor) ?? Floors.FirstOrDefault();
            floorSelectionContext = actualContext; lastPublishedFloor = actualFloor;
        }
        var path = stagedFrame;
        var render = path is not null && snapshot.HasValue && Flag(snapshot.Value, "alignedVisible") ? pendingRender : null;
        var stateForRender = snapshot;
        var oldFramePath = cachedFramePath; var oldFrame = cachedFrame;
        var oldRenderKey = cachedRenderKey; var oldRenderPng = cachedRenderPng; var oldRender = cachedRender;
        try
        {
            if (render.HasValue)
            {
                ValidateRender(render.Value, stateForRender!.Value);
                if (render.Value.GetProperty("clientWidth").GetInt32() != frameWidth || render.Value.GetProperty("clientHeight").GetInt32() != frameHeight)
                    throw new InvalidDataException("贴合坐标与当前保存画面尺寸不一致。");
            }
            var images = await Task.Run(() => (Frame: path is null ? null : oldFramePath == path ? oldFrame : DecodeFile(path),
                Overlay: render.HasValue ? DecodeRender(render.Value, oldRenderKey, oldRenderPng, oldRender) : null));
            if (closing || !IsVisible || generation != current || viewGeneration != view) return;
            FrameImage = images.Frame;
            cachedFramePath = path; cachedFrame = images.Frame;
            AlignedImage = images.Overlay?.Image;
            if (images.Overlay is { } overlay)
            {
                cachedRenderKey = overlay.Key; cachedRenderPng = overlay.Png; cachedRender = overlay.Image;
                ImageWidth = overlay.Image.PixelWidth; ImageHeight = overlay.Image.PixelHeight;
                ClientWidth = overlay.ClientWidth; ClientHeight = overlay.ClientHeight;
                OverlayTransform = overlay.Transform;
            }
        }
        catch (Exception error)
        {
            if (!closing && generation == current && viewGeneration == view) { AlignedImage = null; Detail = "预览未显示：" + error.Message; }
        }
    }

    private void ClearChoices()
    {
        if (Candidates.Count > 0) Candidates.Clear();
        if (Floors.Length > 0) { Floors = []; Changed(nameof(Floors)); }
        SelectedCandidate = null; SelectedFloor = null;
        floorSelectionContext = lastPublishedFloor = null;
    }

    private static void ValidateRender(JsonElement render, JsonElement state)
    {
        if (!Flag(state, "displayAuthorized") || !state.TryGetProperty("render", out var current))
            throw new InvalidDataException("当前本局未授权显示这张图片。");
        foreach (var key in new[] { "token", "season", "mode", "mapId", "floor", "profileId", "frameId", "generation", "roundId", "displayGeneration", "resourceIdentity" })
            if (!render.TryGetProperty(key, out var actual) || !current.TryGetProperty(key, out var expected) || !JsonElement.DeepEquals(actual, expected))
                throw new InvalidDataException("图片已不属于当前本局、画面或资源。");
        if (String(render, "frameId") != String(state, "frameId"))
            throw new InvalidDataException("图片已不属于当前画面。");
    }
    private sealed record Overlay(BitmapSource Image, Transform Transform, int ClientWidth, int ClientHeight, string Key, string Png);
    private static Overlay DecodeRender(JsonElement render, string? previousKey, string? previousPng, BitmapSource? previousImage)
    {
        var key = string.Join("/", render.GetProperty("resourceIdentity").GetRawText(), String(render, "mapId"),
            String(render, "floor"), String(render, "effectiveProfileId"));
        var png = render.GetProperty("pngBase64").GetString()!;
        var bitmap = previousImage is not null && previousKey == key && previousPng == png ? previousImage : Decode(Convert.FromBase64String(png));
        if (bitmap.PixelWidth != render.GetProperty("width").GetInt32() || bitmap.PixelHeight != render.GetProperty("height").GetInt32())
            throw new InvalidDataException("贴合图片尺寸不一致。");
        var h = render.GetProperty("matrix");
        var values = h.EnumerateArray().SelectMany(row => row.EnumerateArray().Select(value => value.GetDouble())).ToArray();
        if (values.Length != 9 || values.Any(value => !double.IsFinite(value)) || values[6] != 0 || values[7] != 0 || values[8] != 1)
            throw new InvalidDataException("当前预览暂不支持此投影矩阵。");
        var transform = new MatrixTransform(new Matrix(values[0], values[3], values[1], values[4], values[2], values[5]));
        transform.Freeze();
        return new(bitmap, transform, render.GetProperty("clientWidth").GetInt32(), render.GetProperty("clientHeight").GetInt32(), key, png);
    }
    private static BitmapSource DecodeFile(string path) => Decode(File.ReadAllBytes(path));
    private static BitmapSource Decode(byte[] bytes)
    {
        using var stream = new MemoryStream(bytes, writable: false);
        var image = new BitmapImage();
        image.BeginInit(); image.CacheOption = BitmapCacheOption.OnLoad; image.StreamSource = stream; image.EndInit(); image.Freeze();
        return image;
    }
    internal static string String(JsonElement value, string key, string fallback = "") => value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.String && !string.IsNullOrEmpty(item.GetString()) ? item.GetString()! : fallback;
    private static bool Flag(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.True;
    private static string FloorName(string floor) => floor switch { "" => "尚未确认", "B1" => "B1 地下室", _ => floor + " 层" };
    private static string Message(string reason, bool success) => reason switch
    {
        "round-prepared" => "本局已准备，可以识别当前画面。",
        "current-frame-pose-authorized" => "当前画面的贴合已通过验证。",
        "identity-not-uniquely-verified" => "地图身份尚未唯一确认。",
        "s1-structure-candidates-awaiting-confirmation" => "候选已更新，等待确认。",
        "round-ended" or "round-stopped" => "本局已结束。",
        "close_map" => "已取消当前识别，本局保持不变。",
        "cancelled" => "已取消识别。",
        _ => success ? "操作已完成。" : "当前操作未通过，请查看详细原因。"
    };

    public void Detach()
    {
        generation++; viewGeneration++;
        request.Cancel();
        connection = null; snapshot = null; pendingRender = null; stagedFrame = null; prepared = false; ServiceUnavailable = false;
        Contexts = []; context = null; Changed(nameof(Contexts)); Changed(nameof(Context));
        cachedFramePath = cachedRenderKey = cachedRenderPng = null; cachedFrame = cachedRender = null;
        floorSelectionContext = lastPublishedFloor = null; frameWidth = frameHeight = 0;
        FrameImage = null; AlignedImage = null; Candidates.Clear(); Floors = [];
        Changed(nameof(Floors)); SelectedCandidate = null; SelectedFloor = null;
        Busy = false; FrameLabel = "尚未选择保存画面"; Status = "先选择本机资源目录。"; Detail = "";
        NotifySnapshot();
    }
    public void BeginClose() { if (closing) return; closing = true; Detach(); }
    public Task DrainCopiesAsync() => Task.WhenAll(copies.ToArray());
}
