"""Canonical numeric display names for S0 maps while preserving internal map IDs.

Internal map IDs (e.g. ``nightmare-01``, ``33956dfb-...``, ``nightmare-layout-104026``,
``difficult-01``) are referenced by structure packages, author packs, custom galleries,
and Route Plaza submissions. Only user-facing display labels are normalized to numbers
so all existing packages and saved routes remain 100% compatible.
"""
from __future__ import annotations

import re

# Explicit numeric mapping for legacy S0 nightmare maps that have UUID or layout IDs.
_S0_NIGHTMARE_EXPLICIT_NUMBERS = {
    "33956dfb-8e0d-4627-a26c-e1904d687084": 22,
    "4f5d6ac0-b1ae-4338-8d6e-b67cd1e3af51": 23,
    "37d291a1-529d-4a0e-8938-e4754e5f7a0d": 24,
    "nightmare-layout-104026": 25,
    "nightmare-layout-104027": 26,
}

_S0_ID_NUMBER_RE = re.compile(r"^(?:nightmare|difficult|hard)-(?:layout-)?0*(\d+)$", re.IGNORECASE)


def s0_numeric_index(map_id: str) -> int | None:
    """Return the canonical numeric index for an S0 map ID, or None if not an S0 ID."""
    mid = str(map_id or "").strip()
    if not mid:
        return None
    if mid in _S0_NIGHTMARE_EXPLICIT_NUMBERS:
        return _S0_NIGHTMARE_EXPLICIT_NUMBERS[mid]
    match = _S0_ID_NUMBER_RE.match(mid)
    if match:
        return int(match.group(1))
    return None


def format_map_display_name(
    map_id: str,
    fallback_name: str = "",
    *,
    season: str = "s0",
    mode: str = "",
) -> str:
    """Format a user-facing map display name.

    - For ``season == 's0'`` with a recognized S0 map ID, returns a clean numeric label
      such as ``01号图`` .. ``26号图`` (or ``<num>号图`` for large layout IDs).
    - For S1/S2+ or synthetic test fixture IDs, preserves ``fallback_name`` (or ``map_id``).
    """
    del mode  # reserved for future mode-specific formatting if needed
    mid = str(map_id or "").strip()
    fallback = str(fallback_name or "").strip()
    if str(season or "").strip().lower() == "s0":
        num = s0_numeric_index(mid)
        if num is not None:
            return f"{num:02d}号图" if num < 100 else f"{num}号图"
    return fallback or mid
