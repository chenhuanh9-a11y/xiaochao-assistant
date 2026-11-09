using System.Text.Json;
using StarMap.Core.Identity;
using StarMap.Core.Session;

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

    private sealed record GroupChoiceIntent(string Round, string Resource, string Group, string[] Members, string Map);
    private sealed record PendingGroupMaterial(long Intent, long Epoch, long FrameGeneration,
        GroupMemberChoicePlan Plan, CapturedClientFrame Frame, BoundResources Resources,
        NativeGeometryPose Pose, CanonicalDisplayCommit Commit, CancellationToken Cancellation);

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
        selectionPending: !ignorePending && _pendingGroupMaterial is not null);

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
        if (_pendingGroupMaterial is { } pending && !ReferenceEquals(pending.Commit.Alignment, _alignment))
            pending.Commit.Alignment?.Close(reason: reason);
        _pendingGroupMaterial = null;
        if (_desiredGroupChoice is not null && _groupChoiceStatus != "confirmed")
        { _groupChoiceStatus = "awaiting-geometry"; _groupChoiceReason = reason; }
    }

    private void ClearGroupChoice(bool resetCycle = true)
    {
        DiscardGroupMaterial("group-choice-reset");
        _desiredGroupChoice = null; _heldGroupMember = null;
        _groupChoiceStatus = _groupChoiceReason = "";
        if (resetCycle) _mapCycle = 0;
    }

    // This read never changes the current render or grants live evidence.
    public RuntimeRenderPacket? ReadPendingGroupMaterial()
    {
        lock (_sync) return PendingGroupCurrent(_pendingGroupMaterial) ? _pendingGroupMaterial!.Commit.Render : null;
    }

    private bool PendingGroupCurrent(PendingGroupMaterial? pending) => pending is not null && !_closed
        && pending.Intent == _groupIntent && pending.Epoch == _epoch && pending.FrameGeneration == _frameGeneration
        && !pending.Cancellation.IsCancellationRequested && _mapOpen && ReferenceEquals(pending.Frame, _frame)
        && ReferenceEquals(pending.Resources, _resources) && _capture.IsCurrent(pending.Frame.Stamp)
        && DesiredGroupCurrent() && GroupMemberChoicePlanner.IsCurrent(pending.Plan, GroupChoiceState(ignorePending: true));

    /// <summary>ACK for consumer material readiness, never a native-window or GPU-present ACK.</summary>
    public bool AcceptGroupMaterialReady(RuntimeRenderPacket packet)
    {
        ArgumentNullException.ThrowIfNull(packet);
        lock (_sync)
        {
            var pending = _pendingGroupMaterial;
            if (_workActive || !PendingGroupCurrent(pending) || !ReferenceEquals(packet, pending!.Commit.Render)) return false;
            _alignment?.Close(reason: "group-member-material-ready");
            ResetSteady();
            _mapId = pending.Plan.MapId; _identitySource = "group-manual"; _heldGroupMember = _mapId;
            _pose = pending.Pose; ApplyCanonicalCommit(pending.Commit);
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
            if (_pendingGroupMaterial is not { } pending || !ReferenceEquals(packet, pending.Commit.Render)) return false;
            DiscardGroupMaterial(reason); _reason = "group-member-awaiting-geometry"; Publish(); return true;
        }
    }

    private static long? GroupNumber(JsonElement payload, string name) => payload.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number) ? number : null;

    private RuntimeOperationResult SelectGroupMember(SessionRuntimeRequest request, long epoch, CancellationToken token)
    {
        GroupMemberChoicePlan plan; CapturedClientFrame? captured; BoundResources bound; long intent;
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
            captured = _mapOpen && _frame is { } current && _capture.IsCurrent(current.Stamp) ? current : null;
            _workActive = captured is not null; Publish();
            if (captured is null) return Receipt(request, true, "group-member-awaiting-geometry");
        }
        try
        {
            using var frame = ToBgr(captured);
            var prepared = PrepareFit(epoch, token, captured, bound, plan.MapId, plan.Scope!.Floor, frame);
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                if (intent != _groupIntent || !GroupMemberChoicePlanner.IsCurrent(plan, GroupChoiceState(ignorePending: true)))
                    return Receipt(request, false, "group-choice-context-expired");
                if (prepared?.Matrix is not { } matrix)
                    return GroupChoiceWaiting(request, prepared?.Result["reason"]?.GetValue<string>() ?? "target-material-unavailable");
                if (prepared.Display?.Material.Layer is null)
                    return GroupChoiceWaiting(request, prepared.Display?.Material.Reason ?? "target-material-unavailable");
                // A separate core owns the candidate. Failed preparation cannot revoke the predecessor's grant.
                var commit = PrepareCanonicalCommit(prepared.Display,
                    ReadResidualEvidence(prepared.Result["selectedRouteCurrentFrameResidual"]!), captured, plan.MapId, plan.Scope.Floor);
                if (commit.Grant is null || commit.Render is null)
                {
                    commit.Alignment?.Close(reason: "group-canonical-rejected");
                    return GroupChoiceWaiting(request, commit.Display.Reason);
                }
                _pendingGroupMaterial = new(intent, epoch, _frameGeneration, plan, captured, bound,
                    new(plan.MapId, plan.Scope.Floor, bound.Id, captured.Stamp.FrameId, matrix), commit, token);
                _groupChoiceStatus = "awaiting-material"; _groupChoiceReason = "正在准备所选地图，当前显示尚未切换";
                _reason = "group-member-awaiting-material"; Publish();
                return Receipt(request, true, _reason);
            }
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            lock (_sync)
            {
                RequireCurrent(epoch, token, captured, bound);
                return intent == _groupIntent ? GroupChoiceWaiting(request, "group-material-error:" + error.GetType().Name)
                    : Receipt(request, false, "group-choice-context-expired");
            }
        }
    }

    private RuntimeOperationResult GroupChoiceWaiting(SessionRuntimeRequest request, string reason)
    {
        _groupChoiceStatus = "awaiting-geometry"; _groupChoiceReason = reason;
        _reason = "group-member-awaiting-geometry"; Publish(); return Receipt(request, true, _reason);
    }

    private RuntimeOperationResult RetryDesiredGroupChoice(SessionRuntimeRequest request, long epoch, CancellationToken token,
        RuntimeOperationResult result)
    {
        object payload;
        lock (_sync)
        {
            if (!DesiredGroupCurrent() || _groupChoiceStatus == "confirmed" || _pendingGroupMaterial is not null || !_mapOpen)
                return result;
            var scope = GroupChoiceState();
            payload = new { mapId = _desiredGroupChoice!.Map, groupId = scope.GroupId,
                groupGeneration = scope.GroupGeneration, cycleId = scope.CycleId, floor = scope.Floor };
        }
        var retry = SelectGroupMember(new("select_group_member", JsonSerializer.SerializeToElement(payload), request.Context), epoch, token);
        return new(new(request.Action, retry.Receipt.Success, request.Context, retry.Snapshot.Context, retry.Receipt.Reason, retry.Receipt.Message), retry.Snapshot);
    }
}
