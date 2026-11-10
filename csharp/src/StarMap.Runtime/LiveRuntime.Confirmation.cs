using System.Text.Json;
using StarMap.Core.Session;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private async ValueTask<RuntimeOperationResult> ConfirmMap(SessionRuntimeRequest request,long epoch,CancellationToken token)
    {
        bool recapture;
        ConfirmationLabelOwner labelOwner;
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
            labelOwner=new(epoch,_resources,_selection,_roundId!,map,_floor,_capture.EvidenceKind);
            _manualLearning=null;
            if(recapture)_frame=null;
            _workActive=recapture;_reason="identity-locked-pose-pending";Publish();
        }
        RuntimeOperationResult result;
        try{result=recapture?await CaptureAndSolve(request,epoch,token).ConfigureAwait(false):Receipt(request,true,"identity-locked-pose-pending");}
        catch(OperationCanceledException){result=Receipt(request,false,"cancelled-or-obsolete");}
        var learning=PersistConfirmationLabel(labelOwner,token);
        lock(_sync)
        {
            if(_epoch==epoch && _roundId==labelOwner.Round && _mapId==labelOwner.Map)
            {_manualLearning=learning;Publish();result=result with {Snapshot=ReadSnapshot()};}
        }
        if(learning.Status=="error")result=result with {Receipt=result.Receipt with {
            Message=result.Receipt.Message+"；"+learning.ErrorMessage()}};
        return result;
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
