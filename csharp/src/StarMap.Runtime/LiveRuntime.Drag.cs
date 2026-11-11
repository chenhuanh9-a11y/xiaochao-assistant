using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Core.Display;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed record GameMapDragResult(bool Success,string Reason,bool FirstMotion=false,bool ProbeRequired=false);

public sealed partial class LiveRuntime
{
    MapDragGesture? _activeDrag;
    MapDragCycle? _dragCycle;
    long _presentationRevision;
    SessionRuntimeRequest? _executingRequest;
    SessionRequestContext? _dragProbeWorkOwner;
    sealed record MapDragGesture(GlobalInputPointerEvent Press,GameFloorClickContext Context,string Floor,
        double BaseX,double BaseY,long Sequence,double TotalX=0,double TotalY=0,bool Moved=false,long PreparedSequence=0);
    sealed record MapDragCycle(string Round,string Resource,string Map,long Cycle,double X,double Y,long Gesture,long Sequence,
        SharedProjectionSource Current)
    { public bool ProbePending {get;set;} }

    bool DragCycleCurrent => _dragCycle is { } cycle && !_closed && _mapOpen && _roundActive
        && cycle.Round==_roundId && cycle.Resource==_resources?.Id && cycle.Map==EffectiveMapId && cycle.Cycle==_mapCycle;
    bool PublicationRetired(SessionRuntimeRequest request) => PointerRetired(request) || _activeDrag is not null
        && (PassiveObservation(request.Action) || request.Action is "select_group_member" or "capture_now" or "fit_manual_frame" or "confirm_route" or "recover_map_open" or "map");
    bool ExecutingPublicationRetired => _executingRequest is { } request && PublicationRetired(request);

    bool DragContextCurrent(GlobalInputPointerEvent pointer,MapDragGesture? gesture=null)
    {
        if(pointer.PressContextSnapshot?.DownIntentSnapshot is not GameFloorClickContext supplied
            || supplied!=CurrentPointerContext() || pointer.ContextId!=pointer.PressContextSnapshot.ContextId
            || pointer.ContextId!=pointerSlot.Read()?.ContextId || !_capture.IsObservationContextCurrent(pointerObservation!.Frame)
            || !RenderCurrent())return false;
        return gesture is null || gesture.Context==supplied && gesture.Floor==_floor
            && gesture.Press.Generation==pointer.Generation && gesture.Press.PointerGeneration==pointer.PointerGeneration
            && gesture.Press.GestureId==pointer.GestureId && pointer.Sequence>gesture.Sequence;
    }

    public GameMapDragResult BeginGameMapDrag(GlobalInputPointerEvent press)
    {
        lock(_sync)
        {
            double age=PointerNowSeconds-press.CreatedAtSeconds;
            if(press.Phase!="down" || press.Sequence<=0 || press.GestureId<=0 || !double.IsFinite(age) || age<0 || age>2
                || !DragContextCurrent(press) || _pendingGroupMaterial is not null && !DragCycleCurrent)
                return new(false,"drag-context-unavailable");
            var context=(GameFloorClickContext)press.PressContextSnapshot!.DownIntentSnapshot!;
            if(press.CreatedAtSeconds<context.ObservationCreatedAtSeconds)return new(false,"drag-context-expired");
            _activeDrag=new(press,context,_floor!,DragCycleCurrent?_dragCycle!.X:0,DragCycleCurrent?_dragCycle!.Y:0,press.Sequence);
            return new(true,"drag-started");
        }
    }

