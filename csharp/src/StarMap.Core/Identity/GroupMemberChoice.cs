namespace StarMap.Core.Identity;

/// <summary>
/// Read-only input snapshot for planning a choice from an already registered group.
/// This value contains intent context only; it does not grant identity or display authority.
/// </summary>
public sealed class GroupMemberChoiceState
{
    private readonly string[] groupMemberIds;

    public string Mode { get; }
    public string GroupId { get; }
    public long GroupGeneration { get; }
    public long? CycleId { get; }
    public string Floor { get; }
    public IReadOnlyList<string> GroupMemberIds => Array.AsReadOnly((string[])groupMemberIds.Clone());
    public string? CurrentGroupMemberId { get; }
    public string? ProvisionalMapId { get; }
    public string? DurableMapId { get; }
    public string? MapId { get; }
    public bool ManualHold { get; }
    public string ManualMemberId { get; }
    public bool RoundActive { get; }
    public string IdentityPhase { get; }
    public bool ResourceLocked { get; }
    public bool ContextPending { get; }
    public bool RoundPreparing { get; }
    public bool SelectionPending { get; }

    public GroupMemberChoiceState(
        string? mode,
        string? groupId,
        long groupGeneration,
        long? cycleId,
        string? floor,
        IEnumerable<string?>? groupMemberIds,
        string? currentGroupMemberId = null,
        string? provisionalMapId = null,
        string? durableMapId = null,
        string? mapId = null,
        bool manualHold = false,
        string? manualMemberId = null,
        bool roundActive = false,
        string? identityPhase = null,
        bool resourceLocked = false,
        bool contextPending = false,
        bool roundPreparing = false,
        bool selectionPending = false)
    {
        Mode = mode ?? string.Empty;
        GroupId = groupId ?? string.Empty;
        GroupGeneration = groupGeneration;
        CycleId = cycleId;
        Floor = floor ?? string.Empty;
        this.groupMemberIds = DistinctMembers(groupMemberIds);
        CurrentGroupMemberId = currentGroupMemberId;
        ProvisionalMapId = provisionalMapId;
        DurableMapId = durableMapId;
        MapId = mapId;
        ManualHold = manualHold;
        ManualMemberId = manualMemberId ?? string.Empty;
        RoundActive = roundActive;
        IdentityPhase = identityPhase ?? string.Empty;
        ResourceLocked = resourceLocked;
        ContextPending = contextPending;
        RoundPreparing = roundPreparing;
        SelectionPending = selectionPending;
    }

    internal string[] CopyGroupMemberIds() => (string[])groupMemberIds.Clone();

    private static string[] DistinctMembers(IEnumerable<string?>? values)
    {
        if (values is null) return [];
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var result = new List<string>();
        foreach (var value in values)
        {
            if (!string.IsNullOrEmpty(value) && seen.Add(value)) result.Add(value);
        }
        return result.ToArray();
    }
}

/// <summary>Typed request; Has* flags preserve whether each required scope key was supplied.</summary>
public sealed record GroupMemberChoiceRequest(
    string? Action,
    string? MapId,
    string? GroupId,
    long? GroupGeneration,
    long? CycleId,
    string? Floor,
    bool HasGroupId = true,
    bool HasGroupGeneration = true,
    bool HasCycleId = true,
    bool HasFloor = true);

/// <summary>Immutable value scope tied to the exact ordered registered member sequence.</summary>
public sealed class GroupMemberChoiceScope : IEquatable<GroupMemberChoiceScope>
{
    private readonly string[] members;

    public string Mode { get; }
    public string GroupId { get; }
    public long GroupGeneration { get; }
    public long? CycleId { get; }
    public string Floor { get; }
    public IReadOnlyList<string> Members => Array.AsReadOnly((string[])members.Clone());
    public string CurrentMemberId { get; }
    public bool ManualHold { get; }
    public string ManualMemberId { get; }

    internal GroupMemberChoiceScope(
        string mode,
        string groupId,
        long groupGeneration,
        long? cycleId,
        string floor,
        IEnumerable<string> members,
        string currentMemberId,
        bool manualHold,
        string manualMemberId)
    {
        Mode = mode;
        GroupId = groupId;
        GroupGeneration = groupGeneration;
        CycleId = cycleId;
        Floor = floor;
        this.members = members.ToArray();
        CurrentMemberId = currentMemberId;
        ManualHold = manualHold;
        ManualMemberId = manualMemberId;
    }

    public bool Equals(GroupMemberChoiceScope? other) => other is not null
        && Mode == other.Mode
        && GroupId == other.GroupId
        && GroupGeneration == other.GroupGeneration
        && CycleId == other.CycleId
        && Floor == other.Floor
        && CurrentMemberId == other.CurrentMemberId
        && ManualHold == other.ManualHold
        && ManualMemberId == other.ManualMemberId
        && members.SequenceEqual(other.members, StringComparer.Ordinal);

    public override bool Equals(object? obj) => obj is GroupMemberChoiceScope other && Equals(other);

