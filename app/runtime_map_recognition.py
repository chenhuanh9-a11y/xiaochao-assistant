from __future__ import annotations

import math
import threading
from collections import defaultdict
from pathlib import Path

import numpy as np

from full_map_retrieval import FullMapRetriever
from live_exemplar_retrieval import LiveExemplarRetriever
from side_door_recognition import SideDoorRecognizer
from spatial_outline_retrieval import SpatialOutlineRetriever


class RuntimeMapRecognizer:
    """Route each floor to the evidence model that actually has references.

    The entrance/side-door index is a first-floor index. Second-floor and
    floorless views therefore use whole-visible-map shape retrieval. Session
    evidence is retained across intermittent G presses but never bypasses the
    strict per-frame acceptance gates.
    """

    def __init__(
        self,
        assets: Path,
        vision_index: Path,
        taxonomy: Path,
        marked_poster: Path,
    ) -> None:
        # The poster-wide dense pass is retained for offline audit, but it is
        # both slow and conflict-prone on partial live frames. Runtime uses one
        # shared entrance extraction for the full index and labeled exemplars.
        self.side = SideDoorRecognizer(vision_index, taxonomy, None)
        self.full = FullMapRetriever(assets, enable_registration=False)
        workspace_root = taxonomy.resolve().parents[1]
        self.live_exemplars = LiveExemplarRetriever(
            workspace_root / "references" / "live-ground-truth.json",
            workspace_root,
            self.side.find_anchors,
        )
        self.spatial_outline = SpatialOutlineRetriever(vision_index, taxonomy)
        self._lock = threading.Lock()
        self._support: dict[str, float] = defaultdict(float)
        self._frames: dict[str, set[int]] = defaultdict(set)
        self._frame_number = 0
        self._previous_floor: int | None = None
        self._accepted_map_id: str | None = None

    @property
    def exit_icon(self) -> np.ndarray:
        return self.side.exit_icon

    @property
    def references(self):
        return self.side.references

    def find_anchors(
        self, frame: np.ndarray, allow_unusual_scales: bool = True
    ) -> list[dict]:
        return self.side.find_anchors(frame, allow_unusual_scales)

    def reset_session(self) -> None:
        """Start a new live match without rebuilding the preloaded indexes."""
        with self._lock:
            self._support.clear()
            self._frames.clear()
            self._frame_number = 0
            self._previous_floor = None
            self._accepted_map_id = None

    def _resolve_floor(self, observed_floor: int | None) -> tuple[int | None, str]:
        """Keep the last explicit floor through a momentarily ambiguous UI frame.

        The floor buttons are small and their highlight can be obscured during
        animation.  An ambiguous read must not invent a floor transition or
        make an already accepted map lose its overlay.  Only an explicit 1/2
        observation is allowed to change the retained floor.
        """
        if observed_floor in (1, 2):
            return observed_floor, "observed-floor-button"
        with self._lock:
            retained = self._previous_floor
        if retained in (1, 2):
            return retained, "session-retained-floor"
        return None, "floor-unresolved"

    @staticmethod
    def _relative_support(candidates: list[dict], temperature: float) -> list[tuple[str, float]]:
        if not candidates:
            return []
        top = float(candidates[0].get("score", 0.0))
        values = []
        for rank, candidate in enumerate(candidates[:5], 1):
            map_id = candidate.get("mapId")
            if map_id:
                score = float(candidate.get("score", 0.0))
                values.append((map_id, math.exp((score - top) / temperature) / math.sqrt(rank)))
        return values

    def _update_session(self, result: dict, selected_floor: int | None) -> dict:
        with self._lock:
            self._frame_number += 1
            frame_number = self._frame_number
            transition = None
            if (
                frame_number > 1
                and selected_floor in (1, 2)
                and self._previous_floor in (1, 2)
                and selected_floor != self._previous_floor
            ):
                transition = {
                    "fromFloor": self._previous_floor,
                    "toFloor": selected_floor,
                    "atEvidenceFrame": frame_number,
                }
            if selected_floor in (1, 2):
                self._previous_floor = selected_floor

            # Identity acceptance is strictly based on the first two valid,
            # still-open map frames. Later frames may refine registration but
            # must never rescue a late recognition failure.
            identity_window_open = frame_number <= 2
            if identity_window_open and result.get("success") and result.get("mapId"):
                if self._accepted_map_id is None:
                    self._accepted_map_id = result["mapId"]

            evidence_sets: list[tuple[float, list[tuple[str, float]]]] = []
            if result.get("markedEvidence") is not None:
                evidence_sets.append((0.55, self._relative_support(result.get("candidates", []), 0.06)))
                marked = result.get("markedEvidence") or {}
                evidence_sets.append((0.45, self._relative_support(marked.get("candidates", []), 0.06)))
            else:
                full = result.get("fullMapEvidence") or {}
                evidence_sets.append((0.70, self._relative_support(full.get("candidates", []), 0.06)))
            live = result.get("liveExemplarEvidence") or {}
            if live.get("candidates"):
                evidence_sets.append((0.90, self._relative_support(live.get("candidates", []), 0.045)))
            if identity_window_open:
                for source_weight, values in evidence_sets:
                    for map_id, value in values:
                        self._support[map_id] += source_weight * value
                        self._frames[map_id].add(frame_number)

            ranked = sorted(self._support.items(), key=lambda item: item[1], reverse=True)
            public = [
                {
                    "mapId": map_id,
                    "support": support,
                    "evidenceFrames": len(self._frames[map_id]),
                }
                for map_id, support in ranked[:5]
            ]
            margin = public[0]["support"] - public[1]["support"] if len(public) > 1 else 0.0
            ratio = public[0]["support"] / max(public[1]["support"], 1e-9) if len(public) > 1 else 0.0
            return {
                "candidateMapId": public[0]["mapId"] if public else None,
                "supportMargin": margin,
                "supportRatio": ratio,
                "evidenceFrameCount": frame_number,
                "ranking": public,
                "accepted": False,
                "reason": "session-consensus-does-not-bypass-per-frame-gate",
                "floorTransition": transition,
                "identityWindowOpen": identity_window_open,
                "identityEvidenceLimit": 2,
                "acceptedMapId": self._accepted_map_id,
            }

    def commit_result(self, result: dict, selected_floor: int | None) -> dict:
        committed_floor = result.get("floor")
        if committed_floor not in (1, 2):
            committed_floor, _ = self._resolve_floor(selected_floor)
        consensus = self._update_session(result, committed_floor)
        if not consensus["identityWindowOpen"] and self._accepted_map_id is None:
            if result.get("success"):
                result["lateRecognitionSuppressed"] = {
                    "mapId": result.get("mapId"),
                    "reason": result.get("reason"),
                }
                result["success"] = False
                result["reason"] = "late-recognition-outside-strict-window"
        elif (
            self._accepted_map_id is not None and result.get("success")
            and result.get("mapId") != self._accepted_map_id
        ):
            result["success"] = False
            result["reason"] = "session-identity-conflict"
        return consensus

    def recognize(
        self,
        frame: np.ndarray,
        selected_floor: int | None = None,
        update_session: bool = True,
        precomputed_anchors: list[dict] | None = None,
    ) -> dict:
        observed_floor = selected_floor
        selected_floor, floor_source = self._resolve_floor(observed_floor)
        with self._lock:
            accepted_map_id = self._accepted_map_id
        if accepted_map_id is not None:
            anchors = precomputed_anchors or self.side.find_anchors(
                frame, allow_unusual_scales=False
            )
            result = {
                "success": True,
                "reason": "accepted-session-identity-retained",
                "mapId": accepted_map_id,
                "floor": selected_floor,
                "confidence": 1.0,
                "margin": 1.0,
                "selectedFloor": selected_floor,
                "anchor": anchors[0] if anchors else None,
                "anchors": anchors,
                "candidates": [{
                    "mapId": accepted_map_id, "floor": selected_floor,
                    "score": 1.0, "source": "strict-first-two-session-identity",
                }],
                "evidenceMode": "strict-window-session-identity",
                "sessionIdentityRetained": True,
                "observedFloor": observed_floor,
                "floorSource": floor_source,
            }
            if update_session:
                result["sessionConsensus"] = self._update_session(result, selected_floor)
            return result
        if selected_floor != 2:
            result = self.side.recognize(
                frame, selected_floor, include_query_features=True,
                early_matcher=self.live_exemplars.recognize,
                precomputed_anchors=precomputed_anchors,
                defer_legacy_when_early_score_at_least=0.50,
            )
            query_features = result.pop("_queryFeatures", [])
            live = result.pop("_earlyLiveEvidence", None) or self.live_exemplars.recognize(
                query_features, selected_floor
            )
            result["liveExemplarEvidence"] = live
            spatial_anchors = precomputed_anchors or result.get("anchors", [])
            spatial = (
                self.spatial_outline.recognize(frame, spatial_anchors, selected_floor)
                if spatial_anchors and not live.get("success")
                else {"success": False, "reason": "not-needed", "candidates": []}
            )
            result["spatialOutlineEvidence"] = spatial
            base_map_id = result.get("mapId")
            base_success = bool(result.get("success"))
            result["legacyReferenceEvidence"] = {
                "mapId": base_map_id, "success": base_success,
                "reason": result.get("reason"), "candidates": result.get("candidates", [])[:10],
                "deferred": bool(result.get("legacyReferenceRankingDeferred")),
            }
            if live.get("success"):
                result.update({
                    "success": True,
                    "reason": "accepted-human-labeled-live-exemplar",
                    "mapId": live.get("mapId"), "floor": live.get("floor"),
                    "confidence": live.get("confidence", 0.0),
                    "margin": live.get("margin", 0.0),
                    "candidates": live.get("candidates", []),
                })
            elif (
                spatial.get("success") and live.get("candidates")
                and spatial.get("mapId") == live.get("mapId")
                and float(live.get("score", 0.0)) >= 0.55
                and float((live.get("candidates") or [{}])[0].get("localScore", 0.0)) >= 0.60
            ):
                # The first sparse view may be information-theoretically
                # ambiguous. A later view inside the strict two-frame window is
                # accepted only when fine spatial outline and a labeled live
                # exemplar independently name the same map.
                result.update({
                    "success": True,
                    "reason": "accepted-spatial-live-consensus",
                    "mapId": live.get("mapId"), "floor": live.get("floor"),
                    "confidence": min(
                        float(spatial.get("confidence", 0.0)),
                        float(live.get("confidence", 0.0)),
                    ),
                    "margin": min(
                        float(spatial.get("margin", 0.0)),
                        float(live.get("margin", 0.0)),
                    ),
                    "candidates": live.get("candidates", []),
                })
            elif (
                base_success and live.get("candidates")
                and live.get("mapId") != base_map_id
                and float(live.get("score", 0.0)) >= 0.50
            ):
                # A labeled real-game entrance is weaker when exploration is
                # sparse, but it is still authoritative negative evidence
                # against a contradictory synthetic/reference-only accept.
                # Fail closed instead of showing the wrong route overlay.
                result.update({
                    "success": False,
                    "reason": "labeled-live-evidence-disagreement",
                    "crossEvidenceConflict": {
                        "legacyMapId": base_map_id,
                        "labeledLiveMapId": live.get("mapId"),
                        "labeledLiveScore": live.get("score"),
                    },
                })
            elif result.get("legacyReferenceRankingDeferred"):
                spatial_map_id = spatial.get("mapId")
                live_map_id = live.get("mapId")
                result.update({
                    "success": False,
                    "reason": (
                        "spatial-live-evidence-disagreement"
                        if spatial_map_id and live_map_id
                        and spatial_map_id != live_map_id
                        else "labeled-live-evidence-unconfirmed"
                    ),
                    "crossEvidenceConflict": {
                        "spatialMapId": spatial_map_id,
                        "labeledLiveMapId": live_map_id,
                        "labeledLiveScore": live.get("score"),
                    },
                })
            result["evidenceMode"] = (
                "human-labeled-live-entrance" if live.get("success")
                else ("spatial-outline-plus-live-exemplar" if result.get("reason") == "accepted-spatial-live-consensus"
                else ("first-floor-entrance" if selected_floor == 1 else "unknown-floor-entrance")
                )
            )
        else:
            full = self.full.recognize(frame, selected_floor)
            result = {
                "success": bool(full.get("success")),
                "reason": full.get("reason"),
                "mapId": full.get("mapId"),
                "floor": full.get("floor"),
                "confidence": full.get("score", 0.0),
                "margin": full.get("margin", 0.0),
                "selectedFloor": selected_floor,
                "candidates": full.get("candidates", []),
                "fullMapEvidence": full,
                "evidenceMode": "whole-visible-map-shape",
            }
        result["observedFloor"] = observed_floor
        result["floorSource"] = floor_source
        if update_session:
            result["sessionConsensus"] = self._update_session(result, selected_floor)
        return result
