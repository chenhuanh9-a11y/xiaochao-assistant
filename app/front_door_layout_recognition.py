from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from front_door_recognition import NightmareFrontDoorRecognizer
from nightmare_candidate import NightmarePack


SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_MAP_PACK = ROOT / "references" / "nightmare-map-pack.json"
LAYOUT_LABELS = {
    "top-left-right": "三门：上＋左＋右",
    "top-left": "两门：上＋左",
    "top-right": "两门：上＋右",
    "left-right": "两门：左＋右",
}
LAYOUT_ORDER = tuple(LAYOUT_LABELS)


class NightmareFrontDoorLayoutRecognizer:
    """Four-way front-door grouping that can only produce shadow evidence.

    The classifier deliberately consumes the registered per-map shadow scores
    instead of inventing a second image matcher.  It compares the best supported
    map in each human-reviewed door-layout group, rejects door-anchor-only views,
    and keeps all 13 maps whenever the group margin is weak.
    """

    MINIMUM_SCORE = 80.0
    MINIMUM_MARGIN = 10.0
    REJECTED_STAGES = {"door-anchor-only"}

    def __init__(
        self,
        pack: NightmarePack | None = None,
        map_pack_path: Path = DEFAULT_MAP_PACK,
        identity_recognizer: NightmareFrontDoorRecognizer | None = None,
    ) -> None:
        self.pack = pack or NightmarePack()
        payload = json.loads(Path(map_pack_path).read_text(encoding="utf-8"))
        rows = payload.get("maps") or []
        self.map_layouts = {
            str(row["id"]): str(row.get("frontDoorLayout") or "")
            for row in rows
        }
        if set(self.map_layouts) != set(self.pack.by_id):
            raise ValueError("front-door-layout-map-coverage-invalid")
        if set(self.map_layouts.values()) != set(LAYOUT_ORDER):
            raise ValueError("front-door-layout-group-coverage-invalid")
        self.layout_maps = {
            layout: tuple(
                map_id for map_id in self.pack.by_id
                if self.map_layouts[map_id] == layout
            )
            for layout in LAYOUT_ORDER
        }
        if sorted(len(value) for value in self.layout_maps.values()) != [3, 3, 3, 4]:
            raise ValueError("front-door-layout-group-size-invalid")
        self.identity_recognizer = (
            identity_recognizer
            if identity_recognizer is not None
            else NightmareFrontDoorRecognizer(self.pack)
        )

    def rank_candidates(self, candidates: list[dict], *, stage: str) -> dict:
        by_map = {
            str(row.get("mapId")): row
            for row in candidates
            if str(row.get("mapId") or "") in self.map_layouts
        }
        groups = []
        for layout in LAYOUT_ORDER:
            rows = sorted(
                (
                    {
                        "mapId": map_id,
                        "score": float((by_map.get(map_id) or {}).get("score", 0.0)),
                    }
                    for map_id in self.layout_maps[layout]
                ),
                key=lambda row: row["score"],
                reverse=True,
            )
            groups.append({
                "layout": layout,
                "label": LAYOUT_LABELS[layout],
                "score": float(rows[0]["score"] if rows else 0.0),
                "supportMapId": rows[0]["mapId"] if rows else None,
                "mapIds": list(self.layout_maps[layout]),
                "rankedMaps": rows,
            })
        groups.sort(key=lambda row: row["score"], reverse=True)
        top = groups[0]
        second = groups[1]
        margin = float(top["score"] - second["score"])
        stage_rejected = str(stage) in self.REJECTED_STAGES
        confident = bool(
            not stage_rejected
            and float(top["score"]) >= self.MINIMUM_SCORE
            and margin >= self.MINIMUM_MARGIN
        )
        detected = str(top["layout"]) if confident else "unknown"
        candidate_map_ids = (
            list(self.layout_maps[detected])
            if confident else list(self.pack.by_id)
        )
        return {
            "success": True,
            "reason": (
                "front-door-layout-shadow-ranked"
                if confident else
                "front-door-layout-unknown"
            ),
            "stage": str(stage),
            "layoutCandidate": str(top["layout"]),
            "detectedLayout": detected,
            "label": LAYOUT_LABELS.get(detected, "门型未知"),
            "score": float(top["score"]),
            "margin": margin,
            "confident": confident,
            "candidateMapIds": candidate_map_ids,
            "groups": groups,
            "automaticMapSubmissionAllowed": False,
            "routeDisplayAllowed": False,
            "runtimePolicy": "development-shadow-layout-only",
        }

    def rank_normalized_mask(self, query: np.ndarray, *, limit: int = 13) -> dict:
        identity = self.identity_recognizer.rank_normalized_mask(query, limit=limit)
        layout = self.rank_candidates(
            list(identity.get("candidates") or []),
            stage=str(identity.get("stage") or "door-anchor-only"),
        )
        return {
            **layout,
            "identityShadow": {
                "candidateMapId": identity.get("candidateMapId"),
                "score": float(identity.get("score", 0.0)),
                "margin": float(identity.get("margin", 0.0)),
                "strongSuggestion": bool(identity.get("strongSuggestion")),
                "displayAllowed": False,
            },
        }
