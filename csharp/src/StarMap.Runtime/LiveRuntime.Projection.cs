using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private sealed record ProjectionDisplayGrant(AlignmentCoreV2 Core,RuntimeRenderPacket Render,
        SharedStructureData NativeReference,DisplayAffine NativeToClient,CapturedClientFrame Frame,
        BoundResources Resources,GeometryGroup Group,string Round,long Cycle,long Generation,long Epoch,
        SessionSelection Selection,SharedProjectionSource Provenance,RuntimeDisplayState Display);
    private sealed record PendingProjectionMaterial(ProjectionDisplayGrant Grant,MapDragCycle Cycle,
        SharedProjectionSource Source,AlignmentCoreV2 TrustedCore,long Intent,CancellationToken Cancellation)
    { public CancellationTokenRegistration Registration {get;set;} }
    private ProjectionDisplayGrant? _projection;
    private PendingProjectionMaterial? _pendingProjection;
    private sealed record ProjectionPreferenceSource(ProjectionDisplayGrant Grant,RuntimeRenderPacket Render,MapDragCycle Cycle);

    private ProjectionPreferenceSource? ProjectionPreferenceCurrentSource()
    {
        // Saved author changes may hide the old token while its accepted geometric binding stays valid.
        // Only this display-preparation path ignores author preference; public reads remain selection-bound.
        if(_projection is not { } grant || _render is not { } render || !DragCycleCurrent
            || _dragCycle!.Current!=grant.Provenance || !ProjectionBindingCurrent(grant)
            || grant.Selection.Season!=_selection.Season || grant.Selection.Mode!=_selection.Mode
            || render.Epoch!=_epoch || render.Stamp!=grant.Frame.Stamp
            || !ReferenceEquals(render.Authorization,grant.Render.Authorization))return null;
        return new(grant,render,_dragCycle);
    }

    private bool ProjectionGrantCurrent(ProjectionDisplayGrant grant) => grant.Selection==_selection && ProjectionBindingCurrent(grant);
    private bool ProjectionBindingCurrent(ProjectionDisplayGrant grant) => !_closed && _mapOpen && _frame is not null
        && ReferenceEquals(_frame,grant.Frame) && ReferenceEquals(_resources,grant.Resources) && ReferenceEquals(_geometryGroup,grant.Group)
        && grant.Round==_roundId && grant.Cycle==_mapCycle && grant.Generation==_frameGeneration && grant.Epoch==_epoch
        && grant.Render.MapId==_mapId && grant.Render.Authorization.Floor==_floor
        && _capture.IsObservationContextCurrent(grant.Frame) && grant.Core.ActiveObservation is { } observation
        && grant.Render.Authorization.Current(observation.ObservationId,new(_roundId!),_mapId!,_floor!,_frameGeneration);

    private bool PendingProjectionCurrent(PendingProjectionMaterial pending) => !pending.Cancellation.IsCancellationRequested
        && pending.Intent==_groupIntent && ReferenceEquals(_alignment,pending.TrustedCore)
        && ReferenceEquals(_dragCycle,pending.Cycle) && pending.Cycle.Current==pending.Source
        && DragCycleCurrent && !pending.Cycle.ProbePending && ProjectionGrantCurrent(pending.Grant);

    private void DiscardProjectionMaterial(string reason)
    {
        var pending=_pendingProjection;_pendingProjection=null;
        pending?.Registration.Unregister();pending?.Grant.Core.Close(reason:reason);
    }

    private void ClearProjectionDisplay(string reason)
    { DiscardProjectionMaterial(reason);_projection?.Core.Close(reason:reason);_projection=null; }

    private bool AcceptProjectionMaterial(RuntimeRenderPacket packet)
    {
        var pending=_pendingProjection;
        if(pending is null || !ReferenceEquals(pending.Grant.Render,packet) || _activeDrag is not null || _workActive
            || !PendingProjectionCurrent(pending))return false;
        pending.Registration.Unregister();_pendingProjection=null;
        _projection?.Core.Close(reason:"projection-replaced");_projection=pending.Grant;
        _dragCycle=_dragCycle! with { Current=pending.Grant.Provenance };
        _render=packet;_canonical=null;_pose=null;_materialProposal=null;
        _display=pending.Grant.Display;
        _reason="shared-root-provisional";Publish();return true;
    }

    // Observation is already current here. Failure never rolls it back or starts a fit.
    private RuntimeOperationResult TryTransientFloorProjection(SessionRuntimeRequest request,long epoch,CancellationToken token,
        CapturedClientFrame captured,BoundResources bound,string floor)
    {
        MapDragCycle cycle;GeometryGroup group;AlignmentCoreV2 trusted;RoundPose pose;long generation,intent;
        SessionSelection selection;
        lock(_sync)
        {
            RequireCurrent(epoch,token,captured,bound);
            if(!DragCycleCurrent || _activeDrag is not null || _pendingGroupMaterial is not null || _pendingProjection is not null
                || _dragCycle!.ProbePending || _floor!=floor || !ReferenceEquals(_frame,captured)
                || _mapId is null || !GeometryGroupCurrent(_geometryGroup,captured,bound,_mapId)
                || _alignment is null || !_alignment.PoseByMap.TryGetValue(_mapId,out var sourcePose) || !sourcePose.Reliable
                || _dragCycle.Current.Floor==floor && !_dragCycle.Current.Provisional)
                return ProjectionUnavailable(request,"transient-projection-unavailable");
            cycle=_dragCycle;group=_geometryGroup!;trusted=_alignment;pose=sourcePose;
            generation=_frameGeneration;intent=_groupIntent;selection=_selection;
        }
        AlignmentCoreV2? displayCore=null;
        try
        {
            string source=SharedProjectionPolicy.SourceFloor(group.Model,floor,cycle.Current);
            var seed=bound.Coordinates.CrossFloorSearchSeed(group.Model.MapId,source,floor,Matrix3.Identity,group.Model.RouteProfileId);
            if(seed is null || group.Model.CanonicalPoseForFloor(floor) is not { } canonical)
                return Unavailable("projection-relation-unavailable");
            var entry=bound.Catalog.Find(bound.Passports.Mode,bound.Passports.Season,bound.Passports.MapProfiles[group.Model.MapId],group.Model.MapId,floor);
            var reference=entry is null?null:bound.LoadReference(entry).Value;
            if(entry is null || reference is null)return Unavailable("projection-target-resource-unavailable");
            var basis=new DisplayBridgeResolver(new OpenCvDisplayRasterValidator()).ResolveDisplayReference(bound.Catalog,entry,
                "shared-structure",DisplayAffine.Identity,neutral:true);
            if(!basis.Accepted || basis.ReferenceToCanonical is not { } toCanonical || basis.CanonicalPackId!=bound.Coordinates.PackId)
                return Unavailable("projection-target-basis-unavailable");
            var native=Matrix3.Multiply(canonical,new(toCanonical.ToArray()));
            if(native is null)return Unavailable("projection-target-basis-unavailable");
            var prepared=PrepareDisplay(bound,entry,native.ToArray(),reference,selection.ProfileId,token);
            var decision=SharedProjectionPolicy.Evaluate(group.Model,group.Model.MapId,floor,new(_mapCycle),
                prepared.Selection.EffectiveProfileId,cycle.Current,seed);
            if(!decision.Accepted)return Unavailable(decision.Reason);
            if(prepared.Material.Layer is null)return Unavailable(prepared.Material.Reason);
            prepared=prepared with { Scene=PrepareMapScene(bound,entry,prepared,token) };
            displayCore=new(floors:bound.Passports.Floors);
            displayCore.SetInitialPose(pose,"projection-display-source");SeedGroupCore(displayCore,group);
            displayCore.BeginObservation(new(_roundId!),group.Model.MapId,floor,generation,[captured.Stamp.FrameId,bound.Id],"transient-floor-projection");
            var authorization=displayCore.AuthorizeSharedProjection();
            if(authorization is null)return Unavailable("projection-core-unavailable");
            lock(_sync)
            {
                RequireOwner();
                var packet=new RuntimeRenderPacket(prepared.Material.Layer,captured,authorization,generation,epoch,remembersGeometry:false,
                    scene:ProjectMapScene(prepared,floor));
                packet=new(packet,cycle.X,cycle.Y,++_presentationRevision);
                var provenance=new SharedProjectionSource(group.Model.MapId,floor,new(_mapCycle),false,true,decision.SourceFloor);
                var display=new RuntimeDisplayState(true,false,packet.IsLive,"shared-root-provisional",selection.ProfileId,
                    prepared.Selection.EffectiveProfileId,prepared.Selection.DisplayKind,prepared.Scene?.Floors.Count??0,
                    prepared.Scene is null?"common-space-source-unavailable":prepared.Scene.Unavailable.Count==0?"common-scene-ready":"common-scene-partial");
                var grant=new ProjectionDisplayGrant(displayCore,packet,prepared.NativeReference,prepared.NativeToClient,
                    captured,bound,group,_roundId!,_mapCycle,generation,epoch,selection,provenance,display);
                var pending=new PendingProjectionMaterial(grant,cycle,cycle.Current,trusted,intent,token);
                _pendingProjection=pending;
                pending.Registration=token.Register(()=>
                {
                    lock(_sync)
                    {
                        if(!ReferenceEquals(_pendingProjection,pending))return;
                        DiscardProjectionMaterial("projection-cancelled");_reason="transient-projection-unavailable";Publish();
                    }
                });
                if(!ReferenceEquals(_pendingProjection,pending) || token.IsCancellationRequested)
                {
                    if(ReferenceEquals(_pendingProjection,pending))DiscardProjectionMaterial("projection-cancelled");
                    else pending.Registration.Unregister();
                    throw new OperationCanceledException("projection-cancelled",token);
                }
                displayCore=null;_reason="shared-root-projection-awaiting-material";Publish();return Receipt(request,true,_reason);
            }
        }
        finally{displayCore?.Close(reason:"projection-not-committed");}
        RuntimeOperationResult Unavailable(string reason)
        { lock(_sync){RequireOwner();return ProjectionUnavailable(request,reason);} }
        void RequireOwner()
        {
            RequireCurrent(epoch,token,captured,bound);
            if(!ReferenceEquals(_frame,captured) || _frameGeneration!=generation || !ReferenceEquals(_dragCycle,cycle)
                || !ReferenceEquals(_geometryGroup,group) || !ReferenceEquals(_alignment,trusted) || _selection!=selection || _groupIntent!=intent)
                throw new OperationCanceledException("projection-context-expired",token);
        }
    }

    private RuntimeOperationResult ProjectionUnavailable(SessionRuntimeRequest request,string reason)
    { _reason=reason;Publish();return Receipt(request,false,reason); }
}
