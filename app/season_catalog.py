"""Editable season display catalog, bundled as an ordinary imported module.

Add a unique s<number> entry to expose a future season without editing QML.
Names must come from confirmed product information; blanks display the number.
Resource status is descriptive only. It never grants a provider access to assets.
"""
SEASON_DEFINITIONS = (
    {"id": "s0", "number": 0, "name": "", "resources": {
        "nightmare": "legacy-s0", "difficult": "legacy-s0"}},
    {"id": "s1", "number": 1, "name": "", "resources": {
        "nightmare": "native-s1", "difficult": "native-s1", "difficulty2": "native-s1"}},
)
