"""Source-owned S1 composition and scoped cumulative-evidence capabilities.

Settings, action payloads, installation-profile feature fields and environment
variables cannot opt into production. A production rollout requires a deliberate
reviewed source change after final resource/install contracts have been verified.
Resources, capture freshness, current floor, fitted geometry, material ACK and
display ownership retain their independent runtime checks. Neither purpose asserts
liveReady or liveValidated. The separate ledger capability still requires positive
coordinate evidence and fresh single-candidate material to lock.
"""
import json
from pathlib import Path
import sys

_NATIVE_S1_PREVIEW = object()
_NATIVE_S1_PLAYTEST = object()
_NATIVE_S1_PRODUCTION_CANDIDATE = object()

# Exact Windows native inventory, guarded repair, author-display dependencies
# and five-context self-check were verified for the formal 1.0.17 candidate.
# The separate source-owned builder lock must still match clean source/version
# and complete resource bytes. Formal cumulative evidence does not enable private
# diagnostic tracing or assert live identity calibration.
_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED = True


def private_native_candidate_purpose():
    from packaged_product_identity import baked_identity
    if baked_identity(require_qt=True) != "qt-isolated":
        return None
    profile = json.loads((Path(sys._MEIPASS) / "installation-profile.json").read_text("utf8"))
    if profile["installerInstance"]["id"] not in ("s1-auto-test", "s1-playtest", "feature-test"):
        return None
    return (_NATIVE_S1_PLAYTEST if profile["installerInstance"]["id"] in ("s1-playtest", "feature-test")
            else _NATIVE_S1_PREVIEW)


def production_native_candidate_purpose():
    """Only the exact baked formal identity may receive the candidate purpose.

    The validated production AppId is D04394A0-DACE-4FF9-874D-3DCB43EE53D1.
    The runtime separately validates scoped cumulative evidence; no private tracing.
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
    return value is _NATIVE_S1_PREVIEW or value is _NATIVE_S1_PLAYTEST


def native_final_round_lock_purpose_is_valid(value):
    """Playtest and feature-test use production round lifetime, independently of tracing.

    The older auto-test token remains an isolated evidence experiment. Neither
    a profile feature field nor a setting may weaken a playtest round lock.
    """
    return (value is _NATIVE_S1_PLAYTEST or
            _PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED is True
            and value is _NATIVE_S1_PRODUCTION_CANDIDATE)


def native_candidate_purpose_is_valid(value):
    return (private_native_candidate_purpose_is_valid(value)
            or (_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED is True
                and value is _NATIVE_S1_PRODUCTION_CANDIDATE))


def native_round_lock_purpose_is_valid(value):
    """Source-owned cumulative evidence capability, separate from tracing.

    Permission enables the scoped ledger, not a map verdict. Each exclusion
    still requires the common-coordinate comparator and each lock fresh material.
    """
    return native_candidate_purpose_is_valid(value)
