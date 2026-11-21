from __future__ import annotations

import json
from io import BytesIO
import os
import stat
import sys
import threading
import queue
import hashlib
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, wait, FIRST_COMPLETED
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from runtime_timeline import observe_function
from failure_sampling import FailureSampling

from nightmare_semantic_identity import (
    SemanticFingerprint, semantic_fingerprint, semantic_similarity,
)
from nightmare_stair_portal import (
    StairPortalIndex, portal_candidate_matrix, stair_pair_group_translation,
)


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
USER_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE_ROOT
DEFAULT_MANIFEST = RESOURCE_ROOT / "references" / "nightmare-map-pack.json"
DEFAULT_EXIT_ANCHORS = RESOURCE_ROOT / "references" / "nightmare-exit-anchors.json"
DEFAULT_STAIR_ANCHORS = RESOURCE_ROOT / "references" / "nightmare-stair-anchors.json"
DEFAULT_CROSS_FLOOR_ATLAS = RESOURCE_ROOT / "references" / "nightmare-cross-floor-atlas.json"
DEFAULT_PORTABLE_LAYOUT_SEEDS = (
    RESOURCE_ROOT / "references" / "nightmare-portable-layout-seeds.json"
)
_DEFAULT_MEMORY_RAW_ROOT = Path(os.path.abspath(os.environ.get(
    "IDENTITYV_NIGHTMARE_MEMORY_ROOT",
    str(USER_ROOT / "work" / "nightmare-memory"),
)))
DEFAULT_MEMORY_ROOT = _DEFAULT_MEMORY_RAW_ROOT.resolve()
SOURCE_STRUCTURE_PROFILE_ID = 'source-structure'
ZHAN10_STRUCTURAL_PROFILE_IDS = frozenset({
    "zhan10-speed", "zhan10-coffin",
})


