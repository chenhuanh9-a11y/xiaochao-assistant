using System.Text.Json;
using StarMap.Core.Identity;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed record RuntimeGroupMember(string MapId, string Name);

public sealed record RuntimeGroupChoice(long Generation, long? CycleId, string? DesiredMapId,
    string? HeldMapId, string? PendingMapId, string Status, string Reason,
    IReadOnlyList<RuntimeGroupMember> Members);

public sealed partial class LiveRuntime
{
    private long _mapCycle, _groupIntent;
    private string? _heldGroupMember;
    private string _groupChoiceStatus = "", _groupChoiceReason = "";
    private GroupChoiceIntent? _desiredGroupChoice;
    private PendingGroupMaterial? _pendingGroupMaterial;
    private SessionRequestContext? _groupChoiceWorkOwner;

    private sealed record GroupChoiceIntent(string Round, string Resource, string Group, string[] Members, string Map);
    private sealed record PendingGroupMaterial(long Intent, long Epoch, long FrameGeneration,
        GroupMemberChoicePlan Plan, CapturedClientFrame Frame, BoundResources Resources,
        NativeGeometryPose? Pose, CanonicalDisplayCommit Commit, CancellationToken Cancellation,
        CapturedClientFrame SourceFrame,RuntimeRenderPacket? SourceRender,MapDragCycle? SourceDragCycle,
        FloorObservationResult? StagedObservation,SessionRequestContext OwnerContext,bool ReusesSourceGeometry=false)
    {
        public CancellationTokenRegistration CancellationRegistration {get;set;}
    }

    private bool GroupChoiceOwnsCancellation(SessionRequestContext context) => _groupChoiceWorkOwner==context
        || _pendingGroupMaterial?.OwnerContext==context;

    private bool HasRegisteredGroup => _nativeIdentity?.RegisteredGroup == true
        && _identitySource is "group-default" or "group-manual";

    private GroupMemberChoiceState GroupChoiceState(bool ignorePending = false) => new(
        _selection.Mode, HasRegisteredGroup ? _nativeIdentity!.GroupId : null,
        _nativeIdentity?.Snapshot()["generation"]?.GetValue<int>() ?? 0,
        _mapCycle == 0 ? null : _mapCycle, _floor,
        HasRegisteredGroup ? _nativeIdentity!.MemberIds : Array.Empty<string>(),
        currentGroupMemberId: _mapId, manualHold: _heldGroupMember is not null,
        manualMemberId: _heldGroupMember, roundActive: _roundActive,
        identityPhase: HasRegisteredGroup ? _heldGroupMember is null ? "GROUP_LOCKED" : "MANUAL_MEMBER_CONFIRMED" : "UNLOCKED",
        selectionPending: !ignorePending && (_pendingGroupMaterial is not null || _pendingProjection is not null));

    private RuntimeGroupChoice ReadGroupChoice() => new(
        _nativeIdentity?.Snapshot()["generation"]?.GetValue<int>() ?? 0,
        _mapCycle == 0 ? null : _mapCycle, _desiredGroupChoice?.Map, _heldGroupMember,
        _pendingGroupMaterial?.Plan.MapId, _groupChoiceStatus, _groupChoiceReason,
        Array.AsReadOnly(HasRegisteredGroup ? _nativeIdentity!.MemberIds.Select(map =>
            new RuntimeGroupMember(map, _resources?.Passports.Maps.FirstOrDefault(row => row.MapId == map)?.Name ?? map)).ToArray() : []));

    private bool DesiredGroupCurrent() => HasRegisteredGroup && _desiredGroupChoice is { } desired
        && desired.Round == _roundId && desired.Resource == _resources?.Id && desired.Group == _nativeIdentity!.GroupId
        && desired.Members.ToHashSet(StringComparer.Ordinal).SetEquals(_nativeIdentity.MemberIds);

    private void DiscardGroupMaterial(string reason)
    {
        _groupIntent++;
        // Both pending material variants belong to this shared display intent.
        DiscardProjectionMaterial(reason);
        var pending=_pendingGroupMaterial;_pendingGroupMaterial=null;
        // Unregister never waits for a callback which may itself need _sync.
        pending?.CancellationRegistration.Unregister();
        if (pending is not null && !ReferenceEquals(pending.Commit.Alignment, _alignment))
            pending.Commit.Alignment?.Close(reason: reason);
        if (_desiredGroupChoice is not null && _groupChoiceStatus != "confirmed")
        { _groupChoiceStatus = "awaiting-geometry"; _groupChoiceReason = reason; }
    }

    private void ClearGroupChoice(bool resetCycle = true)
    {
        DiscardGroupMaterial("group-choice-reset");
        _desiredGroupChoice = null; _heldGroupMember = null;
        _groupChoiceStatus = _groupChoiceReason = "";
        if (resetCycle) {_mapCycle=0;ClearProjectionDisplay("group-choice-reset");}
    }

