using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private int _probeMisses;

    private async ValueTask<RuntimeOperationResult> RefreshMap(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            if (!_roundActive || !_mapOpen) return Receipt(request, false, "map-refresh-not-active");
            // A material acknowledgement completes the existing user choice;
            // passive maintenance cannot supersede that pending transaction.
            if (_pendingGroupMaterial is not null || _pendingProjection is not null) return Receipt(request, true, "refresh-coalesced");
        }
        if(DragCycleCurrent)
            return await RetryDesiredGroupChoice(request,epoch,token,await ProbeMapAfterDrag(request,epoch,token).ConfigureAwait(false)).ConfigureAwait(false);
        return await CaptureAndSolve(request, epoch, token).ConfigureAwait(false);
    }

    private RuntimeOperationResult PassiveProbeMiss(SessionRuntimeRequest request, long epoch, CancellationToken token,
        CapturedClientFrame? captured, BoundResources bound, FloorObservationResult? read, object diagnostic)
    {
        lock (_sync)
        {
            RequireCurrent(epoch, token, captured, bound);
            bool? probeOpen = bound.Passports.Mode == "difficult" ? read?.MapOpen
                : read is null ? null : read.MapObservation["detected"]?.GetValue<bool>() == true;
            if (_mapOpen && probeOpen is { } observed) _probeMisses = observed ? 0 : _probeMisses+1;
            _pose = null; RevokeDisplay("map-not-observed");
            if (_mapOpen && probeOpen == false && _probeMisses >= 2)
            { AdvanceOwnedEpoch(); RevokeFrame("map-closed"); }
            _workActive = false; _reason = "map-not-observed";
            _diagnostics = System.Text.Json.JsonSerializer.SerializeToElement(diagnostic, Json);
            Publish(); return Receipt(request, false, _reason);
        }
    }
}
