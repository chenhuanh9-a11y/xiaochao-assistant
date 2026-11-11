using System.Windows;
using StarMap.Core.Session;
using StarMap.Runtime;

namespace StarMap.App;

internal sealed partial class RuntimePage
{
    readonly ManualMapPickerService manualPicker;
    internal Action? ManualPickerScopeChanged { get; set; }
    internal Action<Window>? PlaceManualPicker { get; set; }
    internal IReadOnlyList<string> ManualPickerKeys => manualPicker.Keys;
    internal bool ToggleManualPicker() => manualPicker.Toggle();
    internal void HandleManualPickerKey(string key) => manualPicker.HandleKey(key);

    ManualMapPickerAvailability ReadManualPickerAvailability() => ReadManualMapAvailability(floatingPicker: true);

    ManualMapPickerAvailability ReadManualMapAvailability(bool floatingPicker)
    {
        var state = runtime?.ReadState(); var view = session?.Read();
        if (closed || configuring || loadingResources || state is not { ResourcesBound: true, RoundActive: true }
            || view is null || view.ResourceLocked || view.ContextPending || state.Context.ResourceId is null)
            return new(null, false, "请先开始本局并等待地图资源就绪，再手动选图。");
        var context = new ManualMapCatalogContext(state.Context.Season, state.Context.Mode, state.Context.ProfileId,
            state.Context.RoundId, state.Context.ResourceId);
        if (floatingPicker && state.Context.Season == "s1" && !state.ManualAlignmentSession)
            return new(context, false, "S1 手动选图请先结束本局，再选择“开始手动贴合”。");
        bool available = pendingCapture is null && !view.Busy && !view.WorkerActive && view.QueuedCount == 0
            && view.Runtime.ManualMapPickAvailable;
        return new(context, available, available ? "" : "请等待当前操作完成后再选择地图。");
    }

    Task<RuntimeManualMapCatalog?> LoadManualPickerCatalog(CancellationToken token)
    {
        var owner = runtime;
        return owner is null ? Task.FromResult<RuntimeManualMapCatalog?>(null)
            : Task.Run(() => owner.ReadManualMapCatalog(token), token);
    }

    SessionDispatchResult? CommitManualMap(string mapId)
    {
        var availability = ReadManualMapAvailability(floatingPicker: false);
        if (!availability.CanPick) { status(availability.Message); return null; }
        var state = runtime!.ReadState();
        if (state.RecognitionPath == "structure-index" && state.RemainingMapIds.Count > 0 && !state.RemainingMapIds.Contains(mapId))
        { status("这张地图已在本局排除，请选择剩余候选。"); return null; }
        return state.ManualAlignmentSession ? Dispatch("confirm_route", new { mapId, inputObservationSequence = mapInput.Sequence })
            : Dispatch("confirm_route", new { mapId });
    }
}