    // This read never changes the current render or grants live evidence.
    public RuntimeRenderPacket? ReadPendingGroupMaterial()
    {
        lock (_sync) return _pendingProjection is { } projection && PendingProjectionCurrent(projection) ? projection.Grant.Render
            :PendingGroupCurrent(_pendingGroupMaterial) ? _pendingGroupMaterial!.Commit.Render : null;
    }

    private bool PendingGroupCurrent(PendingGroupMaterial? pending) => pending is not null && !_closed
        && pending.Intent == _groupIntent && pending.Epoch == _epoch && pending.FrameGeneration == _frameGeneration
        && !pending.Cancellation.IsCancellationRequested && _mapOpen && ReferenceEquals(pending.SourceFrame, _frame)
        && ReferenceEquals(pending.SourceRender,_render) && ReferenceEquals(pending.SourceDragCycle,_dragCycle)
        && _capture.IsObservationContextCurrent(pending.SourceFrame)
        && ReferenceEquals(pending.Resources, _resources)
        && (pending.ReusesSourceGeometry ? RenderCurrent() : _capture.IsCurrent(pending.Frame.Stamp))
        && DesiredGroupCurrent() && GroupMemberChoicePlanner.IsCurrent(pending.Plan, GroupChoiceState(ignorePending: true));

    /// <summary>ACK for consumer material readiness, never a native-window or GPU-present ACK.</summary>
    public bool AcceptGroupMaterialReady(RuntimeRenderPacket packet)
    {
        ArgumentNullException.ThrowIfNull(packet);
        lock (_sync)
        {
            if(ReferenceEquals(_pendingProjection?.Grant.Render,packet))return AcceptProjectionMaterial(packet);
            var pending = _pendingGroupMaterial;
            if (_activeDrag is not null || _workActive || !PendingGroupCurrent(pending) || !ReferenceEquals(packet, pending!.Commit.Render)
                || !_nativeIdentity!.TrySelectRegisteredMember(pending.Plan.MapId,out var selectedIdentity)) return false;
            pending.CancellationRegistration.Unregister();
            if(!pending.ReusesSourceGeometry)
            {
                _alignment?.Close(reason: "group-member-material-ready");
                ResetSteady();
            }
            _nativeIdentity=selectedIdentity;
            _mapId = pending.Plan.MapId; _identitySource = "group-manual"; _heldGroupMember = _mapId;
            if(pending.StagedObservation is { } observation)
            {
                _frame=pending.Frame;_frameGeneration=pending.Commit.Render!.Generation;
                _floor=pending.Plan.Scope!.Floor;_observedFloor=observation.FloorId;
                RememberPointerObservation(pending.Frame,observation);_probeMisses=0;_entryCandidates.Clear();
            }
            _pose = pending.Pose;
            if(pending.ReusesSourceGeometry)
            {
                _render=pending.Commit.Render;_display=pending.Commit.Display;
                RegisterMaterialProposal();
            }
            else ApplyCanonicalCommit(pending.Commit);
            _pendingGroupMaterial = null;
            _groupChoiceStatus = "confirmed"; _groupChoiceReason = "本局保持手动选择";
            _reason = "group-member-confirmed"; Publish();
            return true;
        }
    }

    public bool RejectGroupMaterial(RuntimeRenderPacket packet, string reason)
    {
        ArgumentNullException.ThrowIfNull(packet);
        lock (_sync)
        {
            if(ReferenceEquals(_pendingProjection?.Grant.Render,packet))
            {DiscardProjectionMaterial(reason);_reason="transient-projection-unavailable";Publish();return true;}
            if (_pendingGroupMaterial is not { } pending || !ReferenceEquals(packet, pending.Commit.Render)) return false;
            DiscardGroupMaterial(reason); _reason = "group-member-awaiting-geometry"; Publish(); return true;
        }
    }

