"""Pure floor observation/intent decisions shared by both frontends.

This policy grants no pose or display authorization. Current geometry and core
checks still own those decisions. UI, capture, group recovery and logging stay
with the caller.
"""
from dataclasses import dataclass

from session_state_machine import stable_floor_from_observations


MANUAL_FLOOR_HOLD_SECONDS = 2.0


@dataclass(frozen=True)
class FloorEvidence:
    observed: int | None
    observations: tuple
    observed_score: float
    strong_latest: bool
    explicit_intent_active: bool
    stable: int | None


def read_floor_evidence(floor_read, observations, *, explicit_floor=None):
    observed = floor_read.get("selectedFloor")
    window = [*observations, observed if observed in (-1, 1, 2) else None][-5:]
    scores = floor_read.get("scores") or {}
    observed_score = float(scores.get(str(observed), 0.0))
    strong_latest = bool(
        observed in (1, 2)
        and floor_read.get("layout") == "localized-triplet"
        and float(floor_read.get("margin", 0.0)) >= 12.0
        and observed_score >= 32.0
    )
    explicit = explicit_floor in (-1, 1, 2)
    stable = explicit_floor if explicit else stable_floor_from_observations(window, strong_latest=strong_latest)
    return FloorEvidence(observed, tuple(window), observed_score, strong_latest, explicit, stable)


@dataclass(frozen=True)
class FloorTransition:
    target: str | None = None
    reason: str = "insufficient-stable-evidence"
    clear_game_intent: bool = False
    manual_hold_ended: bool = False
    manual_override: bool = False
    hold_active: bool = False
    game_intent_active: bool = False
    clear_floor_required: bool = False
    apply_target: bool = False


def decide_floor_transition(evidence, *, current_floor, floor_required=False,
                            manual_override=False, hard_locked=False, hold_until=0.0,
                            cycle_id=None, game_intent=None, now):
    """Original `_consider_floor_observation` hold/intent/target rules.

    A pending game click may block conflicting observations before any hold
    expiry or floor-required mutation. Unknown latest evidence never inherits a
    floor from historical votes. An explicit intent may override a passive lock.
    """
    stable = evidence.stable
    explicit = evidence.explicit_intent_active
    clear_intent = False
    if not explicit and isinstance(game_intent, dict) and game_intent.get("awaitingObservation"):
        if game_intent.get("cycleId") == cycle_id and now <= float(game_intent.get("expiresAt", 0.0)):
            if stable != game_intent.get("floor"):
                return FloorTransition(reason="pending-click-mismatch", manual_override=manual_override)
        clear_intent = True
        game_intent = None
    if stable is None:
        return FloorTransition(clear_game_intent=clear_intent, manual_override=manual_override)
    target = "B1" if stable == -1 else str(stable)
    hold_active = bool(manual_override and not hard_locked and now < hold_until)
    hold_ended = bool(manual_override and not hard_locked and not hold_active)
    effective_override = False if hold_ended else manual_override
    intent_active = bool(not explicit and isinstance(game_intent, dict)
        and game_intent.get("cycleId") == cycle_id and game_intent.get("floor") == stable
        and now <= float(game_intent.get("expiresAt", 0.0)))
    intent_active = bool(intent_active or explicit)
    if isinstance(game_intent, dict) and not intent_active:
        clear_intent = True
    required_cleared = bool(floor_required and evidence.observed == stable
        and (target == current_floor or intent_active or not (hard_locked or hold_active)))
    common = dict(target=target, clear_game_intent=clear_intent,
        manual_hold_ended=hold_ended, manual_override=effective_override,
        hold_active=hold_active, game_intent_active=intent_active,
        clear_floor_required=required_cleared)
    if target == current_floor:
        return FloorTransition(**{**common, "clear_game_intent": clear_intent or intent_active},
            reason="current-floor-authorization-applied" if required_cleared else "already-current-floor")
    if (hard_locked or hold_active) and not intent_active:
        return FloorTransition(**common, reason="hard-lock-deferred" if hard_locked else "manual-hold-deferred")
    return FloorTransition(**{**common, "clear_game_intent": clear_intent or intent_active},
        reason="floor-state-applied", apply_target=True)
