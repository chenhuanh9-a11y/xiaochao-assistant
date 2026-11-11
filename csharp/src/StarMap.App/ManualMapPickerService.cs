using System.Windows;
using System.Windows.Threading;
using StarMap.Core.Session;
using StarMap.Runtime;

namespace StarMap.App;

internal sealed record ManualMapPickerAvailability(ManualMapCatalogContext? Context, bool CanPick, string Message);

/// <summary>UI owner of a read-only catalog and one pending selection intent.
/// Catalog work has one active job and at most the latest open request; all
/// identity and geometry writes remain in the existing Session/Runtime.</summary>
internal sealed class ManualMapPickerService
{
    readonly Func<ManualMapPickerAvailability> readAvailability;
    readonly Func<CancellationToken, Task<RuntimeManualMapCatalog?>> loadCatalog;
    readonly Func<string, SessionDispatchResult?> commit;
    readonly Action scopeChanged;
    readonly Action<string> status;
    readonly Action<Window>? place;
    readonly Func<ManualMapPickerWindow> createWindow;
    readonly DispatcherTimer timer;
    ManualMapPickerWindow? window;
    ManualMapPickerState? state;
    RuntimeManualMapCatalog? catalog;
    ManualMapCatalogContext? openedContext;
    CancellationTokenSource? cancellation;
    Task catalogTask = Task.CompletedTask;
    long generation;
    bool closed, loading;
    string message = "";
    (long Generation, ManualMapPickerState? State, int Page, string? Stage, string? Group, bool Loading,
        bool CanPick, string AvailabilityMessage, string Message)? rendered;

    internal ManualMapPickerService(Dispatcher dispatcher, Func<ManualMapPickerAvailability> readAvailability,
        Func<CancellationToken, Task<RuntimeManualMapCatalog?>> loadCatalog, Func<string, SessionDispatchResult?> commit,
        Action scopeChanged, Action<string> status, Action<Window>? place = null, Func<ManualMapPickerWindow>? createWindow = null)
    {
        this.readAvailability = readAvailability; this.loadCatalog = loadCatalog; this.commit = commit;
        this.scopeChanged = scopeChanged; this.status = status; this.place = place;
        this.createWindow = createWindow ?? (() => new());
        timer = new(TimeSpan.FromMilliseconds(125), DispatcherPriority.Background, (_, _) => Synchronize(), dispatcher);
        timer.Stop();
    }

    internal bool Visible { get; private set; }
    internal ManualMapPickerWindow? Window => window;
    internal ManualMapPickerState? State => state;
    internal string? LastError { get; private set; }
    internal IReadOnlyList<string> Keys => Visible ? ["F1", "F2", "F3", "F4", "PageUp", "PageDown", "Backspace", "Escape"] : [];

    internal bool Toggle() { if (Visible) { Hide(); return true; } return Open(); }
    internal bool Open()
    {
        if (closed) return false;
        var availability = readAvailability();
        if (!availability.CanPick || availability.Context is null) { status(availability.Message); return false; }
        generation++; cancellation?.Cancel(); openedContext = availability.Context;
        state = null; catalog = null; LastError = null; message = "正在读取地图资源…"; Visible = true;
        if (window is null)
        {
            window = createWindow();
            window.OptionRequested += Choose;
            window.NavigationRequested += HandleKey;
            window.CloseRequested += Hide;
        }
        RequestCatalog(); Render(); window.Show(); place?.Invoke(window); timer.Start(); scopeChanged();
        return true;
    }

    internal void Hide()
    {
        generation++; cancellation?.Cancel(); Visible = false; state = null; catalog = null; openedContext = null;
        timer.Stop(); window?.Hide(); scopeChanged();
    }

    void RequestCatalog()
    {
        if (loading || !Visible || closed) return;
        loading = true; var tokenSource = new CancellationTokenSource(); cancellation = tokenSource;
        catalogTask = Load(generation, openedContext!, tokenSource);
    }