    public override int GetHashCode()
    {
        var hash = new HashCode();
        hash.Add(Mode, StringComparer.Ordinal);
        hash.Add(GroupId, StringComparer.Ordinal);
        hash.Add(GroupGeneration);
        hash.Add(CycleId);
        hash.Add(Floor, StringComparer.Ordinal);
        foreach (var member in members) hash.Add(member, StringComparer.Ordinal);
        hash.Add(CurrentMemberId, StringComparer.Ordinal);
        hash.Add(ManualHold);
        hash.Add(ManualMemberId, StringComparer.Ordinal);
        return hash.ToHashCode();
    }

    internal string[] CopyMembers() => (string[])members.Clone();
}

/// <summary>Intent-only result. Runtime must still fit and commit material before applying it.</summary>
public sealed record GroupMemberChoicePlan(
    bool Accepted,
    bool Changed,
    string Reason,
    string Message,
    string MapId = "",
    bool Hold = false,
    GroupMemberChoiceScope? Scope = null);

/// <summary>Pure planner for selecting only within the group's registered member list.</summary>
public static class GroupMemberChoicePlanner
{
    private static readonly HashSet<string> AvailableIdentityPhases = new(StringComparer.Ordinal)
    {
        "GROUP_LOCKED", "MEMBER_VERIFIED", "MANUAL_MEMBER_CONFIRMED"
    };

    public static GroupMemberChoicePlan Plan(GroupMemberChoiceState state, GroupMemberChoiceRequest request)
    {
        ArgumentNullException.ThrowIfNull(state);
        ArgumentNullException.ThrowIfNull(request);

        static GroupMemberChoicePlan Reject(string reason, string message) =>
            new(false, false, reason, message);

        if (request.Action != "select_group_member")
            return Reject("unsupported-action", "当前操作不属于相似组切换");

        var scope = CreateScope(state);
        var members = scope.CopyMembers();
        if (!state.RoundActive || scope.GroupId.Length == 0 || members.Length < 2
            || scope.CycleId is null || !AvailableIdentityPhases.Contains(state.IdentityPhase))
            return Reject("group-unavailable", "当前没有可切换的相似组，请先开始识别");

        if (!request.HasGroupId || !request.HasGroupGeneration || !request.HasCycleId || !request.HasFloor
            || request.GroupId is null || request.GroupGeneration is null || request.CycleId is null || request.Floor is null
            || request.GroupId != scope.GroupId || request.GroupGeneration.Value != scope.GroupGeneration
            || request.CycleId.Value != scope.CycleId.Value || request.Floor != scope.Floor)
            return Reject("choice-scope-changed", "相似组或楼层已变化，请按当前列表重新选择");

        if (scope.Floor.Length == 0 || !members.Contains(scope.CurrentMemberId, StringComparer.Ordinal))
            return Reject("current-member-unavailable", "当前组成员或楼层尚未就绪");

        if (state.ResourceLocked || state.ContextPending || state.RoundPreparing)
            return Reject("context-unavailable", "正在切换环境或更新资源，请稍后选择");

        if (state.SelectionPending)
            return Reject("choice-pending", "正在核对上次选择，请等待结果");

        var target = request.MapId ?? string.Empty;
        if (!members.Contains(target, StringComparer.Ordinal))
            return Reject("outside-current-group", "只能选择当前相似组内的地图");

        if (target == scope.CurrentMemberId && scope.ManualHold)
            return new(true, false, "already-current", "当前已使用此地图", target, scope.ManualHold, scope);

        if (target == scope.CurrentMemberId)
            return new(true, true, "prepare-current-member", "正在核对当前地图，核对成功后才会保持本局选择",
                target, true, scope);

        return new(true, true, "prepare-member", "正在核对所选地图，核对成功后才会切换",
            target, true, scope);
    }

    /// <summary>Revalidates the result's group/floor scope; pose and frame checks belong to Runtime.</summary>
    public static bool IsCurrent(GroupMemberChoicePlan plan, GroupMemberChoiceState state)
    {
        ArgumentNullException.ThrowIfNull(plan);
        ArgumentNullException.ThrowIfNull(state);
        return plan.Accepted && plan.Scope is not null && state.RoundActive
            && AvailableIdentityPhases.Contains(state.IdentityPhase)
            && !state.ResourceLocked && !state.ContextPending
            && plan.Scope.Equals(CreateScope(state));
    }

    private static GroupMemberChoiceScope CreateScope(GroupMemberChoiceState state)
    {
        var members = state.CopyGroupMemberIds();
        var current = FirstTruthy(state.CurrentGroupMemberId, state.ProvisionalMapId, state.DurableMapId, state.MapId);
        return new GroupMemberChoiceScope(state.Mode, state.GroupId, state.GroupGeneration, state.CycleId,
            state.Floor, members, current, state.ManualHold, state.ManualMemberId);
    }

    private static string FirstTruthy(params string?[] candidates)
    {
        foreach (var candidate in candidates)
            if (!string.IsNullOrEmpty(candidate)) return candidate;
        return string.Empty;
    }
}
