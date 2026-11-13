"""User-confirmed F1 front-entry ambiguity groups, separate from local cohorts.

Membership was confirmed against the current client-resource comparison boards.
There are deliberately no side-entry groups and no inferred/expanded members.
"""
from __future__ import annotations

import hashlib
import json

from season_context import SeasonContext


CONFIRMED_FRONT_GROUPS = {
    "nightmare": (("04", "12", "19"), ("03", "09"), ("07", "08", "20")),
    "difficult": (("08", "25"), ("03", "10"), ("04", "21"),
                  ("20", "26"), ("05", "09-2")),
}


class ConfirmedFrontGroups:
    """Explicit entrance-scoped registry bound to the production typed provider.

    A group is discoverable only from an F1 front observation. Once established,
    the runtime owns its round/member state across subsequent floor changes.
    """
    condition_index = None
    error = None

    def __init__(self, pack, *, season="s0"):
        self.context = SeasonContext(season=season, mode=pack.mode)
        self.mode = pack.mode
        self.map_ids = frozenset(pack.by_id)
        prefix = "nightmare-" if self.mode == "nightmare" else "difficult-map-"
        # The reviewed legacy membership is S0-only, even if new maps reuse IDs.
        memberships = CONFIRMED_FRONT_GROUPS[self.mode] if season == "s0" else ()
        self.groups = tuple(dict(
            groupId=f"confirmed-front:{self.mode}:" + "-".join(numbers),
            mode=self.mode, entranceKind="front", floor="1",
            memberIds=tuple(prefix + number for number in numbers),
        ) for numbers in memberships)
        identity = self.groups if season == "s0" else (self.context.key, self.groups)
        self.index_digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def group_for(self, map_id, *, floor, entrance_kind):
        if str(floor) != "1" or entrance_kind != "front":
            return None
        return next((group for group in self.groups if map_id in group["memberIds"]), None)

    def validate(self, group, provider):
        """Check real typed F1 bindings; group membership grants no pose authority."""
        if group not in self.groups or not set(group["memberIds"]) <= self.map_ids:
            raise ValueError("confirmed-front-group-members-unavailable")
        if provider is None:
            raise ValueError("confirmed-front-group-provider-unavailable")
        for map_id in group["memberIds"]:
            reference = provider("shared-structure", map_id, "1")
            metadata = getattr(reference, "metadata", {}) if reference is not None else {}
            binding = metadata.get("binding") or {}
            if (reference is None or binding.get("mapId") != map_id
                    or str(binding.get("floor")) != "1"
                    or not metadata.get("structureVersionKey")):
                raise ValueError(f"confirmed-front-group-binding-unavailable:{map_id}/1")