    async Task Load(long request, ManualMapCatalogContext context, CancellationTokenSource tokenSource)
    {
        try
        {
            var result = await loadCatalog(tokenSource.Token);
            if (!Current(request, context)) return;
            if (result is null || result.Context != context) { Hide(); return; }
            catalog = result;
            state = new(result.Maps.Select(row => new ManualMapPickerEntry(row.MapId, row.Name, row.Direction, row.ThumbnailPath, row.ThumbnailStamp)).ToArray(), context.Mode);
            message = result.Maps.Count == 0 ? "当前模式没有可选地图，请检查地图资源。" : "";
        }
        catch (OperationCanceledException) when (tokenSource.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (Current(request, context)) { LastError = error.ToString(); message = "地图资源读取失败，请检查资源后重新打开选图。"; }
        }
        finally
        {
            if (ReferenceEquals(cancellation, tokenSource)) cancellation = null;
            tokenSource.Dispose(); loading = false;
            if (Visible && !closed)
            {
                if (request != generation) RequestCatalog();
                else Render(reposition: true);
            }
        }
    }

    bool Current(long request, ManualMapCatalogContext context) => !closed && Visible && request == generation
        && openedContext == context && readAvailability().Context == context;

    internal void Synchronize()
    {
        if (!Visible || closed) return;
        if (readAvailability().Context != openedContext) { Hide(); return; }
        Render();
    }

    internal void HandleKey(string key)
    {
        if (!Visible || closed) return;
        Synchronize(); if (!Visible) return;
        if (key == "Escape") { Hide(); return; }
        if (loading || !readAvailability().CanPick || state is null) return;
        if (key is "F1" or "F2" or "F3" or "F4") Choose(key[1] - '1');
        else if (key == "PageUp") { if (state.Turn(-1)) Render(reposition: true); }
        else if (key == "PageDown") { if (state.Turn(1)) Render(reposition: true); }
        else if (key == "Backspace") { if (state.Back()) Render(reposition: true); }
    }

    void Choose(int index)
    {
        if (!Visible || closed) return;
        Synchronize();
        if (!Visible || loading || state is null || catalog is null || !readAvailability().CanPick) return;
        if ((uint)index >= (uint)state.Options.Count) return;
        var chosen = state.Choose(index);
        if (chosen is null) { Render(reposition: true); return; }
        var result = commit(chosen.MapId);
        if (result?.Disposition is SessionDispatchDisposition.Enqueued or SessionDispatchDisposition.Coalesced) { Hide(); return; }
        message = "未提交地图选择，请等待当前操作完成，或选择本局剩余的候选。"; Render();
    }

    void Render(bool reposition = false)
    {
        if (!Visible || window is null) return;
        var availability = readAvailability();
        if (availability.Context != openedContext) { Hide(); return; }
        var key = (generation, state, state?.Page ?? 0, state?.Stage, state?.GroupLabel, loading,
            availability.CanPick, availability.Message, message);
        if (rendered == key)
        {
            if (reposition && window.IsVisible) place?.Invoke(window);
            return;
        }
        rendered = key;
        string mode = openedContext?.Mode switch { "nightmare" => "噩梦", "difficult" => "困难", _ => "" };
        bool busy = loading || !availability.CanPick;
        string description = !availability.CanPick ? availability.Message : message.Length > 0 ? message
            : state?.Stage == "groups" ? "先选路线组合，再选本局地图。"
            : state?.SingleGroup == true ? "选择本局地图，选好后按这张图贴合和显示。"
            : $"已选 {state?.GroupLabel} · 选择本局地图。";
        string help = "点击或按 F1 / F2 / F3 / F4 选择" + (state?.PageCount > 1 ? "，PgUp/PgDn 翻页" : "")
            + (state is { Stage: "maps", SingleGroup: false } ? "，Backspace 返回" : "") + "，Esc 关闭";
        window.Render("手动选图 · " + mode, description, help, state is { PageCount: > 1 } ? $"{state.Page + 1} / {state.PageCount}" : "",
            state?.Options ?? [], busy, navigation: state is { } current
                ? (current.Stage == "maps" && !current.SingleGroup, current.Page > 0, current.Page < current.PageCount - 1) : default);
        if (reposition && window.IsVisible) place?.Invoke(window);
    }

    internal async Task DrainAsync() { while (loading) await catalogTask; }
    internal async Task CloseAsync()
    {
        if (closed) return;
        closed = true; Hide(); await DrainAsync();
        if (window is { } owned) { owned.Dispose(); await owned.DrainImagesAsync(); }
        window = null;
    }
}
