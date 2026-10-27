from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from side_door_recognition import (
    anchor_descriptor,
    central_descriptor,
    descriptor_containment,
    descriptor_similarity,
    map_color_mask,
    runtime_descriptor_sampling,
    topology,
    topology_similarity,
)


@dataclass
class LiveExemplar:
    map_id: str
    map_name: str
    floor: int
    source: str
    local: np.ndarray
    wide: np.ndarray
    full: np.ndarray
    topology_value: dict | None


def _read_image(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class LiveExemplarRetriever:
    """Nearest labeled live entrance evidence, independent of exploration tint.

    Only map-colored geometry around the exit anchor is retained. Room tint is
    deliberately excluded because it changes as the player explores.
    """

    def __init__(self, registry_path: Path, workspace_root: Path, anchor_finder) -> None:
        self.exemplars: list[LiveExemplar] = []
        if not registry_path.exists():
            return
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        for session in data.get("sessions", []):
            map_id = session.get("mapId")
            if not map_id:
                continue
            examples = [
                {
                    "path": relative,
                    "floor": session.get("floor", session.get("observedFloor", 1)),
                }
                for relative in session.get("trainingFrames", [])
            ]
            # Newer sessions may contain labeled examples from both floors.
            # Keep the legacy list above so existing accepted sessions remain
            # byte-for-byte compatible, while avoiding silently labelling all
            # examples as floor 1.
            examples.extend(session.get("trainingExamples", []))
            for example in examples:
                relative = example.get("path")
                floor = int(example.get("floor", 1))
                if not relative or floor not in (1, 2):
                    continue
                path = workspace_root / Path(relative)
                image = _read_image(path)
                if image is None:
                    continue
                anchors = anchor_finder(image)
                if not anchors:
                    continue
                # Anchor finder is score-sorted. The strongest icon correlation
                # is the map exit; choosing the largest box can select a room/UI
                # fragment at a neighboring scale.
                anchor = anchors[0]
                mask = map_color_mask(image)
                step, sample_radius = runtime_descriptor_sampling(image.shape[1])
                local = anchor_descriptor(mask, anchor, 9, None, step, sample_radius)
                self.exemplars.append(LiveExemplar(
                    map_id=map_id,
                    map_name=session.get("mapName", map_id),
                    floor=floor,
                    source=str(path.resolve()),
                    local=local,
                    wide=anchor_descriptor(mask, anchor, 16, None, step, sample_radius),
                    full=anchor_descriptor(mask, anchor, 24, None, step, sample_radius),
                    topology_value=topology(local),
                ))

    @staticmethod
    def prepare_features(frame: np.ndarray, anchors: list[dict]) -> list[dict]:
        if not anchors:
            return []
        mask = map_color_mask(frame)
        step, sample_radius = runtime_descriptor_sampling(frame.shape[1])
        features = []
        for anchor in anchors[:4]:
            local = anchor_descriptor(mask, anchor, 9, None, step, sample_radius)
            features.append({
                "anchor": anchor,
                "local": local,
                "wide": anchor_descriptor(mask, anchor, 16, None, step, sample_radius),
                "full": anchor_descriptor(mask, anchor, 24, None, step, sample_radius),
                "topology": topology(local),
            })
        return features

    def recognize_frame(
        self, frame: np.ndarray, anchors: list[dict], selected_floor: int | None
    ) -> dict:
        return self.recognize(self.prepare_features(frame, anchors), selected_floor)

    def recognize(self, query_features: list[dict], selected_floor: int | None) -> dict:
        if not self.exemplars or not query_features:
            return {"success": False, "reason": "no-live-exemplar", "candidates": []}
        by_map: dict[str, dict] = {}
        for feature in query_features[:4]:
            anchor = feature["anchor"]
            query_local = feature["local"]
            query_wide = feature["wide"]
            query_full = feature["full"]
            query_topology = feature["topology"]
            for exemplar in self.exemplars:
                if selected_floor is not None and exemplar.floor != selected_floor:
                    continue
                local_score = descriptor_similarity(
                    central_descriptor(query_local), central_descriptor(exemplar.local)
                )
                wide_score = descriptor_similarity(query_wide, exemplar.wide)
                # Either view may contain more explored geometry. Bidirectional
                # containment handles both the early and later member of a pair.
                query_in_reference = descriptor_containment(query_full, exemplar.full, tolerance=2)
                reference_in_query = descriptor_containment(exemplar.full, query_full, tolerance=2)
                containment = max(query_in_reference, reference_in_query)
                topology_score = topology_similarity(query_topology, exemplar.topology_value)
                score = (
                    local_score * 0.30 + wide_score * 0.30
                    + containment * 0.28 + topology_score * 0.12
                )
                candidate = {
                    "mapId": exemplar.map_id,
                    "mapName": exemplar.map_name,
                    "floor": exemplar.floor,
                    "score": float(score),
                    "localScore": float(local_score),
                    "wideScore": float(wide_score),
                    "containmentScore": float(containment),
                    "topologyScore": float(topology_score),
                    "anchor": {key: anchor[key] for key in ("x", "y", "width", "height")},
                    "exemplar": exemplar.source,
                }
                current = by_map.get(exemplar.map_id)
                if current is None or candidate["score"] > current["score"]:
                    by_map[exemplar.map_id] = candidate
        ranked = sorted(by_map.values(), key=lambda item: item["score"], reverse=True)
        if not ranked:
            return {"success": False, "reason": "no-floor-matched-live-exemplar", "candidates": []}
        top = ranked[0]
        margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
        success = bool(top["score"] >= 0.74 and top["localScore"] >= 0.60)
        return {
            "success": success,
            "reason": "accepted-live-exemplar" if success else "live-exemplar-low-score",
            "mapId": top["mapId"], "mapName": top["mapName"], "floor": top["floor"],
            "score": top["score"], "confidence": top["score"], "margin": margin,
            "candidateCount": len(ranked), "candidates": ranked[:5],
        }