def imread_unicode(path: Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None


class NightmareSourceCandidateStore:
    """Load provenance-bound PNG candidates without treating labels as IDs."""
    def __init__(self, resource_root, *, read_resource=None):
        self.read_resource=read_resource
        self.resource_root=Path(resource_root)
        self.index_path=self.resource_root/'references/nightmare-source-candidate-index.json'
        self.entries={}
        self.error=None
        self.feature_store=None
        self._sift_features=None
        self._sift_feature_status=dict(ready=False,reason='not-loaded')
        self._sift_feature_lock=threading.RLock()
        self._rgba_cache={}
        self._rgba_lock=threading.RLock()
        self._runtime_bindings={}
        try:
            payload=json.loads(self.index_path.read_text(encoding='utf8') if read_resource is None else read_resource(self.index_path))
            if payload.get('schemaVersion')!=1:raise ValueError('unsupported source-candidate index')
            self.feature_store=payload.get('featureStore')
            rows=payload.get('entries') or []
            keys=[(str(r['sourceGroup']),str(r['variant']),str(r['sourceFloor'])) for r in rows]
            if len(set(keys))!=len(keys):raise ValueError('duplicate source-candidate entry')
            self.entries={key:dict(row) for key,row in zip(keys,rows)}
            self.by_candidate_map={}
            self.by_source_candidate_id={}
            self.by_source_group={}
            for row in rows:
                self.by_source_group.setdefault(str(row['sourceGroup']),[]).append(dict(row))
                candidate_map_id=row.get('candidateMapId')
                if candidate_map_id:
                    self.by_candidate_map.setdefault(str(candidate_map_id),[]).append(dict(row))
                source_candidate_id=row.get('sourceCandidateId')
                if source_candidate_id:
                    self.by_source_candidate_id.setdefault(str(source_candidate_id),[]).append(dict(row))
            for values in self.by_candidate_map.values():
                values.sort(key=lambda row:(str(row['sourceGroup']),str(row['variant']),str(row['sourceFloor'])))
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.error=str(error)
            self.feature_store=None
            self.by_candidate_map={}
            self.by_source_candidate_id={}
            self.by_source_group={}

    def load_rgba(self, source_group, variant, source_floor):
        """Decode only the requested source image, preserving its alpha channel."""
        key=(str(source_group),str(variant),str(source_floor))
        row=self.entries.get(key)
        if row is None:return None
        if row.get('identityBinding') is not None or row.get('gameFloor') is not None or row.get('routeProfileId') is not None:
            raise ValueError('unreviewed source candidate must not contain a runtime binding')
        with self._rgba_lock:
            cached=self._rgba_cache.get(key)
            if cached is not None:return cached
        path=self.resource_root/Path(row['resourcePath'])
        image=(imread_unicode(path,cv2.IMREAD_UNCHANGED) if self.read_resource is None else
               cv2.imdecode(np.frombuffer(self.read_resource(path),np.uint8),cv2.IMREAD_UNCHANGED))
        if image is None or image.ndim!=3 or image.shape[2]!=4:return None
        if image.shape[:2]!=tuple(reversed(row['canvasPx'])):return None
        with self._rgba_lock:
            self._rgba_cache[key]=image
            while len(self._rgba_cache)>4:
                self._rgba_cache.pop(next(iter(self._rgba_cache)))
        return image

    @staticmethod
    def runtime_map_id(source_group):
        """Return an explicitly namespaced structure-reference identity."""
        return f'source-structure-{str(source_group)}'

    @staticmethod
    def source_group_for_runtime_map_id(map_id):
        prefix='source-structure-'
        value=str(map_id or '')
        return value[len(prefix):] if value.startswith(prefix) else None

    def bind_runtime_sheet(self, map_id, game_floor, candidate):
        """Bind one verified source sheet to the observed game-floor slot.

        ``game_floor`` is the current game observation. ``sourceFloor`` remains
        producer metadata and is never used to derive it.
        """
        group=self.source_group_for_runtime_map_id(map_id)
        if group is None or str(game_floor) not in {'1','2','B1'} or not isinstance(candidate,dict):
            return False
        key=(str(candidate.get('sourceGroup')),str(candidate.get('producerVariantLabel') or candidate.get('variant')),
             str(candidate.get('producerSourceFloorLabel') or candidate.get('sourceFloor')))
        row=self.entries.get(key)
        if row is None or key[0]!=group or not row.get('resourcePath'):
            return False
        if row.get('identityBinding') is not None or row.get('gameFloor') is not None or row.get('routeProfileId') is not None:
            return False
        with self._rgba_lock:
            self._runtime_bindings[(str(map_id),str(game_floor))]=key
        return True

    def bound_row_for_runtime_map(self, map_id, game_floor):
        with self._rgba_lock:
            key=self._runtime_bindings.get((str(map_id),str(game_floor)))
        return dict(self.entries[key]) if key in self.entries else None

    def load_bound_rgba(self, map_id, game_floor):
        row=self.bound_row_for_runtime_map(map_id,game_floor)
        if row is None:return None
        return self.load_rgba(row['sourceGroup'],row['variant'],row['sourceFloor'])

    def bound_resource_path(self, map_id, game_floor):
        row=self.bound_row_for_runtime_map(map_id,game_floor)
        if row is None:return None
        path=self.resource_root/Path(row['resourcePath'])
        return path if path.is_file() else None

    def candidates_for_map(self, map_id, *, variant=None, source_floor=None):
        """List provenance-bound source sheets for a candidate map ID.

        ``source_floor`` is the producer's floor label (for example
        ``floor-1``), deliberately not a game-floor number.
        """
        rows=self.by_candidate_map.get(str(map_id),())
        if variant is not None:rows=(row for row in rows if row.get('variant')==str(variant))
        if source_floor is not None:rows=(row for row in rows if row.get('sourceFloor')==str(source_floor))
        return [dict(row) for row in rows]

    def candidates_for_source_group(self, source_group, *, variant=None, source_floor=None):
        """List source sheets even when owner evidence has not bound a map ID."""
        rows=self.by_source_group.get(str(source_group),())
        if variant is not None:rows=(row for row in rows if row.get('variant')==str(variant))
        if source_floor is not None:rows=(row for row in rows if row.get('sourceFloor')==str(source_floor))
        return [dict(row) for row in rows]

    def candidates_for_source_candidate_id(self, candidate_id, *, variant=None, source_floor=None):
        """Resolve a producer-assigned source key without treating it as a runtime map ID."""
        rows=self.by_source_candidate_id.get(str(candidate_id),())
        if variant is not None:rows=(row for row in rows if row.get('variant')==str(variant))
        if source_floor is not None:rows=(row for row in rows if row.get('sourceFloor')==str(source_floor))
        return [dict(row) for row in rows]

    def load_rgba_for_map(self, map_id, variant, source_floor):
        """Load one source sheet only when the candidate key is unambiguous."""
        rows=self.candidates_for_map(map_id,variant=variant,source_floor=source_floor)
        if len(rows)!=1:return None
        row=rows[0]
        return self.load_rgba(row['sourceGroup'],row['variant'],row['sourceFloor'])

    def load_sift_features(self, *, max_edge=1000, opencv_version=None):
        """Load the import-time descriptor cache without decoding source PNGs."""
        with self._sift_feature_lock:
            if self._sift_features is not None or self._sift_feature_status.get('attempted'):
                return dict(self._sift_features or {}),dict(self._sift_feature_status)
            self._sift_feature_status=dict(attempted=True,ready=False,reason='feature-store-unavailable')
            config=self.feature_store
            if not isinstance(config,dict):return {},dict(self._sift_feature_status)
            try:
                relative=Path(str(config['path']))
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('unsafe feature-store path')
                if (int(config.get('schemaVersion',0))!=1
                        or int(config.get('maxEdge',0))!=int(max_edge)
                        or str(config.get('opencvVersion'))!=str(opencv_version)):
                    self._sift_feature_status=dict(attempted=True,ready=False,reason='extractor-mismatch',
                        featureOpenCVVersion=config.get('opencvVersion'),runtimeOpenCVVersion=str(opencv_version),
                        featureMaxEdge=config.get('maxEdge'),runtimeMaxEdge=int(max_edge))
                    return {},dict(self._sift_feature_status)
                path=self.resource_root/relative
                with np.load(path if self.read_resource is None else BytesIO(self.read_resource(path)),allow_pickle=False) as archive:
                    metadata=json.loads(str(archive['metadata'].item()))
                    if any(metadata.get(key)!=value for key,value in config.items() if key!='path'):
                        raise ValueError('feature-store metadata mismatch')
                    if int(metadata.get('entryCount',0))!=len(self.entries):
                        raise ValueError('feature-store entry count mismatch')
                    features={};keypoint_count=0
                    for key,row in self.entries.items():
                        feature_key=str(row['featureKey'])
                        points=np.asarray(archive[f'{feature_key}_keypoints'],dtype=np.float32)
                        descriptors=np.asarray(archive[f'{feature_key}_descriptors'],dtype=np.float32)
                        if points.ndim!=2 or points.shape[1]!=7 or descriptors.ndim!=2 or descriptors.shape[1]!=128 or len(points)!=len(descriptors):
                            raise ValueError(f'invalid SIFT feature row: {feature_key}')
                        keypoints=[cv2.KeyPoint(float(p[0]),float(p[1]),float(p[2]),float(p[3]),
                            float(p[4]),int(p[5]),int(p[6])) for p in points]
                        descriptors=descriptors.copy() if len(descriptors) else None
                        features[key]=_Features(keypoints,descriptors,float(row.get('featureScale',1.0)))
                        keypoint_count+=len(keypoints)
                self._sift_features=features
                self._sift_feature_status=dict(attempted=True,ready=len(features)==len(self.entries),reason='ready',
                    entryCount=len(features),keypointCount=keypoint_count,path=relative.as_posix())
                if not self._sift_feature_status['ready']:
                    self._sift_features=None
                return dict(self._sift_features or {}),dict(self._sift_feature_status)
            except (OSError,ValueError,KeyError,TypeError,cv2.error) as error:
                self._sift_features=None
                self._sift_feature_status=dict(attempted=True,ready=False,reason='feature-store-invalid',error=str(error))
                return {},dict(self._sift_feature_status)

    def validate(self):
        missing=[]
        for key,row in self.entries.items():
            if not (self.resource_root/Path(row['resourcePath'])).is_file():missing.append(key)
        return dict(success=not self.error and not missing and len(self.entries)==24,
            indexPath=str(self.index_path),entryCount=len(self.entries),missing=missing,error=self.error,
            candidateMapIds=sorted(self.by_candidate_map),candidateMapIndexRows=sum(map(len,self.by_candidate_map.values())),
            sourceCandidateIds=sorted(self.by_source_candidate_id),
            sourceCandidateIndexRows=sum(map(len,self.by_source_candidate_id.values())),
            featureStorePath=(self.feature_store or {}).get('path'),
            featureStoreExists=bool(self.feature_store and (self.resource_root/Path(str(self.feature_store.get('path','')))).is_file()),
            unboundSourceGroups=sorted(group for group,rows in self.by_source_group.items()
                if not any(row.get('candidateMapId') for row in rows)),
            runtimeIdentityBindings=0,gameFloorBindings=0,authorBindings=0,
            displayEligible=False,scope='source-images-loaded; game-floor/author-route mapping unresolved')


def nightmare_map_crop(frame: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    h, w = frame.shape[:2]
    if w / max(h, 1) >= 1.45:
        x0, y0 = int(w * 0.10), int(h * 0.05)
        return frame[y0:int(h * 0.94), x0:int(w * 0.88)], (x0, y0)
    return frame, (0, 0)


def map_color_mask(image: np.ndarray, *, live: bool) -> np.ndarray:
    """Isolate the gray/brown map drawing while ignoring colored route ink."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    # Route-sheet watermarks sit in the dark 35-60 range and otherwise create
    # thousands of fake gray corridors on 4096 px sources.
    minimum = 68
    neutral = (saturation <= 72) & (value >= minimum) & (value <= 205)
    brown = (
        (hue >= 4) & (hue <= 32) & (saturation >= 22)
        & (saturation <= 175) & (value >= minimum) & (value <= 205)
    )
    mask = (neutral | brown).astype(np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def author_route_structure_mask(
    route: np.ndarray,
    *,
    profile_id: str | None = None,
) -> np.ndarray:
    """Extract author-sheet geometry without erasing author map colours.

    The common route mask assumes that map architecture is gray/brown and
    that saturated pixels are annotations.  Zhan10 sheets use pale blue/pink
    architecture whose saturation overlaps that annotation range, so the
    common filter leaves only fragmented room texture.  Their exported alpha
    channel separates transparent background, but exports can also contain
    opaque black background. Exclude that explicitly; inside the painted
    boundary, saturation below 140 retains the map while excluding vivid
    route strokes. Wang's warm room outlines are retained below; remaining
    profiles keep the established common mask.
    """
    route_bgr = (
        route[:, :, :3]
        if route.ndim == 3 and route.shape[2] >= 3 else route
    )
    if (
        str(profile_id or "") in ZHAN10_STRUCTURAL_PROFILE_IDS
        and route.ndim == 3
        and route.shape[2] >= 4
    ):
        saturation = cv2.cvtColor(
            route_bgr, cv2.COLOR_BGR2HSV,
        )[:, :, 1]
        return (
            (route[:, :, 3] >= 128) & (saturation < 140)
            & np.any(route_bgr != 0, axis=2)
        ).astype(np.uint8)

    reference = map_color_mask(route_bgr, live=False).astype(np.uint8)
    if route.ndim == 3 and route.shape[2] >= 4:
        reference &= (route[:, :, 3] >= 8).astype(np.uint8)
    hsv = cv2.cvtColor(route_bgr, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    annotation = (saturation >= 82) & (value >= 105)
    if str(profile_id or '') == 'wang-qingxin':
        # Wang uses warm/yellow ink for room outlines, not route arrows.
        # Reuse the existing brown architecture hue band, including its vivid
        # outline pixels; dilating these as annotations erodes real walls.
        architecture_outline = annotation & (hue >= 4) & (hue <= 32)
        reference |= architecture_outline.astype(np.uint8)
        annotation &= ~architecture_outline
    annotation = cv2.dilate(
        annotation.astype(np.uint8), np.ones((5, 5), np.uint8),
    )
    reference &= (annotation == 0).astype(np.uint8)
    return reference


REFERENCE_CLIENT_AREA = 2560 * 1440
SAFE_DISPLAY_ASPECT_RATIO = 16.0 / 9.0


def visible_map_components(
    frame: np.ndarray,
    *,
    minimum_area: int = 1000,
    observation_bounds: tuple[int, int, int, int] | None = None,
) -> list[tuple[np.ndarray, tuple[int, int, int, int], dict]]:
    """Enumerate the original eligible islands, largest first, without source data."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    search = np.zeros_like(gray, dtype=np.uint8)
    y0, y1 = int(h * 0.22), int(h * 0.80)
    x0, x1 = int(w * 0.16), int(w * 0.76)
    if observation_bounds is not None:
        x0, y0, x1, y1 = observation_bounds
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise ValueError("Observation bounds must lie inside this frame")
    search[y0:y1, x0:x1] = (gray[y0:y1, x0:x1] >= 75).astype(np.uint8) * 255
    count, _labels, stats, centers = cv2.connectedComponentsWithStats(search, 8)
    candidates = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if area < int(minimum_area) or width > int(w * 0.58) or height > int(h * 0.62):
            continue
        cx, cy = (float(value) for value in centers[index])
        candidates.append({
            "index": index, "x": x, "y": y, "width": width,
            "height": height, "area": area, "center": [cx, cy],
        })
    result = []
    for chosen in sorted(candidates, key=lambda row: -row["area"]):
        pad = 8
        x = max(0, chosen["x"] - pad)
        y = max(0, chosen["y"] - pad)
        right = min(w, chosen["x"] + chosen["width"] + pad)
        bottom = min(h, chosen["y"] + chosen["height"] + pad)
        result.append((frame[y:bottom, x:right], (x, y, right, bottom), chosen))
    return result


def visible_map_component(frame, *, minimum_area=1000, observation_bounds=None):
    """Preserve the legacy largest-island extraction and default search domain."""
    rows = visible_map_components(frame, minimum_area=minimum_area,
                                  observation_bounds=observation_bounds)
    return rows[0] if rows else None


def live_stair_screen_candidates(frame: np.ndarray, *, visible_override: tuple | None = None) -> list[dict]:
    """Locate repeated stair-tread lines inside the current live map island.

    Floor switches preserve the viewport, so a stair room visible on both
    floors is a stronger short-term anchor than an offline crop relation.  The
    detector deliberately runs only inside the largest live map component;
    otherwise task text, HUD bars and the recorder overlay create many parallel
    line false positives.
    """
    visible = visible_override if visible_override is not None else visible_map_component(frame)
    if visible is None:
        return []
    patch, bounds, _component = visible
    height, width = patch.shape[:2]
    unit = max(height, width) / 1000.0
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    gradient = cv2.morphologyEx(
        normalized, cv2.MORPH_GRADIENT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )
    edges = cv2.Canny(gradient, 36, 108)
    architecture = map_color_mask(patch, live=True).astype(np.uint8)
    architecture = cv2.dilate(
        architecture, np.ones((max(3, round(7 * unit)) | 1,) * 2, np.uint8),
    )
    edges[architecture == 0] = 0
    lines = cv2.HoughLinesP(
        edges, rho=1, theta=np.pi / 180,
        threshold=max(9, round(11 * unit)),
        minLineLength=max(7, round(9 * unit)),
        maxLineGap=max(2, round(3 * unit)),
    )
    if lines is None:
        return []
    axis_lines: list[dict] = []
    minimum = max(7.0, 8.0 * unit)
    maximum = max(44.0, 82.0 * unit)
    for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4):
        dx, dy = float(x1 - x0), float(y1 - y0)
        length = float(np.hypot(dx, dy))
        if length < minimum or length > maximum:
            continue
        if abs(dy) <= max(2.0, abs(dx) * 0.12):
            axis_lines.append({
                "orientation": "h", "along0": float(min(x0, x1)),
                "along1": float(max(x0, x1)), "cross": float((y0 + y1) / 2.0),
                "length": abs(dx),
            })
        elif abs(dx) <= max(2.0, abs(dy) * 0.12):
            axis_lines.append({
                "orientation": "v", "along0": float(min(y0, y1)),
                "along1": float(max(y0, y1)), "cross": float((x0 + x1) / 2.0),
                "length": abs(dy),
            })

    candidates: list[dict] = []
    for orientation in ("h", "v"):
        oriented = [row for row in axis_lines if row["orientation"] == orientation]
        adjacency: list[set[int]] = [set() for _row in oriented]
        for index, first in enumerate(oriented):
            for other_index in range(index + 1, len(oriented)):
                second = oriented[other_index]
                intersection = max(
                    0.0,
                    min(first["along1"], second["along1"])
                    - max(first["along0"], second["along0"]),
                )
                overlap = intersection / max(
                    1.0, min(first["length"], second["length"]),
                )
                separation = abs(first["cross"] - second["cross"])
                length_ratio = max(first["length"], second["length"]) / max(
                    min(first["length"], second["length"]), 1.0,
                )
                if (
                    1.2 * unit <= separation <= 36.0 * unit
                    and overlap >= 0.55 and length_ratio <= 1.8
                ):
                    adjacency[index].add(other_index)
                    adjacency[other_index].add(index)
        pending = set(range(len(oriented)))
        while pending:
            seed = pending.pop()
            group = {seed}
            frontier = [seed]
            while frontier:
                current = frontier.pop()
                for neighbor in adjacency[current]:
                    if neighbor in pending:
                        pending.remove(neighbor)
                        group.add(neighbor)
                        frontier.append(neighbor)
            members = [oriented[index] for index in group]
            if len(members) < 3:
                continue
            cross_values = sorted(row["cross"] for row in members)
            span = cross_values[-1] - cross_values[0]
            if span < 4.0 * unit or span > 95.0 * unit:
                continue
            along0 = float(np.median([row["along0"] for row in members]))
            along1 = float(np.median([row["along1"] for row in members]))
            if along1 - along0 < 7.0 * unit:
                continue
            tread_clusters: list[list[float]] = []
            for value in cross_values:
                if not tread_clusters or value - np.mean(tread_clusters[-1]) > 2.4 * unit:
                    tread_clusters.append([value])
                else:
                    tread_clusters[-1].append(value)
            treads = [float(np.mean(cluster)) for cluster in tread_clusters]
            if len(treads) < 3:
                continue
            gaps = np.diff(treads)
            gap_cv = float(np.std(gaps) / max(np.mean(gaps), 1e-6)) if len(gaps) >= 2 else 1.0
            if gap_cv > 0.72:
                continue
            if orientation == "h":
                center = [(along0 + along1) / 2.0, (treads[0] + treads[-1]) / 2.0]
            else:
                center = [(treads[0] + treads[-1]) / 2.0, (along0 + along1) / 2.0]
            score = min(
                1.0,
                0.42 + 0.08 * min(len(treads), 6) + 0.22 * max(0.0, 1.0 - gap_cv),
            )
            candidates.append({
                "screenCenter": [
                    float(center[0] + bounds[0]), float(center[1] + bounds[1]),
                ],
                "orientation": orientation,
                "treadCount": len(treads),
                "gapCv": gap_cv,
                "score": float(score),
            })
    candidates.sort(key=lambda row: row["score"], reverse=True)
    selected: list[dict] = []
    for row in candidates:
        center = np.asarray(row["screenCenter"], dtype=np.float64)
        if any(
            np.linalg.norm(center - np.asarray(other["screenCenter"], dtype=np.float64))
            < max(18.0, 34.0 * unit)
            for other in selected
        ):
            continue
        selected.append(row)
    return selected[:12]


def _merged_intervals(intervals: list[tuple[int, int]], gap: int = 3) -> list[tuple[int, int]]:
    rows = sorted((int(a), int(b)) for a, b in intervals if b > a)
    merged: list[list[int]] = []
    for start, end in rows:
        if not merged or start > merged[-1][1] + gap:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _axis_wall_clusters(
    edge: np.ndarray,
    *,
    horizontal: bool,
    icon_size: float,
) -> list[dict]:
    """Collect nearly axis-aligned map-wall fragments into shared wall lines."""
    minimum = max(12, round(icon_size * 0.70))
    lines = cv2.HoughLinesP(
        edge, 1, np.pi / 360.0,
        threshold=max(10, round(icon_size * 0.55)),
        minLineLength=minimum,
        maxLineGap=max(8, round(icon_size * 0.65)),
    )
    if lines is None:
        return []
    raw: list[tuple[int, int, int]] = []
    for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4):
        dx, dy = abs(int(x1) - int(x0)), abs(int(y1) - int(y0))
        if horizontal and dx >= max(minimum, dy * 5):
            raw.append((round((int(y0) + int(y1)) / 2), min(int(x0), int(x1)), max(int(x0), int(x1))))
        elif not horizontal and dy >= max(minimum, dx * 5):
            raw.append((round((int(x0) + int(x1)) / 2), min(int(y0), int(y1)), max(int(y0), int(y1))))
    clusters: list[dict] = []
    coordinate_tolerance = max(2, round(icon_size * 0.13))
    for coordinate, start, end in sorted(raw):
        nearest = min(
            clusters,
            key=lambda row: abs(float(np.median(row["coordinates"])) - coordinate),
            default=None,
        )
        if nearest is None or abs(float(np.median(nearest["coordinates"])) - coordinate) > coordinate_tolerance:
            clusters.append({"coordinates": [coordinate], "intervals": [(start, end)]})
        else:
            nearest["coordinates"].append(coordinate)
            nearest["intervals"].append((start, end))
    result = []
    for cluster in clusters:
        intervals = _merged_intervals(cluster["intervals"], gap=coordinate_tolerance)
        result.append({
            "coordinate": int(round(float(np.median(cluster["coordinates"])))),
            "intervals": intervals,
            "support": int(sum(end - start for start, end in intervals)),
        })
    return result


def _local_floor_brown_mask(image: np.ndarray) -> np.ndarray:
    bgr = image.astype(np.int16)
    blue, green, red = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
    mean = (blue + green + red) / 3.0
    chroma = np.maximum(np.maximum(blue, green), red) - np.minimum(np.minimum(blue, green), red)
    return (
        (red >= blue + 6) & (red >= green - 7)
        & (mean >= 55) & (mean <= 215) & (chroma <= 105)
    )


