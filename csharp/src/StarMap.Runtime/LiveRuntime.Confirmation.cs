using System.Text.Json;
using StarMap.Core.Session;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private async ValueTask<RuntimeOperationResult> ConfirmMap(SessionRuntimeRequest request,long epoch,CancellationToken token)
    {
        bool recapture;
        lock(_sync)
        {
            RequireCurrent(epoch,token);
            if(!_roundActive || _resources is null)return Receipt(request,false,"start-round-before-confirming-map");
            bool explicitMap=request.Payload.TryGetProperty("mapId",out var supplied) && supplied.ValueKind!=JsonValueKind.Null;
            if(explicitMap && supplied.ValueKind!=JsonValueKind.String)return Receipt(request,false,"invalid-manual-map");
            string map=explicitMap?supplied.GetString()!:(_selected>=0 && _selected<_candidates.Length?_candidates[_selected].MapId:"");
            if(!_resources.Passports.MapProfiles.ContainsKey(map))return Receipt(request,false,"invalid-manual-map");
            if(_structurePool is { } pool && !pool.Remaining.Contains(map))return Receipt(request,false,"map-excluded-this-round");
            AdvanceOwnedEpoch();epoch=_epoch;
            LockIdentity(map,"manual");
            // Publish the human choice before a possibly blocked acquisition.
            // A floor seed is view intent; only the new observation may fit it.
            if(explicitMap && _floor is null && !_manualAlignmentSession)_floor=_resources.Passports.DefaultEntryFloor;
            _mapRecovery=null;_structureSession?.RevokeCurrent();_structureTicket=null;
            RevokeDisplay("identity-confirmed-pose-pending");_pose=null;
            // An open cycle was established by a real observation, even if
            // its current floor is unknown or a previous capture failed.
            recapture=_manualAlignmentSession || _mapOpen;
            if(recapture)_frame=null;
            _workActive=recapture;_reason="identity-locked-pose-pending";Publish();
        }
        if(!recapture)return Receipt(request,true,"identity-locked-pose-pending");
        return await CaptureAndSolve(request,epoch,token).ConfigureAwait(false);
    }

    private RuntimeOperationResult ConfirmationProbeMiss(SessionRuntimeRequest request,long epoch,CancellationToken token,
        CapturedClientFrame? captured,BoundResources bound,object diagnostic)
    {
        lock(_sync)
        {
            RequireCurrent(epoch,token,captured,bound);
            if(captured is not null){AdvanceOwnedEpoch();RevokeFrame("map-not-observed");}
            else{_pose=null;RevokeDisplay("map-not-observed");}
            _workActive=false;_reason="map-not-observed";
            _diagnostics=JsonSerializer.SerializeToElement(diagnostic,Json);Publish();return Receipt(request,false,_reason);
        }
    }
}
