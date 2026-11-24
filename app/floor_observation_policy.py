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


def read_floor_evidence(floor_read, observations, *, explicit_floor=None, contract=None):
    observed = floor_read.get("selectedFloor")
    allowed = contract.observed_values if contract is not None else (-1, 1, 2)
    observed = observed if type(observed) is int and observed in allowed else None
    window = [*observations, observed][-5:]
    scores = floor_read.get("scores") or {}
    observed_score = float(scores.get(str(observed), 0.0))
    strong_latest = False
    explicit = type(explicit_floor) is int and explicit_floor in allowed
    stable = explicit_floor if explicit else stable_floor_from_observations(window, strong_latest=strong_latest, allowed_values=allowed)
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
                            cycle_id=None, game_intent=None, now, contract=None):
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
    target = contract.floor_id_for(stable) if contract is not None else "B1" if stable == -1 else str(stable)
    if target is None:
        return FloorTransition(reason="floor-contract-unavailable")
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


def game_floor_control_hit(cursor, client_bounds, floor_read):
    """Read clicks only from complete current-frame registered geometry."""
    if (not isinstance(floor_read, dict) or floor_read.get("layout") not in
            {"localized-s0-vertical", "localized-floor-contract"}):
        return None
    if floor_read.get("layout") == "localized-s0-vertical":
        from s0_vertical_controls import control_hit
        return control_hit(cursor, client_bounds, floor_read)
    if floor_read.get("layout") == "localized-floor-contract":
        from floor_contract import floor_contract
        contract = floor_contract(floor_read.get("mode"), season=floor_read.get("season"))
        if (contract is None or floor_read.get("mapOpen") is not True
                or floor_read.get("controlGeometryVerified") is not True
                or floor_read.get("controlAxis") != contract.control_axis):
            return None
        rects = floor_read.get("controlRects")
        if not isinstance(rects, (list, tuple)) or len(rects) != len(contract.floors):
            return None
        import math
        left, top, right, bottom = map(float, client_bounds)
        width, height = right - left, bottom - top
        if min(width, height) < 100:
            return None
        frame_size = floor_read.get("controlFrameSize")
        if frame_size is not None:
            if (not isinstance(frame_size, (list, tuple)) or len(frame_size) != 2
                    or not all(type(v) in (int, float) and math.isfinite(v) for v in frame_size)
                    or any(abs(a - b) > 1 for a, b in zip((width, height), frame_size))):
                return None
        point = ((cursor[0] - left) / width, (cursor[1] - top) / height)
        hits, centers, prev_end = [], [], None
        for definition, row in zip(contract.floors, rects):
            try:
                x0, y0, x1, y1 = row["rect"]
            except (KeyError, TypeError, ValueError):
                return None
            if (type(row.get("floor")) is not int or row["floor"] != definition.observed_value
                    or not all(type(x) in (int, float) and math.isfinite(x) for x in (x0, y0, x1, y1))
                    or not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)):
                return None
            start = y0 if contract.control_axis == "vertical" else x0
            end = y1 if contract.control_axis == "vertical" else x1
            if prev_end is not None and prev_end >= start:
                return None
            prev_end = end
            centers.append((start + end) / 2)
            if x0 <= point[0] <= x1 and y0 <= point[1] <= y1:
                hits.append(definition.observed_value)
        if any(a >= b for a, b in zip(centers, centers[1:])):
            return None
        return hits[0] if len(hits) == 1 else None
    return None


def cursor_in_floor_column(cursor, client_bounds, floor_read):
    """Return True when a press lands inside the localized floor column area.

    Prevents edge/gap clicks between floor buttons from initiating a map drag.
    """
    if not isinstance(floor_read, dict):
        return False
    import math
    try:
        if (len(cursor) != 2 or len(client_bounds) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in (*cursor, *client_bounds))):
            return False
        left, top, right, bottom = map(float, client_bounds)
        width, height = right - left, bottom - top
        if min(width, height) < 100:
            return False
        px, py = (cursor[0] - left) / width, (cursor[1] - top) / height
        rects = floor_read.get("controlRects")
        if isinstance(rects, (list, tuple)) and rects:
            xs0, ys0, xs1, ys1 = [], [], [], []
            for row in rects:
                x0, y0, x1, y1 = row["rect"]
                if not all(type(v) in (int, float) and math.isfinite(v) for v in (x0, y0, x1, y1)):
                    return False
                xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
            pad = 0.004
            return (min(xs0) - pad <= px <= max(xs1) + pad
                    and min(ys0) - pad <= py <= max(ys1) + pad)
        bounds = floor_read.get("controlBounds")
        if (isinstance(bounds, (list, tuple)) and len(bounds) == 4
                and all(type(v) in (int, float) and math.isfinite(v) for v in bounds)):
            bx0, by0, bx1, by1 = bounds
            return (bx0 / width <= px <= bx1 / width and by0 / height <= py <= by1 / height)
    except (KeyError, TypeError, ValueError):
        return False
    return False