def _local_floor_color_span(
    image: np.ndarray,
    map_mask: np.ndarray,
    *,
    horizontal: bool,
    cross_start: int,
    cross_end: int,
    span_start: int,
    span_end: int,
    icon_position: float,
    icon_size: float,
    _brown: np.ndarray | None = None,
) -> tuple[int, int, float]:
    """Trim a wall-pair span at a persistent gray/brown floor transition.

    This is what separates the gray exit corridor from an attached brown room
    even when the two share an opening and therefore have no wall across the
    corridor.  Short dark/bright runs from the exit/player icons are ignored.
    """
    height, width = map_mask.shape
    span_limit = width if horizontal else height
    span_start = max(0, min(span_limit - 1, int(span_start)))
    span_end = max(span_start + 1, min(span_limit, int(span_end)))
    cross_limit = height if horizontal else width
    cross_start = max(0, min(cross_limit - 1, int(cross_start) + 2))
    cross_end = max(cross_start + 1, min(cross_limit, int(cross_end) - 2))
    brown = _local_floor_brown_mask(image) if _brown is None else _brown
    # Only ordinary bool/uint8 ndarrays are production masks.  Other dtypes
    # (including wide integers, floats, NaN-bearing arrays, and subclasses)
    # retain the historical masked-mean path to preserve its numeric domain.
    batchable_brown = (
        type(brown) is np.ndarray
        and brown.dtype in (np.dtype(bool), np.dtype(np.uint8))
    )
    if not batchable_brown:
        values: list[float] = []
        known: list[bool] = []
        for position in range(span_start, span_end):
            if horizontal:
                valid = map_mask[cross_start:cross_end, position] > 0
                color = brown[cross_start:cross_end, position]
            else:
                valid = map_mask[position, cross_start:cross_end] > 0
                color = brown[position, cross_start:cross_end]
            known.append(bool(valid.mean() >= 0.20) if valid.size else False)
            values.append(float(color[valid].mean()) if valid.any() else 0.0)
    elif horizontal:
        # Reduce the same ROI once along its cross axis, preserving the
        # original position order and zero-valid fallback.
        valid_roi = map_mask[cross_start:cross_end, span_start:span_end] > 0
        color_roi = brown[cross_start:cross_end, span_start:span_end]
        valid_count = valid_roi.sum(axis=0)
        brown_count = np.where(valid_roi, color_roi, 0).sum(axis=0)
        cross_size = valid_roi.shape[0]
        known = [
            bool(count / cross_size >= 0.20) if cross_size else False
            for count in valid_count
        ]
        values = [
            float(brown / count) if count else 0.0
            for brown, count in zip(brown_count, valid_count)
        ]
    else:
        valid_roi = map_mask[span_start:span_end, cross_start:cross_end] > 0
        color_roi = brown[span_start:span_end, cross_start:cross_end]
        valid_count = valid_roi.sum(axis=1)
        brown_count = np.where(valid_roi, color_roi, 0).sum(axis=1)
        cross_size = valid_roi.shape[1]
        known = [
            bool(count / cross_size >= 0.20) if cross_size else False
            for count in valid_count
        ]
        values = [
            float(brown / count) if count else 0.0
            for brown, count in zip(brown_count, valid_count)
        ]
    if not values:
        return span_start, span_end, 0.0
    smooth_width = max(5, round(icon_size * 0.35)) | 1
    smooth = cv2.blur(
        np.asarray(values, dtype=np.float32)[None, :], (smooth_width, 1),
    )[0]
    raw_normalized = (float(icon_position) - span_start) / max(span_end - span_start, 1)
    endpoint = "start" if raw_normalized <= 0.50 else "end"
    inward = max(round(icon_size * 1.5), round((cross_end - cross_start) * 0.65))
    if endpoint == "start":
        probe = min(span_end - 1, max(span_start, round(icon_position) + inward))
    else:
        probe = max(span_start, min(span_end - 1, round(icon_position) - inward))
    local_probe = probe - span_start
    target_brown = bool(smooth[local_probe] >= 0.43)
    mismatch_run = max(5, round(icon_size * 0.35))

    def scan(step: int) -> int:
        cursor = local_probe
        mismatch = 0
        boundary = 0 if step < 0 else len(values)
        while 0 <= cursor < len(values):
            same = (smooth[cursor] >= 0.43) == target_brown
            if not known[cursor]:
                same = True
            mismatch = 0 if same else mismatch + 1
            if mismatch >= mismatch_run:
                boundary = cursor + mismatch_run if step < 0 else cursor - mismatch_run + 1
                break
            cursor += step
        return boundary

    local_start = max(0, min(local_probe, scan(-1)))
    local_end = min(len(values), max(local_probe + 1, scan(1)))
    selected = smooth[local_start:local_end]
    consistency = float(np.mean((selected >= 0.43) == target_brown)) if selected.size else 0.0
    return span_start + local_start, span_start + local_end, consistency


def local_side_door_rectangle(
    frame: np.ndarray,
    visible_bounds: tuple[int, int, int, int],
    live_icon: dict | None,
    *, _reuse_floor_color_mask: bool = False,
) -> dict:
    """Find the tight local gray/brown rectangle that directly contains the exit.

    The whole explored-map envelope is intentionally irrelevant.  Parallel
    wall pairs establish the local strip; persistent floor-color transitions
    cut it at attached rooms.  There is no fixed 2:1 aspect-ratio assumption.
    """
    if not live_icon:
        return {"success": False, "reason": "missing-door-anchor"}
    x0, y0, x1, y1 = (int(value) for value in visible_bounds)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(frame.shape[1], x1), min(frame.shape[0], y1)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return {"success": False, "reason": "invalid-visible-map-bounds"}
    patch = frame[y0:y1, x0:x1]
    mask = map_color_mask(patch, live=True)
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    icon_size = float(max(live_icon.get("size", (20, 20))))
    icon_x = float(live_icon.get("center", (0.0, 0.0))[0]) - x0
    icon_y = float(live_icon.get("center", (0.0, 0.0))[1]) - y0
    if not (0 <= icon_x < patch.shape[1] and 0 <= icon_y < patch.shape[0]):
        return {"success": False, "reason": "door-anchor-outside-map"}
    horizontal_walls = _axis_wall_clusters(edge, horizontal=True, icon_size=icon_size)
    vertical_walls = _axis_wall_clusters(edge, horizontal=False, icon_size=icon_size)
    candidates: list[dict] = []
    brown = None

    def evaluate_pairs(walls: list[dict], horizontal: bool) -> None:
        nonlocal brown
        cross_icon = icon_y if horizontal else icon_x
        longitudinal_icon = icon_x if horizontal else icon_y
        maximum_gap = icon_size * 9.0
        minimum_gap = max(10.0, icon_size * 0.65)
        for index, first in enumerate(walls):
            for second in walls[index + 1:]:
                a, b = sorted((int(first["coordinate"]), int(second["coordinate"])))
                separation = float(b - a)
                if separation < minimum_gap or separation > maximum_gap:
                    continue
                if not (a - icon_size * 0.55 <= cross_icon <= b + icon_size * 0.55):
                    continue
                intervals = [*first["intervals"], *second["intervals"]]
                if not intervals:
                    continue
                start = min(row[0] for row in intervals)
                end = max(row[1] for row in intervals)
                if end - start < max(14.0, separation * 0.80):
                    continue
                if not (start - icon_size * 0.70 <= longitudinal_icon <= end + icon_size * 0.70):
                    continue
                if _reuse_floor_color_mask and brown is None:
                    # Static for this exact patch. Keep this local to one
                    # rectangle call, never reuse across frames or anchors.
                    brown = _local_floor_brown_mask(patch)
                refined_start, refined_end, color_consistency = _local_floor_color_span(
                    patch, mask, horizontal=horizontal,
                    cross_start=a, cross_end=b,
                    span_start=start, span_end=end,
                    icon_position=longitudinal_icon, icon_size=icon_size,
                    _brown=brown,
                )
                # The icon can cover the terminal wall. Keep the whole glyph
                # inside the inferred rectangle without extending into another room.
                refined_start = min(refined_start, round(longitudinal_icon - icon_size * 0.52))
                refined_end = max(refined_end, round(longitudinal_icon + icon_size * 0.52))
                long_size = float(refined_end - refined_start)
                if long_size < separation * 1.02:
                    continue
                normalized = (longitudinal_icon - refined_start) / max(long_size, 1.0)
                endpointness = abs(normalized - 0.5) * 2.0
                if endpointness < 0.28:
                    continue
                support = min(
                    1.0,
                    float(first["support"] + second["support"]) / max(2.0 * (end - start), 1.0),
                )
                aspect = long_size / max(separation, 1.0)
                longitudinal_limit = patch.shape[1] if horizontal else patch.shape[0]
                span_coverage = min(1.0, long_size / max(float(longitudinal_limit), 1.0))
                cross_centeredness = max(
                    0.0,
                    1.0 - abs(cross_icon - (a + b) / 2.0) / max(separation * 0.5, 1.0),
                )
                score = (
                    support * 0.22 + color_consistency * 0.18
                    + min(endpointness, 1.0) * 0.20
                    + min(aspect, 4.0) / 4.0 * 0.08
                    + span_coverage * 0.20 + cross_centeredness * 0.12
                )
                refined_cross_start = min(a, round(cross_icon - icon_size * 0.15))
                refined_cross_end = max(b, round(cross_icon + icon_size * 0.15))
                local_bounds = (
                    (refined_start, refined_cross_start, refined_end, refined_cross_end)
                    if horizontal else (refined_cross_start, refined_start, refined_cross_end, refined_end)
                )
                candidates.append({
                    "bounds": [
                        int(local_bounds[0] + x0), int(local_bounds[1] + y0),
                        int(local_bounds[2] + x0), int(local_bounds[3] + y0),
                    ],
                    "orientation": "horizontal" if horizontal else "vertical",
                    "score": float(score), "wallSupport": float(support),
                    "colorConsistency": float(color_consistency),
                    "aspectRatio": float(aspect),
                    "spanCoverage": float(span_coverage),
                    "crossCenteredness": float(cross_centeredness),
                    "normalizedDoorPosition": float(normalized),
                })

    evaluate_pairs(horizontal_walls, True)
    evaluate_pairs(vertical_walls, False)
    if not candidates:
        return {"success": False, "reason": "local-rectangle-not-found"}
    candidates.sort(key=lambda row: row["score"], reverse=True)
    best = dict(candidates[0])
    runner_score = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
    best.update({
        "success": bool(
            best["score"] >= 0.53
            and best["colorConsistency"] >= 0.72
            and abs(best["normalizedDoorPosition"] - 0.5) >= 0.18
        ),
        "reason": "local-exit-rectangle" if best["score"] >= 0.53 else "local-rectangle-weak",
        "distinctMargin": float(best["score"] - runner_score),
        "candidateCount": len(candidates),
    })
    return best


