"""Source-owned S1 current-frame composition purposes, never identity authority.

Settings, action payloads, installation-profile feature fields and environment
variables cannot opt into production. A production rollout requires a deliberate
reviewed source change after final resource/install contracts have been verified.
Resources, capture freshness, current floor, fitted geometry, material ACK and
display ownership retain their independent runtime checks. Neither purpose is
liveReady, liveValidated, or a durable identity-lock capability.
"""
import json
from pathlib import Path
import sys

_NATIVE_S1_PREVIEW = object()
_NATIVE_S1_PRODUCTION_CANDIDATE = object()

# Exact Windows native inventory, guarded repair, author-display dependencies
# and five-context self-check were verified for the formal 1.0.17 candidate.
# The separate source-owned builder lock must still match clean source/version
# and complete resource bytes. This does not enable private experiments or
# assert live identity calibration.
_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED = True


def private_native_candidate_purpose():
    from packaged_product_identity import baked_identity
    if baked_identity(require_qt=True) != "qt-isolated":
        return None
    profile = json.loads((Path(sys._MEIPASS) / "installation-profile.json").read_text("utf8"))
    if profile["installerInstance"]["id"] not in ("s1-auto-test", "s1-playtest"):
        return None
    return _NATIVE_S1_PREVIEW


def production_native_candidate_purpose():
    """Only the exact baked formal identity may receive the candidate purpose.

    The validated production AppId is D04394A0-DACE-4FF9-874D-3DCB43EE53D1.
    This deliberately does not grant private elimination experiments or tracing.
    """
    from packaged_product_identity import baked_identity
    if baked_identity(require_qt=True) != "qt-production":
        return None
    if _PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED is not True:
        return None
    return _NATIVE_S1_PRODUCTION_CANDIDATE


def packaged_native_candidate_purpose():
    """Qt composition root; private and formal identities are not interchangeable."""
    from packaged_product_identity import baked_identity
    if baked_identity(require_qt=True) == "qt-production":
        return production_native_candidate_purpose()
    return private_native_candidate_purpose()


def private_native_candidate_purpose_is_valid(value):
    """Narrow private experiment/implicit diagnostic permission; never formal."""
    return value is _NATIVE_S1_PREVIEW


def native_candidate_purpose_is_valid(value):
    return (private_native_candidate_purpose_is_valid(value)
            or (_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED is True
                and value is _NATIVE_S1_PRODUCTION_CANDIDATE))
