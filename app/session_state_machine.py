from __future__ import annotations


def verified_map_hotkey_frame(evidence: dict, floor_read: dict | None) -> bool:
    """Accept either strong map canvas proof or a settled floor selector.

    The floor triplet is itself map-only UI.  Requiring the generic canvas
    detector in the same frame made real G opens fail whenever the map fade or
    fog briefly weakened its legacy anchors, even though the selected floor
    was already unambiguous.  A strong selector therefore becomes an
    independent map-open edge; it still cannot authorize any aligned pixels.
    Current-frame route geometry remains the separate display gate.
    """
    floor = floor_read or {}
    selected = floor.get("selectedFloor")
    if selected not in (-1, 1, 2):
        return False
    if evidence.get("detected"):
        return True
    localized_triplet = bool(
        floor.get("layout") == "localized-triplet"
        and float(floor.get("margin", 0.0)) >= 8.0
        and float(evidence.get("backdropRatio", 0.0)) >= 0.80
    )
    if localized_triplet:
        return True
    # During the real F1 map fade, the canvas texture can be non-flat while
    # the map-only UI remains unambiguous.  Accept that combination only when
    # shape, all stable neutral anchors, and the localized three-button floor
    # triplet agree.  This is still an open-edge signal, never route-display
    # authority; the current-frame residual gate remains downstream.
    return bool(
        evidence.get("shapeAccepted") is True
        and evidence.get("layoutFamily") == "wide"
        and int(evidence.get("matchedAnchors", 0) or 0) >= 4
        and int(evidence.get("neutralMatchedAnchors", 0) or 0) >= 4
        and floor.get("layout") == "localized-triplet"
        and len(floor.get("centers") or ()) == 3
        and float(floor.get("margin", 0.0) or 0.0) >= 12.0
        and float(evidence.get("backdropRatio", 0.0) or 0.0) >= 0.72
    )


def locked_map_canvas_without_floor_is_authorized(
    evidence: dict,
    floor_read: dict | None,
    *,
    round_locked: bool,
) -> bool:
    """Let a locked round open on strong canvas proof before buttons settle.

    The floor highlight is useful for selecting the layer, but it is not the
    only proof that the map is open. Once map identity is locked, a detected,
    flat, nearly full-strength map canvas may temporarily reuse the remembered
    floor. Normal floor probes can replace it as soon as the highlight paints.
    """
    return bool(
        round_locked
        and evidence.get("detected")
        and evidence.get("flatMapCanvas")
        # Live map fades measured 0.83-0.87 while the old 0.94 fallback
        # rejected them.  This branch is locked-round-only and does not bypass
        # the later rendered-profile residual gate, so 0.80 safely recognizes
        # the open edge without granting a pose.
        and float(evidence.get("backdropRatio", 0.0)) >= 0.80
        and (floor_read or {}).get("selectedFloor") not in (-1, 1, 2)
    )


def resolve_runtime_floor(observed: int | None, manual: str | None) -> tuple[str, str]:
    if manual in {"1", "2", "B1"}:
        return manual, "manual"
    if observed == -1:
        return "B1", "automatic"
    if observed in (1, 2):
        return str(observed), "automatic"
    return "2", "manual-required"


def stable_floor_from_observations(
    observations: list[int | None],
    *,
    strong_latest: bool = False,
) -> int | None:
    """Use the latest valid detector reading without waiting for more frames.

    Earlier observations never fill an unknown current reading. The keyword
    remains compatible with callers; detector validity is the confirmation.
    """
    latest = observations[-1] if observations else None
    return latest if latest in (-1, 1, 2) else None


def map_closed_after_probe_misses(previous_misses: int, probe_open: bool, required: int = 2) -> tuple[int, bool]:
    """Debounce the lightweight map-state probe so one imperfect frame cannot hide the sheet."""
    misses = 0 if probe_open else previous_misses + 1
    return misses, misses >= required


def map_probe_transition(
    map_is_open: bool,
    previous_misses: int,
    probe_open: bool | None,
    *,
    close_allowed: bool = True,
    required_misses: int = 2,
) -> tuple[int, str | None]:
    """Use passive vision only to maintain an already-open map cycle.

    Opening is intentionally edge-triggered by the configured map key.  A
    cached closing frame must never be able to create a new visible cycle.
    """
    if not map_is_open:
        return 0, None
    # A capture/detector exception is indeterminate, not evidence that the
    # player closed the map.  Likewise, do not let the passive maintenance
    # probe tear down a current-frame alignment while it is still producing a
    # verified replacement. Explicit G/mouse-close edges remain immediate.
    if probe_open is None:
        return previous_misses, None
    if not close_allowed and not probe_open:
        return 0, None
    misses, closed = map_closed_after_probe_misses(
        previous_misses, probe_open, required=max(1, int(required_misses)),
    )
    return misses, "close" if closed else None


def map_hotkey_transition(
    map_is_open: bool,
    capture_in_progress: bool,
    silent_capture: bool = False,
) -> str:
    """Treat the map key as verified wake-up, never proof that a map opened."""
    if map_is_open:
        return "close"
    if capture_in_progress:
        return "cancel-silent" if silent_capture else "ignore"
    return "verify"


def global_key_is_allowed(game_hwnd: int | None, foreground_hwnd: int | None) -> bool:
    """Global shortcuts are inert unless the bound game is the foreground app."""
    return bool(game_hwnd and foreground_hwnd and int(game_hwnd) == int(foreground_hwnd))


def trusted_foreground_is_allowed(
    game_hwnd: int | None,
    foreground_hwnd: int | None,
    assistant_pid: int | None,
    foreground_pid: int | None,
) -> bool:
    """Keep UI alive while either the game or this assistant owns foreground.

    Assistant-owned popups (notably the ttk route combobox) can briefly become
    the foreground window even though the game remains directly underneath.
    They are safe for display, but do not authorize global game shortcuts.
    """
    return bool(
        global_key_is_allowed(game_hwnd, foreground_hwnd)
        or (
            foreground_hwnd
            and assistant_pid
            and foreground_pid
            and int(assistant_pid) == int(foreground_pid)
        )
    )


def ranked_event_is_current(event_token: int | None, capture_generation: int) -> bool:
    return event_token is None or event_token == capture_generation


def display_event_is_current(event_cycle: int | None, active_cycle: int | None) -> bool:
    """Only the active map-open cycle may change a visible route surface."""
    return event_cycle is None or (active_cycle is not None and event_cycle == active_cycle)


def confirmed_overlay_source_is_allowed(
    confirmed_map_id: str | None,
    candidate_map_id: str | None,
) -> bool:
    """A human-confirmed round may only display that exact map's layer."""
    return bool(
        confirmed_map_id
        and candidate_map_id
        and confirmed_map_id == candidate_map_id
    )