def live_side_door_direction(
    bounds: tuple[int, int, int, int],
    live_icon: dict | None,
    local_rectangle: dict | None = None,
) -> dict:
    """Classify direction from the map rectangle's long axis and door endpoint.

    The author groups a horizontal map whose side door is at its right endpoint
    as ``right``.  A vertical map is ``up`` or ``down`` according to the door's
    endpoint.  Merely being closest to one edge is not enough: partial
    exploration can move the visible boundary past the door and previously made
    a horizontal/right layout look like a down layout.

    The current pack has no left-side first-stage category, but the geometric
    rule itself supports all four endpoints. Near-square rectangles and doors
    near the middle stay unknown when no independent local rectangle exists.
    """
    if not live_icon:
        return {
            "direction": None, "reliable": False, "marginPixels": 0.0,
            "rectangleOrientation": "unknown", "reason": "missing-door-anchor",
        }
    # A long corridor touching the exit is not automatically the side-door
    # room.  The map-10 right-door live frames expose exactly that trap: a weak
    # vertical wall pair continues below the exit and used to be called an
    # ``up`` rectangle, filtering the real right-door map out before its walls
    # were compared.  Require two supported walls before replacing the whole
    # visible envelope with the local rectangle.
    local_rectangle_usable = bool(
        local_rectangle and local_rectangle.get("success")
        and float(local_rectangle.get("score", 0.0)) >= 0.63
        and float(local_rectangle.get("wallSupport", 0.0)) >= 0.44
        and float(local_rectangle.get("colorConsistency", 0.0)) >= 0.85
    )
    direction_bounds = (
        local_rectangle.get("bounds") if local_rectangle_usable else bounds
    )
    x0, y0, x1, y1 = (float(value) for value in direction_bounds)
    cx, cy = (float(value) for value in live_icon.get("center", (0.0, 0.0)))
    width = x1 - x0
    height = y1 - y0
    if width <= 1.0 or height <= 1.0 or not (x0 <= cx <= x1 and y0 <= cy <= y1):
        return {
            "direction": None, "reliable": False, "marginPixels": 0.0,
            "rectangleOrientation": "unknown", "reason": "invalid-map-rectangle",
        }

    horizontal_ratio = width / height
    vertical_ratio = height / width
    normalized_x = (cx - x0) / width
    normalized_y = (cy - y0) / height
    icon_reliable = bool(
        float(live_icon.get("score", 0.0)) >= 0.78
        and float(live_icon.get("distinctMargin", 0.0)) >= 0.10
    )

    direction = None
    orientation = "unknown"
    endpoint_margin = 0.0
    reason = "rectangle-too-square"
    # The user's rule has no fixed ratio.  Once independent wall evidence has
    # established a local rectangle, the strictly longer axis is sufficient.
    # The legacy whole-envelope fallback remains conservative.
    minimum_axis_ratio = 1.02 if local_rectangle_usable else 1.35
    endpoint_limit = 0.64 if local_rectangle_usable else 0.68
    endpoint_start = 1.0 - endpoint_limit
    if horizontal_ratio >= minimum_axis_ratio:
        orientation = "horizontal"
        endpoint_margin = (normalized_x - 0.50) * width
        if normalized_x >= endpoint_limit:
            direction = "右"
            reason = "horizontal-door-at-right-end"
        elif normalized_x <= endpoint_start:
            direction = "左"
            endpoint_margin = (0.50 - normalized_x) * width
            reason = "horizontal-door-at-left-end"
        else:
            reason = "horizontal-door-not-at-end"
    elif vertical_ratio >= minimum_axis_ratio:
        orientation = "vertical"
        if normalized_y <= endpoint_start:
            direction = "上"
            endpoint_margin = (0.50 - normalized_y) * height
            reason = "vertical-door-at-top-end"
        elif normalized_y >= endpoint_limit:
            direction = "下"
            endpoint_margin = (normalized_y - 0.50) * height
            reason = "vertical-door-at-bottom-end"
        else:
            reason = "vertical-door-not-at-end"

    local_reliable = bool(
        local_rectangle_usable
        and float(live_icon.get("score", 0.0)) >= 0.58
    )
    reliable = bool((icon_reliable or local_reliable) and direction is not None)
    return {
        "direction": direction if reliable else None,
        "reliable": reliable,
        "marginPixels": float(max(0.0, endpoint_margin)),
        "rectangleOrientation": orientation,
        "rectangleAspectRatio": float(max(horizontal_ratio, vertical_ratio)),
        "normalizedDoorPosition": [float(normalized_x), float(normalized_y)],
        "rectangleBounds": [float(x0), float(y0), float(x1), float(y1)],
        "rectangleSource": (
            "local-exit-rectangle" if local_rectangle_usable
            else "visible-map-envelope"
        ),
        "localRectangleAccepted": local_rectangle_usable,
        "localRectangle": local_rectangle,
        "reason": reason if (icon_reliable or local_reliable) else "door-anchor-not-distinct",
    }


@dataclass(frozen=True)
class NightmareMap:
    id: str
    name: str
    direction: str
    door_count: int
    shape: str
    side_door_crop: tuple[int, int, int, int]
    one_flow: Path
    layers: dict[str, Path]
    front_door_layout: str = "unknown"


class NightmarePack:
    mode='nightmare'
    floors=('1','2','B1')
    def __init__(self, manifest_path: Path = DEFAULT_MANIFEST, *, read_resource=None) -> None:
        self.manifest_path = Path(manifest_path)
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8") if read_resource is None else read_resource(self.manifest_path))
        self.resource_root=self.manifest_path.parent.parent
        self.source_candidates=NightmareSourceCandidateStore(self.resource_root, read_resource=read_resource)
        configured = os.environ.get("IDENTITYV_NIGHTMARE_MAPS")
        bundled = USER_ROOT / "assets" / "nightmare-maps"
        # A producer's sourceRoot is provenance for resource-root manifests,
        # not an installed resource location (nor a useful preload diagnostic).
        uses_source_root = any(item.get("resourceBase", "source-root") == "source-root"
                               for item in raw["maps"])
        if not uses_source_root:
            self.source_root = self.resource_root
        elif configured:
            self.source_root = Path(configured)
        elif bundled.is_dir():
            self.source_root = bundled
        elif getattr(sys, "frozen", False):
            # Missing installed assets must remain missing, even when the
            # producer's development tree happens to exist on this machine.
            self.source_root = self.resource_root / "assets" / "nightmare-maps"
        else:
            self.source_root = Path(raw["sourceRoot"])
        self.pack_id = raw["packId"]
        self.display_name = raw["displayName"]
        self.default_entry_floor = int(raw.get("defaultEntryFloor", 2))
        self.identity_profiles = {item['id']: item.get('identityProfileId', 'zhan10-speed') for item in raw['maps']}
        self.route_profiles = {item['id']: item.get('routeProfileId', 'wang-qingxin') for item in raw['maps']}
        self.maps = []
        for item in raw["maps"]:
            resource_base = item.get("resourceBase", "source-root")
            if resource_base not in {"source-root", "resource-root"}:
                raise ValueError(f"unsupported nightmare resource base: {resource_base}")
            root = self.manifest_path.parent.parent if resource_base == "resource-root" else self.source_root
            self.maps.append(NightmareMap(
                id=item["id"], name=item["name"], direction=item["direction"],
                door_count=int(item["doorCount"]), shape=item["shape"],
                side_door_crop=tuple(int(value) for value in item["sideDoorCrop"]),
                one_flow=root / Path(item["oneFlow"]),
                layers={key: root / Path(value) for key, value in item["layers"].items()},
                front_door_layout=str(item.get("frontDoorLayout") or "unknown"),
            ))
        self.by_id = {item.id: item for item in self.maps}
        if len(self.by_id) != len(self.maps):
            raise ValueError("duplicate nightmare map identity")

    def register_structure_reference(self, source_group):
        """Expose a source group only to the explicit structure consumer."""
        group=str(source_group)
        if not self.source_candidates.candidates_for_source_group(group):return None
        map_id=self.source_candidates.runtime_map_id(group)
        if map_id not in self.by_id:
            self.by_id[map_id]=NightmareMap(
                id=map_id,name=f'结构参考 · 来源组 {group}',direction='未知',
                door_count=0,shape='结构参考',side_door_crop=(0,0,0,0),
                one_flow=Path(),layers={},front_door_layout='unknown')
        return map_id

    def validate(self) -> dict:
        missing = []
        for item in self.maps:
            for floor in self.floors:
                if floor not in item.layers:
                    missing.append({"mapId": item.id, "part": floor, "path": "missing layer binding"})
            x0, y0, x1, y1 = item.side_door_crop
            if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
                missing.append({"mapId": item.id, "part": "二楼侧门局部", "path": str(item.side_door_crop)})
            for label, path in [("一图流", item.one_flow), *item.layers.items()]:
                if not path.is_file():
                    missing.append({"mapId": item.id, "part": label, "path": str(path)})
        return {
            "success": not missing and bool(self.maps),
            "mapCount": len(self.maps),
            "layerCount": sum(len(item.layers) for item in self.maps),
            "sourceRoot": str(self.source_root),
            "sourcePolicy": "external-readonly",
            "sourceCandidates": self.source_candidates.validate(),
            "missing": missing,
        }

    def filter(
        self,
        direction: str = "全部",
        door_count: str = "全部",
        shape: str = "全部",
    ) -> list[NightmareMap]:
        return [item for item in self.maps if (
            (direction == "全部" or item.direction == direction)
            and (door_count == "全部" or item.door_count == int(door_count))
            and (shape == "全部" or item.shape == shape)
        )]


# Per map floor. Recognition reads at most the newest 6 usable crops.
CONFIRMATION_SAMPLES_ON_DISK = 12
SUCCESS_SAMPLES_PER_FLOOR = 6


