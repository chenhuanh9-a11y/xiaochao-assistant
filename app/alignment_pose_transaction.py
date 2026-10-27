"""Commit coordinator for the V2 display/pose commit.

The transaction owns only the compatibility projections which the V2 writer
updates together.  Recognition and adjudication remain outside this module.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class AlignmentCommitContext:
    cycle_id: object
    map_id: str
    floor: str
    frame_generation: int
    surface_generation: int
    drag_offset: tuple
    zoom_transform: tuple
    preview_offset: tuple


@dataclass
class AlignmentPoseTransaction:
    """Snapshot, context guard, and ordered group/core commit phases.

    This is deliberately not a claim of full atomicity: later binding,
    sibling, tracker, and compositor phases have their own existing failure
    semantics after the trusted core commit.
    """

    owner: object
    core: object
    map_id: str
    floor: str
    authorization: object
    context: AlignmentCommitContext
    front_sibling_context_current: bool
    group_before: object
    pose_before: object
    anchors_before: object
    tracker_store_before: object
    tracker_alias_before: object
    tracker_key_before: object

    def coordinate_group_and_core(
        self, *, commit_group, commit_core, cache_candidate,
        revoke_display, hide_overlay, hide_route,
    ) -> str:
        """Run the two commit phases which must precede all display side effects.

        The caller supplies already-validated operations.  ``group`` is
        rollback-safe; after ``core`` succeeds the trusted pose is retained
        on later compositor/display failures, matching the pre-refactor
        production behavior.
        """
        if not commit_group() or not self.is_current():
            cache_candidate("v2-commit-context-stale")
            self.rollback()
            revoke_display("v2-commit-context-stale")
            hide_overlay()
            hide_route("alignment-v2-commit-context-stale")
            return "group-context-stale"
        if not commit_core() or not self.is_current():
            cache_candidate("v2-atomic-commit-failed")
            self.rollback()
            revoke_display("v2-atomic-commit-failed")
            hide_overlay()
            hide_route("alignment-v2-atomic-commit-failed")
            return "core-context-stale"
        return "core-committed"

    @classmethod
    def capture(
        cls,
        owner: object,
        core: object,
        *,
        map_id: str,
        floor: str,
        authorization: object,
        front_sibling_context_current: bool,
    ) -> "AlignmentPoseTransaction":
        context = AlignmentCommitContext(
            cycle_id=getattr(owner, "active_map_cycle_id", None),
            map_id=str(map_id), floor=str(floor),
            frame_generation=int(getattr(owner, "current_frame_generation", 0)),
            surface_generation=int(getattr(owner, "surface_visibility_generation", 0)),
            drag_offset=tuple(owner._active_open_cycle_drag_offset(str(map_id))),
            zoom_transform=tuple(owner._active_open_cycle_zoom_transform(str(map_id)).ravel()),
            preview_offset=tuple(owner._active_mouse_drag_preview_offset()),
        )
        return cls(
            owner=owner, core=core, map_id=str(map_id), floor=str(floor),
            authorization=authorization, context=context,
            front_sibling_context_current=bool(front_sibling_context_current),
            group_before=copy.deepcopy(getattr(owner, "round_layer_group", None)),
            pose_before=copy.deepcopy(core.pose_by_map),
            anchors_before=copy.deepcopy(core.anchor_pools),
            tracker_store_before=copy.deepcopy(getattr(owner, "recovery_anchor_trackers", {})),
            tracker_alias_before=copy.deepcopy(getattr(owner, "recovery_anchor_tracker", None)),
            tracker_key_before=getattr(owner, "recovery_anchor_tracker_key", None),
        )

    def is_current(self) -> bool:
        owner = self.owner
        core = self.core
        gate_confirmed_id = str(getattr(getattr(owner, "gate", None), "confirmed_id", ""))
        return bool(
            self.context.cycle_id == getattr(owner, "active_map_cycle_id", None)
            and (
                self.context.map_id == gate_confirmed_id
                or (
                    self.front_sibling_context_current
                    and gate_confirmed_id in {
                        str(self.map_id),
                        str((getattr(owner, "front_group_sibling_pending", None) or {}).get(
                            "fromMemberId", "",
                        )),
                    }
                )
            )
            and self.context.floor == str(getattr(owner, "current_floor", ""))
            and self.context.frame_generation == int(getattr(owner, "current_frame_generation", 0))
            and self.context.surface_generation == int(getattr(owner, "surface_visibility_generation", 0))
            and self.context.drag_offset == tuple(owner._active_open_cycle_drag_offset(self.map_id))
            and self.context.zoom_transform == tuple(owner._active_open_cycle_zoom_transform(self.map_id).ravel())
            and self.context.preview_offset == tuple(owner._active_mouse_drag_preview_offset())
            and bool(getattr(owner, "map_is_open", False))
            and not bool(getattr(owner, "map_visual_suppressed", False))
            and core.can_display(
                authorization=self.authorization,
                cycle_id=self.context.cycle_id, map_id=self.context.map_id,
                floor=self.context.floor, frame_generation=self.context.frame_generation,
            )
        )

    def rollback(self) -> None:
        """Restore every compatibility projection written by the V2 commit."""
        owner = self.owner
        owner.round_layer_group = copy.deepcopy(self.group_before)
        self.core.pose_by_map = copy.deepcopy(self.pose_before)
        self.core.anchor_pools = copy.deepcopy(self.anchors_before)
        owner.recovery_anchor_trackers = copy.deepcopy(self.tracker_store_before)
        owner.recovery_anchor_tracker = copy.deepcopy(self.tracker_alias_before)
        owner.recovery_anchor_tracker_key = self.tracker_key_before
