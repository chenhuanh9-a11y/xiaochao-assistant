"""Data-driven front-door group competition and member state.

The entrance index has two different kinds of evidence: ``frontDoorLayouts``
classifies the human-facing topology, while ``ambiguousGroups`` defines the
reviewed comparison groups whose membership is safe to use for fallback.  The
latter is the membership authority; maps not in a reviewed group remain
singletons.  This module still deliberately does not authorize route pixels;
the daily assistant owns current-frame geometry validation.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_INDEX = RESOURCE_ROOT / "references" / "nightmare-entry-recognition-index.json"
GROUP_LOCK_MINIMUM_SCORE = 80.0
GROUP_LOCK_MINIMUM_MARGIN = 10.0
STABLE_MEMBER_MARGIN = 3.0


@dataclass(frozen=True)
class FrontDoorGroup:
    group_id: str
    member_ids: tuple[str, ...]
    layout: str | None = None
    fallback_member_ids: tuple[str, ...] = ()
    source_id: str | None = None
    aligned_window: tuple[int, int, int, int] = ()
    difference_boxes: tuple[tuple[int, int, int, int], ...] = ()
    clean_sources: tuple[dict, ...] = ()

    def comparison_source(self, member_id: str) -> dict | None:
        return next((row for row in self.clean_sources if row.get("mapId") == member_id), None)

    def window_to_recognition(self, member_id: str) -> np.ndarray | None:
        """Map the reviewed clean comparison grid into this member's legacy layer.

        The generator aligns rounded clean door anchors, not legacy pixels.
        Preserve its component/route bounds so unequal legacy scales are
        converted explicitly in both the difference probe and sibling seed.
        """
        row = self.comparison_source(member_id)
        if row is None or len(self.aligned_window) != 4:
            return None
        try:
            cx0, cy0, cx1, cy1 = map(float, row["cleanComponentBox"])
            rx0, ry0, rx1, ry1 = map(float, row["recognitionComponentBox"])
            ax, ay = map(float, row["frontDoorAnchor"])
            if not (cx1 > cx0 and cy1 > cy0 and rx1 > rx0 and ry1 > ry0):
                return None
            sx, sy = (rx1-rx0)/(cx1-cx0), (ry1-ry0)/(cy1-cy0)
            wx, wy = self.aligned_window[:2]
            matrix = np.float64([
                [sx, 0, rx0 + sx*(round(ax+wx)-cx0)],
                [0, sy, ry0 + sy*(round(ay+wy)-cy0)], [0, 0, 1],
            ])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return matrix if np.isfinite(matrix).all() else None


@dataclass
class FrontDoorGroupState:
    state: str = "UNLOCKED"
    group_id: str | None = None
    provisional_member_id: str | None = None
    verified_member_id: str | None = None
    generation: int = 0
    first_evidence_at: float | None = None
    group_locked_at: float | None = None
    member_verified_at: float | None = None
    last_switch_reason: str | None = None

    def reset(self) -> None:
        self.__dict__.update(
            state="UNLOCKED", group_id=None, provisional_member_id=None,
            verified_member_id=None, generation=0, first_evidence_at=None,
            group_locked_at=None, member_verified_at=None,
            last_switch_reason=None,
        )


class FrontDoorGroupIndex:
    """Build explicit reviewed groups and perform stable group competition."""

    def __init__(self, path: Path = DEFAULT_INDEX) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != "identityv-nightmare-entry-recognition-index-v1":
            raise ValueError("front-door-group-index-schema-unsupported")
        maps = tuple(str(row.get("mapId")) for row in payload.get("maps") or [])
        if not maps or any(not map_id for map_id in maps):
            raise ValueError("front-door-group-index-map-coverage-invalid")
        layout_by_map: dict[str, str] = {}
        for layout_row in payload.get("frontDoorLayouts") or []:
            layout = str(layout_row.get("id") or "")
            for value in layout_row.get("mapIds") or ():
                map_id = str(value)
                if not layout or map_id not in maps or map_id in layout_by_map:
                    raise ValueError("front-door-group-index-layout-invalid")
                layout_by_map[map_id] = layout
        if set(layout_by_map) != set(maps):
            raise ValueError("front-door-group-index-layout-coverage-invalid")

        groups: list[FrontDoorGroup] = []
        claimed: set[str] = set()
        for row in payload.get("ambiguousGroups") or []:
            members = tuple(str(value) for value in row.get("mapIds") or ())
            source_id = str(row.get("id") or "") or None
            layout = str(row.get("layout") or "")
            if (
                not layout or not members
                or any(value not in maps for value in members)
                or any(layout_by_map.get(value) != layout for value in members)
            ):
                raise ValueError("front-door-group-index-member-invalid")
            if claimed.intersection(members):
                raise ValueError("front-door-group-index-overlap-invalid")
            compact = {
                frozenset({"nightmare-04", "nightmare-12"}): "front-tl-04-12",
                frozenset({"nightmare-03", "nightmare-09"}): "front-tr-03-09",
                frozenset({"nightmare-07", "nightmare-08"}): "front-lr-07-08",
            }.get(frozenset(members), source_id or "front-reviewed-group")
            groups.append(FrontDoorGroup(
                compact,
                members,
                layout=layout,
                fallback_member_ids=members,
                source_id=source_id,
                aligned_window=tuple(
                    int(value) for value in row.get("alignedWindow") or ()
                    if len(row.get("alignedWindow") or ()) == 4
                ),
                difference_boxes=tuple(
                    tuple(int(value) for value in box)
                    for box in row.get("differenceBoxes") or ()
                    if len(box) == 4
                ),
                clean_sources=tuple(dict(value) for value in row.get("cleanSources") or ()),
            ))
            claimed.update(members)
        for map_id in maps:
            if map_id not in claimed:
                suffix = map_id.rsplit("-", 1)[-1]
                groups.append(FrontDoorGroup(
                    f"front-single-{suffix}", (map_id,),
                    layout=layout_by_map[map_id],
                    fallback_member_ids=(map_id,),
                ))
        self.groups = tuple(groups)
        self.by_id = {
            member: group
            for group in self.groups
            for member in group.member_ids
        }
        if set(self.by_id) != set(maps):
            raise ValueError("front-door-group-index-map-coverage-invalid")

    def group_for(self, map_id: str | None) -> FrontDoorGroup | None:
        return self.by_id.get(str(map_id or ""))

    @staticmethod
    def _score(row: dict | None) -> float:
        try:
            return float((row or {}).get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def compete(
        self,
        rows: Iterable[dict],
        *,
        stage: str,
        anchor_supported: bool,
    ) -> dict:
        by_id = {
            str(row.get("mapId") or ""): row
            for row in rows
            if isinstance(row, dict) and str(row.get("mapId") or "") in self.by_id
        }
        competitions = []
        for order, group in enumerate(self.groups):
            members = sorted(
                (
                    {"mapId": member, "score": self._score(by_id.get(member))}
                    for member in group.member_ids
                ),
                key=lambda row: (-row["score"], group.member_ids.index(row["mapId"])),
            )
            competitions.append({
                "groupId": group.group_id,
                "layout": group.layout,
                "memberIds": list(group.member_ids),
                "score": float(members[0]["score"] if members else 0.0),
                "provisionalMemberId": members[0]["mapId"] if members else None,
                "alignedWindow": list(group.aligned_window),
                "rankedMembers": members,
                "differenceBoxes": [list(box) for box in group.difference_boxes],
                "order": order,
            })
        competitions.sort(key=lambda row: (-row["score"], row["order"]))
        top = competitions[0] if competitions else {}
        second = competitions[1] if len(competitions) > 1 else {}
        margin = float(top.get("score", 0.0) - second.get("score", 0.0))
        locked = bool(
            anchor_supported
            and str(stage) != "door-anchor-only"
            and float(top.get("score", 0.0)) >= GROUP_LOCK_MINIMUM_SCORE
            and margin >= GROUP_LOCK_MINIMUM_MARGIN
        )
        return {
            "locked": locked,
            "groupId": top.get("groupId") if locked else None,
            "provisionalMemberId": top.get("provisionalMemberId") if locked else None,
            "groupLayout": top.get("layout") if locked else None,
            "score": float(top.get("score", 0.0)),
            "margin": margin,
            "minimumScore": GROUP_LOCK_MINIMUM_SCORE,
            "minimumMargin": GROUP_LOCK_MINIMUM_MARGIN,
            "lockReason": "group-score-margin" if locked else None,
            "stage": str(stage),
            "competitions": competitions,
            "candidateOnly": True,
            "currentFrameRouteResidualRequired": True,
            "authorizesDirectDisplay": False,
        }

    def transition(
        self,
        state: FrontDoorGroupState,
        competition: dict,
        *,
        now: float | None = None,
        member_verified: bool = False,
        verified_member_id: str | None = None,
    ) -> dict:
        timestamp = time.monotonic() if now is None else float(now)
        if state.first_evidence_at is None and competition.get("competitions"):
            state.first_evidence_at = timestamp
        locked_group = str(competition.get("groupId") or "")
        provisional = str(competition.get("provisionalMemberId") or "")
        events = []
        competition_group_id = str(competition.get("groupId") or "")
        competition_still_locks_state = bool(
            competition.get("locked")
            and competition_group_id
            and competition_group_id == state.group_id
        )
        group_lost = False
        if (
            state.state in {"GROUP_LOCKED", "MEMBER_VERIFIED"}
            and not competition_still_locks_state
        ):
            group_lost = True
            previous_group = state.group_id
            previous_member = state.provisional_member_id
            state.reset()
            events.append({
                "event": "front-group-unlocked",
                "previousGroupId": previous_group,
                "previousMemberId": previous_member,
                "reason": "current-frame-group-competition-lost",
            })
        if state.state == "UNLOCKED" and competition.get("locked") and not group_lost:
            state.state = "GROUP_LOCKED"
            state.group_id = locked_group or None
            state.provisional_member_id = provisional or None
            state.generation += 1
            state.group_locked_at = timestamp
            events.append({
                "event": "front-group-locked",
                "groupId": state.group_id,
                "provisionalMemberId": state.provisional_member_id,
                "groupLockLatencyMs": round(
                    max(0.0, timestamp - (state.first_evidence_at or timestamp)) * 1000.0, 2,
                ),
            })
            events.append({
                "event": "front-provisional-member",
                "groupId": state.group_id,
                "memberId": state.provisional_member_id,
                "groupGeneration": state.generation,
                "candidateOnly": True,
                "currentFrameRouteResidualRequired": True,
            })
        elif state.state == "GROUP_LOCKED":
            current_score = next(
                (float(row.get("score", 0.0)) for row in competition.get("competitions", [])
                 if row.get("groupId") == state.group_id),
                0.0,
            )
            current_members = next(
                (row.get("rankedMembers") or [] for row in competition.get("competitions", [])
                 if row.get("groupId") == state.group_id),
                [],
            )
            best = str((current_members[0] if current_members else {}).get("mapId") or "")
            best_score = float((current_members[0] if current_members else {}).get("score", 0.0))
            incumbent_score = next(
                (float(row.get("score", 0.0)) for row in current_members
                 if row.get("mapId") == state.provisional_member_id),
                0.0,
            )
            if (
                best and best != state.provisional_member_id
                and best_score - incumbent_score >= STABLE_MEMBER_MARGIN
            ):
                previous = state.provisional_member_id
                state.provisional_member_id = best
                state.generation += 1
                state.last_switch_reason = "same-group-member-current-frame-margin"
                events.append({
                    "event": "front-member-switch",
                    "groupId": state.group_id,
                    "fromMemberId": previous,
                    "toMemberId": best,
                    "switchReason": state.last_switch_reason,
                    "switchMargin": round(best_score - incumbent_score, 3),
                    "groupGeneration": state.generation,
                    "currentScore": current_score,
                })
        if (
            state.state == "GROUP_LOCKED"
            and member_verified
            and verified_member_id
            and str(verified_member_id) == state.provisional_member_id
        ):
            state.state = "MEMBER_VERIFIED"
            state.verified_member_id = str(verified_member_id)
            state.member_verified_at = timestamp
            events.append({
                "event": "front-member-verified",
                "groupId": state.group_id,
                "memberId": state.verified_member_id,
                "groupGeneration": state.generation,
                "memberVerificationLatencyMs": round(
                    max(0.0, timestamp - (state.group_locked_at or timestamp)) * 1000.0, 2,
                ),
            })
        return {
            "state": state.state,
            "groupId": state.group_id,
            "provisionalMemberId": state.provisional_member_id,
            "verifiedMemberId": state.verified_member_id,
            "groupGeneration": state.generation,
            "events": events,
        }