    /// <summary>Retire worker publication before Session cancellation can reenter the runtime. No observation changes.</summary>
    public GameMapDragResult PrepareGameMapDragMotion(GlobalInputPointerEvent pointer,MapDragStep step,SessionSnapshot issuedThrough)
    {
        lock(_sync)
        {
            if(!ValidateDragMotion(pointer,step,out var active))return new(false,"drag-context-expired");
            bool first=!active.Moved && (step.DeltaX!=0 || step.DeltaY!=0);
            if(first)
            {
                if(issuedThrough.Closed || string.IsNullOrEmpty(issuedThrough.SessionId) || issuedThrough.LastIssuedSequence<0)
                    return new(false,"drag-session-unavailable");
                pointerRetiredThrough[issuedThrough.SessionId]=Math.Max(pointerRetiredThrough.GetValueOrDefault(issuedThrough.SessionId),issuedThrough.LastIssuedSequence);
                _progressRequest=null;_materialProposal=null;
                DiscardGroupMaterial("map-drag-started");
            }
            _activeDrag=active with {PreparedSequence=pointer.Sequence};
            return new(true,"drag-motion-prepared",first);
        }
    }

    bool ValidateDragMotion(GlobalInputPointerEvent pointer,MapDragStep step,out MapDragGesture active)
    {
        active=_activeDrag!;
        return active is not null && pointer.Phase is "move" or "up" && DragContextCurrent(pointer,active)
            && double.IsFinite(step.DeltaX) && double.IsFinite(step.DeltaY) && double.IsFinite(step.TotalX) && double.IsFinite(step.TotalY)
            && Math.Abs(step.TotalX-active.TotalX-step.DeltaX)<=1e-6 && Math.Abs(step.TotalY-active.TotalY-step.DeltaY)<=1e-6;
    }

    public GameMapDragResult ApplyGameMapDragMotion(GlobalInputPointerEvent pointer,MapDragStep step)
    {
        lock(_sync)
        {
            if(!ValidateDragMotion(pointer,step,out var active) || active.PreparedSequence!=pointer.Sequence)return new(false,"drag-context-expired");
            bool moved=active.Moved || step.DeltaX!=0 || step.DeltaY!=0;
            if(moved)
            {
                _materialProposal=null;
                var provenance=DragCycleCurrent?_dragCycle!.Current:new SharedProjectionSource(EffectiveMapId!,_floor!,new(_mapCycle),true,false);
                _dragCycle=new(_roundId!,_resources!.Id,EffectiveMapId!,_mapCycle,
                    active.BaseX+step.TotalX,active.BaseY+step.TotalY,pointer.GestureId,pointer.Sequence,provenance)
                    {ProbePending=_dragCycle?.ProbePending??false};
                _render=new(_render!,_dragCycle.X,_dragCycle.Y,++_presentationRevision);
            }
            if(pointer.Phase=="up" && DragCycleCurrent)_dragCycle!.ProbePending=moved;
            _activeDrag=pointer.Phase=="up"?null:active with {Sequence=pointer.Sequence,TotalX=step.TotalX,TotalY=step.TotalY,Moved=moved};
            _reason=moved?"map-drag-translated":"map-drag-no-motion";Publish();
            return new(true,_reason,ProbeRequired:pointer.Phase=="up" && moved);
        }
    }

    public void CancelGameMapDrag(long gestureId)
    {lock(_sync){if(_activeDrag?.Press.GestureId==gestureId)_activeDrag=null;}}
    void ClearDragPresentation(){_activeDrag=null;_dragCycle=null;ClearProjectionDisplay("drag-presentation-cleared");}
    void PreserveDragPresentation()
    {if(DragCycleCurrent && _render is not null)_render=new(_render,_dragCycle!.X,_dragCycle.Y,++_presentationRevision);}

