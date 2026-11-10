using System.Text.Json;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private bool S1FloorObserverAvailable => _resources?.StructureCatalog is not null && _selection.Season=="s1"
        && _resources.Passports.Floors.ToHashSet().SetEquals(["-1","-2","-3","-4"]);
    private bool S1ManualIdentity => _identitySource=="manual" && _mapId is not null;
    private static bool PassiveObservation(string action) => action is "refresh_map" or "observe_manual_floor" or "observe_candidate_floor" or "probe_map_after_drag";

    private async ValueTask<RuntimeOperationResult> ObserveS1Floor(SessionRuntimeRequest request,long epoch,CancellationToken token,bool manual)
    {
        if(DragCycleCurrent)return await ProbeMapAfterDrag(request,epoch,token).ConfigureAwait(false);
        BoundResources bound;bool recover;
        lock(_sync)
        {
            RequireCurrent(epoch,token);
            recover=manual && (!_mapOpen || Text(request.Payload,"recoveryId") is not null);
            if(!_roundActive || !S1FloorObserverAvailable || manual!=_manualAlignmentSession || manual && !S1ManualIdentity || !_capture.IsBound
                || !manual && !_mapOpen || recover && _mapRecovery is null)
                return Receipt(request,false,manual?"manual-floor-observation-not-active":"candidate-probe-not-current");
            bound=_resources!;
            _workActive=true;Publish();
        }
        if(recover)return await RecoverMapOpen(request,epoch,token).ConfigureAwait(false);
        if(manual)return await CaptureAndSolve(request,epoch,token).ConfigureAwait(false);
        try
        {
            // A candidate floor probe is not a new pose observation. Keep its
            // published frame, exact packet and candidate list while capturing.
            var acquisition=await _capture.CaptureAsync(token).ConfigureAwait(false);
            CheckCurrent(epoch,token,null,bound);
            if(acquisition.Frame is not { } captured)return FailCurrent(request,epoch,"candidate-floor-probe-failed");
            CheckCurrent(epoch,token,captured,bound);
            using var pixels=ToBgr(captured);
            var read=FloorObservation.Observe(pixels,"s1",bound.Passports.Mode,s1Use:S1FloorUse.StructureCandidate);
            lock(_sync)
            {
                RequireCurrent(epoch,token,captured,bound);
                // Candidate facade normalizes raw unknown to false for legacy
                // consumers. Only the detector's explicit negative closes G.
                if(read.FloorRead["mapOpen"]?.GetValue<bool>()==false)return CloseS1Observation(request,"candidate-map-closed");
                if(!read.AutomaticEligible || read.FloorId is not { } floor)
                {
                    _pose=null;_observedFloor=null;RevokeDisplay("candidate-floor-unavailable");
                    _reason="candidate-floor-unavailable";Publish();return Receipt(request,false,_reason);
                }
                if(floor==_floor && RenderCurrent())
                { _reason="candidate-floor-current";Publish();return Receipt(request,true,_reason); }
                _pose=null;RevokeDisplay(floor==_floor?"candidate-pose-unavailable":"candidate-floor-changed");
            }
            // Original _probe_s1_candidate_floor starts a separate fresh
            // capture_now after a layer change or a withdrawn candidate pose.
            return await CaptureAndSolve(request,epoch,token).ConfigureAwait(false);
        }
        catch(OperationCanceledException){return Receipt(request,false,"cancelled-or-obsolete");}
        catch(Exception error)
        { return FailCurrent(request,epoch,"candidate-floor-probe-failed",new {error=error.GetType().Name}); }
    }

    // Called only under the runtime lock after current capture validation.
    private RuntimeOperationResult CloseS1Observation(SessionRuntimeRequest request,string reason)
    {
        AdvanceOwnedEpoch();RevokeFrame("map-closed");_floor=_observedFloor=null;_reason=reason;
        Publish();return Receipt(request,false,reason);
    }

    private RuntimeOperationResult S1ManualObservationMiss(SessionRuntimeRequest request,long epoch,CancellationToken token,
        CapturedClientFrame? captured,BoundResources bound,FloorObservationResult? read,object diagnostic)
    {
        lock(_sync)
        {
            RequireCurrent(epoch,token,captured,bound);
            if(read?.MapOpen==false)return CloseS1Observation(request,"s1-map-closed");
            _frame=captured;_floor=_observedFloor=null;_pose=null;
            if(captured is not null && read is not null)RememberPointerObservation(captured,read);
            ResetSteady();_geometryGroup=null;RevokeDisplay("s1-floor-unknown");
            _structureSession?.RevokeCurrent();_structureTicket=null;
            _reason="s1-current-floor-unavailable";_diagnostics=JsonSerializer.SerializeToElement(diagnostic,Json);
            Publish();return Receipt(request,false,_reason);
        }
    }
}
