"""Resource selection for the structure/editor-only Windows Setup profile.

Operate on an authorized resource inventory; never discover private map data.
The editor gallery and helper are separate, EXE-adjacent build inputs.
"""
from __future__ import annotations

from pathlib import PurePosixPath

LIGHT_PROFILE = "structure-editor-light"
AUTHOR_DISPLAY_PREFIXES = (
    "assets/suju/",
    "assets/zhan10-native/",
    "assets/zhan10-legacy-clean/",
    "assets/authors/",
)
SOFTWARE_DELIVERY_FORMAT = "setup"


def partition_resources(inventory):
    """Return retained/omitted rows without changing hashes or resource IDs.

    Input paths are relative to _internal, as in the authorized resource lock.
    Only image directories are omitted; catalogs, pure structure, registration
    metadata, native-similarity resources and precomputed data stay unchanged.
    """
    retained, omitted = {}, {}
    for name, row in inventory.items():
        path = PurePosixPath(name)
        if (not isinstance(name, str) or "\\" in name or ":" in name
                or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or name in ("", ".")):
            raise ValueError("Expected a normalized relative resource path")
        target = omitted if name.startswith(AUTHOR_DISPLAY_PREFIXES) else retained
        target[name] = row
    return retained, omitted
