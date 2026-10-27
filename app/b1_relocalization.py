"""Fail-closed B1 recovery policy shared by runtime and tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class B1RelocalizationState:
    map_id: str | None = None
    mode: str = "UNAVAILABLE"
    last_success_frame_generation: int | None = None
    anchor_age_ms: float | None = None
    consecutive_local_failures: int = 0
    wide_searches: int = 0

    def reset(self) -> None:
        self.map_id = None
        self.mode = "UNAVAILABLE"
        self.last_success_frame_generation = None
        self.anchor_age_ms = None
        self.consecutive_local_failures = 0
        self.wide_searches = 0


def choose_b1_policy(
    state: B1RelocalizationState | None,
    *,
    map_id: str,
    first_teleport: bool = False,
    discontinuous_jump: bool = False,
    local_failures: int | None = None,
    group_pose_available: bool = False,
) -> dict:
    """Return one bounded recovery mode; no generic threshold relaxation."""
    current = state or B1RelocalizationState()
    effective_local_failures = (
        current.consecutive_local_failures
        if local_failures is None else int(local_failures)
    )
    same_map_local = bool(
        group_pose_available
        and current.map_id == str(map_id)
        and current.mode == "LOCAL_FAST"
        and not first_teleport
        and not discontinuous_jump
        and effective_local_failures < 1
    )
    needs_wide_escalation = bool(
        group_pose_available
        and current.map_id == str(map_id)
        and current.mode in {"LOCAL_FAST", "ESCALATE_WIDE"}
        and effective_local_failures >= 1
        and not first_teleport
        and not discontinuous_jump
    )
    if needs_wide_escalation:
        mode = "ESCALATE_WIDE"
        wide_first = True
        allow_wide_fallback = True
        reason = "consecutive-local-failures-wide"
    elif same_map_local:
        mode = "LOCAL_FAST"
        wide_first = False
        allow_wide_fallback = False
        reason = "trusted-b1-local-fast"
    elif first_teleport or discontinuous_jump or not group_pose_available:
        mode = "INITIAL_WIDE"
        wide_first = True
        allow_wide_fallback = True
        reason = "first-teleport-wide" if first_teleport else "b1-discontinuous-wide"
    else:
        mode = "LOCAL_FAST"
        wide_first = False
        allow_wide_fallback = False
        reason = "trusted-b1-local-fast-retry"
    return {
        "mode": mode,
        "wideFirst": wide_first,
        "allowWideFallback": allow_wide_fallback,
        "reason": reason,
        "groupPoseAvailable": bool(group_pose_available),
        "localFailures": effective_local_failures,
        "candidateOnly": True,
        "currentFrameValidationRequired": True,
    }


def record_b1_result(
    state: B1RelocalizationState,
    *,
    map_id: str,
    success: bool,
    mode: str,
    frame_generation: int | None = None,
    anchor_age_ms: float | None = None,
) -> dict:
    state.map_id = str(map_id)
    state.anchor_age_ms = anchor_age_ms
    if success:
        state.mode = "LOCAL_FAST"
        state.last_success_frame_generation = frame_generation
        state.consecutive_local_failures = 0
        return {"mode": state.mode, "reason": "wide-success-return-to-local-fast" if mode != "LOCAL_FAST" else "local-fast-success"}
    state.consecutive_local_failures += 1
    if mode == "INITIAL_WIDE":
        state.wide_searches += 1
    if state.consecutive_local_failures >= 1 and mode == "LOCAL_FAST":
        state.mode = "ESCALATE_WIDE"
        return {"mode": "ESCALATE_WIDE", "reason": "consecutive-local-failures", "consecutiveFailures": state.consecutive_local_failures}
    return {"mode": mode, "reason": "b1-current-frame-failed", "consecutiveFailures": state.consecutive_local_failures}
