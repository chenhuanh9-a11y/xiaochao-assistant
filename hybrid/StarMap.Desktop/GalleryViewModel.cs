using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.Bridge;

namespace StarMap.Desktop;

public sealed record FloorChoice(string Id, string Name);

/// <summary>UI-thread-owned selection; workers own resources, never navigation or visibility.</summary>
public sealed class GalleryViewModel : INotifyPropertyChanged
{
    private IWorkerConnection? worker;
    private BrowseCatalog? catalog;
    private BrowseCatalog? pendingCatalog;
    private BrowsePreview? pendingPreview;
    private Task<BitmapSource>? previewDecode;
    private CancellationTokenSource contextCancellation = new(), previewCancellation = new();
    private long contextGeneration, previewGeneration;
    private bool closing, restoring, visible = true, needsReload;
    private BrowseContext? context;
    private RouteProfile? profile;
    private FloorChoice? floor;
    private BrowseRow? selectedRow;
    private ImageSource? preview;
    private string search = "", status = "选择本机资源目录，开始浏览地图。", previewStatus = "选择地图与楼层";
    private bool busy;
    private readonly Dictionary<string, (string? Profile, string? Floor, string? Key, string Search)> selections = new();
    private static readonly FloorChoice AllFloors = new("", "全部楼层");

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
    private bool Set<T>(ref T storage, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(storage, value)) return false;
        storage = value;
        Changed(name);
        return true;
    }

    public BrowseContext[] Contexts { get; private set; } = [];
    public RouteProfile[] Profiles { get; private set; } = [];
    public FloorChoice[] Floors { get; private set; } = [AllFloors];
    public ICollectionView? Rows { get; private set; }
    public bool Busy { get => busy; private set => Set(ref busy, value); }
    public bool IsVisible
    {
        get => visible;
        set
        {
            if (!Set(ref visible, value) || !value || closing) return;
            if (pendingCatalog is { } loaded) { pendingCatalog = null; ApplyCatalog(loaded); }
            else if (needsReload) _ = ReloadAsync();
            else _ = PublishPreviewAsync();
        }
    }
    public string Status { get => status; private set => Set(ref status, value); }
    public string PreviewStatus { get => previewStatus; private set => Set(ref previewStatus, value); }
    public ImageSource? Preview { get => preview; private set => Set(ref preview, value); }
    public string SelectedTitle => selectedRow?.Title ?? "地图预览";
    public string SelectedDescription => selectedRow?.Description ?? "选择左侧的地图与楼层";
    public BrowseContext? Context
    {
        get => context;
        set
        {
            if (Equals(context, value)) return;
            RememberSelection();
            context = value;
            Changed();
            if (!restoring) _ = ReloadAsync();
        }
    }
    public RouteProfile? Profile
    {
        get => profile;
        set { if (Set(ref profile, value) && !restoring) FilterRows(); }
    }
    public FloorChoice? Floor
    {
        get => floor;
        set { if (Set(ref floor, value) && !restoring) FilterRows(); }
    }
    public string Search
    {
        get => search;
        set { if (Set(ref search, value) && !restoring) FilterRows(); }
    }
    public BrowseRow? SelectedRow
    {
        get => selectedRow;
        set
        {
            if (!Set(ref selectedRow, value)) return;
            Changed(nameof(SelectedTitle));
            Changed(nameof(SelectedDescription));
            if (!restoring) _ = LoadPreviewAsync();
        }
    }

    public async Task AttachAsync(IWorkerConnection connection)
    {
        RevokeContext();
        worker = connection;
        var generation = contextGeneration;
        selections.Clear();
        ClearCatalog();
        Busy = true;
        Status = "正在连接本机资源服务…";
        try
        {
            var hello = await connection.HelloAsync(contextCancellation.Token);
            if (closing || worker != connection || generation != contextGeneration) return;
            Contexts = hello.Contexts;
            Changed(nameof(Contexts));
            restoring = true;
            Context = Contexts.FirstOrDefault();
            restoring = false;
            if (IsVisible) await ReloadAsync(); else needsReload = true;
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (worker == connection && generation == contextGeneration && !closing) Status = error.Message;
        }
        finally { if (worker == connection && generation == contextGeneration && !closing) Busy = false; }
    }

    public async Task ReloadAsync()
    {
        if (closing || worker is null || Context is null) return;
        needsReload = false;
        RememberSelection();
        RevokeContext();
        var generation = contextGeneration;
        var connection = worker;
        var selectedContext = Context;
        ClearCatalog();
        Busy = true;
        Status = "正在读取地图资源…";
        try
        {
            var result = await connection.CatalogAsync(selectedContext.Season, selectedContext.Mode, contextCancellation.Token);
            if (closing || generation != contextGeneration || worker != connection || Context != selectedContext) return;
            if (result.Season != selectedContext.Season || result.Mode != selectedContext.Mode)
                throw new InvalidDataException("资源服务返回了其他赛季或模式。");
            if (IsVisible) ApplyCatalog(result); else pendingCatalog = result;
            Status = $"已读取 {result.Rows.Select(row => row.MapId).Distinct().Count()} 份图 · {result.Rows.Length} 张楼层图片";
            if (result.Warnings.Length > 0) Status += " · " + string.Join("；", result.Warnings);
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!closing && generation == contextGeneration) Status = "资源读取失败：" + error.Message;
        }
        finally { if (!closing && generation == contextGeneration) Busy = false; }
    }

    private void ApplyCatalog(BrowseCatalog result)
    {
        catalog = result;
        Profiles = result.Profiles;
        Floors = [AllFloors, .. result.Rows.Select(row => new FloorChoice(row.Floor, row.FloorLabel)).DistinctBy(item => item.Id)];
        Changed(nameof(Profiles)); Changed(nameof(Floors));
        Rows = CollectionViewSource.GetDefaultView(result.Rows);
        Rows.Filter = item => item is BrowseRow row && Matches(row);
        Changed(nameof(Rows));
        selections.TryGetValue(ContextKey(Context!), out var saved);
        restoring = true;
        Profile = Profiles.FirstOrDefault(item => item.Id == saved.Profile) ?? Profiles.FirstOrDefault();
        Floor = Floors.FirstOrDefault(item => item.Id == saved.Floor) ?? AllFloors;
        Search = saved.Search ?? "";
        restoring = false;
        FilterRows(saved.Key);
    }

    private bool Matches(BrowseRow row) => row.ProfileId == Profile?.Id
        && (string.IsNullOrEmpty(Floor?.Id) || row.Floor == Floor.Id)
        && (string.IsNullOrWhiteSpace(Search) || (row.Title + " " + row.MapId).Contains(Search, StringComparison.OrdinalIgnoreCase));

    private void FilterRows(string? preferredKey = null)
    {
        if (Rows is null) return;
        var key = preferredKey ?? SelectedRow?.Key;
        Rows.Refresh();
        var visible = Rows.Cast<BrowseRow>().ToArray();
        SelectedRow = visible.FirstOrDefault(row => row.Key == key)
            ?? visible.FirstOrDefault(row => row.Available) ?? visible.FirstOrDefault();
    }

    private async Task LoadPreviewAsync()
    {
        RevokePreview();
        var generation = previewGeneration;
        var currentCatalog = catalog;
        var row = SelectedRow;
        var connection = worker;
        Preview = null;
        PreviewStatus = row is null ? "没有符合条件的地图" : row.Available ? "正在读取图片…" : "图片文件缺失，请检查图包后刷新。";
        if (row is null || !row.Available || currentCatalog is null || connection is null || closing) return;
        try
        {
            var result = await connection.PreviewAsync(currentCatalog.CatalogId, row.Key, previewCancellation.Token);
            if (result.CatalogId != currentCatalog.CatalogId || result.Key != row.Key)
                throw new InvalidDataException("预览响应与当前地图不一致。");
            if (closing || generation != previewGeneration || catalog != currentCatalog || worker != connection || SelectedRow != row) return;
            pendingPreview = result;
            await PublishPreviewAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!closing && generation == previewGeneration) PreviewStatus = "图片读取失败：" + error.Message;
        }
    }

    private async Task PublishPreviewAsync()
    {
        if (!IsVisible || closing || pendingPreview is not { } result || Preview is not null) return;
        var generation = previewGeneration;
        try
        {
            var bitmap = await (previewDecode ??= Task.Run(() => Decode(result)));
            if (closing || !IsVisible || generation != previewGeneration) return;
            Preview = bitmap; PreviewStatus = "";
        }
        catch (Exception error)
        {
            if (!closing && IsVisible && generation == previewGeneration) PreviewStatus = "图片读取失败：" + error.Message;
        }
    }

    private static BitmapSource Decode(BrowsePreview preview)
    {
        if (preview.Width is < 1 or > 1600 || preview.Height is < 1 or > 1600)
            throw new InvalidDataException("预览图片尺寸无效。");
        var bytes = Convert.FromBase64String(preview.PngBase64);
        using var stream = new MemoryStream(bytes, writable: false);
        var image = new BitmapImage();
        image.BeginInit();
        image.CacheOption = BitmapCacheOption.OnLoad;
        image.StreamSource = stream;
        image.EndInit();
        if (image.PixelWidth != preview.Width || image.PixelHeight != preview.Height)
            throw new InvalidDataException("预览图片尺寸与应答不一致。");
        image.Freeze();
        return image;
    }

    private static string ContextKey(BrowseContext value) => value.Season + "/" + value.Mode;
    private void RememberSelection()
    {
        if (catalog is not null)
            selections[catalog.Season + "/" + catalog.Mode] = (Profile?.Id, Floor?.Id, SelectedRow?.Key, Search);
    }
    private void RevokePreview()
    {
        previewGeneration++;
        pendingPreview = null; previewDecode = null;
        previewCancellation.Cancel();
        previewCancellation.Dispose();
        previewCancellation = new();
    }
    private void RevokeContext()
    {
        contextGeneration++;
        pendingCatalog = null;
        contextCancellation.Cancel();
        contextCancellation.Dispose();
        contextCancellation = new();
        RevokePreview();
    }
    private void ClearCatalog()
    {
        catalog = null;
        restoring = true;
        Profiles = [];
        Rows = null;
        SelectedRow = null;
        Preview = null;
        Changed(nameof(Profiles));
        Changed(nameof(Rows));
        restoring = false;
        PreviewStatus = "选择地图与楼层";
    }
    public void BeginClose()
    {
        if (closing) return;
        closing = true;
        RevokeContext();
        Preview = null;
    }

    public void Detach()
    {
        RevokeContext();
        worker = null;
        needsReload = false;
        ClearCatalog();
        Busy = false;
        Status = "选择本机资源目录，开始浏览地图。";
    }
}