def _retention_stat(path: Path, root: Path):
    """Fail closed for links/reparse points, including any ancestor of root.

    Only housekeeping uses this guard; recognition and user-owned evidence
    are unchanged. Recheck before deletion as a tree may change after listing.
    This is not an atomic defense against a hostile concurrent filesystem.
    """
    try:
        path_abs = Path(os.path.abspath(path))
        root_abs = Path(os.path.abspath(root))
        path_abs.relative_to(root_abs)
        result = None
        for item in (path_abs, *path_abs.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return None
            if result is None:
                result = info
        path_abs.resolve(strict=True).relative_to(root_abs.resolve(strict=True))
        return result
    except (OSError, ValueError, RuntimeError):
        return None


def _retention_files(directory: Path, root: Path | None = None):
    """Enumerate regular owned files without traversing symlinks/junctions."""
    root = directory if root is None else root
    pending = [directory]
    while pending:
        parent = pending.pop()
        info = _retention_stat(parent, root)
        if info is None or not stat.S_ISDIR(info.st_mode):
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for path in children:
            info = _retention_stat(path, root)
            if info is None:
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                yield path, info


def _retention_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class _RetentionCandidate:
    path: Path
    identity: tuple


def _oldest_first(directory: Path, root: Path | None = None) -> list[_RetentionCandidate]:
    """Finished owned files (not in-flight dot files), oldest first."""
    files = [(info.st_mtime_ns, path.name, path, _retention_identity(info))
             for path, info in _retention_files(directory, root)
             if not path.name.startswith('.')]
    return [_RetentionCandidate(path, identity) for _mtime, _name, path, identity in sorted(files)]


def _delete_quietly(paths, root: Path) -> None:
    for entry in paths:
        path = entry.path
        try:
            info = _retention_stat(path, root)
            if (info is None or not stat.S_ISREG(info.st_mode)
                    or _retention_identity(info) != entry.identity):
                continue
            path.unlink()
        except OSError:
            pass


class NightmareMemoryStore:
    """Append-only human labels plus game-map crops used as future exemplars."""

    def __init__(
        self,
        pack: NightmarePack,
        root: Path = DEFAULT_MEMORY_ROOT,
        sample_validator: Callable[[np.ndarray], bool] | None = None,
        portable_seed_path: Path | None = DEFAULT_PORTABLE_LAYOUT_SEEDS,
    ) -> None:
        self.pack = pack
        self.root = Path(root)
        # Keep the unresolved default only for cleanup admission. Recognition
        # and persistence retain their existing resolved resource-path semantics.
        self._retention_root = Path(os.path.abspath(
            _DEFAULT_MEMORY_RAW_ROOT if root is DEFAULT_MEMORY_ROOT else root))
        self.log_path = self.root / "confirmations.jsonl"
        self.alignment_acceptance_path = self.root / "alignment-acceptances.jsonl"
        self.alignment_rejection_path = self.root / "alignment-rejections.jsonl"
        self.alignment_failure_path = self.root / "alignment-failures.jsonl"
        self.alignment_success_evidence_path = (
            self.root / "alignment-success-evidence.jsonl"
        )
        self.sample_validator = sample_validator
        self.portable_seed_path = Path(portable_seed_path) if portable_seed_path is not None else None
        self._sample_validation_cache: dict[str, bool] = {}
        self._lock = threading.RLock()
        # Failure I/O must never hold the learning/read lock used by solvers.
        self._failure_write_lock = threading.RLock()
        self._failure_submit_lock = threading.Lock()
        self._failure_queue = queue.Queue(maxsize=1)
        self._failure_sampling = FailureSampling()
        self._failure_disk_bytes = None
        self._failure_writer = None
        self.failure_saved_count = 0
        self.failure_write_error = None
        self.failure_last_record = None

    def _sample_is_usable(
        self,
        relative: str | None,
        recorded_evidence: bool | None = None,
    ) -> bool:
        if not relative:
            return False
        if recorded_evidence is False:
            return False
        if recorded_evidence is True:
            return (self.root / relative).is_file()
        if self.sample_validator is None:
            return (self.root / relative).is_file()
        cached = self._sample_validation_cache.get(relative)
        if cached is not None:
            return cached
        frame = imread_unicode(self.root / relative)
        try:
            usable = bool(frame is not None and self.sample_validator(frame))
        except Exception:
            usable = False
        self._sample_validation_cache[relative] = usable
        return usable

    def records(self) -> list[dict]:
        with self._lock:
            if not self.log_path.is_file():
                return []
            rows = []
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("mapId") in self.pack.by_id and row.get("floor") in {"1", "2", "B1"}:
                    rows.append(row)
            return rows

    def resolved_confirmation_records(self) -> list[dict]:
        """Deduplicate consistent screenshots and quarantine conflicting labels.

        Earlier builds could append the exact same captured frame under multiple
        map/floor labels. Feeding all of them back teaches contradictory classes.
        Keep the append-only audit intact. Repeated identical labels collapse to
        their newest row, while any hash carrying multiple labels is excluded
        from recognition until a future explicit correction workflow can resolve
        it. Guessing which old label is right would preserve poisoned training.
        """
        rows = self.records()
        latest_by_hash: dict[str, tuple[int, dict]] = {}
        labels_by_hash: dict[str, set[tuple[str, str]]] = defaultdict(set)
        unkeyed: list[tuple[int, dict]] = []
        for index, row in enumerate(rows):
            sample_hash = str(row.get("sampleSha256") or "")
            if sample_hash:
                latest_by_hash[sample_hash] = (index, row)
                labels_by_hash[sample_hash].add((
                    str(row.get("mapId")), str(row.get("floor")),
                ))
            else:
                unkeyed.append((index, row))
        resolved = [
            *unkeyed,
            *(
                value for sample_hash, value in latest_by_hash.items()
                if len(labels_by_hash[sample_hash]) == 1
            ),
        ]
        resolved.sort(key=lambda item: item[0])
        return [row for _index, row in resolved]

    def alignment_acceptances(self) -> list[dict]:
        """Return valid player-accepted fits without granting display authority."""
        with self._lock:
            if not self.alignment_acceptance_path.is_file():
                return []
            rows = []
            for line in self.alignment_acceptance_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    matrix = np.asarray(row.get("matrixSourceToClient"), dtype=np.float64)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if (
                    row.get("status") == "user-visual-accepted"
                    and row.get("runtimePolicy") == "prior-only-revalidate-current-frame"
                    and row.get("mapId") in self.pack.by_id
                    and row.get("floor") in {"1", "2", "B1"}
                    and matrix.shape == (3, 3)
                    and np.isfinite(matrix).all()
                ):
                    rows.append(row)
            return rows

    def alignment_failures(self) -> list[dict]:
        """Return automatically captured no-display incidents for diagnosis."""
        with self._failure_write_lock:
            if not self.alignment_failure_path.is_file():
                return []
            rows = []
            for line in self.alignment_failure_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    row.get("status") == "automatic-alignment-failure"
                    and self._valid_failure_identity(row.get("mapId"), row.get("floor"), row.get("diagnostic"))
                ):
                    rows.append(row)
            return rows

    @staticmethod
    def alignment_failure_sampling_class(observations):
        """Diagnostic availability only, never a claim of a verified pose."""
        return 'recovery' if any(
            isinstance((observations or {}).get(name), np.ndarray)
            and (observations or {})[name].size > 0
            for name in ('nativeGray', 'translationMask')
        ) else 'initial-or-unknown'

    def _valid_failure_identity(self, map_id, floor, diagnostic):
        if map_id in self.pack.by_id and floor in {'1', '2', 'B1'}:
            return True
        # Unknown identity is a diagnostic bucket, never a map/training label.
        mode=getattr(self.pack,'mode',None)
        return (map_id is None and floor in {'unknown', '1', '2', 'B1'}
                and mode in {'difficult','nightmare'}
                and isinstance(diagnostic, dict)
                and diagnostic.get('stage') == 'identity-rejection'
                and diagnostic.get('mode') == mode
                and diagnostic.get('cycleId') is not None)

    @staticmethod
    def _failure_sampling_key(map_id, profile_id, floor, observations=None, sampling_scope=None):
        if map_id is None and profile_id is None and sampling_scope is None:
            return None
        sampling_class=NightmareMemoryStore.alignment_failure_sampling_class(observations)
        key=(map_id,profile_id,floor,sampling_class)
        return key+(str(sampling_scope),) if sampling_scope is not None else key

    def can_enqueue_alignment_failure(self, floor, observations=None, *,
                                      map_id=None, profile_id=None, cycle_id=None,
                                      sampling_scope=None):
        # Legacy callers lack map/profile; only enqueue can decide their bucket.
        # No disk reads or waits on the solver's producer path.
        if floor not in {'unknown', '1', '2', 'B1'} or not self._failure_submit_lock.acquire(blocking=False):
            return False
        try:
            key=self._failure_sampling_key(map_id,profile_id,floor,observations,sampling_scope)
            return not self._failure_queue.full() and self._failure_sampling.eligible(key, time.monotonic())
        finally:
            self._failure_submit_lock.release()

    def enqueue_alignment_failure(self, map_id, floor, reason, fit, frame,
                                  client_bounds=None, *, cycle_id=None,
                                  diagnostic=None, observations=None,
                                  sampling_scope=None) -> bool:
        """Best effort: one sample per map/profile/floor/class per minute.

        One daemon and one waiting slot; a stalled disk cannot grow the queue
        or block the producer. Accepted inputs must be owned immutable snapshots.
        Successful writes start cooldown. Failed writes back off, then retry.
        A finite disk budget preserves all existing evidence without overwrites.
        """
        if not self._failure_submit_lock.acquire(blocking=False):
            return False
        try:
            sampling_class = self.alignment_failure_sampling_class(observations)
            diagnostic = {**(diagnostic or {}), 'samplingClass': sampling_class,
                          'samplingEnqueuedMonotonicAt': time.perf_counter()}
            if sampling_scope is not None:
                diagnostic['samplingScope']=str(sampling_scope)
            key=self._failure_sampling_key(map_id,diagnostic.get('profileId'),floor,
                observations,diagnostic.get('samplingScope'))
            if (not self._valid_failure_identity(map_id, floor, diagnostic)
                    or self._failure_queue.full()
                    or not self._failure_sampling.eligible(key, time.monotonic())):
                return False
            if self._failure_writer is None:
                self._failure_writer = threading.Thread(
                    target=self._write_alignment_failures, daemon=True,
                    name='alignment-failure-writer')
                self._failure_writer.start()
            self._failure_queue.put_nowait(dict(
                map_id=map_id, floor=floor, reason=reason, fit=fit, frame=frame,
                client_bounds=client_bounds, cycle_id=cycle_id, lossless=True,
                diagnostic=diagnostic, observations=observations))
            self._failure_sampling.pending.add(key)
            self._failure_sampling.last_status = 'queued'
            return True
        except Exception as error:
            self.failure_write_error = f'{type(error).__name__}: {error}'
            return False
        finally:
            self._failure_submit_lock.release()

    def _write_alignment_failures(self):
        while True:
            task = self._failure_queue.get()
            key=self._failure_sampling_key(task['map_id'],task['diagnostic'].get('profileId'),
                task['floor'],task.get('observations'),task['diagnostic'].get('samplingScope'))
            success = False
            row = None
            write_error = None
            try:
                row = self.record_alignment_failure(**task)
                success = True
            except Exception as error:
                write_error = f'{type(error).__name__}: {error}'
                # Rescan only on the writer after a retry, e.g. when the user
                # has freed space; never scan or sleep on the producer thread.
                self._failure_disk_bytes = None
            finally:
                with self._failure_submit_lock:
                    self.failure_write_error = write_error
                    if success:
                        self.failure_last_record = row
                        self.failure_saved_count += 1
                    self._failure_sampling.completed(key, time.monotonic(), success)
                self._failure_queue.task_done()
                del task

    def _reserve_failure_bytes(self, size):
        """Writer-only accounting includes old samples and the JSONL journal.

        Over budget, the oldest failure images are deleted first so the
        newest evidence is kept; their JSONL rows remain as the audit trail.
        """
        if not self._retention_root_allowed():
            raise OSError('alignment-failure-unsafe-cleanup-root; existing evidence preserved')
        if self._failure_disk_bytes is None:
            self._failure_disk_bytes = sum(info.st_size for _, info in _retention_files(self.root / 'failures'))
            journal = _retention_stat(self.alignment_failure_path, self.root)
            if journal is not None and stat.S_ISREG(journal.st_mode):
                self._failure_disk_bytes += journal.st_size
        budget = self._failure_sampling.max_disk_bytes
        if self._failure_disk_bytes + size > budget:
            for entry in _oldest_first(self.root / 'failures'):
                path = entry.path
                if self._failure_disk_bytes + size <= budget:
                    break
                try:
                    info = _retention_stat(path, self.root / 'failures')
                    if (info is None or not stat.S_ISREG(info.st_mode)
                            or _retention_identity(info) != entry.identity):
                        continue
                    freed = info.st_size
                    path.unlink()
                except OSError:
                    continue
                self._failure_disk_bytes -= freed
        if self._failure_disk_bytes + size > budget:
            raise OSError('alignment-failure-budget-exhausted; existing evidence preserved')
        self._failure_disk_bytes += size

    def _retention_root_allowed(self):
        # Missing roots are safe only if all existing ancestors are safe;
        # first-write callers may reserve their journal before mkdir.
        root = self._retention_root
        while not root.exists() and not root.is_symlink():
            if root == root.parent:
                return False
            root = root.parent
        info = _retention_stat(root, root)
        return info is not None and stat.S_ISDIR(info.st_mode)

    def alignment_failure_sampling_status(self):
        """In-memory status for a consumer to report on change, never disk I/O."""
        with self._failure_submit_lock:
            row = self.failure_last_record or {}
            return dict(state=self._failure_sampling.last_status,
                        pendingSamples=self._failure_queue.qsize(),
                        savedCount=self.failure_saved_count,
                        writeError=self.failure_write_error,
                        retryAtMonotonic=self._failure_sampling.retry_at,
                        diskBytes=self._failure_disk_bytes,
                        diskBudgetBytes=self._failure_sampling.max_disk_bytes,
                        lastSamplePath=row.get('samplePath'),
                        lastCycleId=row.get('cycleId'))

    def _alignment_failure_image(self, frame, map_id, floor, lossless):
        extension = '.png' if lossless else '.jpg'
        ok, encoded = cv2.imencode(extension, frame, [] if lossless else [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise ValueError('alignment-failure-frame-encode-failed')
        # Reuse the existing content-addressed failure directory and atomic I/O.
        digest = hashlib.sha256(encoded.tobytes()).hexdigest()
        relative = Path('failures') / (map_id if map_id is not None else 'unknown') / floor / f'{digest[:20]}{extension}'
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            self._reserve_failure_bytes(encoded.nbytes)
            temporary = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            try:
                with temporary.open('wb') as handle:
                    handle.write(encoded.tobytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return relative, digest

    def record_alignment_failure(
        self,
        map_id: str,
        floor: str,
        reason: str,
        fit: dict | None,
        frame: np.ndarray,
        client_bounds: tuple[int, int, int, int] | None = None,
        *,
        cycle_id: int | None = None,
        attempt: int | None = None,
        lossless: bool = False,
        diagnostic: dict | None = None,
        observations: dict | None = None,
    ) -> dict:
        """Persist one bounded final failure frame without treating it as a label."""
        if (
            not self._valid_failure_identity(map_id, floor, diagnostic)
            or not isinstance(frame, np.ndarray)
            or frame.ndim != 3
            or not frame.size
        ):
            raise ValueError("invalid-alignment-failure")
        fit = fit or {}
        with self._failure_write_lock:
            relative, digest = self._alignment_failure_image(frame, map_id, floor, lossless)
            observation_paths = {}
            for name, pixels in (observations or {}).items():
                observation_paths[name] = self._alignment_failure_image(pixels, map_id, floor, True)[0].as_posix()
            recorded_at = datetime.now().astimezone().isoformat(timespec='milliseconds')
            row = {
                "event": "alignment-failure-sample-written",
                "occurredMonotonicAt": time.perf_counter(),
                "schema": "identityv-nightmare-alignment-failure-v1",
                "capturedAt": diagnostic.get('frameCapturedAtUtc') if diagnostic is not None else recorded_at,
                "recordedAt": recorded_at,
                "sequenceNs": time.time_ns(),
                "mapId": map_id,
                "mapName": self.pack.by_id[map_id].name if map_id is not None else None,
                "floor": floor,
                "status": "automatic-alignment-failure",
                "runtimePolicy": "diagnostic-only-not-training-label",
                "reason": reason,
                "cycleId": cycle_id,
                "attempt": attempt,
                "diagnostic": diagnostic,
                "observationPaths": observation_paths,
                "samplePath": relative.as_posix(),
                "sampleSha256": digest,
                "clientSize": [int(frame.shape[1]), int(frame.shape[0])],
                "clientBounds": list(client_bounds) if client_bounds else None,
                "alignmentMode": fit.get("alignmentMode"),
                "matrixSourceToClient": fit.get("matrixSourceToClient"),
                "fitEvidence": {
                    key: fit.get(key) for key in (
                        "success", "reason", "templateScore", "distinctMargin",
                        "queryPixels", "scale", "screenOverlap", "searchMode",
                        "semanticPrecisionOverride", "exitAnchor", "exitAnchorConflict",
                        "visualResidual", "rotationDegrees", "projectedFootprint",
                        "displayValidation", "attempts",
                    ) if key in fit
                },
            }
            self.root.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
            # A disk-full short write can leave a partial final line. Keep its
            # bytes for diagnosis, but do not concatenate the next valid event.
            if self.alignment_failure_path.is_file() and self.alignment_failure_path.stat().st_size:
                with self.alignment_failure_path.open('rb') as previous:
                    previous.seek(-1, os.SEEK_END)
                    if previous.read(1) != b'\n':
                        serialized = '\n' + serialized
            self._reserve_failure_bytes(len(serialized.encode('utf-8')))
            with self.alignment_failure_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
        return row

    def record_alignment_success_evidence(
        self,
        map_id: str,
        floor: str,
        fit: dict,
        frame: np.ndarray,
        client_bounds: tuple[int, int, int, int] | None = None,
        *,
        route_profile_id: str,
        source_floor: str | None = None,
        cycle_id: int | None = None,
    ) -> dict:
        """Persist one runtime-validated fit as diagnostic evidence only.

        This record is deliberately excluded from confirmation labels and
        accepted priors.  It exists so a successful cross-floor frame can be
        replayed offline instead of disappearing after the live session.
        """
        matrix = np.asarray(fit.get("matrixSourceToClient"), dtype=np.float64)
        if (
            map_id not in self.pack.by_id
            or floor not in {"1", "2", "B1"}
            or not fit.get("success")
            or fit.get("profileCurrentFrameValidated") is not True
            or not isinstance(frame, np.ndarray)
            or frame.ndim != 3
            or not frame.size
            or matrix.shape != (3, 3)
            or not np.isfinite(matrix).all()
        ):
            raise ValueError("invalid-alignment-success-evidence")
        ok, encoded = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 94],
        )
        if not ok:
            raise ValueError("alignment-success-frame-encode-failed")
        digest = hashlib.sha256(encoded.tobytes()).hexdigest()
        relative = (
            Path("runtime-successes") / map_id / floor
            / f"{digest[:20]}.jpg"
        )
        path = self.root / relative
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                temporary = path.with_name(
                    f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                try:
                    with temporary.open("wb") as handle:
                        handle.write(encoded.tobytes())
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            row = {
                "schema": "identityv-nightmare-alignment-success-evidence-v1",
                "capturedAt": datetime.now().astimezone().isoformat(
                    timespec="milliseconds",
                ),
                "sequenceNs": time.time_ns(),
                "mapId": map_id,
                "mapName": self.pack.by_id[map_id].name,
                "floor": floor,
                "sourceFloor": source_floor,
                "status": "automatic-current-frame-validated-success",
                "runtimePolicy": "diagnostic-only-not-training-or-prior",
                "routeProfileId": str(route_profile_id),
                "cycleId": cycle_id,
                "samplePath": relative.as_posix(),
                "sampleSha256": digest,
                "clientSize": [int(frame.shape[1]), int(frame.shape[0])],
                "clientBounds": list(client_bounds) if client_bounds else None,
                "matrixSourceToClient": matrix.tolist(),
                "fitEvidence": {
                    key: fit.get(key) for key in (
                        "reason", "alignmentMode", "searchMode",
                        "templateScore", "distinctMargin", "scale",
                        "scaleX", "scaleY", "anisotropy", "rotationDegrees",
                        "profileAxisLocked", "discardedSeedRotationDegrees",
                        "discardedSeedShearDegrees", "visualResidual",
                        "screenOverlap", "displayValidation",
                    ) if key in fit
                },
            }
            self.root.mkdir(parents=True, exist_ok=True)
            with self.alignment_success_evidence_path.open(
                "a", encoding="utf-8", newline="\n",
            ) as handle:
                handle.write(json.dumps(
                    row, ensure_ascii=False, default=str,
                    separators=(",", ":"),
                ) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Diagnostic-only frames: keep the newest few per map floor.
            cleanup_root = self.root / 'runtime-successes'
            if self._retention_root_allowed():
                _delete_quietly(_oldest_first(path.parent, cleanup_root)[:-SUCCESS_SAMPLES_PER_FLOOR], cleanup_root)
        return row

    def record_confirmation(
        self,
        map_id: str,
        floor: str,
        frame: np.ndarray | None,
        client_bounds: tuple[int, int, int, int] | None = None,
    ) -> dict:
        if map_id not in self.pack.by_id or floor not in {"1", "2", "B1"}:
            raise ValueError("invalid-nightmare-memory-label")
        with self._lock:
            sample_rel = None
            sample_hash = None
            sample_shape = None
            sample_rejected = False
            if frame is not None and frame.size and self.sample_validator is not None:
                try:
                    sample_rejected = not bool(self.sample_validator(frame))
                except Exception:
                    sample_rejected = True
            if frame is not None and frame.size and not sample_rejected:
                crop, _ = nightmare_map_crop(frame)
                ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok:
                    sample_hash = hashlib.sha256(encoded.tobytes()).hexdigest()
                    sample_rel = Path("samples") / map_id / floor / f"{sample_hash[:20]}.jpg"
                    sample_path = self.root / sample_rel
                    sample_path.parent.mkdir(parents=True, exist_ok=True)
                    if not sample_path.exists():
                        temporary = sample_path.with_name(
                            f".{sample_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                        )
                        try:
                            with temporary.open("wb") as handle:
                                handle.write(encoded.tobytes())
                                handle.flush()
                                os.fsync(handle.fileno())
                            os.replace(temporary, sample_path)
                            self._sample_validation_cache[sample_rel.as_posix()] = True
                        finally:
                            try:
                                temporary.unlink(missing_ok=True)
                            except OSError:
                                pass
                    sample_shape = [int(crop.shape[1]), int(crop.shape[0])]
            row = {
                "schemaVersion": 1,
                "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "packId": self.pack.pack_id,
                "mapId": map_id,
                "mapName": self.pack.by_id[map_id].name,
                "floor": floor,
                "labelSource": "human-confirmed-in-daily-assistant",
                "state": "candidate-memory",
                "runtimeDisplayEnabled": False,
                "samplePath": sample_rel.as_posix() if sample_rel else None,
                "sampleSha256": sample_hash,
                "sampleSize": sample_shape,
                "sampleEvidenceAccepted": False if sample_rejected else bool(sample_rel),
                "clientBounds": list(client_bounds) if client_bounds else None,
            }
            self.root.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            if sample_rel is not None:
                self._prune_samples(map_id, floor)
            return row

    def _prune_samples(self, map_id: str, floor: str) -> None:
        """Bound one map floor's crops without changing what recognition reads.

        Recognition uses the newest usable crops (``exemplar_paths``) and its
        reliability saturates well below that count. Those files and the
        newest few on disk are kept; older crops are deleted while the JSONL
        labels stay, so a missing file simply reads as an unusable sample.
        """
        directory = self.root / "samples" / map_id / floor
        if not self._retention_root_allowed():
            return
        keep = {path.resolve() for path in self.exemplar_paths(floor).get(map_id, ())}
        cleanup_root = self.root / 'samples'
        files = _oldest_first(directory, cleanup_root)
        keep.update(entry.path.resolve() for entry in files[-CONFIRMATION_SAMPLES_ON_DISK:])
        stale = [entry for entry in files if entry.path.resolve() not in keep]
        _delete_quietly(stale, cleanup_root)
        for entry in stale:
            path = entry.path
            self._sample_validation_cache.pop(path.relative_to(self.root).as_posix(), None)

    def exemplar_paths(self, floor: str, limit_per_map: int = 6) -> dict[str, list[Path]]:
        grouped: dict[str, list[Path]] = {item.id: [] for item in self.pack.maps}
        seen: dict[str, set[str]] = {item.id: set() for item in self.pack.maps}
        for row in reversed(self.resolved_confirmation_records()):
            map_id = row["mapId"]
            if row.get("floor") != floor or len(grouped[map_id]) >= limit_per_map:
                continue
            rel = row.get("samplePath")
            if not rel or rel in seen[map_id]:
                continue
            path = self.root / Path(rel)
            if self._sample_is_usable(rel, row.get("sampleEvidenceAccepted")):
                grouped[map_id].append(path)
                seen[map_id].add(rel)
        return grouped

    def confirmation_counts(self, floor: str) -> dict[str, int]:
        hashes: dict[str, set[str]] = {item.id: set() for item in self.pack.maps}
        for row in self.resolved_confirmation_records():
            if (
                row.get("floor") == floor
                and row.get("sampleSha256")
                and self._sample_is_usable(
                    row.get("samplePath"), row.get("sampleEvidenceAccepted"),
                )
            ):
                hashes[row["mapId"]].add(row["sampleSha256"])
        return {map_id: len(values) for map_id, values in hashes.items()}


@dataclass
class _Features:
    keypoints: list
    descriptors: np.ndarray | None
    scale: float


class NightmareCandidateEngine:
    """Advisory SIFT shortlist. It never authorizes route display."""

    def __init__(
        self,
        pack: NightmarePack,
        max_edge: int = 1000,
        memory_store: NightmareMemoryStore | None = None,
        structure_resource_loader: Callable | None = None,
        structure_direct_resource_loader: Callable | None = None,
        shared_reference_loader: Callable | None = None,
        structure_profile_provider: Callable | None = None,
        structure_cache_context_provider: Callable | None = None,
    ) -> None:
        self.pack = pack
        self.max_edge = max_edge
        # Optional precomputed author geometry.  Keep this as a duck-typed
        # callback so the candidate engine remains usable without the daily
        # assistant/resource module (including the standalone audit tools).
        self._structure_resource_loader = structure_resource_loader
        self._structure_direct_resource_loader = structure_direct_resource_loader
        self._shared_reference_loader = shared_reference_loader
        self._structure_profile_provider = structure_profile_provider
        self._structure_cache_context_provider = structure_cache_context_provider
        self.sift = cv2.SIFT_create(nfeatures=900, contrastThreshold=0.025)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self._references: dict[tuple[str, str], _Features] = {}
        self._structure_references: dict[tuple[str, str], _Features] = {}
        self._semantic_source_references: dict[
            tuple[str, str], SemanticFingerprint
        ] = {}
        self._profile_structure_references: dict[
            tuple[str, str, str], _Features
        ] = {}
        self._profile_semantic_source_references: dict[
            tuple[str, str, str], SemanticFingerprint
        ] = {}
        self._profile_clean_reference_keys: set[tuple[str, str, str]] = set()
        self._structure_preload_diagnostics: dict = {}
        self._structure_cache_context: str | None = None
        self._partial_reference_masks: dict[
            tuple[str, str], tuple[np.ndarray, np.ndarray, tuple[int, int]]
        ] = {}
        # The exit sweep keeps a separate downscaled mask cache.  Reusing the
        # full 4K masks for all 13 maps costs roughly 180 MB and makes the
        # first parallel sweep unnecessarily slow.
        self._fast_reference_masks: dict[
            tuple[str, str], tuple[np.ndarray, np.ndarray, tuple[int, int], float]
        ] = {}
        # Canonical in-map exit icon. Unlike walls, this semantic marker does
        # not repeat along corridors, so it can disambiguate a tiny explored
        # island without relaxing the geometric display gate.
        self._exit_icon_template: np.ndarray | None = None
        self._source_exit_icon_cache: dict[tuple[str, str], dict | None] = {}
        # Alignment and overlay construction used to decode the same 4K sheet
        # twice. Keep a small shared cache so a confirmed map can go from the
        # captured frame to a visible overlay inside the interaction budget.
        self._route_image_cache: dict[tuple[str, str], np.ndarray] = {}
        if callable(self._shared_reference_loader):
            # Shared recognition has no typed N-domain exit/stair metadata yet.
            # Do not read legacy author-domain JSON and do not silently reuse
            # its portable or cross-floor seeds.
            self._precomputed_source_exit_icons = {}
            self._cross_floor_transfers = {}
            self.stair_portals = None
        else:
            try:
                anchor_payload = json.loads(
                    DEFAULT_EXIT_ANCHORS.read_text(encoding="utf-8")
                )
                self._precomputed_source_exit_icons = dict(
                    anchor_payload.get("maps", {})
                )
            except (OSError, json.JSONDecodeError, TypeError):
                self._precomputed_source_exit_icons = {}
            try:
                atlas_path = (
                    DEFAULT_CROSS_FLOOR_ATLAS
                    if DEFAULT_CROSS_FLOOR_ATLAS.is_file() else DEFAULT_STAIR_ANCHORS
                )
                stair_payload = json.loads(atlas_path.read_text(encoding="utf-8"))
                self._cross_floor_transfers = {
                    (
                        str(entry.get("mapId")),
                        str(transfer.get("fromFloor")),
                        str(transfer.get("toFloor")),
                    ): transfer
                    for entry in stair_payload.get("maps", [])
                    for transfer in (
                        entry.get("floorPairs")
                        or entry.get("crossFloorTransfers", [])
                    )
                    if transfer.get("usable")
                }
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                self._cross_floor_transfers = {}
            try:
                self.stair_portals = StairPortalIndex(DEFAULT_STAIR_ANCHORS)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                self.stair_portals = None
        anchor_item = self.pack.by_id.get("nightmare-06")
        if anchor_item is not None and not callable(self._shared_reference_loader):
            anchor_route = imread_unicode(anchor_item.layers["2"])
            if anchor_route is not None and anchor_route.shape[0] >= 586 and anchor_route.shape[1] >= 1895:
                self._exit_icon_template = cv2.cvtColor(
                    anchor_route[546:586, 1855:1895], cv2.COLOR_BGR2GRAY,
                )
        self.memory_store = memory_store or NightmareMemoryStore(pack)
        self._memory_references: dict[tuple[str, str, str], _Features] = {}
        self._semantic_memory_references: dict[
            tuple[str, str, str], SemanticFingerprint
        ] = {}
        self._lock = threading.RLock()
        # Slow SIFT/structure fitting may still be finishing for the previous
        # floor when the player switches. Route decoding and the floor-2 exit
        # fast path must not queue behind that obsolete global engine lock.
        self._route_lock = threading.RLock()
        self._fast_cache_lock = threading.RLock()

    def _active_structure_profile_id(self) -> str:
        provider = self._structure_profile_provider
        if not callable(provider):
            return "wang-qingxin"
        try:
            profile_id = str(provider() or "").strip()
        except Exception:
            return "wang-qingxin"
        return profile_id or "wang-qingxin"

    def _shared_structure_enabled(self) -> bool:
        return callable(self._shared_reference_loader)

    def _shared_structure_product(self, map_id: str, floor: str):
        loader = self._shared_reference_loader
        if not callable(loader):
            return None
        try:
            return loader(self._active_structure_profile_id(), map_id, floor)
        except Exception:
            return None

    def route_image(self, map_id: str, floor: str) -> np.ndarray | None:
        """Return a shared decoded sheet for alignment and overlay rendering."""
        if map_id not in self.pack.by_id or floor not in {"1", "2", "B1"}:
            return None
        source_store=getattr(self.pack,'source_candidates',None)
        if source_store is not None and source_store.source_group_for_runtime_map_id(map_id):
            return source_store.load_bound_rgba(map_id,floor)
        key = (map_id, floor)
        with self._route_lock:
            cached = self._route_image_cache.get(key)
            if cached is not None:
                return cached
            image = imread_unicode(self.pack.by_id[map_id].layers[floor])
            if image is None:
                return None
            self._route_image_cache[key] = image
            # Three floors for the current map plus a few recently used sheets
            # are enough for rapid switches without retaining the whole pack.
            while len(self._route_image_cache) > 6:
                oldest = next(iter(self._route_image_cache))
                if oldest == key and len(self._route_image_cache) == 1:
                    break
                self._route_image_cache.pop(oldest, None)
            return image

    def _prepare(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        h, w = image.shape[:2]
        scale = min(1.0, self.max_edge / max(h, w))
        if scale < 1.0:
            image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        return cv2.createCLAHE(2.0, (8, 8)).apply(gray), scale

    def _features(self, image: np.ndarray) -> _Features:
        prepared, scale = self._prepare(image)
        keypoints, descriptors = self.sift.detectAndCompute(prepared, None)
        return _Features(keypoints or [], descriptors, scale)

    def _match(self, reference: _Features, query: _Features, *, include_correspondences=False) -> dict:
        empty = {
            "score": -2.16, "inliers": 0, "goodMatches": 0,
            "inlierRatio": 0.0, "medianError": 99.0, "matrix": None,
        }
        if reference.descriptors is None or query.descriptors is None:
            return empty
        pairs = self.matcher.knnMatch(reference.descriptors, query.descriptors, k=2)
        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.76 * pair[1].distance]
        # Repeated reference texture must not cast many votes for one live
        # feature. Keep the best correspondence for each observed keypoint.
        unique_targets = {}
        for match in sorted(good, key=lambda match: match.distance):
            unique_targets.setdefault(match.trainIdx, match)
        good = list(unique_targets.values())
        inliers = 0
        ratio = 0.0
        median_error = 99.0
        matrix3 = None
        if len(good) >= 5:
            src = np.float32([reference.keypoints[m.queryIdx].pt for m in good])
            dst = np.float32([query.keypoints[m.trainIdx].pt for m in good])
            matrix, mask = cv2.estimateAffinePartial2D(
                src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0,
                maxIters=2000, confidence=0.995,
            )
            if matrix is not None and mask is not None:
                keep = mask.ravel().astype(bool)
                # A collapsed point/line can have zero residual but is not a
                # map pose. Require a finite, invertible 2D observation fit.
                if (int(keep.sum()) < 3 or not np.isfinite(matrix).all()
                        or np.linalg.matrix_rank(matrix[:, :2]) < 2
                        or np.linalg.matrix_rank(src[keep] - src[keep].mean(axis=0)) < 2
                        or np.linalg.matrix_rank(dst[keep] - dst[keep].mean(axis=0)) < 2):
                    return empty
                inliers = int(keep.sum())
                ratio = inliers / max(len(good), 1)
                predicted = cv2.transform(src[keep, None, :], matrix)[:, 0, :]
                median_error = float(np.median(np.linalg.norm(predicted - dst[keep], axis=1)))
                matrix3 = np.vstack([matrix, [0.0, 0.0, 1.0]])
        score = inliers + 10.0 * ratio + min(len(good), 60) * 0.07 - min(median_error, 12.0) * 0.18
        result = {
            "score": float(score), "inliers": inliers, "goodMatches": len(good),
            "inlierRatio": float(ratio), "medianError": float(median_error),
            "matrix": matrix3,
        }
        if include_correspondences:
            result['referencePointsScaled']=np.float32(
                [reference.keypoints[m.queryIdx].pt for m in good]) if good else np.empty((0,2),np.float32)
            result['queryPointsScaled']=np.float32(
                [query.keypoints[m.trainIdx].pt for m in good]) if good else np.empty((0,2),np.float32)
        return result

    def reload_memory(self) -> None:
        with self._lock:
            self._memory_references.clear()
            self._semantic_memory_references.clear()


def observed_current_map_edges(patch: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Keep map boundaries supported by sharp pixels in this observation.

    Thresholding a smooth exploration fade creates an artificial closed wall.
    Its geometry cannot validate a source sheet. Use image edges, independent
    of the candidate projection, to distinguish that fade from drawn walls.
    A one-pixel neighbourhood accommodates mask/line rasterization. No bad-fit
    pixels are removed based on the candidate, and insufficient support fails
    the existing minimum-wall-evidence check.
    """
    gray = cv2.cvtColor(patch[:, :, :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0) / 8.0
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1) / 8.0
    neighbourhood = np.ones((9, 9), np.uint8)
    span = cv2.dilate(gray, neighbourhood) - cv2.erode(gray, neighbourhood)
    # Contrast-normalized transition width: a sharp step changes at least a
    # quarter of the local range per pixel, unlike a broad exploration fade.
    # This retains a low-contrast hard wall; absolute Canny thresholds do not.
    sharp = ((np.hypot(gx, gy) >= span * .25) & (span > 0)).astype(np.uint8)
    sharp = cv2.dilate(sharp, np.ones((3, 3), np.uint8))
    if sharp.shape != query.shape:
        sharp = cv2.resize(sharp, (query.shape[1], query.shape[0]),
                           interpolation=cv2.INTER_NEAREST)
    boundary = cv2.morphologyEx(query, cv2.MORPH_GRADIENT,
                              np.ones((3, 3), np.uint8)) > 0
    return boundary & (sharp > 0)


def _wall_normal_support(mask: np.ndarray) -> tuple[np.ndarray, ...]:
    """Cardinal inward normals; a raster corner may support both axes."""
    pixels = mask.astype(np.float32)
    x = cv2.Sobel(pixels, cv2.CV_32F, 1, 0)
    y = cv2.Sobel(pixels, cv2.CV_32F, 0, 1)
    ax, ay = np.abs(x), np.abs(y)
    horizontal, vertical = ax >= ay, ay >= ax
    return (horizontal & (x > 0), horizontal & (x < 0),
            vertical & (y > 0), vertical & (y < 0))


@observe_function('validation')
def selected_route_current_frame_residual(
    frame: np.ndarray | None,
    route: np.ndarray | None,
    matrix_source_to_client,
    *,
    maximum_dimension: int | None = None,
    visible_override: tuple | None = None,
    reference_override: np.ndarray | None = None,
    shared_reference=None,
    route_profile_id: str | None = None,
) -> dict:
    """Validate the exact author pixels that are about to become visible.

    Recognition-sheet residuals cannot authorize a Lianghapi overlay after an
    author-coordinate conversion.  Compare the currently explored live map to
    the transformed, annotation-suppressed author sheet immediately before
    rendering.  The comparison is intentionally one-way: unexplored source
    rooms do not count against the fit, while every visible live wall/room must
    be explained by the selected author sheet.
    """
    route_shape = getattr(route, "shape", None)
    shared_mask = getattr(shared_reference, "validation_mask", None)
    if frame is None or (route is None and not isinstance(shared_mask, np.ndarray)):
        return {"verified": False, "reason": "missing-frame-or-route"}
    matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return {"verified": False, "reason": "invalid-transform"}
    visible = visible_override or visible_map_component(frame)
    if visible is None:
        return {"verified": False, "reason": "no-visible-map-component"}
    patch, bounds, _component = visible
    query = map_color_mask(patch, live=True).astype(np.uint8)
    from season_optional_room import filter_query
    query = filter_query(frame, query, bounds)
    evaluation_scale = 1.0
    if maximum_dimension is not None and max(query.shape) > maximum_dimension:
        evaluation_scale = float(maximum_dimension) / float(max(query.shape))
        query = cv2.resize(
            query, (
                max(1, round(query.shape[1] * evaluation_scale)),
                max(1, round(query.shape[0] * evaluation_scale)),
            ),
            interpolation=cv2.INTER_NEAREST,
        )
    query_pixels = int(round(float(query.sum()) / (evaluation_scale ** 2)))
    if query_pixels < 1500:
        return {
            "verified": False,
            "reason": "insufficient-current-map-pixels",
            "queryPixels": query_pixels,
        }

    if isinstance(shared_mask, np.ndarray):
        # Shared mode owns recognition geometry even when a display-only
        # author sheet is still present in the caller. Never let that sheet
        # become the residual reference by virtue of a non-None route.
        reference = shared_mask.astype(np.uint8, copy=False)
    elif route is None:
        reference = shared_mask.astype(np.uint8, copy=False)
    elif (
        isinstance(reference_override, np.ndarray)
        and reference_override.ndim == 2
        and (route_shape is None or reference_override.shape == route_shape[:2])
    ):
        # Translation refinement evaluates several matrices for the same
        # route. Reuse its already annotation-suppressed structural mask;
        # rebuilding HSV/morphology six times on a 4096px author sheet costs
        # over a second while contributing no new evidence.
        reference = reference_override.astype(np.uint8, copy=False)
    else:
        reference = author_route_structure_mask(
            route, profile_id=route_profile_id,
        )

    x0, y0 = (float(bounds[0]), float(bounds[1]))
    source_to_patch = matrix[:2].astype(np.float32).copy()
    source_to_patch[0, 2] -= x0
    source_to_patch[1, 2] -= y0
    source_to_patch[:2] *= float(evaluation_scale)
    projected = cv2.warpAffine(
        reference, source_to_patch, (query.shape[1], query.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    if not projected.any():
        return {
            "verified": False,
            "reason": "selected-route-outside-visible-map",
            "queryPixels": query_pixels,
        }

    edge_kernel = np.ones((3, 3), np.uint8)
    query_edges = observed_current_map_edges(patch, query)
    projected_edges = cv2.morphologyEx(
        projected, cv2.MORPH_GRADIENT, edge_kernel,
    ) > 0
    edge_count = int(round(float(query_edges.sum()) / evaluation_scale))
    if edge_count < 200 or not projected_edges.any():
        return {
            "verified": False,
            "reason": "insufficient-wall-edges",
            "queryPixels": query_pixels,
            "queryEdgeCount": edge_count,
        }
    query_height, query_width = query_edges.shape
    edge_grid_cells = 0
    minimum_edges_per_cell = max(
        30, int(query_edges.sum() * 0.015),
    )
    for grid_y in range(3):
        cell_y0 = round(grid_y * query_height / 3.0)
        cell_y1 = round((grid_y + 1) * query_height / 3.0)
        for grid_x in range(3):
            cell_x0 = round(grid_x * query_width / 3.0)
            cell_x1 = round((grid_x + 1) * query_width / 3.0)
            if int(query_edges[
                cell_y0:cell_y1, cell_x0:cell_x1,
            ].sum()) >= minimum_edges_per_cell:
                edge_grid_cells += 1
    distance_map = cv2.distanceTransform(
        (~projected_edges).astype(np.uint8), cv2.DIST_L2, 3,
    ) / float(evaluation_scale)
    normal_residuals = {}
    for name, observed_normal, projected_normal in zip(
        ("inside-right", "inside-left", "inside-down", "inside-up"),
        _wall_normal_support(query), _wall_normal_support(projected),
    ):
        support = query_edges & observed_normal
        count = int(round(support.sum() / evaluation_scale))
        if count < 200:
            continue
        reference_edges = projected_edges & projected_normal
        distances_for_normal = (cv2.distanceTransform(
            (~reference_edges).astype(np.uint8), cv2.DIST_L2, 3,
        ) / float(evaluation_scale) if reference_edges.any()
            else np.full(query.shape, np.inf, np.float32))
        values = distances_for_normal[support]
        middle = float(np.median(values))
        three = float(np.mean(values <= 3.0))
        six = float(np.mean(values <= 6.0))
        normal_residuals[name] = dict(queryEdgeCount=count,
            medianPixels=middle, withinThreePixels=three, withinSixPixels=six,
            verified=bool(middle <= 4.5 and three >= .45 and six >= .62))
    # Translation conflict needs disagreement on both opposed walls and a
    # majority of at least one side beyond the existing six-pixel tolerance.
    # Coverage alone also fails on broad painted outlines whose median is
    # still inside that tolerance. Fine accuracy remains the aggregate's job.
    conflicting_axes = [axis for axis, pair in (
        ("x", ("inside-right", "inside-left")),
        ("y", ("inside-down", "inside-up")),
    ) if all(name in normal_residuals and normal_residuals[name]["withinSixPixels"] < .62
             for name in pair)
        and any(normal_residuals[name]["medianPixels"] > 6.0 for name in pair)]
    distances = distance_map[query_edges]
    median = float(np.median(distances))
    p90 = float(np.percentile(distances, 90))
    within_three = float(np.mean(distances <= 3.0))
    within_six = float(np.mean(distances <= 6.0))
    explained = cv2.dilate(
        projected, np.ones((7, 7), np.uint8),
    ) > 0
    explained_fraction = float(np.mean(explained[query > 0]))
    verified = bool(
        median <= 4.5
        and within_three >= 0.45
        and within_six >= 0.62
        # A tiny newly explored exit island contains fog-frontier/player-room
        # pixels that no clean author sheet should reproduce.  The live map-05
        # entry frame still has sub-2px wall median and >63% edges within 3px;
        # allowing 68% one-way pixel explanation keeps that exact sparse case
        # while the independent edge gates continue to reject shifted sheets.
        and explained_fraction >= 0.68
        and not conflicting_axes
    )
    return {
        "verified": verified,
        "reason": (
            "selected-route-current-frame-residual-verified"
            if verified else "selected-route-current-frame-residual-failed"
        ),
        "queryPixels": query_pixels,
        "queryEdgeCount": edge_count,
        "queryEdgeGridCells3x3": edge_grid_cells,
        "wallNormalResiduals": normal_residuals,
        "conflictingWallAxes": conflicting_axes,
        "medianPixels": median,
        "p90Pixels": p90,
        "withinThreePixels": within_three,
        "withinSixPixels": within_six,
        "explainedPixelFraction": explained_fraction,
    }


def _front_local_point_residuals(
    predicted: np.ndarray,
    observed: np.ndarray,
) -> dict:
    residual = np.linalg.norm(
        np.asarray(observed, dtype=np.float64)
        - np.asarray(predicted, dtype=np.float64),
        axis=1,
    )
    if residual.size == 0:
        return {
            "count": 0,
            "medianPixels": float("inf"),
            "p90Pixels": float("inf"),
            "maxPixels": float("inf"),
            "rmsPixels": float("inf"),
            "withinTwoPixels": 0.0,
            "withinThreePixels": 0.0,
        }
    return {
        "count": int(residual.size),
        "medianPixels": float(np.median(residual)),
        "p90Pixels": float(np.percentile(residual, 90)),
        "maxPixels": float(np.max(residual)),
        "rmsPixels": float(np.sqrt(np.mean(residual ** 2))),
        "withinTwoPixels": float(np.mean(residual <= 2.0)),
        "withinThreePixels": float(np.mean(residual <= 3.0)),
    }


def fit_axis_locked_point_geometry(
    source_points, observed_points, matrix, *, local_points, query_shape,
    forward_backward, flow_error_values, initial_count, component,
    evaluation_scale=1.0, maximum_relative_scale_correction=0.08, started=None,
    distribute_temporal_support=False,
):
    """Shared H0/H1 fit and disjoint validation for measured point pairs."""
    started = time.perf_counter() if started is None else started
    points = local_points
    base_linear = matrix[:2, :2].copy()
    baseline_points = source_points @ base_linear.T
    local_x = points[:, 0]
    local_y = points[:, 1]
    grid_x = np.floor(local_x / max(1.0, query_shape[1] / 4.0)).astype(int)
    grid_y = np.floor(local_y / max(1.0, query_shape[0] / 4.0)).astype(int)
    support_mask = ((grid_x + grid_y) % 2) == 0
    if int(support_mask.sum()) < 3 or int((~support_mask).sum()) < 3:
        order = np.lexsort((local_x, local_y))
        support_mask = np.zeros(len(points), dtype=bool)
        support_mask[order[::2]] = True
    support_geometry = baseline_points[support_mask]
    singular = np.linalg.svd(support_geometry-support_geometry.mean(axis=0), compute_uv=False)
    if (distribute_temporal_support and len(points) >= 6 and (len(singular) < 2
        or singular[-1] / max(float(singular[0]), 1e-9) < 0.10
        or float(np.min(np.ptp(support_geometry, axis=0))) < 24.0)):
        # Checkerboard/alternating rows can put all support on one wall even
        # when the complete measured set spans a room. Partition by source
        # geometry only (never fit error), retaining a disjoint validation half.
        chosen = [0]
        distances = np.linalg.norm(baseline_points-baseline_points[0], axis=1)
        for _ in range(len(points)//2-1):
            distances[chosen] = -1
            index = int(np.argmax(distances))
            chosen.append(index)
            distances = np.minimum(distances,
                np.linalg.norm(baseline_points-baseline_points[index], axis=1))
        support_mask[:] = False
        support_mask[chosen] = True
    support_indices = np.flatnonzero(support_mask)
    validation_indices = np.flatnonzero(~support_mask)
    if len(support_indices) < 3 or len(validation_indices) < 3:
        return {
            "success": False,
            "reason": "front-local-support-validation-split-failed",
            "acceptedPointCount": int(len(points)),
        }
    support_baseline = baseline_points[support_indices]
    support_observed = observed_points[support_indices]
    ransac_matrix, ransac_inliers = cv2.estimateAffinePartial2D(
        support_baseline.astype(np.float32),
        support_observed.astype(np.float32),
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=1200,
        confidence=0.995,
        refineIters=10,
    )
    if ransac_matrix is None or ransac_inliers is None:
        return {
            "success": False,
            "reason": "front-local-support-ransac-failed",
            "acceptedPointCount": int(len(points)),
        }
    support_inlier_mask = ransac_inliers[:, 0] > 0
    if int(support_inlier_mask.sum()) < 3:
        return {
            "success": False,
            "reason": "front-local-support-inliers-insufficient",
            "acceptedPointCount": int(len(points)),
        }
    fit_baseline = support_baseline[support_inlier_mask]
    fit_observed = support_observed[support_inlier_mask]
    fit_centered = fit_baseline - np.mean(fit_baseline, axis=0)
    singular_values = np.linalg.svd(fit_centered, compute_uv=False)
    condition = float(
        singular_values[-1] / max(float(singular_values[0]), 1e-9)
    ) if len(singular_values) >= 2 else 0.0
    spread = np.ptp(fit_baseline, axis=0)
    scale_observable = bool(
        condition >= 0.10 and float(np.min(spread)) >= 24.0
    )

    def robust_fit(fixed_factor: float | None) -> tuple[float, np.ndarray]:
        weights = np.ones(len(fit_baseline), dtype=np.float64)
        factor = 1.0 if fixed_factor is None else float(fixed_factor)
        translation = np.median(
            fit_observed - factor * fit_baseline, axis=0,
        )
        for _iteration in range(6):
            if fixed_factor is None:
                weighted_baseline = np.sum(
                    fit_baseline * weights[:, None], axis=0,
                ) / max(float(weights.sum()), 1e-9)
                weighted_observed = np.sum(
                    fit_observed * weights[:, None], axis=0,
                ) / max(float(weights.sum()), 1e-9)
                centered_baseline = fit_baseline - weighted_baseline
                centered_observed = fit_observed - weighted_observed
                denominator = float(np.sum(
                    centered_baseline * centered_baseline * weights[:, None],
                ))
                factor = float(np.sum(
                    centered_baseline * centered_observed * weights[:, None],
                ) / max(denominator, 1e-9))
            delta = fit_observed - factor * fit_baseline
            translation = np.sum(delta * weights[:, None], axis=0) / max(
                float(weights.sum()), 1e-9,
            )
            errors = np.linalg.norm(
                fit_observed - (factor * fit_baseline + translation), axis=1,
            )
            center_error = float(np.median(errors))
            robust_sigma = max(
                0.5,
                1.4826 * float(np.median(np.abs(errors - center_error))),
            )
            weights = np.minimum(
                1.0,
                2.5 * robust_sigma / np.maximum(errors, 1e-6),
            )
        return factor, np.asarray(translation, dtype=np.float64)

    h0_factor, h0_translation = robust_fit(1.0)
    h1_factor, h1_translation = robust_fit(None)
    if not np.isfinite([h0_factor, h1_factor]).all() or h1_factor <= 0.0:
        return {"success": False, "reason": "front-local-fit-nonfinite"}
    h0_prediction = h0_factor * baseline_points[validation_indices] + h0_translation
    h1_prediction = h1_factor * baseline_points[validation_indices] + h1_translation
    h0_residual = _front_local_point_residuals(
        h0_prediction, observed_points[validation_indices],
    )
    h1_residual = _front_local_point_residuals(
        h1_prediction, observed_points[validation_indices],
    )
    h0_rms = float(h0_residual["rmsPixels"])
    h1_rms = float(h1_residual["rmsPixels"])
    improvement = (
        (h0_rms - h1_rms) / max(h0_rms, 1.0)
        if np.isfinite(h0_rms) and np.isfinite(h1_rms) else 0.0
    )
    scale_factor = float(h1_factor)
    scale_error = scale_factor - 1.0
    ambiguity = min(
        1.0,
        0.45 * float(np.median(forward_backward)) / 2.5
        + 0.25 * float(np.median(flow_error_values)) / 100.0
        + 0.30 * (
            1.0
            - len(np.unique(np.round(observed_points / 4.0), axis=0))
            / max(1, len(observed_points))
        ),
    )
    h0_valid = bool(
        h0_residual["medianPixels"] <= 3.0
        and h0_residual["p90Pixels"] <= 8.0
        and h0_residual["maxPixels"] <= 20.0
        and ambiguity <= 0.45
    )
    h1_valid = bool(
        scale_observable
        and
        abs(scale_error) <= float(maximum_relative_scale_correction)
        and abs(scale_error) >= 0.003
        and h1_residual["medianPixels"] <= 3.0
        and h1_residual["p90Pixels"] <= 8.0
        and h1_residual["maxPixels"] <= 20.0
        and improvement >= 0.20
        and ambiguity <= 0.45
    )

    def model_row(
        factor: float,
        translation: np.ndarray,
        residual: dict,
        valid: bool,
    ) -> dict:
        model = np.eye(3, dtype=np.float64)
        model[:2, :2] = base_linear * float(factor)
        model[:2, 2] = translation
        residual_row = {
            "verified": bool(valid),
            "verificationSource": "front-local-held-out-geometry",
            "validationBasis": "disjoint-current-frame-structure-points",
            "validationPointCount": int(residual["count"]),
            "medianPixels": float(residual["medianPixels"]),
            "p90Pixels": float(residual["p90Pixels"]),
            "maxPixels": float(residual["maxPixels"]),
            "rmsPixels": float(residual["rmsPixels"]),
            "withinTwoPixels": float(residual["withinTwoPixels"]),
            "withinThreePixels": float(residual["withinThreePixels"]),
        }
        return {
            "success": bool(valid),
            "scaleFactor": float(factor),
            "scaleError": float(factor - 1.0),
            "translation": [float(value) for value in translation],
            "matrixSourceToClient": model.tolist(),
            "selectedRouteCurrentFrameResidual": residual_row,
        }

    h0 = model_row(h0_factor, h0_translation, h0_residual, h0_valid)
    h1 = model_row(h1_factor, h1_translation, h1_residual, h1_valid)
    selected = h1 if h1_valid else h0 if h0_valid else None
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    result = {
        "success": selected is not None,
        "reason": (
            "front-local-scale-plus-translation"
            if selected is h1 else
            "front-local-translation-only"
            if selected is h0 else
            "front-local-held-out-geometry-failed"
        ),
        "mode": (
            "scale-plus-translation" if selected is h1 else
            "translation-only" if selected is h0 else None
        ),
        "selected": selected,
        "h0": h0,
        "h1": h1,
        "sameVisibleComponent": True,
        "visibleComponent": component,
        "initialPointCount": initial_count,
        "acceptedPointCount": int(len(points)),
        "supportPointCount": int(len(fit_baseline)),
        "validationPointCount": int(len(validation_indices)),
        "geometryCondition": condition,
        "scaleObservable": scale_observable,
        "supportSpreadPixels": [float(value) for value in spread],
        "matchAcceptanceRatio": float(len(points) / max(initial_count, 1)),
        "matchAmbiguity": float(ambiguity),
        "medianForwardBackwardPixels": float(np.median(forward_backward)),
        "medianPatchError": float(np.median(flow_error_values)),
        "h0Validation": h0_residual,
        "h1Validation": h1_residual,
        "scaleFactor": scale_factor,
        "scaleError": scale_error,
        "improvement": float(improvement),
        "evaluationScale": float(evaluation_scale),
        "preprocessAndFitMs": float(elapsed_ms),
    }
    return result


class RouteDisplayGate:
    """Small auditable state machine for the fail-closed viewer."""

    def __init__(self, pack: NightmarePack) -> None:
        self.pack = pack
        self.selected_id: str | None = None
        self.confirmed_id: str | None = None

    def select(self, map_id: str | None) -> None:
        selected = map_id if map_id in self.pack.by_id else None
        self.selected_id = selected
        if selected != self.confirmed_id:
            self.confirmed_id = None

    def confirm(self) -> bool:
        if self.selected_id not in self.pack.by_id:
            self.confirmed_id = None
            return False
        self.confirmed_id = self.selected_id
        return True

    def hide_for_new_frame(self) -> None:
        self.confirmed_id = None

    def route_path(self, floor: str) -> Path | None:
        if self.confirmed_id not in self.pack.by_id or floor not in {"1", "2", "B1"}:
            return None
        source_store=getattr(self.pack,'source_candidates',None)
        if source_store is not None and source_store.source_group_for_runtime_map_id(self.confirmed_id):
            return source_store.bound_resource_path(self.confirmed_id,floor)
        path = self.pack.by_id[self.confirmed_id].layers.get(floor)
        if path is None:return None
        return path if path.is_file() else None