    // Capture and floor observation only: the published numeric frame and pose are not this probe's input/output.
    async ValueTask<RuntimeOperationResult> ProbeMapAfterDrag(SessionRuntimeRequest request,long epoch,CancellationToken token)
    {
        RuntimeRenderPacket? owner;MapDragCycle cycle;BoundResources bound;
        lock(_sync)
        {
            RequireCurrent(epoch,token);
            if(_activeDrag is not null)return Receipt(request,true,"drag-probe-coalesced");
            if(!DragCycleCurrent)return Receipt(request,false,"drag-cycle-expired");
            cycle=_dragCycle!;owner=_render;bound=_resources!;
            if(request.Action=="probe_map_after_drag" && (GroupNumber(request.Payload,"gestureId")!=cycle.Gesture
                || GroupNumber(request.Payload,"pointerSequence")!=cycle.Sequence))return Receipt(request,false,"drag-probe-expired");
            _dragProbeWorkOwner=request.Context;
            _workActive=true;Publish();
        }
        try
        {
            var acquisition=await _capture.CaptureAsync(token).ConfigureAwait(false);
            CheckCurrent(epoch,token,null,bound);
            if(acquisition.Frame is not { } captured)throw new InvalidDataException("drag-probe-capture-failed");
            CheckCurrent(epoch,token,captured,bound);
            using var pixels=ToBgr(captured);
            var read=FloorObservation.Observe(pixels,bound.Passports.Season,bound.Passports.Mode,
                _options.FloorSettings is { } settings?JsonNode.Parse(settings.GetRawText())?.AsObject():null,
                _manualAlignmentSession?S1FloorUse.ManualMap:bound.UsesS1StructureFit?S1FloorUse.StructureCandidate:_options.S1FloorUse);
            bool project=false;string? target=null;
            lock(_sync)
            {
                RequireCurrent(epoch,token,captured,bound);
                if(!ReferenceEquals(owner,_render) || !ReferenceEquals(cycle,_dragCycle))return Receipt(request,false,"drag-probe-expired");
                bool? open=read.FloorRead["mapOpen"]?.GetValue<bool>();
                if(open is null && bound.Passports.Season=="s0")open=bound.Passports.Mode=="difficult"?read.MapOpen:read.MapObservation["detected"]?.GetValue<bool>();
                if(open!=true)
                {
                    if(open==false)_probeMisses++;
                    _pose=null;RevokeDisplay("map-not-observed");
                    if(open==false && _probeMisses>=2){AdvanceOwnedEpoch();RevokeFrame("map-closed");}
                    _reason="map-not-observed";Publish();return Receipt(request,false,_reason);
                }
                _probeMisses=0;
                target=ResolveObservedFloor(read);cycle.ProbePending=false;
                bool unknownS1Candidate=bound.Passports.Season=="s1" && !_manualAlignmentSession && !read.AutomaticEligible;
                bool contradiction=read.AutomaticEligible && read.FloorId is { } actual && actual!=_floor;
                project=!unknownS1Candidate && target is not null && target!=_floor && read.AutomaticEligible && read.FloorId==target;
                if(project)
                {
                    RevokeDisplay("transient-floor-observed");_frame=captured;_floor=target;_pose=null;
                    RememberPointerObservation(captured,read);_reason="transient-floor-observed";Publish();
                }
                else if(unknownS1Candidate || contradiction)
                {
                    if(unknownS1Candidate)ClearDragPresentation();
                    _pose=null;RevokeDisplay(unknownS1Candidate?"drag-floor-unavailable":"drag-floor-projection-pending");
                    _reason=unknownS1Candidate?"drag-floor-unavailable":"drag-floor-projection-pending";
                    Publish();return Receipt(request,false,_reason);
                }
                else{_reason="post-drag-observation-current";Publish();return Receipt(request,true,_reason);}
            }
            return TryTransientFloorProjection(request,epoch,token,captured,bound,target!);
        }
        catch(OperationCanceledException){return Receipt(request,false,"cancelled-or-obsolete");}
        catch(Exception error)
        {
            lock(_sync)
            {
                if(!Obsolete(epoch,token,null,bound) && _activeDrag is null && ReferenceEquals(owner,_render) && ReferenceEquals(cycle,_dragCycle))
                {_pose=null;RevokeDisplay("drag-probe-failed");_reason="drag-probe-failed";Publish();}
                return Receipt(request,false,"drag-probe-failed:"+error.GetType().Name);
            }
        }
    }
}
