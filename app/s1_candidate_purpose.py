"""Trusted private composition policy; settings and payloads never mint grants.

This token permits only native current-frame S1 candidate work. Resources,
capture freshness, fitted geometry, material ACK and display ownership retain
their separate checks. It is not liveReady or an identity-lock capability.
"""
import json
from pathlib import Path
import sys

_NATIVE_S1_PREVIEW = object()


def private_native_candidate_purpose():
    from packaged_product_identity import baked_identity
    if baked_identity(require_qt=True) != "qt-isolated":
        return None
    profile = json.loads((Path(sys._MEIPASS) / "installation-profile.json").read_text("utf8"))
    if profile["installerInstance"]["id"] != "s1-auto-test":
        return None
    return _NATIVE_S1_PREVIEW


def native_candidate_purpose_is_valid(value):
    return value is _NATIVE_S1_PREVIEW
