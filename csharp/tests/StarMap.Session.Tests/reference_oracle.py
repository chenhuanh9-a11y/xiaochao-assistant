"""Independent expected values from the unchanged production Python pure policies.

No Qt, live runtime, capture, game input, resource loading, or recognition mocks.
"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "app"))
from ui_action_state import ImmediateUiActionStateMachine, UiActionToken
import session_state_machine as p

machine = ImmediateUiActionStateMachine()
decisions = []


def take(decision):
    s = decision.snapshot
    decisions.append(dict(generation=s.token.generation, phase=s.phase.value,
        accepted=decision.accepted, coalesced=decision.coalesced, stale=decision.stale,
        enqueue=decision.should_enqueue_background, attempted=decision.token.generation,
        target=s.target_state, visible=s.visible_state, input=s.input_at_ms,
        visual=s.visual_at_ms, enqueued=s.enqueued_at_ms, settled=s.settled_at_ms,
        count=s.coalesced_inputs, reason=s.reason))
    return decision.token


one = take(machine.begin("capture", target_state="map-1", visual_state="requested", now_ms=100))
take(machine.mark_enqueued(one, now_ms=101))
take(machine.begin("capture", target_state="map-1", visual_state="ignored", now_ms=102))
two = take(machine.begin("capture", target_state="map-2", visual_state="requested", now_ms=103))
take(machine.settle(one, final_state="wrong", now_ms=104))
take(machine.mark_enqueued(two, now_ms=105))
take(machine.fail(two, fallback_state="failed", reason="fixture", now_ms=106))
three = take(machine.begin("capture", target_state="map-2", visual_state="requested", now_ms=110))
take(machine.cancel(three, reason="closed", now_ms=111))
take(machine.settle(three, now_ms=112))
take(machine.cancel(UiActionToken("unknown", 9), now_ms=113))

policy = []
for is_open in (False, True):
    for capture in (False, True):
        for silent in (False, True):
            policy.append(p.map_hotkey_transition(is_open, capture, silent))
for is_open in (False, True):
    for misses in (0, 1, 2):
        for probe in (None, False, True):
            for allowed in (False, True):
                policy.append(p.map_probe_transition(is_open, misses, probe, close_allowed=allowed))
for observed in (None, -1, 0, 1, 2, 3):
    for manual in (None, "1", "2", "B1", "B4"):
        policy.append(p.resolve_runtime_floor(observed, manual))
for observations in ([], [1], [1, None], [1, 2], [-1], [2, 99]):
    policy.append(p.stable_floor_from_observations(observations))
for selected in (None, -1, 0, 1, 2, 3):
    for detected in (False, True):
        evidence = dict(detected=detected, flatMapCanvas=True, backdropRatio=0.8)
        policy.append(p.verified_map_hotkey_frame(evidence, dict(selectedFloor=selected)))
        policy.append(p.locked_map_canvas_without_floor_is_authorized(evidence,
            dict(selectedFloor=selected), round_locked=True))
for event in (None, 1, 2):
    policy.extend((p.ranked_event_is_current(event, 2), p.display_event_is_current(event, None),
        p.display_event_is_current(event, 2)))
for confirmed, candidate in ((None, None), ("a", None), ("a", "a"), ("a", "b")):
    policy.append(p.confirmed_overlay_source_is_allowed(confirmed, candidate))
for game, foreground in ((None, None), (0, 1), (1, 1), (1, 2)):
    policy.append(p.global_key_is_allowed(game, foreground))
    policy.append(p.trusted_foreground_is_allowed(game, foreground, 5, 5))

print(json.dumps(dict(ui=decisions, policy=policy), ensure_ascii=False, allow_nan=False))
