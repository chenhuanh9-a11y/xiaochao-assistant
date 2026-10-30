"""Plan a scoped group-member intent; never commit identity, pixels or grants."""
from dataclasses import dataclass


@dataclass(frozen=True)
class GroupMemberChoiceScope:
    mode: str
    group_id: str
    group_generation: int
    cycle_id: object
    floor: str
    members: tuple[str, ...]
    current_member_id: str
    manual_hold: bool
    manual_member_id: str


@dataclass(frozen=True)
class GroupMemberChoicePlan:
    accepted: bool
    changed: bool
    reason: str
    message: str
    map_id: str = ""
    hold: bool = False
    scope: GroupMemberChoiceScope | None = None


def _scope(state):
    members = tuple(dict.fromkeys(str(value) for value in state.get("groupMemberIds", ()) if value))
    return GroupMemberChoiceScope(str(state.get("mode") or ""), str(state.get("groupId") or ""),
        state.get("groupGeneration", 0), state.get("cycleId"), str(state.get("floor") or ""),
        members, str(state.get("currentGroupMemberId") or state.get("provisionalMapId")
                     or state.get("durableMapId") or state.get("mapId") or ""),
        bool(state.get("manualGroupMemberHeld")), str(state.get("manualGroupMemberId") or ""))


def group_member_choice_is_current(plan, state):
    """Additional group/floor scope check; the owner still checks pose/frame scope."""
    return bool(plan.accepted and plan.scope is not None and state.get("roundActive")
                and state.get("identityPhase") in {"GROUP_LOCKED", "MEMBER_VERIFIED", "MANUAL_MEMBER_CONFIRMED"}
                and not state.get("resourceLocked") and not state.get("contextPending")
                and plan.scope == _scope(state))


def plan_group_member_choice(state, action, payload):
    """Validate intent only; accepted selection still needs fit + material ACK.

    Hold is a proposed post-commit value. The runtime owns its lifetime, reset
    rules and atomic pose/display transaction. Author pixels are not an input.
    """
    def reject(reason, message):
        return GroupMemberChoicePlan(False, False, reason, message)

    if action not in {"select_group_member", "resume_group_automatic"}:
        return reject("unsupported-action", "当前操作不属于相似组切换")
    scope = _scope(state)
    if (not state.get("roundActive") or not scope.group_id or len(scope.members) < 2
            or scope.cycle_id is None
            or state.get("identityPhase") not in {"GROUP_LOCKED", "MEMBER_VERIFIED", "MANUAL_MEMBER_CONFIRMED"}):
        return reject("group-unavailable", "当前没有可切换的相似组，请先开始识别")
    if any(name not in payload or payload[name] != value for name, value in (
            ("groupId", scope.group_id), ("groupGeneration", scope.group_generation),
            ("cycleId", scope.cycle_id), ("floor", scope.floor))):
        return reject("choice-scope-changed", "相似组或楼层已变化，请按当前列表重新选择")
    if not scope.floor or scope.current_member_id not in scope.members:
        return reject("current-member-unavailable", "当前组成员或楼层尚未就绪")
    if state.get("resourceLocked") or state.get("contextPending") or state.get("roundPreparing"):
        return reject("context-unavailable", "正在切换环境或更新资源，请稍后选择")
    if state.get("groupMemberSelectionPending"):
        return reject("choice-pending", "正在核对上次选择，请等待结果")
    held = bool(state.get("manualGroupMemberHeld"))
    if action == "resume_group_automatic":
        return GroupMemberChoicePlan(True, held, "resume-automatic" if held else "already-automatic",
            "恢复自动组内复核" if held else "当前已由自动复核选择", scope.current_member_id, False, scope)
    target = str(payload.get("mapId") or "")
    if target not in scope.members:
        return reject("outside-current-group", "只能选择当前相似组内的地图")
    if target == scope.current_member_id and held:
        return GroupMemberChoicePlan(True, False, "already-current", "当前已使用此地图", target, held, scope)
    if target == scope.current_member_id:
        return GroupMemberChoicePlan(True, True, "prepare-current-member", "正在核对当前地图，核对成功后才会保持本局选择",
                                     target, True, scope)
    return GroupMemberChoicePlan(True, True, "prepare-member", "正在核对所选地图，核对成功后才会切换",
                                 target, True, scope)