    private static long? GroupNumber(JsonElement payload, string name) => payload.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number) ? number : null;

    private async ValueTask<RuntimeOperationResult> SelectGroupMember(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        GroupMemberChoicePlan plan; CapturedClientFrame? captured; BoundResources bound; long intent;
        CapturedClientFrame? sourceFrame;RuntimeRenderPacket? sourceRender;MapDragCycle? sourceCycle;long sourceGeneration;bool fresh;
        lock (_sync)
        {
            RequireCurrent(epoch, token);
            bool cancelWaiting = _heldGroupMember is not null && _desiredGroupChoice is { } desired
                && desired.Map != _mapId && Text(request.Payload, "mapId") == _mapId;
            plan = GroupMemberChoicePlanner.Plan(GroupChoiceState(cancelWaiting), new(request.Action,
                Text(request.Payload, "mapId"), Text(request.Payload, "groupId"), GroupNumber(request.Payload, "groupGeneration"),
                GroupNumber(request.Payload, "cycleId"), Text(request.Payload, "floor")));
            if (!plan.Accepted) return Receipt(request, false, plan.Reason, plan.Message);
            if (!plan.Changed)
            {
                if (cancelWaiting)
                {
                    DiscardGroupMaterial("group-choice-returned-to-current-member");
                    _desiredGroupChoice = new(_roundId!, _resources!.Id, _nativeIdentity!.GroupId!, _nativeIdentity.MemberIds.ToArray(), _mapId!);
                    _groupChoiceStatus = "confirmed"; _groupChoiceReason = "本局保持手动选择"; Publish();
                }
                return Receipt(request, true, plan.Reason, plan.Message);
            }
            DiscardGroupMaterial("group-choice-replaced"); intent = _groupIntent;
            bound = _resources!;
            _desiredGroupChoice = new(_roundId!, bound.Id, _nativeIdentity!.GroupId!, _nativeIdentity.MemberIds.ToArray(), plan.MapId);
            _groupChoiceStatus = "awaiting-geometry"; _groupChoiceReason = "已选择地图，等待定位；当前显示尚未切换";
            if(plan.MapId==_mapId && RenderCurrent())
            {
                _groupChoiceWorkOwner=request.Context;
                var currentCommit=new CanonicalDisplayCommit(_alignment,_canonical,new RuntimeRenderPacket(_render!),_display,_geometryGroup);
                return StageGroupMaterial(request,new(intent,epoch,_frameGeneration,plan,_frame!,bound,_pose!,currentCommit,token,
                    _frame!,_render,_dragCycle,null,request.Context,ReusesSourceGeometry:true));
            }
            fresh=DragCycleCurrent;sourceFrame=_frame;sourceRender=_render;sourceCycle=_dragCycle;sourceGeneration=_frameGeneration;
            captured = !fresh && _mapOpen && _frame is { } current && _capture.IsCurrent(current.Stamp) ? current : null;
            _workActive = fresh || captured is not null; Publish();
            if (sourceFrame is null || fresh && !_capture.IsObservationContextCurrent(sourceFrame) || !fresh && captured is null)
                return GroupChoiceWaiting(request,"group-current-observation-unavailable");
            _groupChoiceWorkOwner=request.Context;
        }
        try
        {
            if(fresh)
            {
                var acquisition=await _capture.CaptureAsync(token).ConfigureAwait(false);
                captured=acquisition.Frame;
                RequireSourceCurrent(captured);
                if(captured is null){lock(_sync)return GroupChoiceWaiting(request,"group-target-capture-unavailable");}
            }
            FloorObservationResult? stagedRead=null;
            using var frame = ToBgr(captured!);
            if(fresh)
            {
                stagedRead=FloorObservation.Observe(frame,bound.Passports.Season,bound.Passports.Mode,
                    _options.FloorSettings is { } settings?System.Text.Json.Nodes.JsonNode.Parse(settings.GetRawText())?.AsObject():null);
                lock(_sync)
                {
                    RequireSourceCurrent(captured);
                    bool open=bound.Passports.Mode=="difficult"?stagedRead.MapOpen==true:stagedRead.MapObservation["detected"]?.GetValue<bool>()==true;
                    if(!open)
                        return PassiveProbeMiss(request,epoch,token,captured,bound,stagedRead,new {stage="group-target-observation",reason="map-not-observed"});
                    _probeMisses=0;
                    string? effective=ResolveObservedFloor(stagedRead);
                    if(!stagedRead.AutomaticEligible || effective!=plan.Scope!.Floor || stagedRead.FloorId!=plan.Scope.Floor)
                    {
                        if(effective is not null)_floor=effective;
                        _pose=null;RevokeDisplay("current-floor-unavailable");
                        return GroupChoiceWaiting(request,"current-floor-unavailable");
                    }
                }
            }
            var prepared = PrepareFit(epoch, token, captured!, bound, plan.MapId, plan.Scope!.Floor, frame,independentObservation:fresh);
            if(prepared?.Display is { } display && prepared.Matrix is not null)
                prepared=prepared with { Display=display with { Scene=PrepareMapScene(bound,prepared.Entry,display,token) } };
            lock (_sync)
            {
                RequireSourceCurrent(captured);
                if (prepared?.Matrix is not { } matrix)
                    return GroupChoiceWaiting(request, prepared?.Result["reason"]?.GetValue<string>() ?? "target-material-unavailable");
                if (prepared.Display?.Material.Layer is null)
                    return GroupChoiceWaiting(request, prepared.Display?.Material.Reason ?? "target-material-unavailable");
                // A separate core owns the candidate. Failed preparation cannot revoke the predecessor's grant.
                var commit = PrepareCanonicalCommit(prepared.Display,
                    ReadResidualEvidence(prepared.Result["selectedRouteCurrentFrameResidual"]!), captured!, plan.MapId, plan.Scope.Floor,
                    observationGeneration:fresh?sourceGeneration+1:sourceGeneration);
                if (commit.Grant is null || commit.Render is null)
                {
                    commit.Alignment?.Close(reason: "group-canonical-rejected");
                    return GroupChoiceWaiting(request, commit.Display.Reason);
                }
                var pending = new PendingGroupMaterial(intent, epoch, sourceGeneration, plan, captured!, bound,
                    new(plan.MapId, plan.Scope.Floor, bound.Id, captured!.Stamp.FrameId, matrix), commit, token,
                    sourceFrame!,sourceRender,sourceCycle,stagedRead,request.Context);
                return StageGroupMaterial(request,pending);
            }
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            lock (_sync)
            {
                RequireSourceCurrent(fresh?null:captured);
                return intent == _groupIntent ? GroupChoiceWaiting(request, "group-material-error:" + error.GetType().Name)
                    : Receipt(request, false, "group-choice-context-expired");
            }
        }
        void RequireSourceCurrent(CapturedClientFrame? observation)
        {
            lock(_sync)
            {
                RequireCurrent(epoch,token,observation,bound);
                if(intent!=_groupIntent || !ReferenceEquals(sourceFrame,_frame) || sourceGeneration!=_frameGeneration
                    || !ReferenceEquals(sourceRender,_render) || !ReferenceEquals(sourceCycle,_dragCycle)
                    || !GroupMemberChoicePlanner.IsCurrent(plan,GroupChoiceState(ignorePending:true))
                    || sourceFrame is null || !_capture.IsObservationContextCurrent(sourceFrame))
                    throw new OperationCanceledException("group-choice-context-expired",token);
            }
        }
    }

    // Both current-material reuse and a freshly fitted member share one pending lifetime.
    // Callers hold _sync through registration and publication.
    private RuntimeOperationResult StageGroupMaterial(SessionRuntimeRequest request,PendingGroupMaterial pending)
    {
        _pendingGroupMaterial=pending;
        pending.CancellationRegistration=pending.Cancellation.Register(()=>
        {
            lock(_sync)
            {
                if(!ReferenceEquals(_pendingGroupMaterial,pending))return;
                DiscardGroupMaterial("group-choice-cancelled");
                _reason="group-member-awaiting-geometry";Publish();
            }
        });
        if(!ReferenceEquals(_pendingGroupMaterial,pending) || pending.Cancellation.IsCancellationRequested)
        {
            // Clearing cannot depend on a cancelled callback we are unregistering.
            if(ReferenceEquals(_pendingGroupMaterial,pending))DiscardGroupMaterial("group-choice-cancelled");
            else pending.CancellationRegistration.Unregister();
            throw new OperationCanceledException("group-choice-cancelled",pending.Cancellation);
        }
        _groupChoiceStatus="awaiting-material";_groupChoiceReason="正在准备所选地图，当前显示尚未切换";
        _reason="group-member-awaiting-material";Publish();return Receipt(request,true,_reason);
    }

    private RuntimeOperationResult GroupChoiceWaiting(SessionRuntimeRequest request, string reason)
    {
        _groupChoiceStatus = "awaiting-geometry"; _groupChoiceReason = reason;
        _reason = "group-member-awaiting-geometry"; Publish(); return Receipt(request, true, _reason);
    }

    private async ValueTask<RuntimeOperationResult> RetryDesiredGroupChoice(SessionRuntimeRequest request, long epoch, CancellationToken token,
        RuntimeOperationResult result)
    {
        object payload;
        lock (_sync)
        {
            if (!DesiredGroupCurrent() || _groupChoiceStatus == "confirmed" || _pendingGroupMaterial is not null || _pendingProjection is not null || !_mapOpen)
                return result;
            var scope = GroupChoiceState();
            payload = new { mapId = _desiredGroupChoice!.Map, groupId = scope.GroupId,
                groupGeneration = scope.GroupGeneration, cycleId = scope.CycleId, floor = scope.Floor };
            _groupChoiceWorkOwner=request.Context;
        }
        var retry = await SelectGroupMember(new("select_group_member", JsonSerializer.SerializeToElement(payload), request.Context), epoch, token).ConfigureAwait(false);
        return new(new(request.Action, retry.Receipt.Success, request.Context, retry.Snapshot.Context, retry.Receipt.Reason, retry.Receipt.Message), retry.Snapshot);
    }
}
