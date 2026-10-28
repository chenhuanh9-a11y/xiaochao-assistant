from __future__ import annotations

import json
import os
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
DEFAULT_MEMORY_ROOT = Path(os.environ.get(
    "IDENTITYV_NIGHTMARE_MEMORY_ROOT",
    str(USER_ROOT / "work" / "nightmare-memory"),
)).resolve()
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
    def __init__(self, resource_root):
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
            payload=json.loads(self.index_path.read_text(encoding='utf8'))
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
        image=imread_unicode(self.resource_root/Path(row['resourcePath']),cv2.IMREAD_UNCHANGED)
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
                with np.load(path,allow_pickle=False) as archive:
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


def project_alignment_matrix_to_client(
    matrix_source_to_client,
    source_client_size,
    target_client_size,
    *,
    maximum_aspect_ratio_delta: float = 0.04,
) -> dict | None:
    """Project a candidate pose between members of one display-layout family.

    Game map geometry is expressed in physical client pixels.  Scale the linear
    transform uniformly by the geometric mean of the width/height ratios while
    projecting its screen anchor on each axis.  A projected pose is only a
    search seed: current-frame geometry must still authorize any display.
    """
    try:
        matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
        source_w, source_h = (float(value) for value in source_client_size)
        target_w, target_h = (float(value) for value in target_client_size)
    except (TypeError, ValueError):
        return None
    if (
        matrix.shape != (3, 3)
        or not np.isfinite(matrix).all()
        or abs(float(np.linalg.det(matrix[:2, :2]))) <= 1e-9
        or min(source_w, source_h, target_w, target_h) <= 0.0
    ):
        return None
    source_aspect = source_w / source_h
    target_aspect = target_w / target_h
    aspect_delta = abs(target_aspect / source_aspect - 1.0)
    if aspect_delta > float(maximum_aspect_ratio_delta):
        return None
    ratio_x = target_w / source_w
    ratio_y = target_h / source_h
    uniform_ratio = float(np.sqrt(ratio_x * ratio_y))
    projected = matrix.copy()
    projected[:2, :2] *= uniform_ratio
    projected[0, 2] *= ratio_x
    projected[1, 2] *= ratio_y
    if not np.isfinite(projected).all():
        return None
    return {
        "matrixSourceToClient": projected.tolist(),
        "sourceClientSize": [int(round(source_w)), int(round(source_h))],
        "clientSize": [int(round(target_w)), int(round(target_h))],
        "layoutScaleX": ratio_x,
        "layoutScaleY": ratio_y,
        "layoutUniformScale": uniform_ratio,
        "layoutAspectRatioDelta": aspect_delta,
        "portableLayoutProjection": True,
        "candidateOnly": True,
        "requiresCurrentFrameValidation": True,
        "authorizesDirectDisplay": False,
    }


def nightmare_map_crop(frame: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    h, w = frame.shape[:2]
    if w / max(h, 1) >= 1.45:
        x0, y0 = int(w * 0.10), int(h * 0.05)
        return frame[y0:int(h * 0.94), x0:int(w * 0.88)], (x0, y0)
    return frame, (0, 0)


def registration_structure_view(image: np.ndarray, *, annotated_reference: bool) -> np.ndarray:
    """Build a registration-only view that favors recurring map structure.

    The source route sheets contain vivid cyan/red guidance that is absent from
    the game.  Suppressing only those colors keeps the gray/brown architecture
    intact.  The blend retains room texture while strengthening wall edges; a
    pure binary skeleton proved too sparse on partially explored live maps.
    """
    if image.ndim == 2:
        gray = image.copy()
    elif annotated_reference:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        vivid = (saturation >= 85) & (value >= 120)
        route_hue = (hue <= 12) | (hue >= 165) | ((hue >= 35) & (hue <= 140))
        mask = (vivid & route_hue).astype(np.uint8) * 255
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        background = cv2.GaussianBlur(gray, (0, 0), 5.0)
        gray[mask > 0] = background[mask > 0]
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(2.0, (8, 8)).apply(gray)
    edges = cv2.Canny(cv2.GaussianBlur(enhanced, (5, 5), 0), 35, 105)
    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )
    return cv2.addWeighted(enhanced, 0.42, edges, 0.58, 0.0)


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


def axis_lock_author_floor_transform(
    matrix: np.ndarray,
    source_shape: tuple[int, ...],
) -> np.ndarray:
    """Remove rotation, shear, and non-uniform scale from a visible floor.

    Nightmare floor sheets and the in-game map use the same screen axes.  A
    reviewed floor relation may contain a small rotation or shear because it
    was estimated from imperfect crops/anchors; that error is useful for
    locating the target neighbourhood but must never tilt or stretch the
    visible sheet.  Preserve the projected source centre and the geometric
    mean scale, then let current-frame evidence correct translation/zoom.
    """
    transform = np.asarray(matrix, dtype=np.float64)
    if (
        transform.shape != (3, 3)
        or not np.isfinite(transform).all()
        or len(source_shape) < 2
    ):
        raise ValueError("invalid author-floor transform")
    source_height, source_width = source_shape[:2]
    source_center = np.asarray([
        float(source_width) / 2.0,
        float(source_height) / 2.0,
        1.0,
    ])
    projected_center = (transform @ source_center)[:2]
    determinant = abs(float(np.linalg.det(transform[:2, :2])))
    if determinant <= 1e-18:
        raise ValueError("degenerate author-floor transform")
    scale = float(np.sqrt(determinant))
    locked = np.eye(3, dtype=np.float64)
    locked[0, 0] = scale
    locked[1, 1] = scale
    locked[:2, 2] = projected_center - locked[:2, :2] @ source_center[:2]
    return locked


REFERENCE_CLIENT_AREA = 2560 * 1440
SAFE_DISPLAY_ASPECT_RATIO = 16.0 / 9.0


def resolution_area_ratio(frame_shape: tuple[int, ...]) -> float:
    """Return physical-pixel area relative to the calibrated 2560x1440 client."""
    try:
        height, width = int(frame_shape[0]), int(frame_shape[1])
    except (TypeError, ValueError, IndexError):
        return 1.0
    return max(0.12, min(3.0, (height * width) / REFERENCE_CLIENT_AREA))


def resolution_scaled_pixels(
    reference_pixels: int,
    frame_shape: tuple[int, ...],
    *,
    minimum: int,
) -> int:
    """Scale area-like thresholds without making tiny clients permissive."""
    return max(int(minimum), round(int(reference_pixels) * resolution_area_ratio(frame_shape)))


def adaptive_exit_icon_scales(frame_shape: tuple[int, ...]) -> np.ndarray:
    """Search the same physical exit glyph across common client resolutions."""
    try:
        height, width = int(frame_shape[0]), int(frame_shape[1])
    except (TypeError, ValueError, IndexError):
        height, width = 1080, 1920
    if width < 1600:
        linear = float(np.sqrt(resolution_area_ratio(frame_shape)))
        lower = max(0.20, 0.38 * linear)
        upper = min(1.55, max(0.72, 1.20 * linear))
        step = max(0.025, min(0.05, 0.05 * linear))
        return np.arange(
            lower, upper + step * 0.5, step, dtype=np.float32,
        )

    # Keep every accepted wide-client lattice point, but do not let the old
    # 1600 px bucket discard physically smaller HUD glyphs. Use the actual
    # 40 px template size as the merge key: duplicate rendered sizes can carry
    # different scale labels into the later 6% consistency gate, so legacy
    # sizes always win and every added size gets one canonical label.
    legacy = np.arange(0.45, 1.161, 0.05)
    content_scale = min(
        float(width) / 1920.0,
        float(max(height, 1)) / 1080.0,
    )
    baseline_minimum_size = round(40.0 * float(legacy[0]))
    expected_minimum_size = round(baseline_minimum_size * content_scale)
    if expected_minimum_size >= baseline_minimum_size:
        return legacy

    # Include one template pixel below the predicted size to absorb resize and
    # antialias rounding, then cover every size up to the legacy 18 px floor.
    # Added scales are candidates only; all identity/residual/display gates are
    # unchanged. At 1919x1080 and 1920x1079 the predicted size still rounds to
    # 18 px, so both keep the exact legacy array instead of creating a new cliff.
    first_size = max(8, expected_minimum_size - 1)
    low_tail = np.asarray([
        size / 40.0
        for size in range(first_size, baseline_minimum_size)
    ], dtype=legacy.dtype)
    return np.concatenate((low_tail, legacy))


def visible_map_component(
    frame: np.ndarray,
    *,
    minimum_area: int = 1000,
) -> tuple[np.ndarray, tuple[int, int, int, int], dict] | None:
    """Return the largest explored-map island in the central game-map canvas."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    search = np.zeros_like(gray, dtype=np.uint8)
    y0, y1 = int(h * 0.22), int(h * 0.80)
    x0, x1 = int(w * 0.16), int(w * 0.76)
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
    if not candidates:
        return None
    chosen = max(candidates, key=lambda row: row["area"])
    pad = 8
    x = max(0, chosen["x"] - pad)
    y = max(0, chosen["y"] - pad)
    right = min(w, chosen["x"] + chosen["width"] + pad)
    bottom = min(h, chosen["y"] + chosen["height"] + pad)
    return frame[y:bottom, x:right], (x, y, right, bottom), chosen


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


def match_live_stair_overlap(previous: list[dict] | None, current: list[dict] | None) -> dict:
    """Find a same-screen stair group across a floor switch."""
    rows = []
    for before in previous or []:
        for after in current or []:
            if before.get("orientation") != after.get("orientation"):
                continue
            before_score = float(before.get("score", 0.0))
            after_score = float(after.get("score", 0.0))
            before_treads = int(before.get("treadCount", 0))
            after_treads = int(after.get("treadCount", 0))
            if min(before_score, after_score) < 0.80 or min(before_treads, after_treads) < 4:
                continue
            first = np.asarray(before.get("screenCenter"), dtype=np.float64)
            second = np.asarray(after.get("screenCenter"), dtype=np.float64)
            if first.shape != (2,) or second.shape != (2,):
                continue
            distance = float(np.linalg.norm(second - first))
            rows.append({
                "distancePixels": distance,
                "dx": float(second[0] - first[0]),
                "dy": float(second[1] - first[1]),
                "orientation": before.get("orientation"),
                "previousScore": before_score,
                "currentScore": after_score,
                "previousTreads": before_treads,
                "currentTreads": after_treads,
                "previousCenter": first.tolist(),
                "currentCenter": second.tolist(),
            })
    rows.sort(key=lambda row: (row["distancePixels"], -min(row["previousScore"], row["currentScore"])))
    best = rows[0] if rows else None
    return {
        "verified": bool(best and best["distancePixels"] <= 96.0),
        "best": best,
        "candidatePairs": len(rows),
    }


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


def side_door_candidate_pool(
    items: list[NightmareMap],
    evidence: dict | None,
    *,
    conflict: bool = False,
) -> tuple[list[NightmareMap], dict]:
    """Apply only fail-closed, supported side-door taxonomy evidence.

    Direction is the only current-frame side-door class that has an independent
    reliability gate. ``shape`` and ``doorCount`` remain explicit metric stages
    but cannot remove a map until a current-frame classifier exists for them.
    """
    all_items = list(items)
    before_count = len(all_items)
    detected_direction = (
        str(evidence.get("direction"))
        if evidence and evidence.get("direction") is not None else None
    )
    stage_ready = bool(evidence and evidence.get("ready"))
    reliable = bool(
        stage_ready and evidence and evidence.get("directionReliable")
    )
    supported_directions = {item.direction for item in all_items}
    supported = bool(
        reliable and detected_direction in supported_directions
    )
    fallback_reason = None
    selected = all_items
    if conflict:
        fallback_reason = "direction-conflict"
    elif not stage_ready:
        fallback_reason = "side-door-stage-not-ready"
    elif not reliable:
        fallback_reason = "direction-unreliable"
    elif not supported:
        fallback_reason = "direction-taxonomy-unsupported"
    else:
        direction_items = [
            item for item in all_items
            if item.direction == detected_direction
        ]
        if not direction_items:
            fallback_reason = "direction-no-candidate-coverage"
        elif len(direction_items) >= before_count:
            fallback_reason = "direction-does-not-reduce-pool"
        else:
            selected = direction_items

    after_direction_count = len(selected)
    pruning_applied = bool(
        fallback_reason is None and after_direction_count < before_count
    )
    return selected, {
        "candidatePoolBeforeCount": before_count,
        "candidatePoolAfterDirectionCount": after_direction_count,
        # There is deliberately no shape/door-count hard cut yet: manifest
        # metadata cannot stand in for reliable evidence from this frame.
        "candidatePoolAfterSecondaryCount": after_direction_count,
        "candidatePoolDirection": detected_direction,
        "candidatePoolSideDoorStageReady": stage_ready,
        "candidatePoolDirectionReliable": reliable,
        "candidatePoolDirectionSupported": supported,
        "candidatePoolPruningApplied": pruning_applied,
        "candidatePoolFallbackUsed": bool(fallback_reason),
        "candidatePoolFallbackReason": fallback_reason,
        "candidatePoolSecondaryStage": (
            "not-applied-no-reliable-current-frame-shape-or-door-count"
        ),
    }


class NightmarePack:
    mode='nightmare'
    floors=('1','2','B1')
    def __init__(self, manifest_path: Path = DEFAULT_MANIFEST) -> None:
        self.manifest_path = Path(manifest_path)
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.resource_root=self.manifest_path.parent.parent
        self.source_candidates=NightmareSourceCandidateStore(self.resource_root)
        configured = os.environ.get("IDENTITYV_NIGHTMARE_MAPS")
        bundled = USER_ROOT / "assets" / "nightmare-maps"
        self.source_root = Path(configured) if configured else (
            bundled if bundled.is_dir() else Path(raw["sourceRoot"])
        )
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


class NightmareMemoryStore:
    """Append-only human labels plus game-map crops used as future exemplars."""

    def __init__(
        self,
        pack: NightmarePack,
        root: Path = DEFAULT_MEMORY_ROOT,
        sample_validator: Callable[[np.ndarray], bool] | None = None,
        portable_seed_path: Path = DEFAULT_PORTABLE_LAYOUT_SEEDS,
    ) -> None:
        self.pack = pack
        self.root = Path(root)
        self.log_path = self.root / "confirmations.jsonl"
        self.alignment_acceptance_path = self.root / "alignment-acceptances.jsonl"
        self.alignment_rejection_path = self.root / "alignment-rejections.jsonl"
        self.alignment_failure_path = self.root / "alignment-failures.jsonl"
        self.alignment_success_evidence_path = (
            self.root / "alignment-success-evidence.jsonl"
        )
        self.sample_validator = sample_validator
        self.portable_seed_path = Path(portable_seed_path)
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

    def confirmation_resolution_report(self) -> dict:
        rows = self.records()
        labels: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for row in rows:
            sample_hash = str(row.get("sampleSha256") or "")
            if sample_hash:
                labels[sample_hash].add((str(row.get("mapId")), str(row.get("floor"))))
        conflicting = {key for key, values in labels.items() if len(values) > 1}
        return {
            "recordCount": len(rows),
            "resolvedCount": len(self.resolved_confirmation_records()),
            "conflictingHashCount": len(conflicting),
            "policy": "conflicting-image-hashes-quarantined",
        }

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

    def latest_alignment_acceptance(
        self,
        map_id: str,
        floor: str,
        client_size: tuple[int, int] | None = None,
    ) -> dict | None:
        """Load a compatible prior; current-frame geometry must still approve it."""
        relevant_rejections = [
            row for row in self.alignment_rejections()
            if row.get("mapId") == map_id and row.get("floor") == floor
        ]
        latest_rejection_at = max((
            str(row.get("rejectedAt", ""))
            for row in relevant_rejections
        ), default="")
        latest_rejection_sequence = max((
            int(row.get("sequenceNs", 0) or 0)
            for row in relevant_rejections
        ), default=0)
        for row in reversed(self.alignment_acceptances()):
            if row.get("mapId") != map_id or row.get("floor") != floor:
                continue
            acceptance_sequence = int(row.get("sequenceNs", 0) or 0)
            rejected_later = bool(
                latest_rejection_sequence > 0
                and acceptance_sequence > 0
                and acceptance_sequence <= latest_rejection_sequence
            )
            if acceptance_sequence == 0 and latest_rejection_at:
                rejected_later = str(row.get("acceptedAt", "")) <= latest_rejection_at
            if rejected_later:
                continue
            remembered_size = row.get("clientSize")
            if client_size and isinstance(remembered_size, list) and len(remembered_size) == 2:
                projected = project_alignment_matrix_to_client(
                    row.get("matrixSourceToClient"), remembered_size, client_size,
                )
                if projected is None:
                    continue
                if projected["sourceClientSize"] != projected["clientSize"]:
                    return {
                        **row,
                        **projected,
                        "runtimePolicy": "prior-only-revalidate-current-frame",
                    }
            return row
        return None

    def portable_layout_seeds(self) -> list[dict]:
        """Load curated geometry-only seeds; none can authorize display."""
        try:
            raw = json.loads(self.portable_seed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        policy = raw.get("policy") or {}
        if not (
            raw.get("schema") == "identityv-nightmare-portable-layout-seeds-v1"
            and policy.get("candidateOnly") is True
            and policy.get("requiresCurrentFrameValidation") is True
            and policy.get("authorizesDirectDisplay") is False
            and policy.get("containsScreenshots") is False
            and policy.get("containsUserPaths") is False
        ):
            return []
        rows = []
        for row in raw.get("seeds") or []:
            try:
                matrix = np.asarray(
                    row.get("matrixRecognitionToClient"), dtype=np.float64,
                )
                width, height = (int(value) for value in row.get("clientSize", []))
            except (TypeError, ValueError):
                continue
            if (
                row.get("mapId") not in self.pack.by_id
                or row.get("floor") not in {"1", "2", "B1"}
                or matrix.shape != (3, 3)
                or not np.isfinite(matrix).all()
                or abs(float(np.linalg.det(matrix[:2, :2]))) <= 1e-9
                or min(width, height) <= 0
            ):
                continue
            rows.append({
                **row,
                "clientSize": [width, height],
                "matrixRecognitionToClient": matrix.tolist(),
                "candidateOnly": True,
                "requiresCurrentFrameValidation": True,
                "authorizesDirectDisplay": False,
            })
        return rows

    def portable_layout_seed_candidates(
        self,
        map_id: str,
        floor: str,
        client_size: tuple[int, int],
        coordinate_adapters,
    ) -> list[dict]:
        """Project curated recognition fits through the neutral coordinate pack."""
        if map_id not in self.pack.by_id or floor not in {"1", "2", "B1"}:
            return []
        if any(
            row.get("mapId") == map_id and row.get("floor") == floor
            for row in self.alignment_rejections()
        ):
            return []
        target_rows = []
        for row in self.portable_layout_seeds():
            canonical = coordinate_adapters.canonical_from_recognition_to_client(
                str(row["mapId"]), str(row["floor"]),
                row["matrixRecognitionToClient"],
            )
            projected = project_alignment_matrix_to_client(
                canonical, row["clientSize"], client_size,
            ) if canonical is not None else None
            if projected is None:
                continue
            target = coordinate_adapters.recognition_from_canonical_to_client(
                map_id, floor, projected["matrixSourceToClient"],
            )
            if target is None:
                continue
            target_rows.append({
                **projected,
                "matrixSourceToClient": target.tolist(),
                "sourceSeedId": row.get("seedId"),
                "sourceMapId": row.get("mapId"),
                "sourceFloor": row.get("floor"),
                "targetMapId": map_id,
                "targetFloor": floor,
                "matrixCoordinateProfileId": "wang-qingxin",
                "runtimePolicy": "candidate-only-revalidate-current-frame",
            })
        return target_rows

    def record_alignment_acceptance(
        self,
        map_id: str,
        floor: str,
        fit: dict,
        client_size: tuple[int, int],
        source_frame: str | None = None,
        frame: np.ndarray | None = None,
    ) -> dict:
        """Persist one explicit visual acceptance as a revalidation-only prior."""
        matrix = np.asarray(fit.get("matrixSourceToClient"), dtype=np.float64)
        if (
            map_id not in self.pack.by_id
            or floor not in {"1", "2", "B1"}
            or not fit.get("success")
            or matrix.shape != (3, 3)
            or not np.isfinite(matrix).all()
        ):
            raise ValueError("invalid-alignment-acceptance")
        evidence_keys = (
            "templateScore", "distinctMargin", "queryPixels", "scale",
            "screenOverlap", "searchMode", "semanticPrecisionOverride",
            "exitAnchor", "visualResidual", "rotationDegrees",
            "projectedFootprint",
        )
        sample_rel = None
        if isinstance(frame, np.ndarray) and frame.ndim == 3 and frame.size:
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 94])
            if ok:
                digest = hashlib.sha256(encoded.tobytes()).hexdigest()
                sample_rel = Path("acceptances") / map_id / floor / f"{digest[:20]}.jpg"
                sample_path = self.root / sample_rel
                sample_path.parent.mkdir(parents=True, exist_ok=True)
                if not sample_path.is_file():
                    temporary = sample_path.with_name(
                        f".{sample_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                    )
                    try:
                        with temporary.open("wb") as handle:
                            handle.write(encoded.tobytes())
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, sample_path)
                    finally:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
        row = {
            "schema": "identityv-nightmare-alignment-acceptance-v1",
            "acceptedAt": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "sequenceNs": time.time_ns(),
            "mapId": map_id,
            "mapName": self.pack.by_id[map_id].name,
            "floor": floor,
            "packId": getattr(self.pack, "pack_id", None),
            "coordinatePackId": fit.get("coordinatePackId"),
            "sourceCoordinateSpace": fit.get("sourceCoordinateSpace"),
            "structureVersionKey": fit.get("structureVersionKey"),
            "sourceVersion": fit.get("sourceVersion"),
            "baseSnapshot": fit.get("baseSnapshot"),
            "status": "user-visual-accepted",
            "runtimePolicy": "prior-only-revalidate-current-frame",
            "sourceFrame": source_frame,
            "samplePath": sample_rel.as_posix() if sample_rel else None,
            "clientSize": [int(client_size[0]), int(client_size[1])],
            "alignmentMode": fit.get("alignmentMode"),
            "reason": fit.get("reason"),
            "matrixSourceToClient": matrix.tolist(),
            "evidence": {key: fit.get(key) for key in evidence_keys if key in fit},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.alignment_acceptance_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return row

    def alignment_rejections(self) -> list[dict]:
        """Return player-labelled bad fits for replay and future hardening."""
        with self._lock:
            if not self.alignment_rejection_path.is_file():
                return []
            rows = []
            for line in self.alignment_rejection_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    matrix = np.asarray(row.get("matrixSourceToClient"), dtype=np.float64)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if (
                    row.get("status") == "user-visual-rejected"
                    and row.get("mapId") in self.pack.by_id
                    and row.get("floor") in {"1", "2", "B1"}
                    and matrix.shape == (3, 3)
                    and np.isfinite(matrix).all()
                ):
                    rows.append(row)
            return rows

    def record_alignment_rejection(
        self,
        map_id: str,
        floor: str,
        fit: dict,
        frame: np.ndarray,
        client_bounds: tuple[int, int, int, int] | None = None,
    ) -> dict:
        """Save one bad visible fit and its full current frame as a regression case."""
        matrix = np.asarray(fit.get("matrixSourceToClient"), dtype=np.float64)
        if (
            map_id not in self.pack.by_id
            or floor not in {"1", "2", "B1"}
            or matrix.shape != (3, 3)
            or not np.isfinite(matrix).all()
            or not isinstance(frame, np.ndarray)
            or frame.ndim != 3
            or not frame.size
        ):
            raise ValueError("invalid-alignment-rejection")
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 94])
        if not ok:
            raise ValueError("alignment-rejection-frame-encode-failed")
        digest = hashlib.sha256(encoded.tobytes()).hexdigest()
        relative = Path("rejections") / map_id / floor / f"{digest[:20]}.jpg"
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
                "schema": "identityv-nightmare-alignment-rejection-v1",
                "rejectedAt": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "sequenceNs": time.time_ns(),
                "mapId": map_id,
                "mapName": self.pack.by_id[map_id].name,
                "floor": floor,
                "status": "user-visual-rejected",
                "runtimePolicy": "same-round-transform-veto-and-offline-regression",
                "samplePath": relative.as_posix(),
                "sampleSha256": digest,
                "clientSize": [int(frame.shape[1]), int(frame.shape[0])],
                "clientBounds": list(client_bounds) if client_bounds else None,
                "alignmentMode": fit.get("alignmentMode"),
                "reason": fit.get("reason"),
                "matrixSourceToClient": matrix.tolist(),
                "fitEvidence": {
                    key: fit.get(key) for key in (
                        "templateScore", "distinctMargin", "queryPixels", "scale",
                        "screenOverlap", "searchMode", "semanticPrecisionOverride",
                        "exitAnchor", "visualResidual", "rotationDegrees",
                        "projectedFootprint", "displayValidation",
                    ) if key in fit
                },
            }
            self.root.mkdir(parents=True, exist_ok=True)
            with self.alignment_rejection_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return row

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
        """Writer-only accounting includes old samples and the JSONL journal."""
        if self._failure_disk_bytes is None:
            self._failure_disk_bytes = sum(p.stat().st_size for p in (self.root / 'failures').rglob('*') if p.is_file())
            if self.alignment_failure_path.is_file():
                self._failure_disk_bytes += self.alignment_failure_path.stat().st_size
        if self._failure_disk_bytes + size > self._failure_sampling.max_disk_bytes:
            raise OSError('alignment-failure-budget-exhausted; existing evidence preserved')
        self._failure_disk_bytes += size

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
            return row

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


def translation_response_peak_evidence(
    response: np.ndarray,
    location: tuple[int, int],
    *,
    exclusion_radius: int,
) -> dict:
    """Measure whether a translation winner is spatially unique.

    A low residual at one repeated room is not enough to move the shared floor
    root by hundreds of pixels. Suppress a neighborhood around the winning
    peak and compare it with the best spatially distinct alternative.
    """
    scores = np.asarray(response, dtype=np.float32)
    if scores.ndim != 2 or not scores.size:
        return {
            "bestScore": 0.0,
            "secondScore": 0.0,
            "distinctMargin": 0.0,
            "exclusionRadius": max(1, int(exclusion_radius)),
        }
    x, y = int(location[0]), int(location[1])
    if not (0 <= y < scores.shape[0] and 0 <= x < scores.shape[1]):
        return {
            "bestScore": 0.0,
            "secondScore": 0.0,
            "distinctMargin": 0.0,
            "exclusionRadius": max(1, int(exclusion_radius)),
        }
    radius = max(1, int(exclusion_radius))
    best = float(scores[y, x])
    alternatives = scores.copy()
    alternatives[
        max(0, y - radius):min(scores.shape[0], y + radius + 1),
        max(0, x - radius):min(scores.shape[1], x + radius + 1),
    ] = -np.inf
    finite = alternatives[np.isfinite(alternatives)]
    second = float(finite.max()) if finite.size else 0.0
    return {
        "bestScore": best,
        "secondScore": second,
        "distinctMargin": max(0.0, best - second),
        "exclusionRadius": radius,
    }


def _binary_window_sums(reference: np.ndarray, height: int, width: int) -> np.ndarray:
    """Exact binary counts, avoiding large integral-expression temporaries.

    Wide searches have millions of valid offsets: a box filter visits all of
    them once instead of allocating three full-size subtraction intermediates.
    Narrow refinement retains the cheaper integral path. Both use exact
    integer-valued sums in float64; no search locations or scores are dropped.
    """
    rows, columns = reference.shape
    if (rows-height+1)*(columns-width+1) >= reference.size // 2:
        sums = cv2.boxFilter(
            reference, cv2.CV_64F, (width, height), anchor=(0, 0),
            normalize=False, borderType=cv2.BORDER_CONSTANT,
        )
        return sums[:rows-height+1, :columns-width+1]
    # Small uint8 windows need neither a full float32 input copy nor a
    # float64 integral image. Bound even the worst uint8 sum before choosing
    # int32; convert only the small valid result back to the original float64
    # score domain, so candidate ranking/ties retain identical arithmetic.
    integer_safe = bool(
        reference.dtype == np.uint8
        and reference.size <= np.iinfo(np.int32).max // 255
    )
    integral = cv2.integral(
        reference, sdepth=cv2.CV_32S if integer_safe else cv2.CV_64F,
    )
    counts = (integral[height:, width:] - integral[:-height, width:]
              - integral[height:, :-width] + integral[:-height, :-width])
    return counts.astype(np.float64, copy=False)


class _BinaryMaskCorrelation:
    """Two exact binary correlations sharing one transform and query spectrum.

    A complex input packs structure and validity into real/imaginary channels.
    Correlation with a real query preserves them independently. Cache lifetime
    is one fit, so spectra never cross frame or resource identities.
    """

    def __init__(self, query: np.ndarray):
        self.query = query
        self.spectra = {}

    def correlate(self, reference: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        height, width = reference.shape
        qh, qw = self.query.shape
        shape = (cv2.getOptimalDFTSize(height), cv2.getOptimalDFTSize(width))
        cached = self.spectra.get(shape)
        if cached is None:
            padded_query = np.zeros(shape, np.float32)
            padded_query[:qh, :qw] = self.query
            spectrum = cv2.dft(padded_query, flags=cv2.DFT_COMPLEX_OUTPUT)
            buffer = np.zeros((*shape, 2), np.float32)
            cached = self.spectra[shape] = spectrum, buffer
        spectrum, buffer = cached
        buffer.fill(0)
        buffer[:height, :width, 0] = reference
        buffer[:height, :width, 1] = valid
        transformed = cv2.dft(buffer, buffer, nonzeroRows=height)
        cv2.mulSpectrums(transformed, spectrum, 0, transformed, True)
        correlated = cv2.idft(transformed, transformed, flags=cv2.DFT_SCALE)
        # Reuse the lane's large FFT storage; callers own only the small valid
        # result, which must survive the next correlation on this same lane.
        counts = correlated[:height - qh + 1, :width - qw + 1].copy()
        # Binary sums are integers; remove floating FFT roundoff, not evidence.
        np.rint(counts, out=counts)
        return counts[:, :, 0], counts[:, :, 1]


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

    def _author_structure_product(
        self,
        route: np.ndarray | None,
        profile_id: str | None,
        map_id: str | None,
        floor: str | None,
    ):
        """Load an optional precomputed author product for this exact sheet.

        The callback is deliberately optional and duck-typed.  Any missing,
        malformed, or unavailable resource falls back to the existing online
        reference builders; a clean mask is never accepted without a matching
        validation mask.
        """
        shared_loader = getattr(self, "_shared_reference_loader", None)
        if callable(shared_loader):
            try:
                product = shared_loader(profile_id, map_id, floor)
            except Exception:
                return None
            clean = getattr(product, "clean_mask", None)
            validation = getattr(product, "validation_mask", None)
            if not isinstance(clean, np.ndarray) or not isinstance(validation, np.ndarray):
                return None
            if clean.ndim != 2 or validation.shape != clean.shape:
                return None
            if isinstance(route, np.ndarray) and tuple(route.shape[:2]) != tuple(clean.shape):
                return None
            return product
        direct_loader = getattr(self, "_structure_direct_resource_loader", None)
        loader = getattr(self, "_structure_resource_loader", None)
        if not callable(direct_loader) and (
            not callable(loader) or route is None
        ):
            return None
        try:
            product = (
                direct_loader(profile_id, map_id, floor)
                if callable(direct_loader) and route is None else
                loader(route, profile_id, map_id, floor)
            )
        except Exception:
            return None
        clean = getattr(product, "clean_mask", None)
        validation = getattr(product, "validation_mask", None)
        shape = tuple(route.shape[:2]) if isinstance(route, np.ndarray) else None
        if not (
            isinstance(clean, np.ndarray)
            and clean.ndim == 2
            and (shape is None or clean.shape == shape)
            and clean.dtype == np.uint8
            and clean.max(initial=0) <= 1
            and isinstance(validation, np.ndarray)
            and validation.ndim == 2
            and (shape is None or validation.shape == shape)
            and validation.dtype == np.uint8
            and validation.max(initial=0) <= 1
        ):
            return None
        return product

    def _active_structure_profile_id(self) -> str:
        provider = getattr(self, "_structure_profile_provider", None)
        if not callable(provider):
            return "wang-qingxin"
        try:
            profile_id = str(provider() or "").strip()
        except Exception:
            return "wang-qingxin"
        return profile_id or "wang-qingxin"

    def _shared_structure_enabled(self) -> bool:
        return callable(getattr(self, "_shared_reference_loader", None))

    def _shared_structure_product(self, map_id: str, floor: str):
        loader = getattr(self, "_shared_reference_loader", None)
        if not callable(loader):
            return None
        try:
            return loader(self._active_structure_profile_id(), map_id, floor)
        except Exception:
            return None

    def _structure_reference_for(
        self, item_id: str, floor: str, profile_id: str,
    ) -> _Features | None:
        if profile_id != "wang-qingxin":
            return getattr(self, "_profile_structure_references", {}).get(
                (profile_id, item_id, floor),
            )
        return getattr(self, "_structure_references", {}).get((item_id, floor))

    def _semantic_source_for(
        self, item_id: str, floor: str, profile_id: str,
    ) -> SemanticFingerprint | None:
        if profile_id != "wang-qingxin":
            return getattr(self, "_profile_semantic_source_references", {}).get(
                (profile_id, item_id, floor),
            )
        return getattr(self, "_semantic_source_references", {}).get(
            (item_id, floor),
        )

    def _refresh_structure_preload_diagnostics(
        self, floor: str, profile_id: str,
    ) -> None:
        """Record the structure source actually available to ranking."""
        clean_keys = getattr(self, "_profile_clean_reference_keys", set())
        clean_count = 0 if profile_id == "wang-qingxin" else sum(
            (profile_id, item.id, floor) in clean_keys for item in self.pack.maps
        )
        self._structure_preload_diagnostics.update({
            "profileId": profile_id,
            "cleanReferenceUsed": bool(clean_count),
            "cleanReferenceCount": int(clean_count),
            "onlineReferenceFallbackCount": int(
                max(0, len(self.pack.maps) - clean_count)
            ),
        })

    def _ensure_structure_cache_context(self, profile_id: str) -> None:
        """Invalidate only profile structure data when its context changes."""
        provider = getattr(self, "_structure_cache_context_provider", None)
        try:
            context = str(provider() or "") if callable(provider) else ""
        except Exception:
            context = ""
        context = f"{profile_id}|{context}"
        if getattr(self, "_structure_cache_context", None) == context:
            return
        getattr(self, "_profile_structure_references", {}).clear()
        getattr(self, "_profile_semantic_source_references", {}).clear()
        getattr(self, "_profile_clean_reference_keys", set()).clear()
        self._structure_cache_context = context

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

    def align_selected_fast(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
    ) -> dict | None:
        """Run the independent confirmed-map fast path without the slow-fit lock."""
        if map_id not in self.pack.by_id or floor != "2":
            return None
        if self._shared_structure_enabled():
            return None
        route = self.route_image(map_id, floor)
        if route is None:
            return None
        return self._align_selected_exit_fast(frame, map_id, floor, route)

    def align_portable_layout_seed_fast(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        seeds: list[dict] | None,
        *,
        budget_ms: float = 1450.0,
    ) -> dict | None:
        """Revalidate portable neutral-coordinate seeds on the current frame.

        The seed owns neither identity nor display permission.  Each candidate
        keeps scale/rotation fixed. B1 may use the existing wide translation
        recovery; F1/F2 are hard-limited to a 180 px local search and can never
        invoke global recenter. Only the exact selected route residual can
        turn a seed into a successful fit.
        """
        if (
            frame is None
            or map_id not in self.pack.by_id
            or floor not in {"1", "2", "B1"}
            or not isinstance(seeds, list)
            or not seeds
        ):
            return None
        if self._shared_structure_enabled():
            return None
        route = self.route_image(map_id, floor)
        if route is None:
            return None
        started = time.perf_counter()
        upstairs_local_only = floor in {"1", "2"}
        maximum_shift = (
            180.0 if upstairs_local_only
            else min(720.0, max(180.0, max(frame.shape[:2]) * 0.44))
        )
        attempts = []
        seen_linear = set()
        for seed in seeds:
            if (time.perf_counter() - started) * 1000.0 >= float(budget_ms):
                break
            if not isinstance(seed, dict):
                continue
            matrix = np.asarray(seed.get("matrixSourceToClient"), dtype=np.float64)
            if (
                matrix.shape != (3, 3)
                or not np.isfinite(matrix).all()
                or abs(float(np.linalg.det(matrix[:2, :2]))) <= 1e-9
            ):
                continue
            linear_key = tuple(np.round(matrix[:2, :2].reshape(-1), 4))
            if linear_key in seen_linear:
                continue
            seen_linear.add(linear_key)
            refined = self.refine_selected_route_translation_fast(
                frame, route, matrix,
                maximum_shift=maximum_shift,
                global_recenter=not upstairs_local_only,
                route_profile_id="wang-qingxin",
            )
            if refined is None:
                attempts.append({
                    "sourceSeedId": seed.get("sourceSeedId"),
                    "reason": "portable-seed-translation-unavailable",
                })
                continue
            attempts.append({
                "sourceSeedId": seed.get("sourceSeedId"),
                "reason": refined.get("reason"),
                "success": bool(refined.get("success")),
                "searchElapsedMs": refined.get("searchElapsedMs"),
            })
            if not refined.get("success"):
                continue
            return {
                **refined,
                "success": True,
                "reason": "portable-layout-seed-current-frame-verified",
                "alignmentMode": "portable-layout-seed-current-frame",
                "searchMode": (
                    "portable-neutral-seed-local-translation"
                    if upstairs_local_only else
                    "portable-neutral-seed-wide-translation"
                ),
                "globalRecenterUsed": not upstairs_local_only,
                "maximumTranslationPixels": maximum_shift,
                "mapId": map_id,
                "floor": floor,
                "plausibleGeometry": True,
                "acceptedPriorUsed": False,
                "portableLayoutSeedUsed": True,
                "portableLayoutSeedId": seed.get("sourceSeedId"),
                "portableSeedAttempts": attempts,
                "portableSeedElapsedMs": round(
                    (time.perf_counter() - started) * 1000.0, 1,
                ),
                "candidateOnly": False,
                "currentFrameDisplayValidated": True,
            }
        return {
            "success": False,
            "reason": "portable-layout-seed-current-frame-unverified",
            "alignmentMode": "portable-layout-seed-current-frame",
            "plausibleGeometry": False,
            "portableLayoutSeedUsed": False,
            "portableSeedAttempts": attempts,
            "portableSeedElapsedMs": round(
                (time.perf_counter() - started) * 1000.0, 1,
            ),
            "exhaustiveSearchSkipped": False,
        }

    def align_selected_author_fast(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        profile_id: str,
        route: np.ndarray | None,
        source_icon_search_bounds: tuple[int, int, int, int] | None = None,
        profile_to_recognition: list[list[float]] | np.ndarray | None = None,
        return_failure: bool = False,
    ) -> dict | None:
        """Fit an already-confirmed alternate-author F2 sheet directly.

        This bypasses the legacy recognition sheet only after map identity is
        locked.  The author sheet must independently provide a unique exit
        icon plus the same wall-residual proof as the normal fast path.
        """
        if self._shared_structure_enabled():
            return None
        if (
            map_id not in self.pack.by_id
            or floor != "2"
            or not profile_id
            or not isinstance(route, np.ndarray)
        ):
            return None
        height, width = route.shape[:2]
        source_icon_override = None
        reviewed_anchor = self._precomputed_source_exit_icons.get(map_id)
        adapter = np.asarray(profile_to_recognition, dtype=np.float64)
        if (
            isinstance(reviewed_anchor, dict)
            and adapter.shape == (3, 3)
            and np.isfinite(adapter).all()
        ):
            try:
                inverse = np.linalg.inv(adapter)
            except np.linalg.LinAlgError:
                inverse = None
            linear_scale = float(
                np.sqrt(abs(np.linalg.det(adapter[:2, :2])))
            )
            point = (
                inverse @ np.asarray([
                    *reviewed_anchor.get("center", ()), 1.0,
                ], dtype=np.float64)
                if inverse is not None
                and len(reviewed_anchor.get("center", ())) == 2
                and linear_scale > 1e-6 else None
            )
            if (
                point is not None
                and abs(float(point[2])) > 1e-9
                and np.isfinite(point).all()
            ):
                center = point[:2] / point[2]
                source_icon_override = {
                    **reviewed_anchor,
                    "center": center.tolist(),
                    "scale": float(reviewed_anchor.get("scale", 0.0))
                    / linear_scale,
                    # The location and scale come from two independently
                    # reviewed records: the canonical exit anchor and the
                    # author-to-recognition adapter.  Do not inherit a weak
                    # visual margin caused by route labels drawn near the icon.
                    "score": max(float(reviewed_anchor.get("score", 0.0)), 0.90),
                    "distinctMargin": max(
                        float(reviewed_anchor.get("distinctMargin", 0.0)), 0.25,
                    ),
                    "reviewedAuthorAdapterAnchor": True,
                }
        reviewed_result = self._align_selected_exit_fast(
            frame, map_id, floor, route,
            cache_key=(str(profile_id), map_id, floor),
            source_icon_search_bounds=(
                source_icon_search_bounds or (0, 0, width, height)
            ),
            source_icon_override=source_icon_override,
            return_failure=return_failure,
        )
        if reviewed_result is not None and reviewed_result.get("success"):
            return reviewed_result
        # Some author sheets redraw the exit a few pixels away from the
        # recognition-sheet anchor.  The reviewed adapter remains the first
        # hypothesis, but a strong route-pixel icon plus the same independent
        # wall residual may recover that local artwork offset (map-03).  No
        # coarse result is emitted between the two hypotheses.
        if source_icon_override is not None:
            return self._align_selected_exit_fast(
                frame, map_id, floor, route,
                cache_key=(str(profile_id), map_id, floor),
                source_icon_search_bounds=(
                    source_icon_search_bounds or (0, 0, width, height)
                ),
                return_failure=return_failure,
            )
        return reviewed_result

    def cross_floor_seed_matrix(
        self,
        map_id: str,
        from_floor: str,
        to_floor: str,
        source_matrix: list[list[float]] | np.ndarray,
    ) -> dict | None:
        """Project a verified transform through the offline floor atlas."""
        # The first production path is deliberately limited to the well-covered
        # F1/F2 pair. B1 candidates remain in the audit index until more live
        # examples prove which basement stair is actually traversable.
        if {from_floor, to_floor} != {"1", "2"}:
            return None
        transfer = self._cross_floor_transfers.get((map_id, from_floor, to_floor))
        if not transfer or float(transfer.get("confidence", 0.0)) < 0.65:
            return None
        previous = np.asarray(source_matrix, dtype=np.float64)
        target_to_source = np.asarray(
            transfer.get("targetToSourceMatrix"), dtype=np.float64,
        )
        if (
            previous.shape != (3, 3)
            or target_to_source.shape != (3, 3)
            or not np.isfinite(previous).all()
            or not np.isfinite(target_to_source).all()
        ):
            return None
        predicted = previous @ target_to_source
        singular = np.linalg.svd(predicted[:2, :2], compute_uv=False)
        rotation = float(np.degrees(np.arctan2(predicted[1, 0], predicted[0, 0])))
        if (
            singular.min() <= 0
            or singular.max() / singular.min() > 1.05
            or abs(rotation) > 3.0
        ):
            return None
        landmarks = list(transfer.get("landmarks") or [])
        metrics = dict(transfer.get("metrics") or {})
        reviewed_landmarks = [
            row for row in landmarks
            if str(row.get("reviewStatus", "")).startswith("geometry-reviewed")
            and not row.get("primary", False)
        ]
        atlas_verified = bool(
            transfer.get("reviewedMappingSearchSeedAllowed")
            and transfer.get("reviewStatus") == "user-approved-multi-anchor-consensus"
            and transfer.get("anchorPolicy") == "equal-vote-multi-anchor"
            and len(reviewed_landmarks) >= 3
            and float(metrics.get("leaveOneOutMaxPixels", 999.0)) <= 10.0
        )
        reviewed_residual = dict(metrics.get("reviewedGeometricResidual") or {})
        restricted_live_test_verified = bool(
            map_id == "nightmare-03"
            and transfer.get("restrictedLiveTestSearchSeedAllowed")
            and transfer.get("liveTestScope") == "nightmare-03-only"
            and transfer.get("liveVisualConfirmationPending") is True
            and transfer.get("reviewStatus") == "user-approved-three-room-live-test"
            and transfer.get("anchorPolicy") == "equal-vote-multi-anchor"
            and len(reviewed_landmarks) >= 3
            and float(reviewed_residual.get("p90Pixels", 999.0)) <= 5.0
            and float(reviewed_residual.get("maxPixels", 999.0)) <= 6.0
        )
        direct_projection_allowed = False
        return {
            "mapId": map_id,
            "matrixSourceToClient": predicted.tolist(),
            "fromFloor": from_floor,
            "toFloor": to_floor,
            "confidence": float(transfer.get("confidence", 0.0)),
            "translation": transfer.get("translation"),
            "scaleRatio": transfer.get("scaleRatio"),
            "rotationDegrees": transfer.get("rotationDegrees"),
            "scaleCalibrationStatus": transfer.get("scaleCalibrationStatus"),
            "requiresIndependentScaleCalibration": bool(
                transfer.get("requiresIndependentScaleCalibration", False)
            ),
            "clusterMatches": int(
                transfer.get("clusterMatches", 0)
                or (transfer.get("featureEvidence") or {}).get("clusterMatches", 0)
            ),
            "semanticStairGroups": int(
                transfer.get("semanticStairGroups", 0)
                or sum(
                    row.get("kind") == "stair-room"
                    for row in transfer.get("landmarks", [])
                )
            ),
            "anchors": transfer.get("anchors", []),
            "landmarks": landmarks,
            "landmarkCount": int(
                metrics.get(
                    "landmarkCount", len(landmarks),
                )
            ),
            "overlapCoverage": float(
                metrics.get("overlapCoverage", 0.0)
            ),
            "anchorPolicy": transfer.get("anchorPolicy"),
            "reviewStatus": transfer.get("reviewStatus"),
            "reviewedLandmarkCount": len(reviewed_landmarks),
            "consensusCornerMaxPixels": metrics.get("consensusCornerMaxPixels"),
            "leaveOneOutMaxPixels": metrics.get("leaveOneOutMaxPixels"),
            "reviewedP90Pixels": reviewed_residual.get("p90Pixels"),
            "reviewedMaximumPixels": reviewed_residual.get("maxPixels"),
            "parallelLinesWeakOnly": True,
            "atlasVerified": atlas_verified,
            "restrictedLiveTestVerified": restricted_live_test_verified,
            "liveTestProjectionAllowed": restricted_live_test_verified,
            "liveTestScope": transfer.get("liveTestScope"),
            "liveVisualConfirmationPending": bool(
                transfer.get("liveVisualConfirmationPending", False)
            ),
            "directProjectionAllowed": direct_projection_allowed,
            "transferSource": (
                "offline-multi-landmark-atlas"
                if atlas_verified else
                "offline-human-reviewed-live-test"
                if restricted_live_test_verified else
                "offline-stair-candidate"
            ),
        }

    def refine_cross_floor_seed_from_live_landmark(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        cross_floor_seed: dict | None,
    ) -> dict | None:
        """Correct a projected floor after the game recenters the map.

        On first entering F1 the visible island is commonly just the shared
        stair/landing room.  Matching that island to the atlas landmark yields
        scale and translation without waiting for any surrounding room shape.
        Failure is non-destructive: callers may keep the inherited same-viewport
        projection, or require this correction when reopening a recentered map.
        """
        if not isinstance(cross_floor_seed, dict) or frame is None:
            return cross_floor_seed
        if cross_floor_seed.get("toFloor") != floor:
            return cross_floor_seed
        seed_matrix = np.asarray(
            cross_floor_seed.get("matrixSourceToClient"), dtype=np.float64,
        )
        if seed_matrix.shape != (3, 3) or not np.isfinite(seed_matrix).all():
            return cross_floor_seed
        seed_scale = float(np.sqrt(abs(np.linalg.det(seed_matrix[:2, :2]))))
        if seed_scale <= 0:
            return cross_floor_seed
        stair_motion = match_live_stair_overlap(
            cross_floor_seed.get("previousLiveStairs"),
            live_stair_screen_candidates(frame),
        )
        stair_result = None
        if stair_motion.get("verified"):
            best_motion = stair_motion.get("best") or {}
            stair_result = dict(cross_floor_seed)
            # Repeated parallel lines are useful for ordering a local search,
            # but the map07 audit showed that they miss the obvious staircase
            # and fire on text/doors.  Keep the motion as a weak hint only: it
            # must not move a visible overlay or satisfy the reopen gate alone.
            stair_result["weakLiveStairMotion"] = {
                "verified": False,
                "landmarkId": "live-stair-motion",
                "kind": "live-stair-motion",
                "score": round(min(
                    float(best_motion.get("previousScore", 0.0)),
                    float(best_motion.get("currentScore", 0.0)),
                ), 4),
                "scale": round(seed_scale, 6),
                "inheritedScale": round(seed_scale, 6),
                "recenterPixels": round(
                    float(best_motion.get("distancePixels", 0.0)), 2,
                ),
                "dx": round(float(best_motion.get("dx", 0.0)), 2),
                "dy": round(float(best_motion.get("dy", 0.0)), 2),
            }
        # A freshly entered first-floor landing can contain far fewer pixels
        # than an ordinary explored map island.  This relaxed threshold is
        # used only after map UI, round identity and source-floor verification.
        component = visible_map_component(frame, minimum_area=240)
        if component is None:
            return stair_result or cross_floor_seed
        _patch, _bounds, live = component
        transfer = self._cross_floor_transfers.get((
            map_id, str(cross_floor_seed.get("fromFloor")), floor,
        ))
        landmarks = list((transfer or {}).get("landmarks", []))
        if not landmarks:
            return stair_result or cross_floor_seed
        if (
            (
                cross_floor_seed.get("atlasVerified")
                or cross_floor_seed.get("restrictedLiveTestVerified")
            )
            and cross_floor_seed.get("anchorPolicy") == "equal-vote-multi-anchor"
        ):
            # A single visible island cannot tell restaurant, theatre, library
            # and stair room apart reliably.  Reopen correction for a reviewed
            # atlas therefore waits for the whole-patch local verifier; an
            # in-place floor-tab switch still uses the offline consensus with
            # require_recenter=False.
            result = stair_result or dict(cross_floor_seed)
            result["liveLandmarkCorrection"] = {
                "verified": False,
                "kind": "multi-anchor-consensus",
                "reason": "single-live-component-cannot-authorize-recenter",
                "requiredAnchorCount": 2,
            }
            return result
        live_width = max(float(live.get("width", 0)), 1.0)
        live_height = max(float(live.get("height", 0)), 1.0)
        live_center = np.asarray(live.get("center"), dtype=np.float64)
        if live_center.shape != (2,) or not np.isfinite(live_center).all():
            return stair_result or cross_floor_seed

        rows = []
        ordered = sorted(
            landmarks,
            key=lambda row: row.get("kind") != "stair-room",
        )
        for landmark in ordered:
            target_size = np.asarray(landmark.get("targetSize"), dtype=np.float64)
            target_center = np.asarray(landmark.get("targetCenter"), dtype=np.float64)
            if (
                target_size.shape != (2,) or target_center.shape != (2,)
                or not np.isfinite(target_size).all()
                or not np.isfinite(target_center).all()
                or np.min(target_size) < 8.0
            ):
                continue
            scale_x = live_width / float(target_size[0])
            scale_y = live_height / float(target_size[1])
            anisotropy = max(scale_x, scale_y) / max(min(scale_x, scale_y), 1e-6)
            # Fog and route annotations may split one side of the room while
            # the other dimension remains intact.  Prefer the dimension that
            # agrees with the inherited verified-floor scale; never allow this
            # shortcut to invent a remote zoom level.
            scale = float(min(
                (scale_x, scale_y),
                key=lambda value: abs(value - seed_scale),
            ))
            scale_change = abs(scale - seed_scale) / max(seed_scale, 1e-6)
            dimension_support = min(scale_x, scale_y) / max(scale_x, scale_y)
            if (
                scale_change > 0.28
                or (anisotropy > 1.80 and dimension_support < 0.52)
            ):
                continue
            predicted_center = (
                seed_matrix @ np.asarray([target_center[0], target_center[1], 1.0])
            )[:2]
            center_distance = float(np.linalg.norm(live_center - predicted_center))
            kind_bonus = 0.22 if landmark.get("kind") == "stair-room" else 0.0
            score = (
                kind_bonus
                + max(0.0, 1.0 - (anisotropy - 1.0) / 0.80) * 0.42
                + max(0.0, 1.0 - scale_change / 0.28) * 0.28
                + max(0.0, 1.0 - center_distance / 420.0) * 0.08
            )
            rows.append({
                "score": score,
                "scale": scale,
                "anisotropy": anisotropy,
                "scaleChange": scale_change,
                "centerDistance": center_distance,
                "landmark": landmark,
                "targetCenter": target_center,
            })
        if not rows:
            return stair_result or cross_floor_seed
        rows.sort(key=lambda row: row["score"], reverse=True)
        best = rows[0]
        if best["score"] < 0.66:
            return stair_result or cross_floor_seed
        scale = float(best["scale"])
        target_center = best["targetCenter"]
        corrected = np.asarray([
            [scale, 0.0, live_center[0] - target_center[0] * scale],
            [0.0, scale, live_center[1] - target_center[1] * scale],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        result = dict(cross_floor_seed)
        result["matrixSourceToClient"] = corrected.tolist()
        result["liveLandmarkCorrection"] = {
            "verified": True,
            "landmarkId": best["landmark"].get("id"),
            "kind": best["landmark"].get("kind"),
            "score": round(float(best["score"]), 4),
            "scale": round(scale, 6),
            "inheritedScale": round(seed_scale, 6),
            "anisotropy": round(float(best["anisotropy"]), 4),
            "recenterPixels": round(float(best["centerDistance"]), 2),
        }
        result["recenterVerified"] = True
        return result

    def align_selected_cross_floor_fast(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        cross_floor_seed: dict | None,
    ) -> dict | None:
        """Refine an F1/F2 mapped position in a small current-frame window.

        Unreviewed stair candidates never authorize display. A reviewed
        multi-room mapping may already own a zero-exploration projection; this
        path then provides an atomic target-frame correction once wall pixels
        appear. Wrong or weak candidates fall through to
        the ordinary exhaustive search.
        """
        if not cross_floor_seed or map_id not in self.pack.by_id:
            return None
        if self._shared_structure_enabled():
            return None
        route = self.route_image(map_id, floor)
        if route is None:
            return None
        predicted = np.asarray(
            cross_floor_seed.get("matrixSourceToClient"), dtype=np.float64,
        )
        if predicted.shape != (3, 3) or not np.isfinite(predicted).all():
            return None
        visible = visible_map_component(frame)
        if visible is None:
            return None
        patch, bounds, component = visible
        query = map_color_mask(patch, live=True).astype(np.uint8)
        query_pixels = int(query.sum())
        if query_pixels < 3000:
            return None
        live_stair_overlap = match_live_stair_overlap(
            cross_floor_seed.get("previousLiveStairs"),
            live_stair_screen_candidates(frame),
        )

        key = (map_id, floor)
        cached_reference = self._partial_reference_masks.get(key)
        if cached_reference is None:
            full_reference = map_color_mask(route, live=False).astype(np.uint8)
            hsv = cv2.cvtColor(route, cv2.COLOR_BGR2HSV)
            _hue, saturation, value = cv2.split(hsv)
            annotation = ((saturation >= 82) & (value >= 105)) | (value >= 218)
            annotation = cv2.dilate(
                annotation.astype(np.uint8), np.ones((5, 5), np.uint8),
            )
            full_valid = (annotation == 0).astype(np.uint8)
            self._partial_reference_masks[key] = (
                full_reference, full_valid, (0, 0),
            )
        else:
            full_reference, full_valid, _offset = cached_reference

        seed_scale = float(np.sqrt(abs(np.linalg.det(predicted[:2, :2]))))
        if not 0.03 <= seed_scale <= 6.0:
            return None
        anchors = list(
            cross_floor_seed.get("landmarks")
            or cross_floor_seed.get("anchors")
            or []
        )
        equal_vote = bool(
            (
                cross_floor_seed.get("atlasVerified")
                or cross_floor_seed.get("restrictedLiveTestVerified")
            )
            and cross_floor_seed.get("anchorPolicy") == "equal-vote-multi-anchor"
        )
        reviewed_centers = [
            np.asarray(row.get("targetCenter"), dtype=np.float64)
            for row in anchors
            if str(row.get("reviewStatus", "")).startswith("geometry-reviewed")
            and not row.get("primary", False)
        ]
        reviewed_centers = [
            center for center in reviewed_centers
            if center.shape == (2,) and np.isfinite(center).all()
        ]
        if equal_vote and len(reviewed_centers) >= 3:
            # Use the geometric median proxy of every reviewed landmark.  The
            # scale sweep therefore stays centered on the atlas consensus and
            # cannot silently turn the first room (historically restaurant)
            # into a master anchor.
            target_anchor = np.median(np.asarray(reviewed_centers), axis=0)
            anchor_selection = "equal-vote-reviewed-centroid"
        else:
            semantic_anchor = next(
                (row for row in anchors if row.get("kind") == "stair-pattern"),
                anchors[0] if anchors else None,
            )
            if semantic_anchor:
                target_anchor = np.asarray(
                    semantic_anchor.get("targetCenter"), dtype=np.float64,
                )
                anchor_selection = "legacy-single-candidate"
            else:
                target_anchor = np.asarray(
                    [route.shape[1] / 2.0, route.shape[0] / 2.0], dtype=np.float64,
                )
                anchor_selection = "reference-center"
        if target_anchor.shape != (2,) or not np.isfinite(target_anchor).all():
            return None
        anchor_screen = (
            predicted @ np.asarray([target_anchor[0], target_anchor[1], 1.0])
        )[:2]
        qh, qw = query.shape
        screen_x, screen_y = (float(value) for value in bounds[:2])
        padding = max(42, min(110, round(max(qw, qh) * 0.16)))
        rows: list[dict] = []
        if cross_floor_seed.get("transferSource") == "offline-multi-landmark-atlas":
            # The game may choose a slightly different viewport zoom for F1
            # and F2 even though the clean sheets share one pixel scale.  A
            # sparse eight-point sweep reaches the observed ~9% difference
            # without restoring the old exhaustive multi-second search.
            scale_values = seed_scale * np.asarray([
                0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.09, 1.12,
            ], dtype=np.float64)
        else:
            scale_values = np.linspace(seed_scale * 0.965, seed_scale * 1.035, 9)
        for scale in scale_values:
            width = max(1, round(full_reference.shape[1] * scale))
            height = max(1, round(full_reference.shape[0] * scale))
            if width < qw or height < qh:
                continue
            resized = cv2.resize(
                full_reference, (width, height), interpolation=cv2.INTER_NEAREST,
            ).astype(np.float32)
            resized_valid = cv2.resize(
                full_valid, (width, height), interpolation=cv2.INTER_NEAREST,
            ).astype(np.float32)
            expected_tx = float(anchor_screen[0] - target_anchor[0] * scale)
            expected_ty = float(anchor_screen[1] - target_anchor[1] * scale)
            expected_x = screen_x - expected_tx
            expected_y = screen_y - expected_ty
            x0 = max(0, int(np.floor(expected_x - padding)))
            y0 = max(0, int(np.floor(expected_y - padding)))
            x1 = min(width, int(np.ceil(expected_x + qw + padding)))
            y1 = min(height, int(np.ceil(expected_y + qh + padding)))
            if x1 - x0 < qw or y1 - y0 < qh:
                continue
            search = resized[y0:y1, x0:x1]
            valid = resized_valid[y0:y1, x0:x1]
            q = query.astype(np.float32)
            positive = float(q.sum())
            intersection = cv2.matchTemplate(search, q, cv2.TM_CCORR)
            integral = cv2.integral(search)
            reference_count = (
                integral[qh:, qw:] - integral[:-qh, qw:]
                - integral[qh:, :-qw] + integral[:-qh, :-qw]
            )
            query_valid = cv2.matchTemplate(valid, q, cv2.TM_CCORR)
            compared_fraction = query_valid / max(positive, 1.0)
            dice = (2.0 * intersection) / np.maximum(
                query_valid + reference_count, 1.0,
            )
            dice = np.minimum(dice, 1.0)
            dice[compared_fraction < 0.45] = -1.0
            remaining = dice.copy()
            for _rank in range(3):
                _minimum, maximum, _min_location, location = cv2.minMaxLoc(remaining)
                source_scaled_x = float(x0 + location[0])
                source_scaled_y = float(y0 + location[1])
                tx = screen_x - source_scaled_x
                ty = screen_y - source_scaled_y
                expected_best_tx = float(anchor_screen[0] - target_anchor[0] * scale)
                expected_best_ty = float(anchor_screen[1] - target_anchor[1] * scale)
                rows.append({
                    "score": float(maximum),
                    "scale": float(scale),
                    "tx": tx,
                    "ty": ty,
                    "seedTranslationError": float(np.hypot(
                        tx - expected_best_tx, ty - expected_best_ty,
                    )),
                })
                suppress_x = max(8, qw // 4)
                suppress_y = max(8, qh // 4)
                lx, ly = location
                remaining[
                    max(0, ly - suppress_y):min(remaining.shape[0], ly + suppress_y + 1),
                    max(0, lx - suppress_x):min(remaining.shape[1], lx + suppress_x + 1),
                ] = -1.0
        if not rows:
            return None
        rows.sort(key=lambda row: row["score"], reverse=True)
        best = rows[0]
        distinct_scale_separation = (
            0.055
            if cross_floor_seed.get("transferSource") == "offline-multi-landmark-atlas"
            else 0.025
        )
        alternative = next((
            row for row in rows[1:]
            if (
                np.hypot(row["tx"] - best["tx"], row["ty"] - best["ty"]) >= 18.0
                or abs(row["scale"] - best["scale"]) / max(best["scale"], 0.001)
                >= distinct_scale_separation
            )
        ), None)
        distinct_score = float((alternative or {}).get("score", 0.0))
        margin = float(best["score"] - distinct_score)
        scale = float(best["scale"])
        matrix = np.asarray([
            [scale, 0.0, best["tx"]],
            [0.0, scale, best["ty"]],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)

        source_to_query = matrix[:2].astype(np.float32).copy()
        source_to_query[0, 2] -= screen_x
        source_to_query[1, 2] -= screen_y
        projected_reference = cv2.warpAffine(
            full_reference, source_to_query, (qw, qh),
            flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        kernel = np.ones((3, 3), np.uint8)
        query_edges = cv2.morphologyEx(query, cv2.MORPH_GRADIENT, kernel) > 0
        projected_edges = cv2.morphologyEx(
            projected_reference, cv2.MORPH_GRADIENT, kernel,
        ) > 0
        edge_count = int(query_edges.sum())
        if edge_count < 200 or not projected_edges.any():
            return None
        distance = cv2.distanceTransform(
            (~projected_edges).astype(np.uint8), cv2.DIST_L2, 3,
        )[query_edges]
        edge_median = float(np.median(distance))
        edge_p90 = float(np.percentile(distance, 90))
        edge_within_three = float(np.mean(distance <= 3.0))
        projected_width = route.shape[1] * scale
        projected_height = route.shape[0] * scale
        fh, fw = frame.shape[:2]
        intersection_area = (
            max(0.0, min(best["tx"] + projected_width, fw) - max(best["tx"], 0.0))
            * max(0.0, min(best["ty"] + projected_height, fh) - max(best["ty"], 0.0))
        )
        box_area = max(1.0, projected_width * projected_height)
        screen_overlap = intersection_area / min(box_area, float(fw * fh))
        scale_delta_limit = (
            0.125
            if cross_floor_seed.get("atlasVerified") else 0.04
        )
        strict_success = bool(
            float(cross_floor_seed.get("confidence", 0.0)) >= 0.65
            and best["score"] >= 0.76
            and margin >= 0.008
            and best["seedTranslationError"] <= padding * 1.05
            and abs(scale - seed_scale) / max(seed_scale, 0.001) <= scale_delta_limit
            and edge_median <= 3.5
            and edge_p90 <= 18.0
            and edge_within_three >= 0.55
            and screen_overlap >= 0.05
        )
        # Current real F2->F1 footage shows the same stair-tread group staying
        # nearly fixed on screen, while dynamic arrows/fog make 55% of all
        # visible wall edges falling within three pixels unnecessarily strict.
        # This branch does not trust the overlap alone: the confirmed target
        # map must still win the whole visible-island search, agree with the
        # previous scale, and keep the residual tail bounded.
        live_stair_success = bool(
            live_stair_overlap.get("verified")
            and float(cross_floor_seed.get("confidence", 0.0)) >= 0.80
            and best["score"] >= 0.79
            and margin >= (
                0.004 if cross_floor_seed.get("atlasVerified") else 0.010
            )
            and best["seedTranslationError"] <= padding * 1.05
            and abs(scale - seed_scale) / max(seed_scale, 0.001) <= scale_delta_limit
            and edge_median <= 4.2
            and edge_p90 <= 18.0
            and edge_within_three >= 0.43
            and screen_overlap >= 0.05
        )
        success = bool(strict_success or live_stair_success)
        return {
            "success": success,
            "reason": (
                "aligned-cross-floor-live-stair-overlap"
                if live_stair_success and not strict_success else
                "aligned-cross-floor-stair-anchor"
                if success else "cross-floor-stair-residual-failed"
            ),
            "alignmentMode": "partial-structure-dice",
            "plausibleGeometry": screen_overlap >= 0.05,
            "score": float(best["score"]),
            "templateScore": float(best["score"]),
            "distinctScore": distinct_score,
            "distinctMargin": margin,
            "transformDistinctScore": distinct_score,
            "transformDistinctMargin": margin,
            "queryPixels": query_pixels,
            "searchMode": (
                "cross-floor-live-stair-overlap"
                if live_stair_success and not strict_success else
                "cross-floor-stair-local"
            ),
            "searchBounds": None,
            "boundaryExpanded": False,
            "acceptedPriorUsed": False,
            "crossFloorSeed": {
                key: cross_floor_seed.get(key)
                for key in (
                    "fromFloor", "toFloor", "confidence", "translation",
                    "clusterMatches", "semanticStairGroups",
                )
            },
            "anchorSelection": anchor_selection,
            "reviewedAnchorCount": len(reviewed_centers),
            "seedTranslationError": float(best["seedTranslationError"]),
            "liveStairOverlap": live_stair_overlap,
            "semanticPrecisionOverride": bool(live_stair_success),
            "visualResidual": {
                "queryEdgeCount": edge_count,
                "medianPixels": edge_median,
                "p90Pixels": edge_p90,
                "withinThreePixels": edge_within_three,
            },
            "visibleComponent": component,
            "scale": scale,
            "anisotropy": 1.0,
            "screenOverlap": float(screen_overlap),
            "inliers": 0,
            "inlierRatio": 0.0,
            "medianError": edge_median,
            "matrixSourceToClient": matrix.tolist(),
        }

    def refine_selected_route_translation_fast(
        self,
        frame: np.ndarray,
        route: np.ndarray,
        matrix_source_to_client,
        *,
        maximum_shift: float = 96.0,
        global_recenter: bool = False,
        route_profile_id: str | None = None,
        visible_override: tuple | None = None,
        map_id: str | None = None,
        floor: str | None = None,
    ) -> dict | None:
        """Correct only translation before a reviewed floor layer is shown.

        The shared-floor relation already owns scale and rotation.  Search a
        screen-space translation at exactly that linear transform, validate
        the final author pixels, and return one atomic replacement.  Ordinary
        same-floor tracking keeps the small local window.  A reviewed
        cross-floor layer group may opt into a much wider recenter window when
        the game has auto-centred the newly selected floor somewhere else;
        scale and rotation remain locked in both modes.
        """

        search_started = time.perf_counter()
        shared_mode = self._shared_structure_enabled()
        if frame is None or (route is None and not shared_mode):
            return None
        matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
        if (
            matrix.shape != (3, 3)
            or not np.isfinite(matrix).all()
            or abs(float(np.linalg.det(matrix[:2, :2]))) <= 1e-9
        ):
            return None
        visible = visible_override or visible_map_component(frame)
        if visible is None:
            return None
        patch, bounds, component = visible
        query = map_color_mask(patch, live=True).astype(np.uint8)
        full_query = query.copy()
        query_pixels = int(query.sum())
        if query_pixels < 1500:
            return None
        resource = self._author_structure_product(
            route, route_profile_id, map_id, floor,
        )
        if shared_mode and resource is None:
            return None
        if resource is not None:
            search_reference = resource.clean_mask
            validation_reference = resource.validation_mask
        else:
            search_reference = author_route_structure_mask(
                route, profile_id=route_profile_id,
            )
            validation_reference = search_reference

        search_scale = min(1.0, 420.0 / max(query.shape))
        if search_scale < 0.999:
            query = cv2.resize(
                query, (
                    max(1, round(query.shape[1] * search_scale)),
                    max(1, round(query.shape[0] * search_scale)),
                ),
                interpolation=cv2.INTER_NEAREST,
            )
            query_pixels = int(query.sum())
        qh, qw = query.shape
        scaled_maximum_shift = float(maximum_shift) * search_scale
        # ``maximum_shift`` is the complete authority boundary. A second
        # percentage cap made a declared 180 px local search silently cover
        # less than 180 px on sparse/narrow visible components.
        padding_cap = scaled_maximum_shift
        padding = int(max(
            10, min(scaled_maximum_shift, padding_cap),
        ))
        search_width = qw + padding * 2
        search_height = qh + padding * 2
        source_to_search = matrix[:2].astype(np.float32).copy()
        source_to_search *= float(search_scale)
        source_to_search[0, 2] -= float(bounds[0]) * search_scale - padding
        source_to_search[1, 2] -= float(bounds[1]) * search_scale - padding
        projected = cv2.warpAffine(
            search_reference, source_to_search, (search_width, search_height),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        if not projected.any():
            return None
        query_float = query.astype(np.float32)
        projected_float = projected.astype(np.float32)
        intersection = cv2.matchTemplate(
            projected_float, query_float, cv2.TM_CCORR,
        )
        reference_count = _binary_window_sums(projected_float, qh, qw)
        dice = (2.0 * intersection) / np.maximum(
            float(query_pixels) + reference_count, 1.0,
        )
        kernel = np.ones((3, 3), np.uint8)
        query_edges = cv2.morphologyEx(
            query, cv2.MORPH_GRADIENT, kernel,
        ).astype(np.float32)
        edge_grid_cells = 0
        binary_query_edges = query_edges > 0
        minimum_edges_per_cell = max(
            30, int(binary_query_edges.sum() * 0.015),
        )
        for grid_y in range(3):
            cell_y0 = round(grid_y * qh / 3.0)
            cell_y1 = round((grid_y + 1) * qh / 3.0)
            for grid_x in range(3):
                cell_x0 = round(grid_x * qw / 3.0)
                cell_x1 = round((grid_x + 1) * qw / 3.0)
                if int(binary_query_edges[
                    cell_y0:cell_y1, cell_x0:cell_x1,
                ].sum()) >= minimum_edges_per_cell:
                    edge_grid_cells += 1
        projected_edges = cv2.morphologyEx(
            projected, cv2.MORPH_GRADIENT, kernel,
        ).astype(np.float32)
        edge_count = float(query_edges.sum())
        if edge_count < 200:
            return None
        edge_intersection = cv2.matchTemplate(
            projected_edges, query_edges, cv2.TM_CCORR,
        )
        reference_edge_count = _binary_window_sums(projected_edges, qh, qw)
        edge_dice = (2.0 * edge_intersection) / np.maximum(
            edge_count + reference_edge_count, 1.0,
        )
        combined = np.minimum(dice, 1.0) * 0.35 + np.minimum(
            edge_dice, 1.0,
        ) * 0.65
        _minimum, score, _min_location, location = cv2.minMaxLoc(combined)
        coarse_peak = translation_response_peak_evidence(
            combined, location,
            exclusion_radius=max(4, round(32.0 * search_scale)),
        )
        coarse_elapsed_ms = (
            time.perf_counter() - search_started
        ) * 1000.0
        offset = np.asarray([
            float(location[0] - padding) / search_scale,
            float(location[1] - padding) / search_scale,
        ], dtype=np.float64)
        candidates = [matrix]
        for correction in (-offset, offset):
            if float(np.linalg.norm(correction)) > float(maximum_shift):
                continue
            adjusted = matrix.copy()
            adjusted[:2, 2] += correction
            candidates.append(adjusted)
        rows = []
        for candidate in candidates:
            residual = selected_route_current_frame_residual(
                frame, route, candidate, maximum_dimension=420,
                visible_override=visible,
                reference_override=validation_reference,
                shared_reference=resource if self._shared_structure_enabled() else None,
            )
            rows.append({"matrix": candidate, "residual": residual})
        rows.sort(key=lambda row: (
            1 if row["residual"].get("verified") else 0,
            float(row["residual"].get("withinThreePixels", 0.0)),
            float(row["residual"].get("withinSixPixels", 0.0)),
            -float(row["residual"].get("medianPixels", 999.0)),
        ), reverse=True)
        coarse_best = rows[0]

        # The global pass deliberately searches at a small resolution so even
        # an 800--960 px recenter stays interactive.  Its translation is
        # therefore quantized by roughly 1/search_scale client pixels.  Run a
        # second, narrow pass around that coarse winner at a tiered 560/640 px
        # resolution; this removes visible quantization without repeating the
        # wide search or changing scale/rotation.
        fine_started = time.perf_counter()
        fine_maximum_dimension = 640 if global_recenter else 560
        fine_scale = min(
            1.0, float(fine_maximum_dimension) / max(full_query.shape),
        )
        fine_radius = min(
            float(maximum_shift),
            max(8.0, min(16.0, 3.0 / max(search_scale, 1e-6))),
        )
        fine_padding = int(max(4, np.ceil(fine_radius * fine_scale)))
        fine_query = full_query
        if fine_scale < 0.999:
            fine_query = cv2.resize(
                fine_query, (
                    max(1, round(fine_query.shape[1] * fine_scale)),
                    max(1, round(fine_query.shape[0] * fine_scale)),
                ),
                interpolation=cv2.INTER_NEAREST,
            )
        fqh, fqw = fine_query.shape
        fine_source_to_search = coarse_best["matrix"][:2].astype(
            np.float32,
        ).copy()
        fine_source_to_search *= float(fine_scale)
        fine_source_to_search[0, 2] -= (
            float(bounds[0]) * fine_scale - fine_padding
        )
        fine_source_to_search[1, 2] -= (
            float(bounds[1]) * fine_scale - fine_padding
        )
        fine_projected = cv2.warpAffine(
            search_reference, fine_source_to_search,
            (fqw + fine_padding * 2, fqh + fine_padding * 2),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        fine_score = 0.0
        fine_peak = {
            "bestScore": 0.0,
            "secondScore": 0.0,
            "distinctMargin": 0.0,
            "exclusionRadius": 0,
        }
        fine_offset = np.zeros(2, dtype=np.float64)
        fine_rows = [coarse_best]
        if fine_projected.any():
            fine_query_float = fine_query.astype(np.float32)
            fine_projected_float = fine_projected.astype(np.float32)
            fine_intersection = cv2.matchTemplate(
                fine_projected_float, fine_query_float, cv2.TM_CCORR,
            )
            fine_integral = cv2.integral(fine_projected_float)
            fine_reference_count = (
                fine_integral[fqh:, fqw:]
                - fine_integral[:-fqh, fqw:]
                - fine_integral[fqh:, :-fqw]
                + fine_integral[:-fqh, :-fqw]
            )
            fine_query_pixels = float(fine_query.sum())
            fine_dice = (2.0 * fine_intersection) / np.maximum(
                fine_query_pixels + fine_reference_count, 1.0,
            )
            fine_query_edges = cv2.morphologyEx(
                fine_query, cv2.MORPH_GRADIENT, kernel,
            ).astype(np.float32)
            fine_projected_edges = cv2.morphologyEx(
                fine_projected, cv2.MORPH_GRADIENT, kernel,
            ).astype(np.float32)
            fine_edge_intersection = cv2.matchTemplate(
                fine_projected_edges, fine_query_edges, cv2.TM_CCORR,
            )
            fine_edge_integral = cv2.integral(fine_projected_edges)
            fine_reference_edge_count = (
                fine_edge_integral[fqh:, fqw:]
                - fine_edge_integral[:-fqh, fqw:]
                - fine_edge_integral[fqh:, :-fqw]
                + fine_edge_integral[:-fqh, :-fqw]
            )
            fine_edge_count = float(fine_query_edges.sum())
            fine_edge_dice = (
                2.0 * fine_edge_intersection
            ) / np.maximum(
                fine_edge_count + fine_reference_edge_count, 1.0,
            )
            fine_combined = np.minimum(fine_dice, 1.0) * 0.35 + np.minimum(
                fine_edge_dice, 1.0,
            ) * 0.65
            _fine_minimum, fine_score, _fine_min_location, fine_location = (
                cv2.minMaxLoc(fine_combined)
            )
            fine_peak = translation_response_peak_evidence(
                fine_combined, fine_location,
                exclusion_radius=max(3, round(8.0 * fine_scale)),
            )
            fine_offset = np.asarray([
                float(fine_location[0] - fine_padding) / fine_scale,
                float(fine_location[1] - fine_padding) / fine_scale,
            ], dtype=np.float64)
            fine_candidates = [coarse_best["matrix"]]
            for fine_correction in (-fine_offset, fine_offset):
                total_correction = (
                    coarse_best["matrix"][:2, 2]
                    + fine_correction
                    - matrix[:2, 2]
                )
                if (
                    float(np.linalg.norm(fine_correction)) > fine_radius + 1.0
                    or float(np.linalg.norm(total_correction))
                    > float(maximum_shift) + 1.0
                ):
                    continue
                adjusted = coarse_best["matrix"].copy()
                adjusted[:2, 2] += fine_correction
                fine_candidates.append(adjusted)
            fine_rows = []
            for candidate in fine_candidates:
                residual = selected_route_current_frame_residual(
                    frame, route, candidate,
                    maximum_dimension=fine_maximum_dimension,
                    visible_override=visible,
                    reference_override=validation_reference,
                    shared_reference=resource if self._shared_structure_enabled() else None,
                )
                fine_rows.append({"matrix": candidate, "residual": residual})
            fine_rows.sort(key=lambda row: (
                1 if row["residual"].get("verified") else 0,
                float(row["residual"].get("withinThreePixels", 0.0)),
                float(row["residual"].get("withinSixPixels", 0.0)),
                -float(row["residual"].get("p90Pixels", 999.0)),
                -float(row["residual"].get("medianPixels", 999.0)),
            ), reverse=True)
        best = fine_rows[0]
        fine_elapsed_ms = (time.perf_counter() - fine_started) * 1000.0
        correction = best["matrix"][:2, 2] - matrix[:2, 2]
        correction_distance = float(np.linalg.norm(correction))
        residual = best["residual"]
        residual["queryEdgeGridCells3x3"] = edge_grid_cells
        large_recenter_authorized = bool(
            not global_recenter
            or correction_distance <= 120.0
            or (
                # First entry to F1/B1 can expose much less than 35k pixels.
                # Spatial spread and tight edge agreement are stronger proof
                # than raw explored area: require at least seven 3x3 regions
                # plus a majority of walls inside three pixels.
                int(residual.get("queryPixels", 0)) >= 18000
                and int(residual.get("queryEdgeCount", 0)) >= 1800
                and edge_grid_cells >= 7
                and float(residual.get("medianPixels", 999.0)) <= 3.0
                and float(residual.get("withinThreePixels", 0.0)) >= 0.55
                and float(residual.get("withinSixPixels", 0.0)) >= 0.75
                and float(
                    residual.get("explainedPixelFraction", 0.0)
                ) >= 0.82
                and float(coarse_peak.get("distinctMargin", 0.0)) >= 0.015
            )
        )
        if not residual.get("verified") or not large_recenter_authorized:
            return {
                "success": False,
                "reason": (
                    "reviewed-floor-large-recenter-evidence-insufficient"
                    if residual.get("verified")
                    and not large_recenter_authorized else
                    "reviewed-floor-translation-residual-failed"
                ),
                "matrixSourceToClient": best["matrix"].tolist(),
                "selectedRouteCurrentFrameResidual": residual,
                "translationSearchScore": float(score),
                "fineTranslationSearchScore": float(fine_score),
                "translationSecondScore": float(
                    coarse_peak.get("secondScore", 0.0)
                ),
                "translationDistinctMargin": float(
                    coarse_peak.get("distinctMargin", 0.0)
                ),
                "fineTranslationSecondScore": float(
                    fine_peak.get("secondScore", 0.0)
                ),
                "fineTranslationDistinctMargin": float(
                    fine_peak.get("distinctMargin", 0.0)
                ),
                "globalRecenter": bool(global_recenter),
                "translationSearchRadius": float(padding / search_scale),
                "translationCorrection": correction.tolist(),
                "coarseTranslationCorrection": (
                    coarse_best["matrix"][:2, 2] - matrix[:2, 2]
                ).tolist(),
                "fineTranslationCorrection": (
                    best["matrix"][:2, 2]
                    - coarse_best["matrix"][:2, 2]
                ).tolist(),
                "coarseSearchScale": float(search_scale),
                "fineSearchScale": float(fine_scale),
                "fineSearchMaximumDimension": fine_maximum_dimension,
                "coarseSearchElapsedMs": round(coarse_elapsed_ms, 1),
                "fineSearchElapsedMs": round(fine_elapsed_ms, 1),
                "searchElapsedMs": round(
                    (time.perf_counter() - search_started) * 1000.0, 1,
                ),
                "searchPasses": 2,
                "visibleComponent": component,
            }
        return {
            "success": True,
            "reason": "reviewed-floor-translation-current-frame-verified",
            "matrixSourceToClient": best["matrix"].tolist(),
            "selectedRouteCurrentFrameResidual": residual,
            "translationCorrection": correction.tolist(),
            "translationSearchScore": float(score),
            "fineTranslationSearchScore": float(fine_score),
            "translationSecondScore": float(
                coarse_peak.get("secondScore", 0.0)
            ),
            "translationDistinctMargin": float(
                coarse_peak.get("distinctMargin", 0.0)
            ),
            "fineTranslationSecondScore": float(
                fine_peak.get("secondScore", 0.0)
            ),
            "fineTranslationDistinctMargin": float(
                fine_peak.get("distinctMargin", 0.0)
            ),
            "globalRecenter": bool(global_recenter),
            "translationSearchRadius": float(padding / search_scale),
            "coarseTranslationCorrection": (
                coarse_best["matrix"][:2, 2] - matrix[:2, 2]
            ).tolist(),
            "fineTranslationCorrection": (
                best["matrix"][:2, 2]
                - coarse_best["matrix"][:2, 2]
            ).tolist(),
            "coarseSearchScale": float(search_scale),
            "fineSearchScale": float(fine_scale),
            "fineSearchMaximumDimension": fine_maximum_dimension,
            "coarseSearchElapsedMs": round(coarse_elapsed_ms, 1),
            "fineSearchElapsedMs": round(fine_elapsed_ms, 1),
            "searchElapsedMs": round(
                (time.perf_counter() - search_started) * 1000.0, 1,
            ),
            "searchPasses": 2,
            "visibleComponent": component,
        }

    def align_selected_profile_cross_floor_fast(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        cross_floor_seed: dict | None,
        route_override: np.ndarray | None,
        *,
        translation_only: bool = False,
    ) -> dict | None:
        """Validate an author-profile cross-floor seed against this frame.

        The reviewed author-space mapping only narrows the search.  Its
        anisotropic scale ratio is kept fixed, a small common zoom correction
        plus translation are searched, and nothing is displayable unless the
        target frame itself passes the wall-edge residual gate.
        """
        if self._shared_structure_enabled():
            return None
        if not (
            isinstance(cross_floor_seed, dict)
            and cross_floor_seed.get("profileCurrentFrameSearchSeed") is True
            and (
                (
                    cross_floor_seed.get("directProjectionAllowed") is False
                    and cross_floor_seed.get("zeroExplorationDisplayAllowed") is False
                )
                or (
                    cross_floor_seed.get("directProjectionAllowed") is True
                    and cross_floor_seed.get("zeroExplorationDisplayAllowed") is True
                    and cross_floor_seed.get("playerReviewedProjectionTrial") is True
                    and cross_floor_seed.get("profileMappingVerified") is True
                )
            )
            and isinstance(route_override, np.ndarray)
            and route_override.ndim == 3
        ):
            return None
        predicted = np.asarray(
            cross_floor_seed.get("matrixSourceToClient"), dtype=np.float64,
        )
        if predicted.shape != (3, 3) or not np.isfinite(predicted).all():
            return None
        reviewed_predicted = predicted.copy()
        seed_linear = reviewed_predicted[:2, :2]
        determinant = float(np.linalg.det(seed_linear))
        seed_singular = np.linalg.svd(seed_linear, compute_uv=False)
        seed_minimum_scale = float(seed_singular.min())
        seed_maximum_scale = float(seed_singular.max())
        seed_anisotropy = seed_maximum_scale / max(seed_minimum_scale, 1e-9)
        seed_rotation = float(np.degrees(np.arctan2(
            seed_linear[1, 0], seed_linear[0, 0],
        )))
        column_x = seed_linear[:, 0]
        column_y = seed_linear[:, 1]
        seed_orthogonality_error = abs(float(
            np.dot(column_x, column_y)
            / max(np.linalg.norm(column_x) * np.linalg.norm(column_y), 1e-9)
        ))
        if (
            determinant <= 0.0
            or not 0.03 <= seed_minimum_scale <= 6.0
            or seed_anisotropy > 1.20
            or abs(seed_rotation) > 3.0
            or seed_orthogonality_error > np.sin(np.radians(3.0))
        ):
            return None
        discarded_seed_rotation = seed_rotation
        discarded_seed_shear = float(np.degrees(np.arcsin(np.clip(
            seed_orthogonality_error, 0.0, 1.0,
        ))))
        try:
            predicted = axis_lock_author_floor_transform(
                reviewed_predicted, route_override.shape,
            )
        except ValueError:
            return None
        seed_linear = predicted[:2, :2]

        visible = visible_map_component(frame)
        if visible is None:
            return None
        patch, bounds, component = visible
        query = map_color_mask(patch, live=True).astype(np.uint8)
        query_pixels = int(query.sum())
        if query_pixels < 3000:
            return {
                "success": False,
                "reason": "profile-cross-floor-current-frame-too-small",
                "alignmentMode": "partial-structure-dice",
                "searchMode": "profile-cross-floor-current-frame",
                "plausibleGeometry": False,
                "queryPixels": query_pixels,
                "visibleComponent": component,
            }

        full_reference = map_color_mask(
            route_override, live=False,
        ).astype(np.uint8)
        hsv = cv2.cvtColor(route_override, cv2.COLOR_BGR2HSV)
        _hue, saturation, value = cv2.split(hsv)
        annotation = ((saturation >= 82) & (value >= 105)) | (value >= 218)
        annotation = cv2.dilate(
            annotation.astype(np.uint8), np.ones((5, 5), np.uint8),
        )
        full_valid = (annotation == 0).astype(np.uint8)

        qh, qw = query.shape
        screen_x, screen_y = (float(value) for value in bounds[:2])
        padding = max(48, min(120, round(max(qw, qh) * 0.18)))
        # F1/F2 must remain a bounded local correction even if a legacy seed
        # carries ``recenterRequired``. Only the B1 teleport recovery is
        # permitted to search the full feasible translation range.
        global_recenter = bool(
            floor == "B1" and cross_floor_seed.get("recenterRequired")
        )
        rows: list[dict] = []
        # The reviewed mapping already fixes the floor-to-floor scale to a
        # narrow band.  A primed round relation can request the translation-
        # only form because its target render is already axis-locked; this
        # avoids spending the local handoff budget re-testing scale.  The
        # general path retains the full +/-5% recovery range.
        factors = np.unique(np.append(
            (
                np.asarray([1.0], dtype=np.float64)
                if translation_only else np.arange(
                    0.96, 1.051, 0.015, dtype=np.float64
                )
                if global_recenter else
                np.arange(0.96, 1.051, 0.0075, dtype=np.float64)
            ),
            1.0,
        ))
        q = query.astype(np.float32)
        positive = float(q.sum())
        for factor in factors:
            linear = seed_linear * float(factor)
            sx = float(np.linalg.norm(linear[:, 0]))
            sy = float(np.linalg.norm(linear[:, 1]))
            minimum_corner = np.zeros(2, dtype=np.float64)
            width = max(1, round(full_reference.shape[1] * sx))
            height = max(1, round(full_reference.shape[0] * sy))
            resized = cv2.resize(
                full_reference, (width, height),
                interpolation=cv2.INTER_NEAREST,
            ).astype(np.float32)
            resized_valid = cv2.resize(
                full_valid, (width, height),
                interpolation=cv2.INTER_NEAREST,
            ).astype(np.float32)
            if width < qw or height < qh:
                continue
            expected_x = (
                screen_x - float(predicted[0, 2]) - minimum_corner[0]
            )
            expected_y = (
                screen_y - float(predicted[1, 2]) - minimum_corner[1]
            )
            if global_recenter:
                # Changing floors can make the game recenter the whole map by
                # hundreds of screen pixels.  The reviewed relation already
                # fixes the linear transform, so search every feasible
                # translation of this target-floor patch and let distributed
                # current-frame geometry authorize the one atomic placement.
                x0, y0, x1, y1 = 0, 0, width, height
            else:
                x0 = max(0, int(np.floor(expected_x - padding)))
                y0 = max(0, int(np.floor(expected_y - padding)))
                x1 = min(width, int(np.ceil(expected_x + qw + padding)))
                y1 = min(height, int(np.ceil(expected_y + qh + padding)))
            if x1 - x0 < qw or y1 - y0 < qh:
                continue
            search = resized[y0:y1, x0:x1]
            valid = resized_valid[y0:y1, x0:x1]
            intersection = cv2.matchTemplate(search, q, cv2.TM_CCORR)
            integral = cv2.integral(search)
            reference_count = (
                integral[qh:, qw:] - integral[:-qh, qw:]
                - integral[qh:, :-qw] + integral[:-qh, :-qw]
            )
            query_valid = cv2.matchTemplate(valid, q, cv2.TM_CCORR)
            compared_fraction = query_valid / max(positive, 1.0)
            dice = (2.0 * intersection) / np.maximum(
                query_valid + reference_count, 1.0,
            )
            dice = np.minimum(dice, 1.0)
            dice[compared_fraction < 0.45] = -1.0
            remaining = dice.copy()
            for _rank in range(3):
                _minimum, maximum, _min_location, location = cv2.minMaxLoc(
                    remaining,
                )
                source_scaled_x = float(x0 + location[0])
                source_scaled_y = float(y0 + location[1])
                tx = screen_x - source_scaled_x - minimum_corner[0]
                ty = screen_y - source_scaled_y - minimum_corner[1]
                rows.append({
                    "score": float(maximum),
                    "factor": float(factor),
                    "sx": sx,
                    "sy": sy,
                    "linear": linear,
                    "tx": tx,
                    "ty": ty,
                    "seedTranslationError": float(np.hypot(
                        tx - float(predicted[0, 2]),
                        ty - float(predicted[1, 2]),
                    )),
                })
                suppress_x = max(8, qw // 4)
                suppress_y = max(8, qh // 4)
                lx, ly = location
                remaining[
                    max(0, ly - suppress_y):min(
                        remaining.shape[0], ly + suppress_y + 1,
                    ),
                    max(0, lx - suppress_x):min(
                        remaining.shape[1], lx + suppress_x + 1,
                    ),
                ] = -1.0
        if not rows:
            return None
        rows.sort(key=lambda row: row["score"], reverse=True)
        best = rows[0]
        alternative = next((
            row for row in rows[1:]
            if (
                np.hypot(row["tx"] - best["tx"], row["ty"] - best["ty"])
                >= 18.0
                or abs(row["factor"] - best["factor"]) >= 0.025
            )
        ), None)
        distinct_score = float((alternative or {}).get("score", 0.0))
        margin = float(best["score"] - distinct_score)
        matrix = np.eye(3, dtype=np.float64)
        matrix[:2, :2] = np.asarray(best["linear"], dtype=np.float64)
        matrix[:2, 2] = [best["tx"], best["ty"]]

        source_to_query = matrix[:2].astype(np.float32).copy()
        source_to_query[0, 2] -= screen_x
        source_to_query[1, 2] -= screen_y
        projected_reference = cv2.warpAffine(
            full_reference, source_to_query, (qw, qh),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        kernel = np.ones((3, 3), np.uint8)
        query_edges = cv2.morphologyEx(query, cv2.MORPH_GRADIENT, kernel) > 0
        projected_edges = cv2.morphologyEx(
            projected_reference, cv2.MORPH_GRADIENT, kernel,
        ) > 0
        edge_count = int(query_edges.sum())
        if edge_count < 200 or not projected_edges.any():
            return None
        distance = cv2.distanceTransform(
            (~projected_edges).astype(np.uint8), cv2.DIST_L2, 3,
        )[query_edges]
        edge_median = float(np.median(distance))
        edge_p90 = float(np.percentile(distance, 90))
        edge_within_three = float(np.mean(distance <= 3.0))
        edge_grid_cells = 0
        minimum_edges_per_cell = max(50, int(edge_count * 0.015))
        for grid_y in range(3):
            cell_y0 = round(grid_y * qh / 3.0)
            cell_y1 = round((grid_y + 1) * qh / 3.0)
            for grid_x in range(3):
                cell_x0 = round(grid_x * qw / 3.0)
                cell_x1 = round((grid_x + 1) * qw / 3.0)
                if int(query_edges[
                    cell_y0:cell_y1, cell_x0:cell_x1,
                ].sum()) >= minimum_edges_per_cell:
                    edge_grid_cells += 1

        route_corners = np.asarray([
            [0.0, 0.0, 1.0],
            [float(route_override.shape[1]), 0.0, 1.0],
            [float(route_override.shape[1]), float(route_override.shape[0]), 1.0],
            [0.0, float(route_override.shape[0]), 1.0],
        ], dtype=np.float64)
        projected_corners = (matrix @ route_corners.T).T[:, :2]
        projected_minimum = projected_corners.min(axis=0)
        projected_maximum = projected_corners.max(axis=0)
        projected_width = float(projected_maximum[0] - projected_minimum[0])
        projected_height = float(projected_maximum[1] - projected_minimum[1])
        fh, fw = frame.shape[:2]
        intersection_area = (
            max(0.0, min(projected_maximum[0], fw) - max(projected_minimum[0], 0.0))
            * max(0.0, min(projected_maximum[1], fh) - max(projected_minimum[1], 0.0))
        )
        box_area = max(1.0, projected_width * projected_height)
        screen_overlap = intersection_area / min(box_area, float(fw * fh))
        marginal_review = (
            cross_floor_seed.get("reviewStatus")
            == "player-visual-marginal-pass-current-frame-required"
        )
        minimum_score = 0.62 if not marginal_review else 0.70
        strict_success = bool(
            float(best["score"]) >= minimum_score
            and margin >= 0.008
            and best["seedTranslationError"] <= padding * 1.05
            and abs(float(best["factor"]) - 1.0) <= 0.051
            and edge_median <= (4.2 if marginal_review else 3.5)
            and edge_p90 <= 18.0
            and edge_within_three >= (0.45 if marginal_review else 0.52)
            and screen_overlap >= 0.05
        )
        precision_success = bool(
            float(best["score"]) >= 0.94
            and best["seedTranslationError"] <= padding * 1.05
            and abs(float(best["factor"]) - 1.0) <= 0.051
            and edge_median <= 1.5
            and edge_p90 <= 5.0
            and edge_within_three >= 0.85
            and screen_overlap >= 0.05
        )
        # A fully explored target floor can contain repeated rooms/corridors,
        # making a second translation score almost as well even though the
        # reviewed floor mapping lands within a few pixels.  Let distributed
        # current-frame structure decide that case: this requires substantial
        # wall evidence in at least six independent grid regions, so a lone
        # suspected room or one parallel corridor can never authorize display.
        mapped_full_structure_success = bool(
            float(best["score"]) >= 0.94
            and best["seedTranslationError"] <= min(24.0, padding * 0.50)
            and abs(float(best["factor"]) - 1.0) <= 0.026
            and query_pixels >= 40000
            and edge_count >= 3000
            and edge_grid_cells >= 6
            and edge_median <= 3.0
            and edge_p90 <= 18.0
            and edge_within_three >= 0.55
            and screen_overlap >= 0.20
        )
        # A saved real map-07 transition has slightly less explored area than
        # the full-structure case above, but eight spatial regions agree with
        # the reviewed seed to within a few pixels.  This consensus gate keeps
        # the p90 tolerance local to a strong, distributed, seed-consistent
        # fit; it cannot be reached by a single room or parallel corridor.
        mapped_transition_consensus_success = bool(
            float(best["score"]) >= 0.88
            and margin >= 0.010
            and best["seedTranslationError"] <= 3.0
            and abs(float(best["factor"]) - 1.0) <= 0.016
            and query_pixels >= 35000
            and edge_count >= 3500
            and edge_grid_cells >= 8
            and edge_median <= 2.0
            and edge_p90 <= 18.5
            and edge_within_three >= 0.54
            and screen_overlap >= 0.20
        )
        # A reviewed floor relation is expressed in shared world coordinates,
        # but the game's per-floor auto-centering can change the screen-space
        # translation by hundreds of pixels.  Permit that large jump only
        # when the target frame contributes strong structure across almost the
        # whole 3x3 grid.  Translation may move freely; scale stays inside the
        # reviewed +/-5% band and rotation remains axis locked.  This gate is
        # intentionally unreachable by one room or one parallel corridor.
        global_recenter_success = bool(
            global_recenter
            and float(best["score"]) >= 0.86
            and abs(float(best["factor"]) - 1.0) <= 0.051
            and query_pixels >= 35000
            and edge_count >= 3500
            and edge_grid_cells >= 8
            and edge_median <= 3.0
            and edge_p90 <= 19.0
            and edge_within_three >= 0.52
            and screen_overlap >= 0.20
        )
        # A saved real F2 -> first-F1 transition exposes seven, not eight,
        # populated grid cells.  Its target-floor wall evidence is much
        # stronger than the general broad-recenter floor: nearly fifty
        # thousand map pixels, 4.5k edges, sub-two-pixel median residual and
        # >=65% within three pixels.  Keep this as a separate high-precision
        # seven-region gate so a sparse room (even if visually repetitive)
        # remains unable to move the shared group.
        global_recenter_seven_region_success = bool(
            global_recenter
            and float(best["score"]) >= 0.88
            and abs(float(best["factor"]) - 1.0) <= 0.026
            and query_pixels >= 45000
            and edge_count >= 4500
            and edge_grid_cells >= 7
            and edge_median <= 2.0
            and edge_p90 <= 12.0
            and edge_within_three >= 0.65
            and screen_overlap >= 0.20
        )
        success = bool(
            strict_success or precision_success
            or mapped_full_structure_success
            or mapped_transition_consensus_success
            or global_recenter_success
            or global_recenter_seven_region_success
        )
        return {
            "success": success,
            "reason": (
                "aligned-profile-cross-floor-current-frame-mapped-full-structure"
                if mapped_full_structure_success
                and not strict_success and not precision_success else
                "aligned-profile-cross-floor-current-frame-mapped-transition-consensus"
                if mapped_transition_consensus_success
                and not strict_success and not precision_success else
                "aligned-profile-cross-floor-current-frame-global-recenter"
                if (
                    global_recenter_success
                    or global_recenter_seven_region_success
                )
                and not strict_success and not precision_success else
                "aligned-profile-cross-floor-current-frame"
                if success else "profile-cross-floor-current-frame-residual-failed"
            ),
            "mapId": map_id,
            "floor": str(floor),
            "alignmentMode": "partial-structure-dice",
            "searchMode": "profile-cross-floor-current-frame",
            "globalRecenter": global_recenter,
            "plausibleGeometry": screen_overlap >= 0.05,
            "score": float(best["score"]),
            "templateScore": float(best["score"]),
            "distinctScore": distinct_score,
            "distinctMargin": margin,
            "queryPixels": query_pixels,
            "seedTranslationError": float(best["seedTranslationError"]),
            "scaleCorrectionFactor": float(best["factor"]),
            "visualResidual": {
                "queryEdgeCount": edge_count,
                "queryEdgeGridCells3x3": edge_grid_cells,
                "medianPixels": edge_median,
                "p90Pixels": edge_p90,
                "withinThreePixels": edge_within_three,
            },
            "visibleComponent": component,
            "scale": float(np.sqrt(best["sx"] * best["sy"])),
            "scaleX": float(best["sx"]),
            "scaleY": float(best["sy"]),
            "anisotropy": float(max(best["sx"], best["sy"]) / min(best["sx"], best["sy"])),
            "rotationDegrees": float(np.degrees(np.arctan2(
                matrix[1, 0], matrix[0, 0],
            ))),
            "screenOverlap": float(screen_overlap),
            "inliers": 0,
            "inlierRatio": 0.0,
            "medianError": edge_median,
            "matrixSourceToClient": matrix.tolist(),
            "matrixCoordinateProfileId": cross_floor_seed.get(
                "matrixCoordinateProfileId",
            ),
            "profileAxisLocked": True,
            "discardedSeedRotationDegrees": float(discarded_seed_rotation),
            "discardedSeedShearDegrees": float(discarded_seed_shear),
            "profileCurrentFrameValidated": success,
            "semanticPrecisionOverride": precision_success,
            "multiRegionStructureOverride": mapped_full_structure_success,
            "multiRegionTransitionConsensus": (
                mapped_transition_consensus_success
            ),
            "globalRecenterValidated": bool(
                global_recenter_success
                or global_recenter_seven_region_success
            ),
            "globalRecenterSevenRegionValidated": (
                global_recenter_seven_region_success
            ),
            "directProjectionAllowed": False,
            "zeroExplorationDisplayAllowed": False,
        }

    def _match_exit_icon(
        self,
        image: np.ndarray,
        search_bounds: tuple[int, int, int, int],
        scales: np.ndarray,
    ) -> dict | None:
        template = self._exit_icon_template
        if template is None:
            return None
        x0, y0, x1, y1 = (int(value) for value in search_bounds)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(image.shape[1], x1), min(image.shape[0], y1)
        if x1 - x0 < 20 or y1 - y0 < 20:
            return None
        search = image[y0:y1, x0:x1]
        gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY) if search.ndim == 3 else search
        rows: list[dict] = []
        for scale in scales:
            width = max(8, round(template.shape[1] * float(scale)))
            height = max(8, round(template.shape[0] * float(scale)))
            if width >= gray.shape[1] or height >= gray.shape[0]:
                continue
            resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
            response = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            remaining = response.copy()
            for _rank in range(3):
                _minimum, score, _minimum_location, location = cv2.minMaxLoc(remaining)
                center = (
                    float(x0 + location[0] + width / 2.0),
                    float(y0 + location[1] + height / 2.0),
                )
                rows.append({
                    "score": float(score), "scale": float(scale),
                    "center": center, "size": (width, height),
                })
                guard_x, guard_y = max(10, width), max(10, height)
                gx0 = max(0, location[0] - guard_x)
                gx1 = min(remaining.shape[1], location[0] + guard_x + 1)
                gy0 = max(0, location[1] - guard_y)
                gy1 = min(remaining.shape[0], location[1] + guard_y + 1)
                remaining[gy0:gy1, gx0:gx1] = -1.0
        if not rows:
            return None
        rows.sort(key=lambda row: row["score"], reverse=True)
        winner = rows[0]
        guard = max(20.0, max(winner["size"]) * 0.80)
        other = next((
            row for row in rows[1:]
            if np.linalg.norm(
                np.asarray(row["center"]) - np.asarray(winner["center"]),
            ) >= guard
        ), None)
        second_score = float((other or {}).get("score", 0.0))
        return {
            **winner,
            "secondScore": second_score,
            "distinctMargin": float(winner["score"] - second_score),
        }

    @staticmethod
    def _live_exit_search_bounds(
        frame_shape: tuple[int, ...],
        visible_bounds: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        """Include a detached exit glyph just outside the wall component.

        Fog and antialiasing can disconnect the exit icon from the largest
        visible architecture component. Searching only that tight component
        missed the real icon in the current map-06 frame and selected a weak
        room texture instead. The bounded local expansion stays around the
        map island, so unrelated HUD icons at screen corners remain excluded.
        """
        x0, y0, x1, y1 = (int(value) for value in visible_bounds)
        height, width = frame_shape[:2]
        resolution_padding = round(168.0 * width / 1920.0)
        padding = max(
            resolution_padding,
            min(
                round(280.0 * width / 1920.0),
                round(max(x1 - x0, y1 - y0) * 0.30),
            ),
        )
        return (
            max(0, x0 - padding),
            max(0, y0 - padding),
            min(width, x1 + padding),
            min(height, y1 + padding),
        )

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

    def _features_with_extractor(
        self, image: np.ndarray, extractor,
    ) -> _Features:
        prepared, scale = self._prepare(image)
        keypoints, descriptors = extractor.detectAndCompute(prepared, None)
        return _Features(keypoints or [], descriptors, scale)

    def _build_preload_item(
        self, item: NightmareMap, floor: str, extractor,
        profile_id: str | None = None,
    ) -> tuple[_Features, _Features, SemanticFingerprint | None]:
        """Build expensive reference data without holding the engine lock."""
        if self._shared_structure_enabled():
            empty = _Features([], None, 1.0)
            resource = self._shared_structure_product(item.id, floor)
            if resource is None:
                return empty, empty, None
            structure = self._features_with_extractor(
                resource.clean_mask * np.uint8(255), extractor,
            )
            descriptor_count = int(
                len(structure.descriptors)
                if structure.descriptors is not None else 0
            )
            if descriptor_count <= 0:
                return empty, empty, None
            return empty, structure, semantic_fingerprint(resource.clean_mask)
        image = imread_unicode(item.layers[floor])
        if image is None:
            empty = _Features([], None, 1.0)
            return empty, empty, None
        reference = self._features_with_extractor(image, extractor)
        clean_used = False
        structure_image = None
        semantic_image = None
        if profile_id and profile_id != "wang-qingxin":
            direct_loader = getattr(
                self, "_structure_direct_resource_loader", None,
            )
            resource = None
            if callable(direct_loader):
                self._structure_preload_diagnostics[
                    "cleanResourceLoadCalls"
                ] = int(
                    self._structure_preload_diagnostics.get(
                        "cleanResourceLoadCalls", 0,
                    ) + 1
                )
                resource = self._author_structure_product(
                    None, profile_id, item.id, floor,
                )
            if resource is not None:
                # The stored clean channel is a binary semantic mask.  SIFT's
                # CLAHE input must use the explicit 0/255 image domain;
                # semantic_fingerprint below intentionally keeps 0/1.
                structure_image = resource.clean_mask * np.uint8(255)
                semantic_image = resource.clean_mask
                clean_used = True
                self._profile_clean_reference_keys.add(
                    (profile_id, item.id, floor),
                )
        if structure_image is None or semantic_image is None:
            structure_image = registration_structure_view(
                image, annotated_reference=True,
            )
            semantic_image = map_color_mask(image, live=False)
        structure = self._features_with_extractor(
            structure_image, extractor,
        )
        if clean_used:
            descriptor_count = int(
                len(structure.descriptors)
                if structure.descriptors is not None else 0
            )
            if descriptor_count > 0:
                descriptor_counts = self._structure_preload_diagnostics.setdefault(
                    "cleanStructureDescriptorCounts", {}
                )
                descriptor_counts[f"{item.id}/{floor}"] = descriptor_count
            else:
                # A valid clean semantic mask may be too simple for SIFT.
                # Keep it for semantic identity, but use the established
                # online structure feature builder for this candidate.
                structure = self._features_with_extractor(
                    registration_structure_view(
                        image, annotated_reference=True,
                    ), extractor,
                )
        semantic = semantic_fingerprint(semantic_image)
        self._structure_preload_diagnostics.update({
            "profileId": profile_id or "wang-qingxin",
            "cleanReferenceUsed": bool(
                self._structure_preload_diagnostics.get(
                    "cleanReferenceUsed", False,
                ) or clean_used
            ),
            "cleanReferenceCount": int(
                self._structure_preload_diagnostics.get(
                    "cleanReferenceCount", 0,
                ) + int(clean_used)
            ),
            "onlineReferenceFallbackCount": int(
                self._structure_preload_diagnostics.get(
                    "onlineReferenceFallbackCount", 0,
                ) + int(not clean_used)
            ),
        })
        return reference, structure, semantic

    def _commit_preload_item(
        self, item: NightmareMap, floor: str,
        built: tuple[_Features, _Features, SemanticFingerprint | None],
        profile_id: str | None = None,
    ) -> None:
        """Publish one fully-built cache entry while the caller owns the lock."""
        key = (item.id, floor)
        reference, structure, semantic = built
        if key not in self._references:
            self._references[key] = reference
        if profile_id and profile_id != "wang-qingxin":
            profile_key = (profile_id, item.id, floor)
            self._profile_structure_references[profile_key] = structure
            if semantic is not None:
                self._profile_semantic_source_references[profile_key] = semantic
        else:
            self._structure_references[key] = structure
            if semantic is not None:
                self._semantic_source_references[key] = semantic

    def _preload_item(
        self, item: NightmareMap, floor: str,
        profile_id: str | None = None,
    ) -> None:
        """Populate one source layer while the caller owns ``self._lock``."""
        key = (item.id, floor)
        profile_ready = (
            (profile_id, item.id, floor)
            in getattr(self, "_profile_structure_references", {})
            if profile_id and profile_id != "wang-qingxin" else
            key in getattr(self, "_structure_references", {})
        )
        if key in self._references and profile_ready:
            return
        built = (
            self._build_preload_item(item, floor, self.sift, profile_id)
            if profile_id and profile_id != "wang-qingxin" else
            self._build_preload_item(item, floor, self.sift)
        )
        if profile_id and profile_id != "wang-qingxin":
            self._commit_preload_item(item, floor, built, profile_id)
        else:
            self._commit_preload_item(item, floor, built)

    def preload(
        self, floor: str = "2", profile_id: str | None = None,
    ) -> None:
        """Warm one floor without blocking foreground ranking on SIFT work."""
        floor = floor if floor in {"1", "2", "B1"} else "2"
        profile_id = profile_id or self._active_structure_profile_id()
        self._ensure_structure_cache_context(profile_id)
        self._structure_preload_diagnostics = {
            "profileId": profile_id,
            "cleanReferenceUsed": False,
            "cleanReferenceCount": 0,
            "onlineReferenceFallbackCount": 0,
            "cleanResourceLoadCalls": 0,
            "authorRouteDecodeCalls": 0,
            "cleanStructureDescriptorCounts": {},
        }
        extractor = cv2.SIFT_create(
            nfeatures=900, contrastThreshold=0.025,
        )
        for item in self.pack.maps:
            with self._lock:
                profile_ready = (
                    (profile_id, item.id, floor)
                    in getattr(self, "_profile_structure_references", {})
                    if profile_id != "wang-qingxin" else
                    (item.id, floor)
                    in getattr(self, "_structure_references", {})
                )
                if (item.id, floor) in self._references and profile_ready:
                    continue
            built = (
                self._build_preload_item(item, floor, extractor, profile_id)
                if profile_id != "wang-qingxin" else
                self._build_preload_item(item, floor, extractor)
            )
            with self._lock:
                if profile_id != "wang-qingxin":
                    self._commit_preload_item(item, floor, built, profile_id)
                else:
                    self._commit_preload_item(item, floor, built)
        self._refresh_structure_preload_diagnostics(floor, profile_id)

    def preload_status(self, floor: str) -> dict:
        """Report floor cache readiness without triggering any image work."""
        normalized = floor if floor in {"1", "2", "B1"} else "2"
        with self._lock:
            loaded = sum(
                (item.id, normalized) in self._references
                for item in self.pack.maps
            )
        total = len(self.pack.maps)
        return {
            "floor": normalized,
            "loaded": int(loaded),
            "total": int(total),
            "ready": bool(total > 0 and loaded == total),
        }

    def preload_selected(self, map_id: str, floor: str) -> bool:
        """Warm only the human-confirmed layer for latency-bounded alignment."""
        if map_id not in self.pack.by_id or floor not in {"1", "2", "B1"}:
            return False
        item = self.pack.by_id[map_id]
        profile_id = self._active_structure_profile_id()
        self._ensure_structure_cache_context(profile_id)
        with self._lock:
            profile_ready = (
                (profile_id, map_id, floor)
                in getattr(self, "_profile_structure_references", {})
                if profile_id != "wang-qingxin" else
                (map_id, floor) in getattr(self, "_structure_references", {})
            )
            if (map_id, floor) in self._references and profile_ready:
                return True
        extractor = cv2.SIFT_create(
            nfeatures=900, contrastThreshold=0.025,
        )
        built = (
            self._build_preload_item(item, floor, extractor, profile_id)
            if profile_id != "wang-qingxin" else
            self._build_preload_item(item, floor, extractor)
        )
        if self._shared_structure_enabled() and (
            built[1].descriptors is None or len(built[1].descriptors) == 0
        ):
            return False
        with self._lock:
            if profile_id != "wang-qingxin":
                self._commit_preload_item(item, floor, built, profile_id)
            else:
                self._commit_preload_item(item, floor, built)
        self._refresh_structure_preload_diagnostics(floor, profile_id)
        return True

    def reload_memory(self) -> None:
        with self._lock:
            self._memory_references.clear()
            self._semantic_memory_references.clear()

    def preload_memory(self, floor: str = "2") -> int:
        """Decode accepted exemplars off the first interactive recognition path."""
        if floor not in {"1", "2", "B1"}:
            return 0
        paths = self.memory_store.exemplar_paths(floor)
        extractor = cv2.SIFT_create(
            nfeatures=900, contrastThreshold=0.025,
        )
        for map_id, items in paths.items():
            for path in items:
                key = (map_id, floor, str(path.resolve()))
                with self._lock:
                    need_features = key not in self._memory_references
                    need_semantic = (
                        key not in self._semantic_memory_references
                    )
                if not need_features and not need_semantic:
                    continue
                image = imread_unicode(path)
                if image is None:
                    continue
                features = (
                    self._features_with_extractor(image, extractor)
                    if need_features else None
                )
                semantic = None
                if need_semantic:
                    visible = visible_map_component(image)
                    if visible is not None:
                        semantic = semantic_fingerprint(
                            map_color_mask(visible[0], live=True)
                        )
                with self._lock:
                    if features is not None:
                        self._memory_references.setdefault(key, features)
                    if semantic is not None:
                        self._semantic_memory_references.setdefault(
                            key, semantic,
                        )
        with self._lock:
            feature_count = sum(
                key[1] == floor for key in self._memory_references
            )
            semantic_count = sum(
                key[1] == floor
                for key in self._semantic_memory_references
            )
        return max(feature_count, semantic_count)

    def _memory_for_floor(self, floor: str) -> tuple[dict[str, list[_Features]], dict[str, int]]:
        paths = self.memory_store.exemplar_paths(floor)
        result: dict[str, list[_Features]] = {item.id: [] for item in self.pack.maps}
        for map_id, items in paths.items():
            for path in items:
                key = (map_id, floor, str(path.resolve()))
                if key not in self._memory_references:
                    image = imread_unicode(path)
                    if image is not None:
                        self._memory_references[key] = self._features(image)
                if key in self._memory_references:
                    result[map_id].append(self._memory_references[key])
        return result, self.memory_store.confirmation_counts(floor)

    def _semantic_memory_for_floor(
        self, floor: str,
    ) -> dict[str, list[SemanticFingerprint]]:
        """Load accepted screenshots through the same map-only representation.

        Old recognition compared a cropped live map against SIFT features from
        the entire remembered game screenshot.  UI and canvas pixels therefore
        dominated the learned exemplar.  This path first extracts only the
        visible room/corridor component from every accepted screenshot.
        """
        paths = self.memory_store.exemplar_paths(floor)
        result: dict[str, list[SemanticFingerprint]] = {
            item.id: [] for item in self.pack.maps
        }
        for map_id, items in paths.items():
            for path in items:
                key = (map_id, floor, str(path.resolve()))
                if key not in self._semantic_memory_references:
                    image = imread_unicode(path)
                    semantic = None
                    if image is not None:
                        visible = visible_map_component(image)
                        if visible is not None:
                            semantic = semantic_fingerprint(
                                map_color_mask(visible[0], live=True)
                            )
                    if semantic is not None:
                        self._semantic_memory_references[key] = semantic
                semantic = self._semantic_memory_references.get(key)
                if semantic is not None:
                    result[map_id].append(semantic)
        return result

    def stair_portal_seed_matrix(
        self,
        map_id: str,
        from_floor: str,
        to_floor: str,
        source_recognition_to_client,
    ) -> dict | None:
        """Create a candidate-only B1<->F1 handoff from a verified source fit."""

        if self._shared_structure_enabled():
            return None
        index = getattr(self, "stair_portals", None)
        if index is None:
            return None
        return index.handoff_seed(
            map_id, from_floor, to_floor, source_recognition_to_client,
        )

    def align_stair_portal_handoff_fast(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        portal_seed: dict | None,
        profile_id: str | None = None,
        route_override: np.ndarray | None = None,
        profile_to_recognition=None,
    ) -> dict | None:
        """Resolve B1/F1 translation from stair candidates plus live walls.

        A stair match proposes a translation only.  Each proposal is checked
        against the target-floor route and current-frame wall residual.  The
        result is displayable only when one structurally verified proposal wins
        by a clear margin; a single repeated-line glyph can never pass alone.
        """

        if not (
            isinstance(portal_seed, dict)
            and portal_seed.get("stairPortalHandoff") is True
            and portal_seed.get("singleStairGlyphNeverAuthorizesDisplay") is True
            and portal_seed.get("requiresUniqueCurrentFrameStructure") is True
            and str(portal_seed.get("toFloor")) == str(floor)
            and map_id in self.pack.by_id
        ):
            return None
        if floor in {"1", "2"}:
            return {
                "success": False,
                "reason": "locked-upstairs-portal-wide-search-disabled",
                "alignmentMode": "stair-portal-current-frame",
                "plausibleGeometry": False,
                "globalRecenterUsed": False,
                "maximumTranslationPixels": 180.0,
            }
        if self._shared_structure_enabled():
            return None
        route = self.route_image(map_id, floor)
        source = np.asarray(
            portal_seed.get("sourceRecognitionMatrixToClient")
            or portal_seed.get("sourceMatrixToClient"), dtype=np.float64,
        )
        route_profile_id = None
        adapter = None
        adapter_inverse = None
        if route_override is not None or profile_to_recognition is not None:
            proposed_route = np.asarray(route_override)
            proposed_adapter = np.asarray(
                profile_to_recognition, dtype=np.float64,
            )
            linear_scale = (
                float(np.max(np.abs(proposed_adapter[:2, :2])))
                if proposed_adapter.shape == (3, 3) else 0.0
            )
            axis_locked = bool(
                proposed_adapter.shape == (3, 3)
                and np.isfinite(proposed_adapter).all()
                and np.allclose(
                    proposed_adapter[2], [0.0, 0.0, 1.0], atol=1e-8,
                )
                and abs(float(proposed_adapter[0, 1]))
                <= max(1e-8, linear_scale * 1e-6)
                and abs(float(proposed_adapter[1, 0]))
                <= max(1e-8, linear_scale * 1e-6)
                and abs(float(np.linalg.det(
                    proposed_adapter[:2, :2],
                ))) > 1e-9
            )
            if (
                proposed_route.ndim not in {2, 3}
                or proposed_route.size == 0
                or not str(profile_id or "").strip()
                or not axis_locked
            ):
                return None
            try:
                adapter_inverse = np.linalg.inv(proposed_adapter)
            except np.linalg.LinAlgError:
                return None
            route = proposed_route
            adapter = proposed_adapter
            route_profile_id = str(profile_id)
        if (
            route is None
            or source.shape != (3, 3)
            or not np.isfinite(source).all()
        ):
            return None
        # Portal geometry is defined in the recognition atlas, but current
        # pixels must be fitted in the coordinate system of the sheet that will
        # actually be rendered.  For an alternate author, carry its reviewed
        # same-floor adapter into the locked linear part of the search.
        base_linear = source[:2, :2].copy()
        if adapter is not None:
            base_linear = base_linear @ adapter[:2, :2]
        visible = visible_map_component(frame)
        if visible is None:
            return {
                "success": False,
                "reason": "stair-portal-no-visible-target-structure",
                "alignmentMode": "stair-portal-current-frame",
                "plausibleGeometry": False,
            }
        patch, bounds, component = visible
        custom_residual = getattr(self, "_portal_residual_evaluator", None)
        if callable(custom_residual):
            evaluate_residual = lambda matrix: custom_residual(  # noqa: E731
                frame, route, matrix,
            )
        else:
            query = map_color_mask(patch, live=True).astype(np.uint8)
            query_pixels = int(query.sum())
            edge_kernel = np.ones((3, 3), np.uint8)
            query_edges = cv2.morphologyEx(
                query, cv2.MORPH_GRADIENT, edge_kernel,
            ) > 0
            edge_count = int(query_edges.sum())
            if query_pixels < 1500 or edge_count < 200:
                return {
                    "success": False,
                    "reason": "stair-portal-current-frame-evidence-insufficient",
                    "alignmentMode": "stair-portal-current-frame",
                    "plausibleGeometry": False,
                    "queryPixels": query_pixels,
                    "queryEdgeCount": edge_count,
                    "visibleComponent": component,
                }
            route_bgr = (
                route[:, :, :3]
                if route.ndim == 3 and route.shape[2] >= 3 else route
            )
            reference = map_color_mask(route_bgr, live=False).astype(np.uint8)
            if route.ndim == 3 and route.shape[2] >= 4:
                reference &= (route[:, :, 3] >= 8).astype(np.uint8)
            hsv = cv2.cvtColor(route_bgr, cv2.COLOR_BGR2HSV)
            _hue, saturation, value = cv2.split(hsv)
            annotation = (saturation >= 82) & (value >= 105)
            annotation = cv2.dilate(
                annotation.astype(np.uint8), np.ones((5, 5), np.uint8),
            )
            reference &= (annotation == 0).astype(np.uint8)
            patch_x, patch_y = float(bounds[0]), float(bounds[1])

            def evaluate_residual(matrix):
                source_to_patch = matrix[:2].astype(np.float32).copy()
                source_to_patch[0, 2] -= patch_x
                source_to_patch[1, 2] -= patch_y
                projected = cv2.warpAffine(
                    reference, source_to_patch,
                    (patch.shape[1], patch.shape[0]),
                    flags=cv2.INTER_NEAREST,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0,
                )
                if not projected.any():
                    return {
                        "verified": False,
                        "reason": "selected-route-outside-visible-map",
                        "queryPixels": query_pixels,
                        "queryEdgeCount": edge_count,
                    }
                projected_edges = cv2.morphologyEx(
                    projected, cv2.MORPH_GRADIENT, edge_kernel,
                ) > 0
                if not projected_edges.any():
                    return {
                        "verified": False,
                        "reason": "insufficient-wall-edges",
                        "queryPixels": query_pixels,
                        "queryEdgeCount": edge_count,
                    }
                distances = cv2.distanceTransform(
                    (~projected_edges).astype(np.uint8), cv2.DIST_L2, 3,
                )[query_edges]
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
                    and explained_fraction >= 0.72
                )
                return {
                    "verified": verified,
                    "reason": (
                        "selected-route-current-frame-residual-verified"
                        if verified else
                        "selected-route-current-frame-residual-failed"
                    ),
                    "queryPixels": query_pixels,
                    "queryEdgeCount": edge_count,
                    "medianPixels": median,
                    "p90Pixels": p90,
                    "withinThreePixels": within_three,
                    "withinSixPixels": within_six,
                    "explainedPixelFraction": explained_fraction,
                }
        target_rows = list(portal_seed.get("targetStairCandidates") or [])[:12]
        if adapter_inverse is not None:
            profile_target_rows = []
            for row in target_rows:
                center = np.asarray(row.get("center"), dtype=np.float64)
                if center.shape != (2,) or not np.isfinite(center).all():
                    continue
                profile_center = adapter_inverse @ np.asarray([
                    center[0], center[1], 1.0,
                ], dtype=np.float64)
                if (
                    not np.isfinite(profile_center).all()
                    or abs(float(profile_center[2])) <= 1e-9
                ):
                    continue
                profile_center = profile_center[:2] / profile_center[2]
                profile_target_rows.append({
                    **row,
                    "recognitionCenter": center.tolist(),
                    "center": profile_center.tolist(),
                })
            target_rows = profile_target_rows
        live_rows = [
            row for row in live_stair_screen_candidates(frame)
            if float(row.get("score", 0.0)) >= 0.72
            and int(row.get("treadCount", 0)) >= 3
        ][:12]
        if not target_rows or not live_rows:
            return {
                "success": False,
                "reason": "stair-portal-endpoint-not-observed",
                "alignmentMode": "stair-portal-current-frame",
                "plausibleGeometry": False,
                "targetCandidateCount": len(target_rows),
                "liveCandidateCount": len(live_rows),
                "visibleComponent": component,
            }

        rotation = float(np.degrees(np.arctan2(
            base_linear[1, 0], base_linear[0, 0],
        )))
        swaps_orientation = abs(abs(rotation) - 90.0) <= 12.0
        factors = (0.94, 1.0, 1.06)
        factor_hypotheses = list(factors)
        proposals: list[dict] = []
        if callable(custom_residual):
            for target_index, target in enumerate(target_rows):
                target_orientation = target.get("orientation")
                for live_index, live in enumerate(live_rows):
                    expected_orientation = (
                        "v" if target_orientation == "h" else "h"
                    ) if swaps_orientation else target_orientation
                    if live.get("orientation") != expected_orientation:
                        continue
                    for factor in factors:
                        matrix = portal_candidate_matrix(
                            base_linear, target.get("center"),
                            live.get("screenCenter"), common_scale=factor,
                        )
                        if matrix is not None:
                            proposals.append({
                                "matrix": matrix,
                                "targetIndex": target_index,
                                "liveIndex": live_index,
                                "targetCenter": target.get("center"),
                                "liveCenter": live.get("screenCenter"),
                                "commonScale": factor,
                                "structureSeedScore": 0.0,
                                "stairScore": min(
                                    float(target.get("score", 0.0)),
                                    float(live.get("score", 0.0)),
                                ),
                            })
        else:
            # Stair centers are deliberately coarse.  Solve translation for the
            # one already-confirmed target map at each locked scale, then retain
            # only wall solutions that also place an independently detected
            # target stair near a live stair.  This is three vectorized searches,
            # not thirteen-map identity recognition or a B1/F1 affine mapping.
            q = query.astype(np.float32)
            positive = float(q.sum())
            qh, qw = query.shape
            portal_limit = max(42.0, min(110.0, max(qw, qh) * 0.12))
            # The game independently auto-fits every floor.  First score a
            # broad but bounded set of target-floor zooms at low resolution,
            # then run the expensive full-resolution translation search only
            # for the two best scales.  This covers real B1/F1 zoom changes
            # without multiplying the live latency by the whole scale bank.
            broad_factors = (
                0.84, 0.88, 0.92, 0.96, 1.0,
                1.04, 1.08, 1.12, 1.16,
            )
            coarse_ratio = 0.32 if max(qw, qh) >= 450 else 0.45
            coarse_qw = max(24, round(qw * coarse_ratio))
            coarse_qh = max(24, round(qh * coarse_ratio))
            coarse_query = cv2.resize(
                q, (coarse_qw, coarse_qh), interpolation=cv2.INTER_NEAREST,
            )
            coarse_query_edges = cv2.resize(
                query_edges.astype(np.uint8),
                (coarse_qw, coarse_qh), interpolation=cv2.INTER_NEAREST,
            ).astype(np.float32)
            coarse_positive = float(coarse_query.sum())
            coarse_edge_count = float(coarse_query_edges.sum())
            scale_scores: list[tuple[float, float]] = []
            for factor in broad_factors:
                linear = base_linear * float(factor)
                sx = float(np.linalg.norm(linear[:, 0]))
                sy = float(np.linalg.norm(linear[:, 1]))
                if not (0.03 <= sx <= 6.0 and 0.03 <= sy <= 6.0):
                    continue
                width = max(1, round(reference.shape[1] * sx))
                height = max(1, round(reference.shape[0] * sy))
                if width < qw or height < qh:
                    continue
                coarse_width = max(1, round(width * coarse_ratio))
                coarse_height = max(1, round(height * coarse_ratio))
                if coarse_width < coarse_qw or coarse_height < coarse_qh:
                    continue
                coarse_reference = cv2.resize(
                    reference, (coarse_width, coarse_height),
                    interpolation=cv2.INTER_NEAREST,
                ).astype(np.float32)
                intersection = cv2.matchTemplate(
                    coarse_reference, coarse_query, cv2.TM_CCORR,
                )
                integral = cv2.integral(coarse_reference)
                reference_count = (
                    integral[coarse_qh:, coarse_qw:]
                    - integral[:-coarse_qh, coarse_qw:]
                    - integral[coarse_qh:, :-coarse_qw]
                    + integral[:-coarse_qh, :-coarse_qw]
                )
                dice = (2.0 * intersection) / np.maximum(
                    coarse_positive + reference_count, 1.0,
                )
                coarse_reference_edges = cv2.morphologyEx(
                    coarse_reference.astype(np.uint8),
                    cv2.MORPH_GRADIENT, edge_kernel,
                ).astype(np.float32)
                edge_intersection = cv2.matchTemplate(
                    coarse_reference_edges, coarse_query_edges,
                    cv2.TM_CCORR,
                )
                edge_integral = cv2.integral(coarse_reference_edges)
                reference_edge_count = (
                    edge_integral[coarse_qh:, coarse_qw:]
                    - edge_integral[:-coarse_qh, coarse_qw:]
                    - edge_integral[coarse_qh:, :-coarse_qw]
                    + edge_integral[:-coarse_qh, :-coarse_qw]
                )
                edge_dice = (2.0 * edge_intersection) / np.maximum(
                    coarse_edge_count + reference_edge_count, 1.0,
                )
                combined = np.minimum(dice, 1.0) * 0.35 + np.minimum(
                    edge_dice, 1.0,
                ) * 0.65
                scale_scores.append((float(combined.max()), float(factor)))
            scale_scores.sort(
                key=lambda row: (row[0], -abs(row[1] - 1.0)), reverse=True,
            )
            factors = tuple(row[1] for row in scale_scores[:2])
            if not factors:
                factors = (1.0,)
            factor_hypotheses = list(factors)
            for factor in factors:
                linear = base_linear * float(factor)
                sx = float(np.linalg.norm(linear[:, 0]))
                sy = float(np.linalg.norm(linear[:, 1]))
                if not (0.03 <= sx <= 6.0 and 0.03 <= sy <= 6.0):
                    continue
                width = max(1, round(reference.shape[1] * sx))
                height = max(1, round(reference.shape[0] * sy))
                if width < qw or height < qh:
                    continue
                resized = cv2.resize(
                    reference, (width, height), interpolation=cv2.INTER_NEAREST,
                ).astype(np.float32)
                intersection = cv2.matchTemplate(resized, q, cv2.TM_CCORR)
                integral = cv2.integral(resized)
                reference_count = (
                    integral[qh:, qw:] - integral[:-qh, qw:]
                    - integral[qh:, :-qw] + integral[:-qh, :-qw]
                )
                dice = (2.0 * intersection) / np.maximum(
                    positive + reference_count, 1.0,
                )
                resized_edges = cv2.morphologyEx(
                    resized.astype(np.uint8), cv2.MORPH_GRADIENT,
                    edge_kernel,
                ).astype(np.float32)
                query_edges_float = query_edges.astype(np.float32)
                edge_intersection = cv2.matchTemplate(
                    resized_edges, query_edges_float, cv2.TM_CCORR,
                )
                edge_integral = cv2.integral(resized_edges)
                reference_edge_count = (
                    edge_integral[qh:, qw:] - edge_integral[:-qh, qw:]
                    - edge_integral[qh:, :-qw] + edge_integral[:-qh, :-qw]
                )
                edge_dice = (2.0 * edge_intersection) / np.maximum(
                    float(edge_count) + reference_edge_count, 1.0,
                )
                remaining = np.minimum(
                    np.minimum(dice, 1.0) * 0.35
                    + np.minimum(edge_dice, 1.0) * 0.65,
                    1.0,
                )
                for _rank in range(8):
                    _minimum, maximum, _min_location, location = cv2.minMaxLoc(
                        remaining,
                    )
                    if maximum <= 0.0:
                        break
                    matrix = np.eye(3, dtype=np.float64)
                    matrix[:2, :2] = linear
                    matrix[0, 2] = patch_x - float(location[0])
                    matrix[1, 2] = patch_y - float(location[1])
                    portal_pairs = []
                    for target_index, target in enumerate(target_rows):
                        target_orientation = target.get("orientation")
                        expected_orientation = (
                            "v" if target_orientation == "h" else "h"
                        ) if swaps_orientation else target_orientation
                        target_center = np.asarray(
                            target.get("center"), dtype=np.float64,
                        )
                        projected = linear @ target_center + matrix[:2, 2]
                        for live_index, live in enumerate(live_rows):
                            if live.get("orientation") != expected_orientation:
                                continue
                            live_center = np.asarray(
                                live.get("screenCenter"), dtype=np.float64,
                            )
                            portal_pairs.append((
                                float(np.linalg.norm(projected - live_center)),
                                target_index, live_index, target_center,
                                live_center, min(
                                    float(target.get("score", 0.0)),
                                    float(live.get("score", 0.0)),
                                ),
                            ))
                    if portal_pairs:
                        distance, target_index, live_index, target_center, live_center, stair_score = min(
                            portal_pairs, key=lambda row: row[0],
                        )
                        if distance <= portal_limit:
                            proposals.append({
                                "matrix": matrix,
                                "targetIndex": target_index,
                                "liveIndex": live_index,
                                "targetCenter": target_center.tolist(),
                                "liveCenter": live_center.tolist(),
                                "commonScale": factor,
                                "structureSeedScore": float(maximum),
                                "portalEndpointDistance": round(distance, 3),
                                "stairScore": stair_score,
                            })
                    suppress_x = max(8, qw // 4)
                    suppress_y = max(8, qh // 4)
                    lx, ly = location
                    remaining[
                        max(0, ly - suppress_y):min(
                            remaining.shape[0], ly + suppress_y + 1,
                        ),
                        max(0, lx - suppress_x):min(
                            remaining.shape[1], lx + suppress_x + 1,
                        ),
                    ] = -1.0
            direct_portal_proposals = []
            for target_index, target in enumerate(target_rows):
                target_orientation = target.get("orientation")
                expected_orientation = (
                    "v" if target_orientation == "h" else "h"
                ) if swaps_orientation else target_orientation
                for live_index, live in enumerate(live_rows):
                    if live.get("orientation") != expected_orientation:
                        continue
                    matrix = portal_candidate_matrix(
                        base_linear, target.get("center"),
                        live.get("screenCenter"), common_scale=1.0,
                    )
                    if matrix is None:
                        continue
                    direct_portal_proposals.append({
                        "matrix": matrix,
                        "targetIndex": target_index,
                        "liveIndex": live_index,
                        "targetCenter": target.get("center"),
                        "liveCenter": live.get("screenCenter"),
                        "commonScale": 1.0,
                        "structureSeedScore": -1.0,
                        "directPortalSeed": True,
                        "stairScore": min(
                            float(target.get("score", 0.0)),
                            float(live.get("score", 0.0)),
                        ),
                    })
            direct_portal_proposals.sort(
                key=lambda row: row["stairScore"], reverse=True,
            )
            proposals.extend(direct_portal_proposals[:12])

        proposals.sort(
            key=lambda row: (
                row["structureSeedScore"], row["stairScore"],
            ),
            reverse=True,
        )
        selected_proposals: list[dict] = []
        for proposal in proposals:
            if proposal.get("directPortalSeed"):
                continue
            if any(
                np.linalg.norm(
                    proposal["matrix"][:2, 2] - other["matrix"][:2, 2]
                ) < 6.0
                and np.linalg.norm(
                    proposal["matrix"][:2, :2] - other["matrix"][:2, :2]
                ) < 0.01
                for other in selected_proposals
            ):
                continue
            selected_proposals.append(proposal)
            if len(selected_proposals) >= 16:
                break
        for proposal in proposals:
            if not proposal.get("directPortalSeed"):
                continue
            if any(
                np.linalg.norm(
                    proposal["matrix"][:2, 2] - other["matrix"][:2, 2]
                ) < 6.0
                for other in selected_proposals
            ):
                continue
            selected_proposals.append(proposal)
            if len(selected_proposals) >= 28:
                break

        rows: list[dict] = []
        for proposal in selected_proposals:
            residual = evaluate_residual(proposal["matrix"])
            if residual.get("reason") in {
                "insufficient-current-map-pixels", "insufficient-wall-edges",
            }:
                continue
            rows.append({**proposal, "residual": residual})

        def rank_key(row: dict) -> tuple:
            residual = row["residual"]
            return (
                1 if residual.get("verified") else 0,
                float(residual.get("withinThreePixels", 0.0)),
                float(residual.get("withinSixPixels", 0.0)),
                float(residual.get("explainedPixelFraction", 0.0)),
                -float(residual.get("medianPixels", 999.0)),
                -float(residual.get("p90Pixels", 999.0)),
                row["stairScore"],
            )

        if not rows:
            return {
                "success": False,
                "reason": "stair-portal-current-frame-evidence-insufficient",
                "alignmentMode": "stair-portal-current-frame",
                "plausibleGeometry": False,
                "targetCandidateCount": len(target_rows),
                "liveCandidateCount": len(live_rows),
                "visibleComponent": component,
            }
        rows.sort(key=rank_key, reverse=True)
        if not callable(custom_residual):
            # The vectorized search works on binary map masks and may stop a few
            # pixels from the raw wall edge.  One bounded phase-correlation step
            # on the best four candidates removes that residual without changing
            # scale/rotation or allowing a distant global jump.
            phase_rows = rows[:8] + [
                row for row in rows if row.get("directPortalSeed")
            ]
            phase_seen: set[int] = set()
            for row in phase_rows:
                if id(row) in phase_seen:
                    continue
                phase_seen.add(id(row))
                matrix = row["matrix"]
                source_to_patch = matrix[:2].astype(np.float32).copy()
                source_to_patch[0, 2] -= patch_x
                source_to_patch[1, 2] -= patch_y
                projected = cv2.warpAffine(
                    reference, source_to_patch,
                    (patch.shape[1], patch.shape[0]),
                    flags=cv2.INTER_NEAREST,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0,
                )
                projected_edges = (
                    cv2.morphologyEx(
                        projected, cv2.MORPH_GRADIENT, edge_kernel,
                    ) > 0
                ).astype(np.float32)
                shift, response = cv2.phaseCorrelate(
                    projected_edges, query_edges.astype(np.float32),
                )
                shift_vector = np.asarray(shift, dtype=np.float64)
                if (
                    not np.isfinite(shift_vector).all()
                    or float(response) < 0.08
                    # Candidate centers move with UI scale and repeated Hough
                    # edges; 1440p map-01 measured a 41 px center offset.  The
                    # phase response plus final wall residual remains the
                    # authority, so this is still a bounded local correction.
                    or np.linalg.norm(shift_vector) > 56.0
                ):
                    continue
                adjusted = matrix.copy()
                adjusted[:2, 2] += shift_vector
                adjusted_residual = evaluate_residual(adjusted)
                candidate = {
                    **row,
                    "matrix": adjusted,
                    "residual": adjusted_residual,
                    "portalPhaseTranslation": shift_vector.tolist(),
                    "portalPhaseResponse": round(float(response), 4),
                }
                if rank_key(candidate) > rank_key(row):
                    row.update(candidate)
            rows.sort(key=rank_key, reverse=True)
        best = rows[0]
        best_matrix = best["matrix"]
        alternatives = [
            row for row in rows[1:]
            if np.linalg.norm(
                row["matrix"][:2, 2] - best_matrix[:2, 2]
            ) >= 18.0
            # Two zoom hypotheses tied to the same static/live endpoint are
            # scale refinement of one portal, not two competing locations.
            # The current-frame wall residual chooses between them.
            and (
                row.get("targetIndex") != best.get("targetIndex")
                or row.get("liveIndex") != best.get("liveIndex")
            )
        ]
        alternative = alternatives[0] if alternatives else None
        best_residual = best["residual"]
        alternative_residual = (alternative or {}).get("residual") or {}
        within_three_margin = float(
            best_residual.get("withinThreePixels", 0.0)
            - alternative_residual.get("withinThreePixels", 0.0)
        )
        median_margin = float(
            alternative_residual.get("medianPixels", 999.0)
            - best_residual.get("medianPixels", 999.0)
        )
        alternative_verified = bool(alternative_residual.get("verified"))
        unique = bool(
            alternative is None
            or not alternative_verified
            or within_three_margin >= 0.08
            or median_margin >= 1.20
        )
        success = bool(best_residual.get("verified") and unique)
        group_relocalization = stair_pair_group_translation(
            portal_seed.get("previousLiveStairs"), live_rows,
            expected_scale=best.get("commonScale"),
        )
        recognition_matrix = (
            best_matrix @ adapter_inverse
            if adapter_inverse is not None else best_matrix
        )
        return {
            "success": success,
            "reason": (
                "aligned-stair-portal-current-frame-verified"
                if success else
                "stair-portal-ambiguous" if best_residual.get("verified") else
                "stair-portal-current-frame-residual-failed"
            ),
            "alignmentMode": "stair-portal-current-frame",
            "searchMode": "target-stair-enumeration-locked-linear",
            "mapId": map_id,
            "floor": str(floor),
            "matrixSourceToClient": best_matrix.tolist(),
            "recognitionMatrixSourceToClient": recognition_matrix.tolist(),
            "profileCurrentFrameValidated": bool(route_profile_id),
            "matrixCoordinateProfileId": (
                route_profile_id or "wang-qingxin"
            ),
            "routeProfileId": route_profile_id or "wang-qingxin",
            "portalRouteCoordinateMode": (
                "selected-author-current-frame"
                if route_profile_id else "recognition-current-frame"
            ),
            "plausibleGeometry": bool(best_residual.get("verified")),
            "currentFrameDisplayValidated": success,
            "portalUniqueWinner": unique,
            "portalTargetCandidateIndex": best["targetIndex"],
            "portalLiveCandidateIndex": best["liveIndex"],
            "portalTargetCenter": best.get("targetCenter"),
            "portalLiveCenter": best.get("liveCenter"),
            # This is movement evidence for the complete B1/F1/F2 layer
            # group, not a separate B1 coordinate. It is consumed only after
            # the B1 wall fit above has independently passed.
            "portalGroupRelocalization": group_relocalization,
            "portalGroupTranslation": (
                group_relocalization.get("groupTranslation")
                if success and group_relocalization.get("success") else None
            ),
            "portalCommonScale": best["commonScale"],
            "portalScaleHypotheses": factor_hypotheses,
            "portalCandidateCount": len(rows),
            "portalAlternativeVerified": alternative_verified,
            "portalAlternativeCommonScale": (
                alternative.get("commonScale") if alternative else None
            ),
            "portalAlternativeTargetCandidateIndex": (
                alternative.get("targetIndex") if alternative else None
            ),
            "portalAlternativeLiveCandidateIndex": (
                alternative.get("liveIndex") if alternative else None
            ),
            "portalAlternativeMatrixSourceToClient": (
                alternative["matrix"].tolist() if alternative else None
            ),
            "portalAlternativeResidual": (
                alternative_residual if alternative else None
            ),
            "portalBestStructureSeedScore": round(float(
                best.get("structureSeedScore", 0.0)
            ), 4),
            "portalAlternativeStructureSeedScore": (
                round(float(alternative.get("structureSeedScore", 0.0)), 4)
                if alternative else None
            ),
            "portalBestEndpointDistance": best.get("portalEndpointDistance"),
            "portalAlternativeEndpointDistance": (
                alternative.get("portalEndpointDistance")
                if alternative else None
            ),
            "portalWithinThreeMargin": round(within_three_margin, 4),
            "portalMedianMarginPixels": round(median_margin, 4),
            "selectedRouteCurrentFrameResidual": best_residual,
            "inliers": int(best_residual.get("queryEdgeCount", 0)),
            "templateScore": float(
                best_residual.get("withinThreePixels", 0.0)
            ),
            "distinctMargin": round(max(within_three_margin, 0.0), 4),
            "visibleComponent": component,
        }

    def _observe_fast_exit_side_door(
        self,
        frame: np.ndarray,
        floor: str = "2",
        *,
        precomputed_visible: tuple | None = None,
    ) -> dict:
        """Collect the cheap, current-frame evidence used before map scoring."""
        started = time.perf_counter()

        def finish(payload: dict) -> dict:
            return {
                **payload,
                "observationElapsedMs": round(
                    (time.perf_counter() - started) * 1000.0, 2,
                ),
            }

        if floor != "2":
            return finish({
                "ready": False,
                "reason": "fast-exit-floor-unsupported",
                "direction": None,
                "directionReliable": False,
                "directionSupported": False,
            })
        visible = (
            precomputed_visible
            if precomputed_visible is not None
            else visible_map_component(frame)
        )
        if visible is None:
            return finish({
                "ready": False,
                "reason": "visible-map-component-unavailable",
                "direction": None,
                "directionReliable": False,
                "directionSupported": False,
            })
        patch, bounds, _component = visible
        query = map_color_mask(patch, live=True)
        query_pixels = int(query.sum())
        minimum_query_pixels = resolution_scaled_pixels(
            12000, frame.shape, minimum=2600,
        )
        if query_pixels < minimum_query_pixels:
            return finish({
                "ready": False,
                "reason": "fast-exit-structure-too-small",
                "direction": None,
                "directionReliable": False,
                "directionSupported": False,
                "queryPixels": query_pixels,
                "minimumQueryPixels": minimum_query_pixels,
                "_visible": visible,
                "_query": query,
            })
        icon_scales = adaptive_exit_icon_scales(frame.shape)
        live_icon = self._match_exit_icon(
            frame,
            self._live_exit_search_bounds(frame.shape, tuple(bounds)),
            icon_scales,
        )
        local_rectangle = (
            local_side_door_rectangle(frame, tuple(bounds), live_icon)
            if live_icon and float(live_icon.get("score", 0.0)) >= 0.78
            else None
        )
        live_direction = live_side_door_direction(
            tuple(bounds), live_icon, local_rectangle,
        )
        rectangle_direction_verified = bool(
            local_rectangle
            and local_rectangle.get("success")
            and float(local_rectangle.get("score", 0.0)) >= 0.70
            and float(local_rectangle.get("wallSupport", 0.0)) >= 0.55
            and live_direction.get("reliable")
        )
        anchor_ready = bool(
            live_icon
            and float(live_icon.get("score", 0.0)) >= 0.78
            and (
                float(live_icon.get("distinctMargin", 0.0)) >= 0.10
                or rectangle_direction_verified
            )
        )
        detected_direction = live_direction.get("direction")
        supported_directions = {item.direction for item in self.pack.maps}
        direction_reliable = bool(live_direction.get("reliable"))
        direction_supported = bool(
            direction_reliable and detected_direction in supported_directions
        )
        return finish({
            "ready": anchor_ready,
            "reason": (
                "fast-exit-anchor-ready"
                if anchor_ready else "fast-exit-anchor-not-ready"
            ),
            "direction": detected_direction if direction_reliable else None,
            "directionReliable": direction_reliable,
            "directionSupported": direction_supported,
            "directionMargin": float(
                live_direction.get("marginPixels", 0.0)
            ),
            "directionEvidence": (
                "map-rectangle+door-endpoint"
                if direction_reliable else "insufficient-side-door-evidence"
            ),
            "queryPixels": query_pixels,
            "minimumQueryPixels": minimum_query_pixels,
            "liveExitIcon": live_icon,
            "liveExitScaleRange": [
                round(float(icon_scales[0]), 4),
                round(float(icon_scales[-1]), 4),
            ] if len(icon_scales) else [],
            "liveSideDoorDirection": live_direction,
            "_visible": visible,
            "_query": query,
            "_liveIcon": live_icon,
            "_liveDirection": live_direction,
        })

    def rank(
        self,
        frame: np.ndarray,
        floor: str = "2",
        limit: int = 5,
        *,
        budget_seconds: float | None = None,
    ) -> list[dict]:
        """Rank maps; foreground callers may enforce a manual-fallback budget."""
        rank_started = time.perf_counter()
        deadline = (
            rank_started + max(0.05, float(budget_seconds))
            if budget_seconds is not None else None
        )
        acquired = self._lock.acquire(
            timeout=max(0.0, deadline - time.perf_counter())
            if deadline is not None else -1
        )
        if not acquired:
            return []
        try:
            if floor not in {"1", "2", "B1"}:
                floor = "2"
            preload_started = time.perf_counter()
            preloaded = all(
                (item.id, floor) in self._references for item in self.pack.maps
            )
            if deadline is not None and not preloaded:
                return []
            structure_profile_id = self._active_structure_profile_id()
            if structure_profile_id == "wang-qingxin":
                self.preload(floor)
            else:
                self.preload(floor, structure_profile_id)
            preload_elapsed_ms = (
                time.perf_counter() - preload_started
            ) * 1000.0
            structure_preload_diagnostics = dict(
                getattr(self, "_structure_preload_diagnostics", {})
            )
            visible_started = time.perf_counter()
            visible = visible_map_component(frame)
            visible_component_elapsed_ms = (
                time.perf_counter() - visible_started
            ) * 1000.0
            side_door_observation = None
            side_door_observation_future = None
            observation_budget_exceeded = False
            direction_future = None
            direction_executor = None
            if floor == "2" and visible is not None:
                _visible_patch, _visible_bounds, visible_component = visible
                frame_area = max(int(frame.shape[0] * frame.shape[1]), 1)
                if int(visible_component.get("area", 0)) / frame_area <= 0.30:
                    # Detect the reliable taxonomy direction concurrently with
                    # query feature extraction, but wait for it before the first
                    # per-map SIFT/semantic comparison. The exit-wall sweep then
                    # continues beside the reduced score pool from the same
                    # captured evidence.
                    direction_executor = ThreadPoolExecutor(
                        max_workers=1,
                        thread_name_prefix="nightmare-direction",
                    )
                    side_door_observation_future = direction_executor.submit(
                        self._observe_fast_exit_side_door,
                        frame, floor, precomputed_visible=visible,
                    )
            query_started = time.perf_counter()
            query_crop, _ = nightmare_map_crop(frame)
            query = self._features(query_crop)
            query_feature_elapsed_ms = (
                time.perf_counter() - query_started
            ) * 1000.0
            query_semantic = None
            semantic_query_started = time.perf_counter()
            if visible is not None:
                query_semantic = semantic_fingerprint(
                    map_color_mask(visible[0], live=True)
                )
            semantic_query_elapsed_ms = (
                time.perf_counter() - semantic_query_started
            ) * 1000.0
            if (
                (query.descriptors is None or len(query.keypoints) < 8)
                and query_semantic is None
            ):
                if direction_executor is not None:
                    direction_executor.shutdown(
                        wait=False, cancel_futures=True,
                )
                return []
            memory_started = time.perf_counter()
            memories, confirmation_counts = self._memory_for_floor(floor)
            memory_feature_load_elapsed_ms = (
                time.perf_counter() - memory_started
            ) * 1000.0
            semantic_memory_started = time.perf_counter()
            semantic_memories = self._semantic_memory_for_floor(floor)
            semantic_memory_load_elapsed_ms = (
                time.perf_counter() - semantic_memory_started
            ) * 1000.0
            observation_wait_started = time.perf_counter()
            try:
                if side_door_observation_future is None:
                    side_door_observation = None
                elif deadline is None:
                    side_door_observation = (
                        side_door_observation_future.result()
                    )
                else:
                    side_door_observation = (
                        side_door_observation_future.result(
                            timeout=max(
                                0.0, deadline - time.perf_counter(),
                            )
                        )
                    )
            except FutureTimeoutError:
                side_door_observation = None
                observation_budget_exceeded = True
            observation_wait_elapsed_ms = (
                time.perf_counter() - observation_wait_started
            ) * 1000.0
            identity_items, pool_metrics = side_door_candidate_pool(
                self.pack.maps, side_door_observation,
            )
            pool_stage_ms = float(
                (side_door_observation or {}).get(
                    "observationElapsedMs", 0.0,
                )
            )
            if side_door_observation is not None:
                direction_future = direction_executor.submit(
                    self.rank_fast_exit_geometry, frame, floor,
                    # When weak door geometry cannot safely prune a group,
                    # the sweep may cover all 13 maps. These fits are
                    # independent and short-lived.
                    max_workers=6,
                    _precomputed_observation=side_door_observation,
                )
            identity_rows = []
            identity_scoring_elapsed_ms = 0.0
            scored_map_ids: set[str] = set()

            def score_items(
                items: list[NightmareMap],
                *,
                semantic_only: bool = False,
            ) -> None:
                nonlocal identity_scoring_elapsed_ms
                scoring_started = time.perf_counter()
                for item in items:
                    if item.id in scored_map_ids:
                        continue
                    if (
                        not semantic_only
                        and deadline is not None
                        and time.perf_counter() >= deadline
                    ):
                        break
                    reference = self._references[(item.id, floor)]
                    source_semantic = self._semantic_source_for(
                        item.id, floor, structure_profile_id,
                    )
                    reference_ready = not (
                        reference.descriptors is None
                        or len(reference.keypoints) < 8
                    )
                    if not semantic_only and not reference_ready:
                        continue
                    if semantic_only:
                        base = {
                            "score": 0.0, "inliers": 0,
                            "goodMatches": 0, "inlierRatio": 0.0,
                            "medianError": 99.0, "matrix": None,
                        }
                        learned = []
                    else:
                        base = self._match(reference, query)
                        learned = [
                            self._match(memory, query)
                            for memory in memories[item.id]
                        ]
                    best_learned = max(
                        learned, key=lambda row: row["score"], default=None,
                    )
                    count = confirmation_counts[item.id]
                    reliability = (
                        0.0 if count == 0
                        else min(1.0, 0.20 + count * 0.25)
                    )
                    memory_boost = min(
                        18.0,
                        max(
                            0.0,
                            (best_learned or {}).get("score", 0.0),
                        ) * 0.55,
                    ) * reliability
                    source_semantic_match = (
                        semantic_similarity(source_semantic, query_semantic)
                        if source_semantic is not None
                        and query_semantic is not None else None
                    )
                    memory_semantic_matches = [
                        semantic_similarity(
                            reference_semantic, query_semantic,
                        )
                        for reference_semantic in semantic_memories[item.id]
                    ] if query_semantic is not None else []
                    best_memory_semantic = max(
                        memory_semantic_matches,
                        key=lambda row: row.score,
                        default=None,
                    )
                    semantic_evidence = (
                        best_memory_semantic or source_semantic_match
                    )
                    semantic_score = float(
                        (semantic_evidence or {}).score
                    ) if semantic_evidence else 0.0
                    semantic_weight = (
                        100.0 if best_memory_semantic is not None else 82.0
                    )
                    scored_map_ids.add(item.id)
                    identity_rows.append({
                        "item": item,
                        "base": base,
                        "bestLearned": best_learned,
                        "confirmationCount": count,
                        "memoryBoost": memory_boost,
                        "sourceSemanticMatch": source_semantic_match,
                        "memorySemanticMatches": memory_semantic_matches,
                        "bestMemorySemantic": best_memory_semantic,
                        "semanticEvidence": semantic_evidence,
                        "semanticScore": semantic_score,
                        "semanticBoost": semantic_weight * semantic_score,
                    })
                identity_scoring_elapsed_ms += (
                    time.perf_counter() - scoring_started
                ) * 1000.0

            score_items(identity_items)
            budget_exceeded = bool(
                observation_budget_exceeded
                or (
                    deadline is not None
                    and time.perf_counter() >= deadline
                )
            )
            semantic_budget_fallback_used = False
            if (
                deadline is not None
                and budget_exceeded
                and query_semantic is not None
                and len(scored_map_ids) < len(identity_items)
            ):
                semantic_budget_fallback_used = True
                score_items(identity_items, semantic_only=True)
            geometry_wait_started = time.perf_counter()
            try:
                if direction_future is None:
                    direction_stage = None
                elif deadline is None:
                    direction_stage = direction_future.result()
                else:
                    direction_stage = direction_future.result(
                        timeout=max(0.0, deadline - time.perf_counter())
                    )
            except FutureTimeoutError:
                direction_stage = None
                budget_exceeded = True
            finally:
                shutdown_started = time.perf_counter()
                if direction_executor is not None:
                    direction_executor.shutdown(
                        wait=deadline is None,
                        cancel_futures=deadline is not None,
                    )
            geometry_wait_elapsed_ms = (
                time.perf_counter() - geometry_wait_started
            ) * 1000.0
            future_shutdown_elapsed_ms = (
                time.perf_counter() - shutdown_started
            ) * 1000.0
            direction_conflict = bool(
                direction_stage
                and direction_stage.get("directionGateFallbackUsed")
            )
            if pool_metrics["candidatePoolPruningApplied"] and direction_conflict:
                # Exit-wall geometry is stronger conflict evidence than the
                # preliminary rectangle direction. Restore the complete pool
                # before margins or identity gates are evaluated.
                identity_items, _fallback_metrics = side_door_candidate_pool(
                    self.pack.maps, side_door_observation, conflict=True,
                )
                # Preserve the initial 13 -> direction-group measurement while
                # recording that the final scoring pool was restored to 13.
                pool_metrics["candidatePoolFallbackUsed"] = True
                pool_metrics["candidatePoolFallbackReason"] = (
                    "direction-conflict"
                )
                score_items(identity_items)
                budget_exceeded = bool(
                    deadline is not None and time.perf_counter() >= deadline
                )
            direction_reliable = bool(
                direction_stage and direction_stage.get("directionReliable")
            )
            detected_direction = (
                str(direction_stage.get("direction")) if direction_reliable else None
            )
            supported_directions = {item.direction for item in self.pack.maps}
            direction_supported = bool(
                detected_direction and detected_direction in supported_directions
            )
            pool_metrics["candidatePoolFinalScoredCount"] = len(scored_map_ids)
            pool_metrics["candidatePoolPrunedCount"] = max(
                0,
                int(pool_metrics["candidatePoolBeforeCount"])
                - len(scored_map_ids),
            )
            pool_metrics["candidatePoolObservationElapsedMs"] = round(
                pool_stage_ms, 2,
            )
            pool_metrics["candidatePoolIdentityScoringElapsedMs"] = round(
                identity_scoring_elapsed_ms, 2,
            )
            pool_metrics["candidatePoolGeometryElapsedMs"] = round(float(
                (direction_stage or {}).get("geometryElapsedMs", 0.0)
            ), 2)
            rank_timing = {
                "preloadElapsedMs": round(preload_elapsed_ms, 2),
                "visibleComponentElapsedMs": round(
                    visible_component_elapsed_ms, 2,
                ),
                "queryFeatureElapsedMs": round(
                    query_feature_elapsed_ms, 2,
                ),
                "semanticQueryElapsedMs": round(
                    semantic_query_elapsed_ms, 2,
                ),
                "memoryFeatureLoadElapsedMs": round(
                    memory_feature_load_elapsed_ms, 2,
                ),
                "semanticMemoryLoadElapsedMs": round(
                    semantic_memory_load_elapsed_ms, 2,
                ),
                "sideObservationWaitElapsedMs": round(
                    observation_wait_elapsed_ms, 2,
                ),
                "geometryWaitElapsedMs": round(
                    geometry_wait_elapsed_ms, 2,
                ),
                "futureShutdownElapsedMs": round(
                    future_shutdown_elapsed_ms, 2,
                ),
                "identityScoringElapsedMs": round(
                    identity_scoring_elapsed_ms, 2,
                ),
                "semanticBudgetFallbackUsed": (
                    semantic_budget_fallback_used
                ),
            }
            side_rows = {
                row["mapId"]: row
                for row in (direction_stage or {}).get("crossMapRows", [])
            }
            raw_live_exit_icon = (
                (direction_stage or {}).get("liveExitIcon")
            )
            logged_live_exit_icon = (
                {
                    **raw_live_exit_icon,
                    # The existing live-event adapter already records this
                    # object. Nest pool diagnostics here so current packages
                    # expose before/after counts and stage timings without an
                    # out-of-scope change to the assistant orchestration file.
                    "candidatePoolMetrics": dict(pool_metrics),
                }
                if isinstance(raw_live_exit_icon, dict) else None
            )
            ranked = []
            for identity in identity_rows:
                item = identity["item"]
                base = identity["base"]
                best_learned = identity["bestLearned"]
                count = identity["confirmationCount"]
                memory_boost = identity["memoryBoost"]
                side_row = side_rows.get(item.id, {})
                side_identity_score = float(side_row.get("sideDoorIdentityScore", 0.0))
                side_door_boost = max(0.0, side_identity_score - 0.55) * 18.0
                source_semantic_match = identity["sourceSemanticMatch"]
                memory_semantic_matches = identity["memorySemanticMatches"]
                best_memory_semantic = identity["bestMemorySemantic"]
                semantic_evidence = identity["semanticEvidence"]
                semantic_score = identity["semanticScore"]
                semantic_boost = identity["semanticBoost"]
                # The same reliable direction still contributes its historical
                # ordering boost inside the reduced pool. It never authorizes
                # display; manual candidates continue to come from the pack.
                direction_hint_boost = 120.0 if (
                    direction_supported and item.direction == detected_direction
                ) else 0.0
                score = (
                    0.22 * base["score"] + 0.20 * memory_boost + side_door_boost
                    + semantic_boost
                    + direction_hint_boost
                )
                ranked.append({
                    "mapId": item.id, "name": item.name, "direction": item.direction,
                    "doorCount": item.door_count, "shape": item.shape, "floor": floor,
                    "score": round(float(score), 3), "baseScore": round(base["score"], 3),
                    "inliers": base["inliers"], "goodMatches": base["goodMatches"],
                    "inlierRatio": round(base["inlierRatio"], 4),
                    "medianError": round(base["medianError"], 3),
                    "memoryConfirmations": count,
                    "memoryBoost": round(float(memory_boost), 3),
                    "memoryInliers": int((best_learned or {}).get("inliers", 0)),
                    "sideDoorDirection": detected_direction,
                    "sideDoorDirectionReliable": direction_reliable,
                    "sideDoorDirectionSupported": direction_supported,
                    "sideDoorDirectionMargin": round(float(
                        (direction_stage or {}).get("directionMargin", 0.0)
                    ), 4),
                    "sideDoorDirectionEvidence": str(
                        (direction_stage or {}).get(
                            "directionEvidence", "insufficient-side-door-evidence"
                        )
                    ),
                    "sideDoorStageReason": str(
                        (direction_stage or {}).get(
                            "reason", "side-door-stage-unavailable"
                        )
                    ),
                    "sideDoorQueryPixels": int(
                        (direction_stage or {}).get("queryPixels", 0)
                    ),
                    "sideDoorMinimumQueryPixels": int(
                        (direction_stage or {}).get("minimumQueryPixels", 0)
                    ),
                    "sideDoorLiveExitIcon": logged_live_exit_icon,
                    "sideDoorLiveExitScaleRange": (
                        (direction_stage or {}).get("liveExitScaleRange") or []
                    ),
                    "sideDoorIdentityScore": round(side_identity_score, 4),
                    "sideDoorExitVerified": bool(side_row.get("sideDoorExitVerified")),
                    "sideDoorExitCenterError": round(float(
                        side_row.get("sideDoorExitCenterError", 999.0)
                    ), 3),
                    "sideDoorBoost": round(float(side_door_boost), 3),
                    "directionHintBoost": round(float(direction_hint_boost), 3),
                    "semanticScore": round(semantic_score, 4),
                    "semanticBoost": round(float(semantic_boost), 3),
                    "semanticSourceScore": round(float(
                        source_semantic_match.score if source_semantic_match else 0.0
                    ), 4),
                    "semanticMemoryScore": round(float(
                        best_memory_semantic.score if best_memory_semantic else 0.0
                    ), 4),
                    "semanticMemoryCount": len(memory_semantic_matches),
                    "semanticPartialScore": round(float(
                        semantic_evidence.partial_score if semantic_evidence else 0.0
                    ), 4),
                    "semanticSilhouetteScore": round(float(
                        semantic_evidence.silhouette_score if semantic_evidence else 0.0
                    ), 4),
                    "semanticTopologyScore": round(float(
                        semantic_evidence.topology_score if semantic_evidence else 0.0
                    ), 4),
                    **pool_metrics,
                    "cleanRecognitionReferenceUsed": bool(
                        structure_preload_diagnostics.get(
                            "cleanReferenceUsed", False,
                        )
                    ),
                    "cleanRecognitionProfileId": structure_profile_id,
                    "structurePreloadDiagnostics": dict(
                        structure_preload_diagnostics,
                    ),
                    "rankTiming": dict(rank_timing),
                    "candidatePoolScored": True,
                    "advisoryOnly": True, "displayAllowed": False,
                })
            ranked.sort(key=lambda row: row["score"], reverse=True)
            top = ranked[0]["score"] if ranked else 0.0
            second = ranked[1]["score"] if len(ranked) > 1 else 0.0
            semantic_top = ranked[0]["semanticScore"] if ranked else 0.0
            semantic_second = ranked[1]["semanticScore"] if len(ranked) > 1 else 0.0
            unique_geometry_map = (
                direction_stage.get("mapId")
                if direction_stage and direction_stage.get("success") else None
            )
            base_order = sorted(
                ranked, key=lambda row: float(row.get("baseScore", -999.0)),
                reverse=True,
            )
            semantic_order = sorted(
                ranked, key=lambda row: float(row.get("semanticScore", -999.0)),
                reverse=True,
            )
            base_winner = base_order[0] if base_order else None
            semantic_winner = semantic_order[0] if semantic_order else None
            base_margin = (
                float(base_order[0]["baseScore"]) - float(base_order[1]["baseScore"])
                if len(base_order) > 1 else 0.0
            )
            independent_semantic_margin = (
                float(semantic_order[0]["semanticScore"])
                - float(semantic_order[1]["semanticScore"])
                if len(semantic_order) > 1 else 0.0
            )
            for row in ranked:
                row["semanticMargin"] = round(float(
                    semantic_top - semantic_second if row is ranked[0] else 0.0
                ), 4)
                structure_consensus = bool(
                    row is ranked[0]
                    and base_winner is row
                    and semantic_winner is row
                    and int(row.get("inliers", 0)) >= 24
                    and int(row.get("goodMatches", 0)) >= 35
                    and float(row.get("inlierRatio", 0.0)) >= 0.42
                    and base_margin >= 8.0
                    and float(row.get("semanticScore", 0.0)) >= 0.78
                    and independent_semantic_margin >= 0.04
                )
                multipoint_feature_consensus = bool(
                    row is ranked[0]
                    and base_winner is row
                    and int(row.get("inliers", 0)) >= 80
                    and int(row.get("goodMatches", 0)) >= 100
                    and float(row.get("inlierRatio", 0.0)) >= 0.55
                    and float(row.get("medianError", 99.0)) <= 2.5
                    and base_margin >= 30.0
                )
                learned_consensus = bool(
                    row is ranked[0]
                    and base_winner is row
                    and int(row.get("inliers", 0)) >= 12
                    and float(row.get("inlierRatio", 0.0)) >= 0.28
                    and row["semanticMemoryCount"] >= 1
                    and semantic_top >= 0.90
                    and semantic_top - semantic_second >= 0.055
                    and (
                        floor != "2"
                        or (
                            direction_supported
                            and row["direction"] == detected_direction
                        )
                    )
                )
                geometry_consensus = bool(
                    row is ranked[0] and unique_geometry_map == row["mapId"]
                )
                identity_authorized = bool(
                    not budget_exceeded
                    and (
                        geometry_consensus or structure_consensus
                        or multipoint_feature_consensus or learned_consensus
                    )
                )
                row["identityHardGatePassed"] = identity_authorized
                row["identityEvidence"] = (
                    "exit-wall-geometry-unique" if geometry_consensus else
                    "independent-structure-consensus" if structure_consensus else
                    "multipoint-feature-geometry-unique"
                    if multipoint_feature_consensus else
                    "accepted-exemplar-consensus" if learned_consensus else
                    "identity-not-unique"
                )
                row["identityRunnerUpMapId"] = (
                    ranked[1]["mapId"] if len(ranked) > 1 else None
                )
                row["identityScoreMargin"] = round(float(top - second), 4)
                row["identityBaseMargin"] = round(float(base_margin), 4)
                row["identitySemanticMargin"] = round(
                    float(independent_semantic_margin), 4,
                )
                row["identityBudgetExceeded"] = bool(budget_exceeded)
                row["identityElapsedMs"] = round(
                    (time.perf_counter() - rank_started) * 1000.0, 2,
                )
                row["strongSuggestion"] = identity_authorized
            if ranked and len(scored_map_ids) < len(self.pack.maps):
                # Keep the established rank(limit=13) and automatic-candidate
                # coverage contract without paying SIFT/semantic cost for maps
                # outside the reliable direction. These neutral rows can be
                # manually selected but can never pass an identity/display gate.
                identity_elapsed_ms = round(
                    (time.perf_counter() - rank_started) * 1000.0, 2,
                )
                for item in self.pack.maps:
                    if item.id in scored_map_ids:
                        continue
                    ranked.append({
                        "mapId": item.id,
                        "name": item.name,
                        "direction": item.direction,
                        "doorCount": item.door_count,
                        "shape": item.shape,
                        "floor": floor,
                        "score": -999.0,
                        "baseScore": 0.0,
                        "inliers": 0,
                        "goodMatches": 0,
                        "inlierRatio": 0.0,
                        "medianError": 999.0,
                        "memoryConfirmations": confirmation_counts[item.id],
                        "memoryBoost": 0.0,
                        "memoryInliers": 0,
                        "sideDoorDirection": detected_direction,
                        "sideDoorDirectionReliable": direction_reliable,
                        "sideDoorDirectionSupported": direction_supported,
                        "sideDoorDirectionMargin": round(float(
                            (direction_stage or {}).get(
                                "directionMargin", 0.0,
                            )
                        ), 4),
                        "sideDoorDirectionEvidence": str(
                            (direction_stage or {}).get(
                                "directionEvidence",
                                "insufficient-side-door-evidence",
                            )
                        ),
                        "sideDoorStageReason": str(
                            (direction_stage or {}).get(
                                "reason", "side-door-stage-unavailable",
                            )
                        ),
                        "sideDoorQueryPixels": int(
                            (direction_stage or {}).get("queryPixels", 0)
                        ),
                        "sideDoorMinimumQueryPixels": int(
                            (direction_stage or {}).get(
                                "minimumQueryPixels", 0,
                            )
                        ),
                        "sideDoorLiveExitIcon": logged_live_exit_icon,
                        "sideDoorLiveExitScaleRange": (
                            (direction_stage or {}).get(
                                "liveExitScaleRange"
                            ) or []
                        ),
                        "sideDoorIdentityScore": 0.0,
                        "sideDoorExitVerified": False,
                        "sideDoorExitCenterError": 999.0,
                        "sideDoorBoost": 0.0,
                        "directionHintBoost": 0.0,
                        "semanticScore": 0.0,
                        "semanticBoost": 0.0,
                        "semanticSourceScore": 0.0,
                        "semanticMemoryScore": 0.0,
                        "semanticMemoryCount": 0,
                        "semanticPartialScore": 0.0,
                        "semanticSilhouetteScore": 0.0,
                        "semanticTopologyScore": 0.0,
                        "cleanRecognitionReferenceUsed": bool(
                            structure_preload_diagnostics.get(
                                "cleanReferenceUsed", False,
                            )
                        ),
                        "cleanRecognitionProfileId": structure_profile_id,
                        "structurePreloadDiagnostics": dict(
                            structure_preload_diagnostics,
                        ),
                        "rankTiming": dict(rank_timing),
                        "semanticMargin": 0.0,
                        "identityHardGatePassed": False,
                        "identityEvidence": "not-scored-direction-pruned",
                        "identityRunnerUpMapId": None,
                        "identityScoreMargin": 0.0,
                        "identityBaseMargin": 0.0,
                        "identitySemanticMargin": 0.0,
                        "identityBudgetExceeded": bool(budget_exceeded),
                        "identityElapsedMs": identity_elapsed_ms,
                        "strongSuggestion": False,
                        **pool_metrics,
                        "candidatePoolScored": False,
                        "advisoryOnly": True,
                        "displayAllowed": False,
                    })
            return ranked[:limit]
        finally:
                    self._lock.release()

    def _prepare_fast_reference(
        self,
        map_id: str,
        floor: str,
        route: np.ndarray | None,
        *,
        cache_key: tuple | None = None,
    ) -> tuple[np.ndarray, np.ndarray, tuple[int, int], float] | None:
        key = cache_key or (map_id, floor)
        with self._fast_cache_lock:
            cached = self._fast_reference_masks.get(key)
        if cached is not None:
            return cached
        if route is None and not self._shared_structure_enabled():
            return None
        shared_product = self._shared_structure_product(map_id, floor) if self._shared_structure_enabled() else None
        if self._shared_structure_enabled() and shared_product is None:
            return None
        original_shape = shared_product.shape if shared_product is not None else route.shape[:2]
        original_height, original_width = original_shape
        profile_id = (
            str(key[0])
            if len(key) >= 3
            and str(key[0]).strip()
            and str(key[0]) != "wang-qingxin"
            else None
        )
        resource = shared_product or self._author_structure_product(
            route, profile_id, map_id, floor,
        )
        if resource is not None:
            # Clean geometry drives only the coarse/anchor search.  The
            # exact validation channel remains paired in the cache and is
            # consumed by the final current-frame residual gate.
            full_reference = resource.clean_mask
            full_valid = resource.validation_mask
        elif profile_id in ZHAN10_STRUCTURAL_PROFILE_IDS:
            full_reference = author_route_structure_mask(
                route, profile_id=profile_id,
            )
            full_valid = full_reference.copy()
        else:
            route_bgr = (
                route[:, :, :3]
                if route.ndim == 3 and route.shape[2] >= 3 else route
            )
            full_reference = map_color_mask(
                route_bgr, live=False,
            ).astype(np.uint8)
            hsv = cv2.cvtColor(route_bgr, cv2.COLOR_BGR2HSV)
            _hue, saturation, value = cv2.split(hsv)
            annotation = (
                ((saturation >= 82) & (value >= 105)) | (value >= 218)
            )
            annotation = cv2.dilate(
                annotation.astype(np.uint8), np.ones((5, 5), np.uint8),
            )
            full_valid = (annotation == 0).astype(np.uint8)
        reference_scale = min(1.0, 1600.0 / max(original_height, original_width))
        if reference_scale < 1.0:
            target = (
                max(1, round(original_width * reference_scale)),
                max(1, round(original_height * reference_scale)),
            )
            full_reference = cv2.resize(
                full_reference, target, interpolation=cv2.INTER_NEAREST,
            )
            full_valid = cv2.resize(
                full_valid, target, interpolation=cv2.INTER_NEAREST,
            )
        built = (
            full_reference, full_valid,
            (original_height, original_width), float(reference_scale),
        )
        with self._fast_cache_lock:
            return self._fast_reference_masks.setdefault(key, built)

    def preload_fast_exit_references(self) -> int:
        """Warm the compact floor-2 structure cache before the first map opens."""
        loaded = 0
        for item in self.pack.maps:
            route = (
                None if self._shared_structure_enabled()
                else imread_unicode(item.layers["2"])
            )
            if self._prepare_fast_reference(item.id, "2", route) is not None:
                loaded += 1
        return loaded

    def _align_selected_exit_fast(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        route: np.ndarray | None,
        *,
        precomputed_visible: tuple[np.ndarray, tuple[int, int, int, int], dict] | None = None,
        precomputed_query: np.ndarray | None = None,
        precomputed_live_icon: dict | None = None,
        return_failure: bool = False,
        cache_key: tuple | None = None,
        source_icon_search_bounds: tuple[int, int, int, int] | None = None,
        source_icon_override: dict | None = None,
    ) -> dict | None:
        """Sub-0.5s confirmed-map fit from exit anchor plus wall residual.

        Identity is already human-confirmed.  Seventeen scale hypotheses around
        the icon-size ratio replace the exhaustive full-floor search; every
        accepted result still has to pass an independent wall-edge residual.
        """
        if floor != "2":
            return None
        visible = precomputed_visible or visible_map_component(frame)
        if visible is None:
            return None
        patch, bounds, component = visible
        query = precomputed_query if precomputed_query is not None else map_color_mask(patch, live=True)
        query_pixels = int(query.sum())
        minimum_query_pixels = resolution_scaled_pixels(
            12000, frame.shape, minimum=2600,
        )
        medium_query_pixels = resolution_scaled_pixels(
            30000, frame.shape, minimum=6500,
        )
        dense_query_pixels = resolution_scaled_pixels(
            60000, frame.shape, minimum=13000,
        )
        sparse_author_query_pixels = resolution_scaled_pixels(
            20000, frame.shape, minimum=4300,
        )
        if query_pixels < minimum_query_pixels:
            return None
        key = cache_key or (map_id, floor)
        cached_reference = self._prepare_fast_reference(
            map_id, floor, route, cache_key=key,
        )
        if cached_reference is None:
            return None
        full_reference, full_valid, original_shape, reference_scale = cached_reference

        item = self.pack.by_id[map_id]
        with self._fast_cache_lock:
            source_icon = self._source_exit_icon_cache.get(key)
            source_cached = key in self._source_exit_icon_cache
        if source_icon_override is not None:
            source_icon = dict(source_icon_override)
            source_cached = True
        if not source_cached:
            precomputed = (
                self._precomputed_source_exit_icons.get(map_id)
                if cache_key is None else None
            )
            built_source_icon = (
                dict(precomputed) if isinstance(precomputed, dict) else
                self._match_exit_icon(
                    route,
                    source_icon_search_bounds or tuple(item.side_door_crop),
                    np.arange(0.30, 2.051, 0.05),
                )
                if route is not None else None
            )
            with self._fast_cache_lock:
                self._source_exit_icon_cache.setdefault(key, built_source_icon)
                source_icon = self._source_exit_icon_cache.get(key)
        live_icon = precomputed_live_icon or self._match_exit_icon(
            frame,
            self._live_exit_search_bounds(frame.shape, tuple(bounds)),
            adaptive_exit_icon_scales(frame.shape),
        )
        live_rectangle = None
        live_direction = None
        icon_unique = bool(
            live_icon
            and float(live_icon.get("distinctMargin", 0.0)) >= 0.10
        )
        if live_icon and not icon_unique:
            live_rectangle = local_side_door_rectangle(
                frame, tuple(bounds), live_icon,
            )
            live_direction = live_side_door_direction(
                tuple(bounds), live_icon, live_rectangle,
            )
        # A duplicated-looking exit glyph is not automatically ambiguous when
        # the user's tight side-door rectangle independently fixes its long
        # axis and endpoint.  The downstream wall residual still decides
        # whether the resulting transform is displayable.
        rectangle_anchor_verified = bool(
            live_rectangle
            and live_rectangle.get("success")
            and float(live_rectangle.get("score", 0.0)) >= 0.70
            and float(live_rectangle.get("wallSupport", 0.0)) >= 0.55
            and float(live_rectangle.get("colorConsistency", 0.0)) >= 0.85
            and live_direction
            and live_direction.get("reliable")
            and live_direction.get("direction") == item.direction
        )
        if not (
            source_icon and live_icon
            and float(source_icon.get("score", 0.0)) >= 0.62
            and float(live_icon.get("score", 0.0)) >= 0.78
            and (icon_unique or rectangle_anchor_verified)
        ):
            return None
        seed_scale = float(live_icon["scale"]) / max(
            float(source_icon["scale"]), 0.001,
        )
        reference_height, reference_width = original_shape
        reference_edge = float(max(reference_height, reference_width))
        nominal_scale = 1200.0 / max(reference_edge, 1.0)
        minimum_scale = max(0.05, nominal_scale * 0.55)
        maximum_scale = min(6.0, nominal_scale * 1.65)
        # Icon templates are sampled in coarse 0.05 steps and the clean/live
        # glyphs have different glow extents.  A ratio just outside the canvas
        # heuristic is therefore still a useful seed as long as the actual
        # searched scales remain inside the normal geometry range.  Map-04's
        # real 2560x1417 frame reports 1.357 while the verified wall optimum is
        # 1.296 (inside the unchanged 1.318 cap).
        if not (
            minimum_scale * 0.92 <= seed_scale <= maximum_scale * 1.08
        ):
            return None

        qh, qw = query.shape
        screen_x, screen_y = (float(value) for value in bounds[:2])
        source_center = np.asarray(source_icon["center"], dtype=np.float64)
        live_center = np.asarray(live_icon["center"], dtype=np.float64)
        query_float = query.astype(np.float32)
        query_positive = max(float(query_float.sum()), 1.0)
        rows = []
        for candidate_scale in np.linspace(
            max(minimum_scale, seed_scale * 0.94),
            min(maximum_scale, seed_scale * 1.06),
            17,
        ):
            translation = live_center - source_center * candidate_scale
            cached_scale = candidate_scale / max(reference_scale, 0.001)
            source_to_query = np.array([
                [cached_scale, 0.0, translation[0] - screen_x],
                [0.0, cached_scale, translation[1] - screen_y],
            ], dtype=np.float32)
            projected_reference = cv2.warpAffine(
                full_reference, source_to_query, (qw, qh),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT, borderValue=0,
            ).astype(np.float32)
            projected_valid = cv2.warpAffine(
                full_valid, source_to_query, (qw, qh),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT, borderValue=0,
            ).astype(np.float32)
            valid_query = query_float * projected_valid
            compared_fraction = float(valid_query.sum()) / query_positive
            if compared_fraction < 0.45:
                continue
            intersection = float((projected_reference * valid_query).sum())
            reference_count = float(
                (projected_reference * projected_valid).sum()
            )
            valid_query_count = float(valid_query.sum())
            score = min(
                1.0,
                2.0 * intersection
                / max(valid_query_count + reference_count, 1.0),
            )
            rows.append({
                "score": score,
                "scale": float(candidate_scale),
                "translation": translation,
                "projectedReference": projected_reference.astype(np.uint8),
                "comparedFraction": compared_fraction,
                "queryCoverage": intersection / max(valid_query_count, 1.0),
                "referenceCoverage": intersection / max(reference_count, 1.0),
                "anchorOffset": [0.0, 0.0],
            })
        if not rows:
            return None
        edge_kernel = np.ones((3, 3), np.uint8)
        query_edges = cv2.morphologyEx(
            query.astype(np.uint8), cv2.MORPH_GRADIENT, edge_kernel,
        ) > 0
        edge_count = int(query_edges.sum())
        if edge_count < resolution_scaled_pixels(
            200, frame.shape, minimum=55,
        ):
            return None
        for row in rows:
            projected_edges = cv2.morphologyEx(
                row["projectedReference"], cv2.MORPH_GRADIENT, edge_kernel,
            ) > 0
            if not projected_edges.any():
                row["edgeMedian"] = 999.0
                row["edgeP90"] = 999.0
                row["edgeWithinThree"] = 0.0
                row["jointScore"] = -999.0
                continue
            distances = cv2.distanceTransform(
                (~projected_edges).astype(np.uint8), cv2.DIST_L2, 3,
            )[query_edges]
            row["edgeMedian"] = float(np.median(distances))
            row["edgeP90"] = float(np.percentile(distances, 90))
            row["edgeWithinThree"] = float(np.mean(distances <= 3.0))
            # Dice alone can prefer a neighbouring scale on a tiny island.
            # The wall-edge term resolves close hypotheses without another
            # exhaustive structure search.
            row["jointScore"] = (
                float(row["score"])
                + 0.25 * row["edgeWithinThree"]
                - 0.01 * min(row["edgeMedian"], 10.0)
            )
        top_dice = max(float(row["score"]) for row in rows)
        close_rows = [
            row for row in rows
            if float(row["score"]) >= max(0.60, top_dice - 0.01)
        ]
        # A valid exit-icon seed can still produce no wall hypothesis above
        # the display threshold on a barely explored or wrong map.  Treat that
        # as ordinary insufficient evidence instead of indexing an empty list
        # and killing the background recognition worker.
        if not close_rows:
            return None
        close_rows.sort(key=lambda row: row["jointScore"], reverse=True)
        best = close_rows[0]

        # Matching the rendered icon centers exactly can move the surrounding
        # walls by several pixels: the clean sheet and the live HUD draw the
        # same exit glyph with different glow/crop extents.  Only on a strong,
        # human-confirmed near miss, search a tiny translation neighbourhood
        # while keeping the semantic exit within ten pixels.  This is not a
        # generic relaxed gate: the refined walls still need an independent
        # residual, high Dice, and a unique exit on both images.
        initial_residual_ok = bool(
            float(best["edgeMedian"]) <= 4.0
            and float(best["edgeP90"]) <= 24.0
            and float(best["edgeWithinThree"]) >= 0.45
        )
        directional_partial_seed = bool(
            float(best["score"]) >= 0.63
            and float(best.get("queryCoverage", 0.0)) >= 0.97
            and float(best.get("referenceCoverage", 0.0)) >= 0.40
            and float(best["edgeP90"]) <= 20.0
            and query_pixels >= dense_query_pixels
        )
        strong_refinement_seed = bool(
            not initial_residual_ok
            and (
                float(best["score"]) >= 0.82
                or directional_partial_seed
            )
            and float(best["comparedFraction"]) >= 0.55
            and float(source_icon.get("distinctMargin", 0.0)) >= 0.25
            and float(live_icon.get("score", 0.0)) >= 0.78
            and float(live_icon.get("distinctMargin", 0.0)) >= 0.12
            and query_pixels >= medium_query_pixels
        )
        if strong_refinement_seed:
            def translated_row(base: dict, offset_x: int, offset_y: int) -> dict | None:
                candidate_scale = float(base["scale"])
                translation = (
                    live_center - source_center * candidate_scale
                    + np.asarray([offset_x, offset_y], dtype=np.float64)
                )
                cached_scale = candidate_scale / max(reference_scale, 0.001)
                source_to_query = np.array([
                    [cached_scale, 0.0, translation[0] - screen_x],
                    [0.0, cached_scale, translation[1] - screen_y],
                ], dtype=np.float32)
                projected_reference = cv2.warpAffine(
                    full_reference, source_to_query, (qw, qh),
                    flags=cv2.INTER_NEAREST,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0,
                ).astype(np.uint8)
                projected_valid = cv2.warpAffine(
                    full_valid, source_to_query, (qw, qh),
                    flags=cv2.INTER_NEAREST,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0,
                ).astype(np.float32)
                valid_query = query_float * projected_valid
                compared_fraction = float(valid_query.sum()) / query_positive
                if compared_fraction < 0.55:
                    return None
                intersection = float(
                    (projected_reference.astype(np.float32) * valid_query).sum()
                )
                valid_query_count = float(valid_query.sum())
                reference_count = float(
                    (projected_reference.astype(np.float32) * projected_valid).sum()
                )
                score = min(
                    1.0,
                    2.0 * intersection
                    / max(valid_query_count + reference_count, 1.0),
                )
                projected_edges = cv2.morphologyEx(
                    projected_reference, cv2.MORPH_GRADIENT, edge_kernel,
                ) > 0
                if not projected_edges.any():
                    return None
                distances = cv2.distanceTransform(
                    (~projected_edges).astype(np.uint8), cv2.DIST_L2, 3,
                )[query_edges]
                edge_median = float(np.median(distances))
                edge_p90 = float(np.percentile(distances, 90))
                edge_within_three = float(np.mean(distances <= 3.0))
                return {
                    "score": float(score),
                    "scale": candidate_scale,
                    "translation": translation,
                    "projectedReference": projected_reference,
                    "comparedFraction": compared_fraction,
                    "queryCoverage": intersection / max(valid_query_count, 1.0),
                    "referenceCoverage": intersection / max(reference_count, 1.0),
                    "edgeMedian": edge_median,
                    "edgeP90": edge_p90,
                    "edgeWithinThree": edge_within_three,
                    "jointScore": (
                        float(score)
                        + 0.32 * edge_within_three
                        - 0.014 * min(edge_median, 10.0)
                    ),
                    "anchorOffset": [float(offset_x), float(offset_y)],
                }

            # The residual optimum can sit at the edge of the icon-size band
            # even when its exact-center Dice ranks fourth (current map-06).
            # Keep the three strongest scales plus that upper endpoint. A 3x3
            # translation grid and one 3x3 fine pass stay below the gameplay
            # latency budget even on the larger explored island.
            refinement_seeds = list(close_rows[:3])
            largest_scale_row = max(
                close_rows, key=lambda row: float(row["scale"]),
            )
            if largest_scale_row not in refinement_seeds:
                refinement_seeds.append(largest_scale_row)
            coarse_refined = []
            for seed in refinement_seeds:
                for offset_y in (-8, 0, 8):
                    for offset_x in (-8, 0, 8):
                        if offset_x == 0 and offset_y == 0:
                            continue
                        row = translated_row(seed, offset_x, offset_y)
                        if row is not None:
                            coarse_refined.append(row)
            if coarse_refined:
                coarse_refined.sort(
                    key=lambda row: row["jointScore"], reverse=True,
                )
                coarse_best = coarse_refined[0]
                center_x, center_y = (
                    int(value) for value in coarse_best["anchorOffset"]
                )
                fine_refined = []
                for offset_y in range(center_y - 2, center_y + 3, 2):
                    for offset_x in range(center_x - 2, center_x + 3, 2):
                        if np.hypot(offset_x, offset_y) > 10.0:
                            continue
                        row = translated_row(
                            coarse_best, offset_x, offset_y,
                        )
                        if row is not None:
                            fine_refined.append(row)
                refined = max(
                    coarse_refined + fine_refined,
                    key=lambda row: row["jointScore"],
                )
                if float(refined["jointScore"]) >= float(best["jointScore"]) + 0.012:
                    best = refined
        scale = float(best["scale"])
        scale_agreement = abs(scale - seed_scale) / max(seed_scale, 0.001)
        edge_median = float(best["edgeMedian"])
        edge_p90 = float(best["edgeP90"])
        edge_within_three = float(best["edgeWithinThree"])
        visual_residual = {
            "queryEdgeCount": edge_count,
            "medianPixels": edge_median,
            "p90Pixels": edge_p90,
            "withinThreePixels": edge_within_three,
        }
        anchor_offset = np.asarray(
            best.get("anchorOffset", [0.0, 0.0]), dtype=np.float64,
        )
        refined_partial_containment_ok = bool(
            float(best["score"]) >= 0.63
            and float(best.get("queryCoverage", 0.0)) >= 0.97
            and float(best.get("referenceCoverage", 0.0)) >= 0.40
            and query_pixels >= dense_query_pixels
        )
        refined_semantic_geometry_ok = bool(
            np.linalg.norm(anchor_offset) <= 10.0
            and (
                float(best["score"]) >= 0.84
                or refined_partial_containment_ok
            )
            and float(best["comparedFraction"]) >= 0.60
            and scale_agreement <= 0.065
            and float(source_icon.get("distinctMargin", 0.0)) >= 0.25
            and float(live_icon.get("score", 0.0)) >= 0.78
            and float(live_icon.get("distinctMargin", 0.0)) >= 0.12
            and query_pixels >= medium_query_pixels
            # Moving player/waypoint markers add unmatched edges to an
            # otherwise exact map. The known ghost regressions remain well
            # separated at p90 >= 24 px, so keep that tail residual as the
            # veto while allowing the current 17-19 px partial-map frames.
            and edge_median <= 4.8
            and edge_p90 <= 20.0
            and edge_within_three >= 0.38
        )
        # Exploration fog creates artificial frontier edges inside an otherwise
        # correctly placed room.  Those edges inflate the symmetric wall
        # residual even when almost every visible map pixel is contained by the
        # clean sheet.  Accept that *different* proof only when a unique exit
        # fixes translation, the icon ratio bounds scale, and the current island
        # is both large and nearly completely contained.  The two saved map-04
        # ghost regressions stay below the size/score/containment gates.
        exploration_frontier_containment_ok = bool(
            np.linalg.norm(anchor_offset) <= 1.0
            and float(best["score"]) >= 0.805
            and float(best.get("queryCoverage", 0.0)) >= 0.995
            and float(best.get("referenceCoverage", 0.0)) >= 0.675
            and float(best["comparedFraction"]) >= 0.85
            and scale_agreement <= 0.05
            and float(source_icon.get("distinctMargin", 0.0)) >= 0.15
            and float(live_icon.get("score", 0.0)) >= 0.78
            and float(live_icon.get("distinctMargin", 0.0)) >= 0.15
            and query_pixels >= dense_query_pixels
            and edge_median <= 9.0
            and edge_p90 <= 55.0
            and edge_within_three >= 0.30
        )
        # Alternate-author sheets can be a materially closer representation of
        # the live floor than the legacy recognition sheet.  On a newly opened
        # F2 map the only explored island may be the side exit plus its adjacent
        # corridor.  In that case exploration-frontier edges raise the p90 tail
        # even though the unique exit, icon-derived scale and nearly all local
        # wall pixels agree.  Keep this proof exclusive to an already-confirmed
        # alternate-author route: the icon alone is never sufficient, and the
        # dense local containment/residual gates still veto a wrong map.
        author_sparse_exit_containment_ok = bool(
            cache_key is not None
            and np.linalg.norm(anchor_offset) <= 1.0
            and float(best["score"]) >= 0.90
            and float(best.get("queryCoverage", 0.0)) >= 0.96
            and float(best.get("comparedFraction", 0.0)) >= 0.90
            and scale_agreement <= 0.04
            and float(source_icon.get("distinctMargin", 0.0)) >= 0.15
            and float(live_icon.get("score", 0.0)) >= 0.80
            and float(live_icon.get("distinctMargin", 0.0)) >= 0.15
            and query_pixels >= sparse_author_query_pixels
            and edge_median <= 1.2
            and edge_p90 <= 28.0
            and edge_within_three >= 0.68
        )
        # On clients narrower than the game's 16:9 safe display area, the HUD
        # exit glyph and the map canvas are not guaranteed to use exactly the
        # same scale.  The icon remains a useful position/scale seed, but the
        # current wall island must be allowed to refine that seed slightly past
        # the legacy +/-6% band.  This is deliberately a separate, strict proof:
        # an almost-complete local containment, precise multi-wall residual and
        # distinct source/live exit are all required.  The icon alone can never
        # authorize the relaxed scale.
        fh, fw = frame.shape[:2]
        client_aspect = float(fw) / max(float(fh), 1.0)
        safe_area_wall_scale_refinement_ok = bool(
            client_aspect < SAFE_DISPLAY_ASPECT_RATIO - 0.02
            and np.linalg.norm(anchor_offset) <= 1.0
            and float(best["score"]) >= 0.79
            and float(best.get("queryCoverage", 0.0)) >= 0.99
            and float(best.get("referenceCoverage", 0.0)) >= 0.64
            and float(best.get("comparedFraction", 0.0)) >= 0.80
            and 0.06 < scale_agreement <= 0.075
            and float(source_icon.get("distinctMargin", 0.0)) >= 0.15
            and float(live_icon.get("score", 0.0)) >= 0.80
            and float(live_icon.get("distinctMargin", 0.0)) >= 0.15
            and query_pixels >= resolution_scaled_pixels(
                24000, frame.shape, minimum=5200,
            )
            and edge_median <= 2.1
            and edge_p90 <= 14.0
            and edge_within_three >= 0.55
        )
        translation = np.asarray(best["translation"], dtype=np.float64)
        matrix = np.array([
            [scale, 0.0, translation[0]],
            [0.0, scale, translation[1]],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        projected_width = reference_width * scale
        projected_height = reference_height * scale
        x0, y0 = translation
        x1, y1 = x0 + projected_width, y0 + projected_height
        intersection_area = (
            max(0.0, min(x1, fw) - max(x0, 0.0))
            * max(0.0, min(y1, fh) - max(y0, 0.0))
        )
        box_area = max(1.0, projected_width * projected_height)
        screen_overlap = intersection_area / min(box_area, float(fw * fh))
        success = bool(
            float(best["score"]) >= 0.60
            # The fast scale lattice intentionally includes the exact +/-6%
            # endpoints.  Floating point division can represent that endpoint
            # as 0.060000000000000005; use a numerical tolerance rather than
            # rejecting an otherwise exact live exit+multi-wall solution.
            and scale_agreement <= 0.06 + 1e-9
            # Sparse exploration leaves soft antialiased wall edges. The
            # existing independently verified slow path accepts the same
            # strong exit+wall solution at a four-pixel median; keeping that
            # gate here avoids a 2-4 second exhaustive retry without allowing
            # the exit icon alone to authorize display.
            and edge_median <= 4.0
            and edge_p90 <= 24.0
            and edge_within_three >= 0.45
            and screen_overlap >= 0.05
        )
        success = bool(success or (
            refined_semantic_geometry_ok and screen_overlap >= 0.05
        ))
        success = bool(success or (
            exploration_frontier_containment_ok and screen_overlap >= 0.05
        ))
        success = bool(success or (
            author_sparse_exit_containment_ok and screen_overlap >= 0.05
        ))
        success = bool(success or (
            safe_area_wall_scale_refinement_ok and screen_overlap >= 0.05
        ))
        if not success and not return_failure:
            return None
        return {
            "success": success,
            "reason": (
                "aligned-fast-exit-anchor-local-refine"
                if (
                    success and refined_semantic_geometry_ok
                    and np.linalg.norm(anchor_offset) >= 1.0
                ) else
                "aligned-fast-exit-anchor-exploration-frontier"
                if success and exploration_frontier_containment_ok else
                "aligned-fast-author-sparse-exit"
                if success and author_sparse_exit_containment_ok else
                "aligned-fast-exit-anchor-wall-scale-refine"
                if success and safe_area_wall_scale_refinement_ok else
                "aligned-fast-exit-anchor-semantic-partial"
                if success and refined_semantic_geometry_ok else
                "aligned-fast-exit-anchor"
                if success else
                "fast-exit-anchor-residual-failed"
            ),
            "alignmentMode": "partial-structure-dice",
            "plausibleGeometry": True,
            "score": float(best["score"]),
            "templateScore": float(best["score"]),
            "distinctScore": float(source_icon.get("secondScore", 0.0)),
            "distinctMargin": float(source_icon.get("distinctMargin", 0.0)),
            "transformDistinctScore": 0.0,
            "transformDistinctMargin": 0.0,
            "queryPixels": query_pixels,
            "scaleSearch": [minimum_scale, maximum_scale],
            "searchMode": (
                "fast-exit-anchor-local-refine"
                if (
                    refined_semantic_geometry_ok
                    and np.linalg.norm(anchor_offset) >= 1.0
                ) else
                "fast-exit-anchor-exploration-frontier"
                if exploration_frontier_containment_ok else
                "fast-author-sparse-exit"
                if author_sparse_exit_containment_ok else
                "fast-exit-anchor-wall-scale-refine"
                if safe_area_wall_scale_refinement_ok else
                "fast-exit-anchor-semantic-partial"
                if refined_semantic_geometry_ok else
                "fast-exit-anchor"
            ),
            "searchBounds": list(item.side_door_crop),
            "searchCropFallback": False,
            "boundaryExpanded": False,
            "fullFloorBoundaryAccepted": False,
            "fullFloorPrecisionAccepted": False,
            "exitAnchor": {
                "sourceScore": float(source_icon["score"]),
                "sourceMargin": float(source_icon["distinctMargin"]),
                "sourceCenter": list(source_icon["center"]),
                "liveScore": float(live_icon["score"]),
                "liveMargin": float(live_icon["distinctMargin"]),
                "liveCenter": list(live_icon["center"]),
                "structureScore": float(best["score"]),
                "scaleAgreement": float(scale_agreement),
                "scaleSeed": float(seed_scale),
                "scaleSeedSource": "exit-icon-size-ratio-fast",
                "translationRefinementPixels": anchor_offset.tolist(),
                "liveAnchorEvidence": (
                    "side-door-rectangle+endpoint"
                    if rectangle_anchor_verified else "unique-exit-icon"
                ),
                "sourceAnchorEvidence": (
                    "reviewed-author-adapter"
                    if source_icon.get("reviewedAuthorAdapterAnchor") else
                    "route-pixel-template"
                ),
                "liveRectangle": ({
                    "bounds": list(live_rectangle.get("bounds", [])),
                    "orientation": live_rectangle.get("orientation"),
                    "score": float(live_rectangle.get("score", 0.0)),
                    "wallSupport": float(live_rectangle.get("wallSupport", 0.0)),
                } if rectangle_anchor_verified else None),
            },
            "exitAnchorConflict": None,
            "exitAnchorConflictOverridden": False,
            "structureIconConsistency": None,
            "semanticPrecisionOverride": bool(
                refined_semantic_geometry_ok
                or exploration_frontier_containment_ok
                or author_sparse_exit_containment_ok
            ),
            "refinedPartialContainment": refined_partial_containment_ok,
            "explorationFrontierContainmentVerified": (
                exploration_frontier_containment_ok
            ),
            "authorSparseExitContainmentVerified": (
                author_sparse_exit_containment_ok
            ),
            "safeAreaWallScaleRefinementVerified": (
                safe_area_wall_scale_refinement_ok
            ),
            "iconRatioGeometryVerified": bool(
                initial_residual_ok or refined_semantic_geometry_ok
                or author_sparse_exit_containment_ok
            ),
            "acceptedPriorUsed": False,
            "acceptedPriorScale": None,
            "visualResidual": visual_residual,
            "visibleComponent": component,
            "candidateDiagnostics": [
                {
                    "score": float(row["score"]),
                    "scale": float(row["scale"]),
                    "comparedFraction": float(row["comparedFraction"]),
                    "queryCoverage": float(row.get("queryCoverage", 0.0)),
                    "referenceCoverage": float(row.get("referenceCoverage", 0.0)),
                }
                for row in rows[:5]
            ],
            "scale": scale,
            "anisotropy": 1.0,
            "screenOverlap": float(screen_overlap),
            "inliers": 0,
            "inlierRatio": 0.0,
            "medianError": 0.0,
            "matrixSourceToClient": matrix.tolist(),
            "mapId": map_id,
            "fastPath": True,
        }

    def rank_fast_exit_geometry(
        self,
        frame: np.ndarray,
        floor: str = "2",
        *,
        max_workers: int = 4,
        _precomputed_observation: dict | None = None,
    ) -> dict:
        """Advisory cross-map sweep using each sheet's stored exit anchor.

        A tiny explored island is too generic for whole-map retrieval.  Once
        the live exit icon is visible, however, every source can be projected
        from its precomputed exit coordinate and checked against the nearby
        walls.  Wrong maps simply fail the same residual gate used by the
        confirmed-map fast path.  Results only reorder candidates; they never
        authorize overlay display or remove the human fallback.
        """
        observation = (
            _precomputed_observation
            if _precomputed_observation is not None
            else self._observe_fast_exit_side_door(frame, floor)
        )
        observation_elapsed_ms = float(
            observation.get("observationElapsedMs", 0.0)
        )

        def public_observation() -> dict:
            return {
                key: observation[key]
                for key in (
                    "reason", "direction", "directionReliable",
                    "directionSupported", "directionMargin",
                    "directionEvidence", "queryPixels",
                    "minimumQueryPixels", "liveExitIcon",
                    "liveExitScaleRange", "liveSideDoorDirection",
                    "observationElapsedMs",
                )
                if key in observation
            }

        if not observation.get("ready"):
            return {
                "success": False,
                **public_observation(),
                "crossMapRows": [],
                "geometryElapsedMs": 0.0,
                "elapsedMs": round(observation_elapsed_ms, 2),
                "advisoryOnly": True,
            }
        visible = observation["_visible"]
        query = observation["_query"]
        live_icon = observation["_liveIcon"]
        live_direction = observation["_liveDirection"]
        query_pixels = int(observation.get("queryPixels", 0))
        minimum_query_pixels = int(
            observation.get("minimumQueryPixels", 0)
        )
        geometry_started = time.perf_counter()
        def fit_item(item: NightmareMap) -> dict | None:
            key = (item.id, floor)
            with self._fast_cache_lock:
                reference_ready = key in self._fast_reference_masks
            if self._shared_structure_enabled() and not reference_ready:
                reference_ready = (
                    self._prepare_fast_reference(item.id, floor, None) is not None
                )
            route = (
                None if reference_ready or self._shared_structure_enabled()
                else self.route_image(item.id, floor)
            )
            if route is None and not reference_ready:
                return None
            fit = self._align_selected_exit_fast(
                frame, item.id, floor, route,
                precomputed_visible=visible,
                precomputed_query=query,
                precomputed_live_icon=live_icon,
            )
            if fit is None:
                return None
            return {
                **fit,
                "mapId": item.id,
                "mapName": item.name,
                "direction": item.direction,
                "sideDoorIdentityScore": 2.0 + float(fit.get("templateScore", 0.0)),
                "sideDoorStructuralScore": float(fit.get("templateScore", 0.0)),
                "sideDoorExitVerified": True,
                "sideDoorExitCenterError": 0.0,
            }

        detected_direction = live_direction.get("direction")
        supported_directions = {item.direction for item in self.pack.maps}
        direction_gate_supported = bool(
            live_direction.get("reliable")
            and detected_direction in supported_directions
        )
        # Door direction is the author's first-stage taxonomy, so do that
        # cheap semantic cut before rendering wall hypotheses for every map.
        # The old order fitted all 13 maps and discarded the wrong directions
        # afterwards, wasting most of the cold-path latency.
        eligible_items = (
            [item for item in self.pack.maps if item.direction == detected_direction]
            if direction_gate_supported else list(self.pack.maps)
        )
        def fit_items(items: list[NightmareMap]) -> list[dict]:
            if not items:
                return []
            workers = max(1, min(int(max_workers), 6, len(items)))
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="nightmare-exit",
            ) as executor:
                return [
                    row for row in executor.map(fit_item, items)
                    if row is not None
                ]

        rows = fit_items(eligible_items)
        direction_gate_fallback_used = False
        if direction_gate_supported and not rows:
            # Door direction is a cheap taxonomy hint, not authority stronger
            # than a unique exit-anchored multi-wall fit. If its group produces
            # no valid geometry, inspect the remaining maps instead of leaving
            # a known-good map permanently unreachable.
            remaining = [
                item for item in self.pack.maps
                if item.direction != detected_direction
            ]
            rows = fit_items(remaining)
            direction_gate_fallback_used = bool(rows)
        rows.sort(
            key=lambda row: (
                float(row.get("templateScore", 0.0)),
                -float((row.get("visualResidual") or {}).get("medianPixels", 999.0)),
            ),
            reverse=True,
        )
        rejected_direction_rows = []
        if direction_gate_supported:
            rejected_direction_rows = [
                row for row in rows if row.get("direction") != detected_direction
            ]
            # Direction is a promotion gate, not a UI filter.  A conflicting
            # geometric fit remains diagnostic evidence but cannot jump above
            # the human-selected taxonomy group.  All maps still remain in the
            # candidate list through the normal fallback path.
            if not direction_gate_fallback_used:
                rows = [
                    row for row in rows
                    if row.get("direction") == detected_direction
                ]
        geometry_elapsed_ms = (
            time.perf_counter() - geometry_started
        ) * 1000.0
        elapsed_ms = observation_elapsed_ms + geometry_elapsed_ms
        geometry_unique = len(rows) == 1
        resolution_linear = float(np.sqrt(resolution_area_ratio(frame.shape)))
        normalized_p90_limit = max(7.0, 20.0 * resolution_linear)
        unique_residual = (
            (rows[0].get("visualResidual") or {}) if geometry_unique else {}
        )
        resolution_precision_verified = bool(
            geometry_unique
            and (
                int(frame.shape[1]) >= 1600
                or float(unique_residual.get("p90Pixels", 999.0))
                <= normalized_p90_limit
                or rows[0].get("safeAreaWallScaleRefinementVerified") is True
            )
        )
        unique = bool(geometry_unique and resolution_precision_verified)
        geometry_direction_override = bool(
            direction_gate_fallback_used and unique
            and rows[0].get("direction") != detected_direction
        )
        geometry_direction_derived = bool(
            unique and not live_direction.get("reliable")
            and rows[0].get("direction") in supported_directions
        )
        effective_direction = (
            rows[0].get("direction")
            if geometry_direction_override or geometry_direction_derived
            else live_direction.get("direction")
        )
        effective_direction_reliable = bool(
            geometry_direction_override or geometry_direction_derived
            or live_direction.get("reliable")
        )
        effective_direction_supported = bool(
            effective_direction_reliable
            and effective_direction in supported_directions
        )
        return {
            "success": unique,
            "reason": (
                "fast-exit-unique-geometry" if unique else
                "fast-exit-geometry-not-resolution-precise"
                if geometry_unique else
                "fast-exit-geometry-ambiguous" if rows else
                "fast-exit-no-geometry-match"
            ),
            "mapId": rows[0]["mapId"] if unique else None,
            "mapName": rows[0]["mapName"] if unique else None,
            "direction": effective_direction,
            "directionReliable": effective_direction_reliable,
            "directionSupported": effective_direction_supported,
            "directionMargin": float(live_direction.get("marginPixels", 0.0)),
            "directionEvidence": (
                "exit-wall-geometry-overrode-door-rectangle"
                if geometry_direction_override else
                "exit-wall-geometry-derived-door-direction"
                if geometry_direction_derived else
                "map-rectangle+door-endpoint"
                if live_direction.get("reliable") else
                "insufficient-side-door-evidence"
            ),
            "directionGateFallbackUsed": direction_gate_fallback_used,
            "directionGeometryOverride": geometry_direction_override,
            "directionGeometryDerived": geometry_direction_derived,
            "resolutionPrecisionVerified": resolution_precision_verified,
            "resolutionP90LimitPixels": round(normalized_p90_limit, 4),
            "resolutionP90Pixels": (
                round(float(unique_residual.get("p90Pixels", 999.0)), 4)
                if geometry_unique else None
            ),
            "liveSideDoorDirection": live_direction,
            "crossMapRows": rows,
            "directionRejectedRows": [
                {
                    "mapId": row["mapId"],
                    "mapName": row["mapName"],
                    "direction": row["direction"],
                    "score": float(row.get("templateScore", 0.0)),
                }
                for row in rejected_direction_rows
            ],
            "crossMapTop": [
                {
                    "mapId": row["mapId"],
                    "mapName": row["mapName"],
                    "direction": row["direction"],
                    "score": float(row.get("templateScore", 0.0)),
                    "sideDoorIdentityScore": float(row.get("sideDoorIdentityScore", 0.0)),
                    "sideDoorExitVerified": True,
                    "sideDoorExitCenterError": 0.0,
                }
                for row in rows[:3]
            ],
            "queryPixels": query_pixels,
            "minimumQueryPixels": minimum_query_pixels,
            "liveExitIcon": live_icon,
            "liveExitScaleRange": observation.get(
                "liveExitScaleRange", []
            ),
            "observationElapsedMs": round(observation_elapsed_ms, 2),
            "geometryElapsedMs": round(geometry_elapsed_ms, 2),
            "elapsedMs": round(elapsed_ms, 2),
            "advisoryOnly": True,
            "displayAllowed": False,
        }

    def align_selected_with_math_pool(
        self, frame, map_id, floor, *, fast_exit_attempted=False,
    ) -> dict:
        """V2 worker-only entry; one request owner, bounded pure math lanes."""
        cpu_limit = max(1, min(4, (os.cpu_count() or 1) // 4))
        lanes = max(1, min(cpu_limit, int(getattr(self, "partial_math_lanes", 3))))
        if lanes == 1:
            result = self.align_selected(
                frame, map_id, floor, skip_exit_fast=fast_exit_attempted,
            )
            return {**result, "mathLanes": 1}
        # The V2 request worker owns this lifetime. Close/reset never join it
        # on the UI thread, and no pool task submits or waits for another task.
        with ThreadPoolExecutor(max_workers=lanes, thread_name_prefix="nightmare-math") as pool:
            result = self.align_selected(
                frame, map_id, floor, math_pool=(pool, lanes),
                skip_exit_fast=fast_exit_attempted,
            )
        return {**result, "mathLanes": lanes}

    def align_selected(
        self,
        frame: np.ndarray,
        map_id: str,
        floor: str,
        accepted_prior: dict | None = None,
        cancelled: Callable[[], bool] | None = None,
        *, math_pool=None, skip_exit_fast=False,
    ) -> dict:
        """Fit the confirmed source layer to the current game frame."""
        with self._lock:
            if cancelled is not None and cancelled():
                return {
                    "success": False,
                    "reason": "alignment-cancelled",
                    "alignmentMode": "cancelled",
                    "plausibleGeometry": False,
                }
            if map_id not in self.pack.by_id or floor not in {"1", "2", "B1"}:
                return {"success": False, "reason": "invalid-selection"}
            shared_mode = self._shared_structure_enabled()
            route = None if shared_mode else self.route_image(map_id, floor)
            if route is None and not shared_mode:
                return {"success": False, "reason": "route-image-missing"}

            # V2 already tried this exact current-frame fast exit before its
            # generic fallback. Legacy/synchronous callers retain that probe.
            fast = None if (skip_exit_fast or shared_mode) else self._align_selected_exit_fast(
                frame, map_id, floor, route,
            )
            if fast is not None and fast.get("success"):
                return fast

            # A newly explored game map may contain only one small connected
            # island.  Its footprint is more informative (and faster) than SIFT.
            partial_started = time.perf_counter()
            partial = self._align_partial_structure(
                frame, map_id, floor, route, accepted_prior=accepted_prior,
                cancelled=cancelled,
                math_pool=math_pool,
            )
            stages = {"partialSearch": (time.perf_counter() - partial_started) * 1000.0}
            if partial is not None and partial.get("reason") == "alignment-cancelled":
                return partial
            if partial is not None and partial.get("success"):
                return {**partial, "solverStageTimingsMs": {**stages, "siftFallback": 0.0}}
            if shared_mode:
                return {
                    **(partial if isinstance(partial, dict) else {}),
                    "success": False,
                    "reason": "shared-structure-alignment-failed",
                    "alignmentMode": "shared-structure",
                    "plausibleGeometry": False,
                    "solverStageTimingsMs": {**stages, "siftFallback": 0.0},
                }
            partial_diagnostic = None
            if (
                isinstance(partial, dict)
                and partial.get("reason") == "partial-structure-ambiguous"
                and partial.get("candidateDiagnostics")
            ):
                # Keep the conservative partial result attached to a later
                # full-floor failure.  The full SIFT fallback can be a
                # materially worse transform. Keep the structural failure
                # available for diagnosis without making a self-reference.
                partial_diagnostic = dict(partial)
            if cancelled is not None and cancelled():
                return {
                    "success": False,
                    "reason": "alignment-cancelled",
                    "alignmentMode": "cancelled",
                    "plausibleGeometry": False,
                }
            # Identity is already locked for this round. Loading SIFT features
            # for every map on the floor added ~4.6 seconds to a failed F1 fit
            # and could not improve this confirmed-map alignment.
            fallback_started = time.perf_counter()
            query_crop, (crop_x, crop_y) = nightmare_map_crop(frame)
            query_futures = None
            if math_pool is not None:
                pool, _lanes = math_pool
                def build_query_features(structural):
                    # Each task owns its extractor/CLAHE buffers. The request
                    # owner alone publishes reference caches and matches rows.
                    image = registration_structure_view(
                        query_crop, annotated_reference=False,
                    ) if structural else query_crop
                    extractor = cv2.SIFT_create(nfeatures=900, contrastThreshold=0.025)
                    return self._features_with_extractor(image, extractor)
                query_futures = [pool.submit(build_query_features, structural)
                                 for structural in (False, True)]
            self.preload_selected(map_id, floor)
            reference = self._references[(map_id, floor)]
            structure_reference = self._structure_reference_for(
                map_id, floor, self._active_structure_profile_id(),
            )
            if structure_reference is None:
                structure_reference = self._structure_references[(map_id, floor)]
            if query_futures is not None:
                query, structure_query = [future.result() for future in query_futures]
            else:
                query = self._features(query_crop)
                structure_query = self._features(
                    registration_structure_view(query_crop, annotated_reference=False)
                )
            attempts = [
                ("appearance", reference, query, self._match(reference, query)),
                (
                    "structure-blend", structure_reference, structure_query,
                    self._match(structure_reference, structure_query),
                ),
            ]
            evaluated = [
                self._evaluate_alignment_fit(
                    fit, source_features, query_features, route.shape, frame.shape,
                    crop_x, crop_y, mode,
                )
                for mode, source_features, query_features, fit in attempts
            ]
            if partial is not None:
                evaluated.append(partial)
            stages["siftFallback"] = (time.perf_counter() - fallback_started) * 1000.0
            successful = [row for row in evaluated if row["success"]]
            if successful:
                best = max(
                    successful,
                    key=lambda row: (row["inliers"], row["inlierRatio"], row["score"]),
                )
                return {**best, "solverStageTimingsMs": stages}
            best = max(
                evaluated,
                key=lambda row: (
                    row.get("plausibleGeometry", False), row.get("inliers", 0),
                    row.get("inlierRatio", 0.0), row.get("score", -99.0),
                ),
            )
            best["attempts"] = [
                {
                    "mode": row.get("alignmentMode"),
                    "reason": row.get("reason"),
                    "inliers": row.get("inliers", 0),
                    "inlierRatio": row.get("inlierRatio", 0.0),
                    "scale": row.get("scale"),
                    "screenOverlap": row.get("screenOverlap"),
                }
                for row in evaluated
            ]
            if partial_diagnostic is not None:
                best["partialStructureDiagnostic"] = partial_diagnostic
            best["solverStageTimingsMs"] = stages
            return best

    def align_best_partial(self, frame: np.ndarray, floor: str) -> dict:
        """Advisory all-map structural search for a confirmed-map conflict."""
        if floor not in {"1", "2", "B1"}:
            return {"success": False, "reason": "invalid-floor"}
        rows = []
        with self._lock:
            visible = visible_map_component(frame)
            live_icon = None
            live_direction = {
                "direction": None, "reliable": False, "marginPixels": 0.0,
            }
            # The author-provided side-door crops describe the second floor.
            # Do not project that semantic anchor onto B1/F1 where it has no
            # defined meaning; those floors keep the existing structural fit.
            if floor == "2" and visible is not None:
                _patch, live_bounds, _component = visible
                live_icon = self._match_exit_icon(
                    frame, tuple(live_bounds), np.arange(0.45, 1.161, 0.05),
                )
                local_rectangle = local_side_door_rectangle(
                    frame, tuple(live_bounds), live_icon,
                )
                live_direction = live_side_door_direction(
                    tuple(live_bounds), live_icon, local_rectangle,
                )
            live_icon_ok = bool(
                live_icon
                and float(live_icon.get("score", 0.0)) >= 0.72
                and float(live_icon.get("distinctMargin", 0.0)) >= 0.12
            )
            for item in self.pack.maps:
                if self._shared_structure_enabled():
                    # Cross-map shared mode is allowed to inspect only the
                    # typed structure store.  The author sheet is display
                    # data and must not become an implicit recognition
                    # reference while an item is being fit.
                    route = None
                    if self._shared_structure_product(item.id, floor) is None:
                        continue
                else:
                    route = imread_unicode(item.layers[floor])
                    if route is None:
                        continue
                # Cross-map scanning stays on the cheap, conservative local
                # reference. Wide expansion is reserved for the human-confirmed
                # map so one generic structure cannot dominate every identity.
                fit = self._align_partial_structure(
                    frame, item.id, floor, route, allow_expansion=False,
                )
                if fit is None:
                    continue
                source_key = (item.id, floor)
                if (
                    floor == "2"
                    and not self._shared_structure_enabled()
                    and source_key not in self._source_exit_icon_cache
                ):
                    self._source_exit_icon_cache[source_key] = self._match_exit_icon(
                        route, tuple(item.side_door_crop),
                        np.arange(0.30, 2.051, 0.05),
                    )
                source_icon = (
                    None if self._shared_structure_enabled()
                    else self._source_exit_icon_cache.get(source_key)
                )
                source_icon_ok = bool(
                    source_icon
                    and float(source_icon.get("score", 0.0)) >= 0.72
                    and float(source_icon.get("distinctMargin", 0.0)) >= 0.10
                )
                exit_center_error = 999.0
                matrix = np.asarray(fit.get("matrixSourceToClient"), dtype=np.float64)
                if (
                    live_icon_ok and source_icon_ok
                    and matrix.shape == (3, 3) and np.isfinite(matrix).all()
                ):
                    projected = matrix @ np.asarray([
                        float(source_icon["center"][0]),
                        float(source_icon["center"][1]), 1.0,
                    ])
                    exit_center_error = float(np.linalg.norm(
                        projected[:2] - np.asarray(live_icon["center"], dtype=np.float64)
                    ))
                exit_center_verified = bool(exit_center_error <= 14.0)
                structural_identity = float(fit.get("templateScore", 0.0)) + 0.55 * float(
                    fit.get("distinctMargin", 0.0)
                )
                identity_score = structural_identity + (
                    1.0 + 0.25 * max(0.0, 1.0 - exit_center_error / 14.0)
                    if exit_center_verified else 0.0
                )
                rows.append({
                    **fit, "mapId": item.id, "mapName": item.name,
                    "direction": item.direction,
                    "sideDoorIdentityScore": identity_score,
                    "sideDoorStructuralScore": structural_identity,
                    "sideDoorExitVerified": exit_center_verified,
                    "sideDoorExitCenterError": exit_center_error,
                })
        if not rows:
            return {"success": False, "reason": "no-partial-candidates"}
        rows.sort(
            key=lambda row: (
                row.get("sideDoorIdentityScore", 0.0),
                row.get("templateScore", 0.0), row.get("distinctMargin", 0.0),
            ),
            reverse=True,
        )
        best = dict(rows[0])
        second_score = float(rows[1].get("sideDoorIdentityScore", 0.0)) if len(rows) > 1 else 0.0
        best["crossMapMargin"] = float(best.get("sideDoorIdentityScore", 0.0) - second_score)
        best["crossMapTop"] = [
            {
                "mapId": row["mapId"], "mapName": row["mapName"],
                "direction": row["direction"],
                "score": row.get("templateScore"),
                "distinctMargin": row.get("distinctMargin"),
                "sideDoorIdentityScore": row.get("sideDoorIdentityScore"),
                "sideDoorExitVerified": row.get("sideDoorExitVerified"),
                "sideDoorExitCenterError": row.get("sideDoorExitCenterError"),
            }
            for row in rows[:3]
        ]
        direction_rows = []
        for direction in ("上", "下", "右"):
            group = [row for row in rows if row["direction"] == direction]
            if group:
                direction_rows.append({
                    "direction": direction,
                    "score": float(group[0]["sideDoorIdentityScore"]),
                    "mapId": group[0]["mapId"],
                })
        direction_rows.sort(key=lambda row: row["score"], reverse=True)
        direction_margin = float(
            direction_rows[0]["score"] - direction_rows[1]["score"]
        ) if len(direction_rows) > 1 else 0.0
        structure_direction = direction_rows[0]["direction"] if direction_rows else None
        best["direction"] = (
            live_direction.get("direction")
            if live_direction.get("reliable") else structure_direction
        )
        best["directionScores"] = direction_rows
        best["directionMargin"] = (
            float(live_direction.get("marginPixels", 0.0))
            if live_direction.get("reliable") else direction_margin
        )
        best["directionReliable"] = bool(
            live_direction.get("reliable")
            or (
                direction_rows
                and best.get("sideDoorExitVerified")
                and direction_margin >= 0.025
            )
        )
        best["directionEvidence"] = (
            "map-rectangle+door-endpoint"
            if best["directionReliable"] and live_direction.get("reliable") else
            "exit-center+side-door-structure"
            if best["directionReliable"] else
            "insufficient-side-door-evidence"
        )
        best["liveSideDoorDirection"] = live_direction
        best["crossMapRows"] = [
            {
                "mapId": row["mapId"], "mapName": row["mapName"],
                "direction": row["direction"],
                "templateScore": float(row.get("templateScore", 0.0)),
                "distinctMargin": float(row.get("distinctMargin", 0.0)),
                "sideDoorIdentityScore": float(row.get("sideDoorIdentityScore", 0.0)),
                "sideDoorExitVerified": bool(row.get("sideDoorExitVerified")),
                "sideDoorExitCenterError": float(row.get("sideDoorExitCenterError", 999.0)),
            }
            for row in rows
        ]
        best["success"] = bool(
            best.get("success") and best["crossMapMargin"] >= 0.02
        )
        if not best["success"]:
            best["reason"] = "cross-map-ambiguous"
        return best

    def _align_partial_structure(
        self, frame: np.ndarray, map_id: str, floor: str, route: np.ndarray | None,
        *, allow_expansion: bool = True, accepted_prior: dict | None = None,
        cancelled: Callable[[], bool] | None = None,
        math_pool=None,
    ) -> dict | None:
        """Fit one explored map island against the confirmed clean route sheet."""
        def was_cancelled() -> bool:
            return bool(cancelled is not None and cancelled())

        def cancelled_result() -> dict:
            return {
                "success": False,
                "reason": "alignment-cancelled",
                "alignmentMode": "cancelled",
                "plausibleGeometry": False,
            }

        if was_cancelled():
            return cancelled_result()
        visible = visible_map_component(frame)
        if visible is None:
            return None
        patch, bounds, component = visible
        query = map_color_mask(patch, live=True)
        query_pixels = int(query.sum())
        if query_pixels < 3000:
            return {
                "success": False, "reason": "partial-map-too-small",
                "alignmentMode": "partial-structure-dice", "plausibleGeometry": False,
                "templateScore": 0.0, "distinctMargin": 0.0,
                "visibleComponent": component,
            }
        key = (map_id, floor)
        cached_reference = self._partial_reference_masks.get(key)
        if cached_reference is None:
            shared_product = self._shared_structure_product(map_id, floor) if self._shared_structure_enabled() else None
            if self._shared_structure_enabled() and shared_product is None:
                return None
            if shared_product is not None:
                full_reference = shared_product.clean_mask
                full_valid = shared_product.validation_mask
            else:
                full_reference = map_color_mask(route, live=False)
                hsv = cv2.cvtColor(route, cv2.COLOR_BGR2HSV)
                _hue, saturation, value = cv2.split(hsv)
                annotation = ((saturation >= 82) & (value >= 105)) | (value >= 218)
                annotation = cv2.dilate(
                    annotation.astype(np.uint8), np.ones((5, 5), np.uint8),
                )
                full_valid = (annotation == 0).astype(np.uint8)
            self._partial_reference_masks[key] = (
                full_reference, full_valid, (0, 0),
            )
        else:
            full_reference, full_valid, _reference_offset = cached_reference

        raw_sx0, raw_sy0, raw_sx1, raw_sy1 = self.pack.by_id[map_id].side_door_crop
        reference_height, reference_width = full_reference.shape[:2]
        sx0 = max(0, min(reference_width, raw_sx0))
        sy0 = max(0, min(reference_height, raw_sy0))
        sx1 = max(0, min(reference_width, raw_sx1))
        sy1 = max(0, min(reference_height, raw_sy1))
        search_crop_fallback = False
        if sx1 - sx0 < 32 or sy1 - sy0 < 32:
            # sideDoorCrop is authored once per map, while several basement
            # sheets use a smaller canvas than floor 2. Never pass an empty
            # inherited crop to OpenCV. Seed the search from the largest actual
            # architecture component on this floor; the confirmed-map search
            # can still expand to the complete sheet below.
            count, _labels, stats, _centers = cv2.connectedComponentsWithStats(
                full_reference.astype(np.uint8), 8,
            )
            viable = [
                index for index in range(1, count)
                if int(stats[index, cv2.CC_STAT_AREA]) >= 300
            ]
            if viable:
                index = max(
                    viable, key=lambda value: int(stats[value, cv2.CC_STAT_AREA]),
                )
                x, y, width, height = (
                    int(value) for value in stats[index, :4]
                )
                sx0, sy0, sx1, sy1 = x, y, x + width, y + height
            else:
                sx0, sy0, sx1, sy1 = 0, 0, reference_width, reference_height
            search_crop_fallback = True
        crop_width, crop_height = sx1 - sx0, sy1 - sy0
        padding = max(24, round(max(crop_width, crop_height) * 0.12))
        rx0, ry0 = max(0, sx0 - padding), max(0, sy0 - padding)
        rx1 = min(full_reference.shape[1], sx1 + padding)
        ry1 = min(full_reference.shape[0], sy1 + padding)
        reference = full_reference[ry0:ry1, rx0:rx1]
        reference_valid = full_valid[ry0:ry1, rx0:rx1]
        reference_offset = (rx0, ry0)

        initial_full_floor_fallback = False
        cold_full_floor = bool(allow_expansion and accepted_prior is None)
        search_resolution = 0.25 if cold_full_floor else 0.50
        if cold_full_floor:
            # A cold fit must search the complete confirmed floor anyway.
            # Starting at the F2 door crop and expanding repeats this search
            # and can seed an artificial scale on F1/B1.
            reference = full_reference
            reference_valid = full_valid
            reference_offset = (0, 0)
            initial_full_floor_fallback = True

        qh, qw = query.shape

        correlation_local = threading.local()
        candidate_rows_cache = {}
        candidate_cache_sources = {}

        def collect_scales(calculate, scales):
            if math_pool is None or not cold_full_floor:
                return [row for scale in scales for row in calculate(scale)]
            pool, lanes = math_pool
            ordered = [None] * len(scales)
            pending = {}
            next_index = 0
            while pending or next_index < len(scales):
                if was_cancelled():
                    for future in pending:
                        future.cancel()
                    return []
                # Refill a freed lane without waiting for its slower batch
                # peers. Keep the same bounded future count and scale-order
                # merge, so every competitor and tie ordering are unchanged.
                while len(pending) < lanes and next_index < len(scales):
                    pending[pool.submit(calculate, scales[next_index])] = next_index
                    next_index += 1
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    ordered[pending.pop(future)] = future.result()
            return [row for rows in ordered for row in rows]

        def compute_candidates_for(
            search_reference: np.ndarray,
            search_valid: np.ndarray,
            search_offset: tuple[int, int],
            scales: list[float],
            downsample: float,
        ) -> list[dict]:
            if downsample == 1.0 and cold_full_floor:
                # Search every location at quarter resolution, then refine each
                # spatial peak in full pixels. Matching the full-size query
                # over an entire floor at every fine scale dominates cold fit.
                # Keep all three spatial competitors at every scale.
                seeds = candidates_for(
                    search_reference, search_valid, search_offset, scales, search_resolution,
                )
                q = query.astype(np.float32)
                positive = float(q.sum())
                seeds_by_scale = defaultdict(list)
                for seed in seeds:
                    seeds_by_scale[seed["scale"]].append(seed)
                def full_scale(scale):
                    rows = []
                    if was_cancelled():
                        return []
                    if not hasattr(correlation_local, "value"):
                        correlation_local.value = _BinaryMaskCorrelation(query)
                    full_correlation = correlation_local.value
                    size = (round(search_reference.shape[1] * scale),
                            round(search_reference.shape[0] * scale))
                    if size[0] < qw or size[1] < qh:
                        return []
                    resized = cv2.resize(search_reference, size, interpolation=cv2.INTER_NEAREST)
                    valid = cv2.resize(search_valid, size, interpolation=cv2.INTER_NEAREST)
                    for seed in seeds_by_scale[scale]:
                        px = round((seed["sourceLocation"][0] - search_offset[0]) * scale)
                        py = round((seed["sourceLocation"][1] - search_offset[1]) * scale)
                        # A small full-pixel neighborhood absorbs thick-wall
                        # aliasing; global competitors still come from seeds.
                        x0, y0 = max(0, px - 16), max(0, py - 16)
                        x1, y1 = min(size[0], px + qw + 17), min(size[1], py + qh + 17)
                        crop = resized[y0:y1, x0:x1]
                        if crop.shape[0] < qh or crop.shape[1] < qw:
                            continue
                        valid_crop = valid[y0:y1, x0:x1]
                        intersection, query_valid = full_correlation.correlate(
                            crop, valid_crop,
                        )
                        count = _binary_window_sums(crop, qh, qw)
                        dice = np.minimum(2.0 * intersection / np.maximum(query_valid + count, 1.0), 1.0)
                        dice[query_valid / max(positive, 1.0) < 0.45] = -1.0
                        _, score, _, location = cv2.minMaxLoc(dice)
                        source_location = [
                            (x0 + location[0]) / scale + search_offset[0],
                            (y0 + location[1]) / scale + search_offset[1],
                        ]
                        rows.append({
                            "score": float(score), "scale": float(scale),
                            "rankAtScale": seed["rankAtScale"],
                            "sourceLocation": source_location,
                            "sourceScaledLocation": [round(value * scale) for value in source_location],
                        })
                    return rows
                return collect_scales(full_scale, scales)
            q = query
            if downsample != 1.0:
                q = cv2.resize(
                    q, (max(1, round(qw * downsample)), max(1, round(qh * downsample))),
                    interpolation=cv2.INTER_NEAREST,
                )
            q = q.astype(np.uint8 if cold_full_floor else np.float32)
            positive = float(q.sum())
            def coarse_scale(scale):
                rows = []
                # OpenCV template matching itself is not interruptible, but a
                # stale map/floor request can stop before the next scale. This
                # prevents rapid close/reopen or floor switching from building
                # a multi-second FIFO queue behind obsolete full-floor scans.
                if was_cancelled():
                    return []
                effective = scale * downsample
                resized = cv2.resize(
                    search_reference,
                    (round(search_reference.shape[1] * effective), round(search_reference.shape[0] * effective)),
                    interpolation=cv2.INTER_NEAREST,
                ).astype(np.uint8 if cold_full_floor else np.float32)
                resized_valid = cv2.resize(
                    search_valid,
                    (resized.shape[1], resized.shape[0]),
                    interpolation=cv2.INTER_NEAREST,
                ).astype(np.uint8 if cold_full_floor else np.float32)
                if resized.shape[0] < q.shape[0] or resized.shape[1] < q.shape[1]:
                    return []
                intersection = cv2.matchTemplate(resized, q, cv2.TM_CCORR)
                integral = cv2.integral(resized)
                rh, rw = q.shape
                reference_count = (
                    integral[rh:, rw:] - integral[:-rh, rw:]
                    - integral[rh:, :-rw] + integral[:-rh, :-rw]
                )
                query_valid = cv2.matchTemplate(resized_valid, q, cv2.TM_CCORR)
                compared_fraction = query_valid / max(positive, 1.0)
                dice = (2.0 * intersection) / np.maximum(query_valid + reference_count, 1.0)
                dice = np.minimum(dice, 1.0)
                dice[compared_fraction < 0.45] = -1.0
                remaining = dice.copy()
                for rank_at_scale in range(3):
                    _minimum, maximum, _min_location, location = cv2.minMaxLoc(remaining)
                    source_location = [
                        float(location[0] / effective + search_offset[0]),
                        float(location[1] / effective + search_offset[1]),
                    ]
                    rows.append({
                        "score": float(maximum), "scale": float(scale),
                        "rankAtScale": rank_at_scale + 1,
                        "sourceLocation": source_location,
                        "sourceScaledLocation": [
                            int(round(source_location[0] * scale)),
                            int(round(source_location[1] * scale)),
                        ],
                    })
                    suppress_x = max(8, q.shape[1] // 2)
                    suppress_y = max(8, q.shape[0] // 2)
                    x0 = max(0, location[0] - suppress_x)
                    x1 = min(remaining.shape[1], location[0] + suppress_x + 1)
                    y0 = max(0, location[1] - suppress_y)
                    y1 = min(remaining.shape[0], location[1] + suppress_y + 1)
                    remaining[y0:y1, x0:x1] = -1.0
                return rows
            return collect_scales(coarse_scale, scales)

        def candidates_for(search_reference, search_valid, search_offset, scales, downsample):
            # Coarse/fine bands overlap, and full-pixel refinement asks again
            # for its coarse seeds. Reuse those exact rows within this fit.
            prefix = (id(search_reference), id(search_valid), search_offset, downsample)
            # Keep temporary expanded crops alive while their identity is a
            # cache key; another crop must not reuse a released array's id.
            candidate_cache_sources[prefix[:2]] = (search_reference, search_valid)
            missing = sorted({scale for scale in scales if (*prefix, scale) not in candidate_rows_cache})
            if missing:
                computed = compute_candidates_for(
                    search_reference, search_valid, search_offset, missing, downsample,
                )
                if was_cancelled():
                    return []
                for scale in missing:
                    candidate_rows_cache[(*prefix, scale)] = []
                for row in computed:
                    candidate_rows_cache[(*prefix, row["scale"])].append(row)
            return [row for scale in scales for row in candidate_rows_cache[(*prefix, scale)]]

        # The supplied sheets range from ~600 px to 4096 px for the same game
        # canvas.  Search by expected projected size instead of raw file scale.
        reference_edge = float(max(full_reference.shape[:2]))
        nominal_scale = 1200.0 / max(reference_edge, 1.0)
        minimum_scale = max(0.05, nominal_scale * 0.55)
        maximum_scale = min(6.0, nominal_scale * 1.65)
        prior_scale = None
        if accepted_prior:
            prior_matrix = np.asarray(
                accepted_prior.get("matrixSourceToClient"), dtype=np.float64,
            )
            if prior_matrix.shape == (3, 3) and np.isfinite(prior_matrix).all():
                candidate_prior_scale = float(
                    np.sqrt(abs(np.linalg.det(prior_matrix[:2, :2])))
                )
                if minimum_scale <= candidate_prior_scale <= maximum_scale:
                    prior_scale = candidate_prior_scale
        coarse_step = max(0.008, (maximum_scale - minimum_scale) / 26.0)
        coarse = candidates_for(
            reference, reference_valid, reference_offset,
            [round(value, 4) for value in np.arange(
                minimum_scale, maximum_scale + coarse_step * 0.5, coarse_step,
            )],
            search_resolution,
        )
        if was_cancelled():
            return cancelled_result()
        if not coarse and allow_expansion:
            # A clipped but technically non-empty inherited side-door crop can
            # still be smaller than the current explored island at every legal
            # scale. Treat that exactly like an out-of-bounds crop and begin on
            # the current floor's complete sheet instead of returning None (the
            # old behavior silently dropped several B1 first-fit attempts).
            search_crop_fallback = True
            initial_full_floor_fallback = True
            reference = full_reference
            reference_valid = full_valid
            reference_offset = (0, 0)
            coarse = candidates_for(
                reference, reference_valid, reference_offset,
                [round(value, 4) for value in np.arange(
                    minimum_scale,
                    maximum_scale + coarse_step * 0.5,
                    coarse_step,
                )],
                search_resolution,
            )
            if was_cancelled():
                return cancelled_result()
        if not coarse:
            return None
        coarse.sort(key=lambda row: row["score"], reverse=True)
        center_scale = coarse[0]["scale"]
        fine_step = max(0.002, coarse_step / 8.0)
        fine_radius = coarse_step * 1.5
        fine = candidates_for(
            reference, reference_valid, reference_offset,
            [round(value, 4) for value in np.arange(
                max(minimum_scale, center_scale - fine_radius),
                min(maximum_scale, center_scale + fine_radius) + fine_step * 0.5,
                fine_step,
            )],
            1.0,
        )
        if was_cancelled():
            return cancelled_result()
        # Reduced-resolution scores seed refinement only; aliasing must not
        # outrank actual full-pixel evidence. Refit every coarse-scale spatial
        # competitor too, including scales outside the best fine band.
        if cold_full_floor:
            coarse_full = candidates_for(
                reference, reference_valid, reference_offset,
                sorted({row["scale"] for row in coarse}), 1.0,
            )
            # A distant scale mode needs the same fine resolution as the
            # winner; a coarse lattice alone can understate its competition.
            scale_scores = {}
            for row in coarse_full:
                scale_scores[row["scale"]] = max(
                    scale_scores.get(row["scale"], -1.0), row["score"],
                )
            ordered_scales = sorted(scale_scores)
            refined_scales = {row["scale"] for row in fine}
            competing_scales = set()
            for index, scale in enumerate(ordered_scales):
                score = scale_scores[scale]
                if index and score < scale_scores[ordered_scales[index - 1]]:
                    continue
                if index + 1 < len(ordered_scales) and score < scale_scores[ordered_scales[index + 1]]:
                    continue
                competing_scales.update(round(value, 4) for value in np.arange(
                    max(minimum_scale, scale - fine_radius),
                    min(maximum_scale, scale + fine_radius) + fine_step * 0.5,
                    fine_step,
                ))
            fine.extend(candidates_for(
                reference, reference_valid, reference_offset,
                sorted(competing_scales - refined_scales), 1.0,
            ))
            candidates = coarse_full + fine
        else:
            candidates = coarse + fine
        if prior_scale is not None:
            prior_step = max(0.0015, coarse_step / 12.0)
            prior_radius = max(0.035, prior_scale * 0.045)
            candidates.extend(candidates_for(
                reference, reference_valid, reference_offset,
                [round(value, 4) for value in np.arange(
                    max(minimum_scale, prior_scale - prior_radius),
                    min(maximum_scale, prior_scale + prior_radius) + prior_step * 0.5,
                    prior_step,
                )],
                1.0,
            ))
            if was_cancelled():
                return cancelled_result()
        candidates.sort(key=lambda row: row["score"], reverse=True)

        def summarize(rows: list[dict]) -> tuple[dict, float, float, float, float]:
            rows.sort(key=lambda row: row["score"], reverse=True)
            winner = rows[0]
            winner_source = np.asarray(winner["sourceLocation"], dtype=np.float64)
            winner_scale = max(float(winner["scale"]), 0.001)
            other = next((
                row for row in rows[1:]
                if np.linalg.norm(
                    np.asarray(row["sourceLocation"]) - winner_source,
                ) >= 35.0
            ), None)
            transform_other = next((
                row for row in rows[1:]
                if (
                    np.linalg.norm(
                        np.asarray(row["sourceLocation"]) - winner_source,
                    ) >= 35.0
                    or abs(float(row["scale"]) - winner_scale) / winner_scale >= 0.04
                )
            ), None)
            other_score = float((other or {}).get("score", 0.0))
            transform_other_score = float(
                (transform_other or {}).get("score", 0.0)
            )
            return (
                winner,
                other_score,
                float(winner["score"] - other_score),
                transform_other_score,
                float(winner["score"] - transform_other_score),
            )

        def touches_search_edge(row: dict, search_bounds: tuple[int, int, int, int]) -> bool:
            bx0, by0, bx1, by1 = search_bounds
            candidate_scale = max(float(row["scale"]), 0.001)
            source_width = qw / candidate_scale
            source_height = qh / candidate_scale
            source_x, source_y = (float(value) for value in row["sourceLocation"])
            guard = max(24.0, min(source_width, source_height) * 0.08)
            return bool(
                source_x <= bx0 + guard
                or source_y <= by0 + guard
                or source_x + source_width >= bx1 - guard
                or source_y + source_height >= by1 - guard
            )

        def targeted_search(
            search_bounds: tuple[int, int, int, int],
            seed_scale: float,
        ) -> list[dict]:
            bx0, by0, bx1, by1 = search_bounds
            search_reference = full_reference[by0:by1, bx0:bx1]
            search_valid = full_valid[by0:by1, bx0:bx1]
            radius = max(0.06, coarse_step * 2.5)
            targeted_step = max(0.006, coarse_step / 2.0)
            targeted_coarse = candidates_for(
                search_reference, search_valid, (bx0, by0),
                [round(value, 4) for value in np.arange(
                    max(minimum_scale, seed_scale - radius),
                    min(maximum_scale, seed_scale + radius) + targeted_step * 0.5,
                    targeted_step,
                )],
                search_resolution,
            )
            if not targeted_coarse:
                return []
            targeted_coarse.sort(key=lambda row: row["score"], reverse=True)
            refined_scale = float(targeted_coarse[0]["scale"])
            targeted_fine = candidates_for(
                search_reference, search_valid, (bx0, by0),
                [round(value, 4) for value in np.arange(
                    max(minimum_scale, refined_scale - coarse_step * 1.25),
                    min(maximum_scale, refined_scale + coarse_step * 1.25)
                    + fine_step * 0.5,
                    fine_step,
                )],
                1.0,
            )
            return targeted_coarse + targeted_fine

        def broad_then_targeted_search(
            search_bounds: tuple[int, int, int, int],
        ) -> list[dict]:
            """Re-scan scale after enlarging the spatial search window.

            A partial island can be taller than the original side-door crop.
            In that case the local search is forced to use an artificially huge
            scale merely to make the template fit. Reusing that scale as the
            only expansion seed misses the real placement entirely (the live
            map-01 floor-1 regression was 2.92x locally versus 1.53x globally).
            """
            bx0, by0, bx1, by1 = search_bounds
            search_reference = full_reference[by0:by1, bx0:bx1]
            search_valid = full_valid[by0:by1, bx0:bx1]
            broad = candidates_for(
                search_reference, search_valid, (bx0, by0),
                [round(value, 4) for value in np.arange(
                    minimum_scale, maximum_scale + coarse_step * 0.5, coarse_step,
                )],
                search_resolution,
            )
            if not broad:
                return []
            broad.sort(key=lambda row: row["score"], reverse=True)
            return broad + targeted_search(search_bounds, float(broad[0]["scale"]))

        (
            best, distinct_score, margin,
            transform_distinct_score, transform_margin,
        ) = summarize(candidates)
        local_bounds = (
            (0, 0, full_reference.shape[1], full_reference.shape[0])
            if initial_full_floor_fallback else
            (rx0, ry0, rx1, ry1)
        )
        search_bounds = local_bounds
        search_mode = (
            "full-floor-first-fit" if cold_full_floor else
            "full-floor-crop-fallback"
            if initial_full_floor_fallback else "side-door-local"
        )
        boundary_expanded = initial_full_floor_fallback
        full_bounds = (0, 0, full_reference.shape[1], full_reference.shape[0])
        if (
            allow_expansion
            and prior_scale is not None
            and search_bounds != full_bounds
        ):
            # An accepted transform supplies a scale prior, never an absolute
            # position. Re-search that narrow scale band across the current
            # floor so a dragged map can relocate without being trapped inside
            # the second-floor side-door crop inherited by F1/B1.
            prior_rows = targeted_search(full_bounds, prior_scale)
            if was_cancelled():
                return cancelled_result()
            if prior_rows:
                candidates.extend(prior_rows)
                (
                    best, distinct_score, margin,
                    transform_distinct_score, transform_margin,
                ) = summarize(candidates)
                search_bounds = full_bounds
                search_mode = "accepted-prior-full-floor"
                boundary_expanded = True
        if (
            allow_expansion
            and not initial_full_floor_fallback
            and
            best["score"] >= 0.55
            and (
                (
                    best["score"] < 0.82
                    and margin >= 0.02
                    and touches_search_edge(best, local_bounds)
                )
                or search_crop_fallback
                or margin < 0.04
            )
        ):
            boundary_expanded = True
            seed_scale = max(float(best["scale"]), 0.001)
            source_query_width = qw / seed_scale
            source_query_height = qh / seed_scale
            expand_x = max(padding * 3, round(source_query_width * 1.25), crop_width // 2)
            expand_y = max(padding * 3, round(source_query_height * 1.25), crop_height // 2)
            expanded_bounds = (
                max(0, sx0 - expand_x), max(0, sy0 - expand_y),
                min(full_reference.shape[1], sx1 + expand_x),
                min(full_reference.shape[0], sy1 + expand_y),
            )
            expanded = broad_then_targeted_search(expanded_bounds)
            if was_cancelled():
                return cancelled_result()
            if expanded:
                candidates.extend(expanded)
                (
                    best, distinct_score, margin,
                    transform_distinct_score, transform_margin,
                ) = summarize(candidates)
                search_bounds = expanded_bounds
                search_mode = "side-door-expanded"
                needs_full_floor = bool(
                    search_crop_fallback
                    or margin < 0.04
                    or (
                        best["score"] < 0.82
                        and best["score"] >= 0.55
                        and margin >= 0.02
                        and touches_search_edge(best, expanded_bounds)
                    )
                )
                if (
                    needs_full_floor
                    and expanded_bounds
                    != (0, 0, full_reference.shape[1], full_reference.shape[0])
                ):
                    full_rows = broad_then_targeted_search(full_bounds)
                    if was_cancelled():
                        return cancelled_result()
                    if full_rows:
                        candidates.extend(full_rows)
                        (
                            best, distinct_score, margin,
                            transform_distinct_score, transform_margin,
                        ) = summarize(candidates)
                        search_bounds = full_bounds
                        search_mode = "full-floor-expanded"

        # A map/floor with no accepted transform is, by definition, an unseen
        # first fit. The side-door crop is only a speed seed and cannot be the
        # spatial boundary: the player may first open the map after walking to
        # any explored room. Search the complete confirmed floor once, while
        # the independent floating sheet remains immediately usable. Session
        # reuse and accepted priors keep later opens on the fast path.
        first_fit_full_floor = bool(
            allow_expansion and accepted_prior is None
        )
        if first_fit_full_floor and search_bounds != full_bounds:
            full_rows = broad_then_targeted_search(full_bounds)
            if was_cancelled():
                return cancelled_result()
            if full_rows:
                candidates.extend(full_rows)
                (
                    best, distinct_score, margin,
                    transform_distinct_score, transform_margin,
                ) = summarize(candidates)
                search_bounds = full_bounds
                search_mode = "full-floor-first-fit"
                boundary_expanded = True

        # Repeated L-shaped corridors can win a pure structure search while
        # placing the sheet tens of pixels away from the actual side exit. For
        # a human-confirmed map, use the fixed exit icon as a semantic anchor,
        # then refine only the scale against the live wall mask. Cross-map
        # advisory scans keep allow_expansion=False and never use this path.
        structure_best = dict(best)
        exit_anchor = None
        exit_anchor_conflict = None
        structure_icon_consistency = None
        # sideDoorCrop and its exit-icon semantics are authored for floor 2.
        # F1/B1 may use the crop as a structural seed, but must not be vetoed
        # by a floor-2-only semantic exit anchor.
        if allow_expansion and floor == "2":
            source_icon = self._match_exit_icon(
                route, (sx0, sy0, sx1, sy1), np.arange(0.30, 2.051, 0.05),
            )
            live_icon = self._match_exit_icon(
                frame,
                self._live_exit_search_bounds(frame.shape, tuple(bounds)),
                np.arange(0.45, 1.161, 0.05),
            )
            source_icon_ok = bool(
                source_icon
                # The human-confirmed map plus its author-supplied side-door
                # crop already constrains identity and location. Route ink can
                # cover the icon (notably map 09), so uniqueness inside that
                # small crop is not required to match a full-screen threshold.
                and source_icon["score"] >= 0.62
            )
            live_icon_ok = bool(
                live_icon
                and live_icon["score"] >= 0.78
                and live_icon["distinctMargin"] >= 0.10
            )
            if source_icon_ok and live_icon_ok:
                icon_scale = float(live_icon["scale"]) / max(float(source_icon["scale"]), 0.001)
                icon_scale_valid = minimum_scale <= icon_scale <= maximum_scale
                structure_scale = float(structure_best["scale"])
                structure_scale_agreement = (
                    abs(structure_scale - icon_scale) / max(icon_scale, 0.001)
                    if icon_scale_valid else 999.0
                )
                structure_source_scaled = np.asarray(
                    structure_best["sourceScaledLocation"], dtype=np.float64,
                )
                projected_structure_icon = (
                    np.asarray(bounds[:2], dtype=np.float64)
                    + np.asarray(source_icon["center"], dtype=np.float64) * structure_scale
                    - structure_source_scaled
                )
                structure_icon_error = float(np.linalg.norm(
                    projected_structure_icon
                    - np.asarray(live_icon["center"], dtype=np.float64)
                ))
                structure_icon_error_limit = float(
                    max(8.0, min(14.0, max(qw, qh) * 0.04))
                )
                structure_icon_geometry_verified = bool(
                    icon_scale_valid
                    and structure_best["score"] >= 0.90
                    and structure_scale_agreement <= 0.04
                    and structure_icon_error <= structure_icon_error_limit
                    and query_pixels >= 20000
                )
                structure_icon_consistency = {
                    "verified": structure_icon_geometry_verified,
                    "structureScore": float(structure_best["score"]),
                    "structureScale": structure_scale,
                    "iconScale": float(icon_scale),
                    "scaleAgreement": float(structure_scale_agreement),
                    "projectedCenter": projected_structure_icon.tolist(),
                    "liveCenter": list(live_icon["center"]),
                    "centerErrorPixels": structure_icon_error,
                    "centerErrorLimitPixels": structure_icon_error_limit,
                }
                seed_scale = float(
                    prior_scale
                    if prior_scale is not None else
                    icon_scale
                    if icon_scale_valid else
                    best["scale"]
                )
                seed_source = (
                    "accepted-prior" if prior_scale is not None else
                    "exit-icon-size-ratio" if icon_scale_valid else
                    "structure"
                )
                radius = max(0.07, seed_scale * 0.12)
                anchor_step = max(0.0015, fine_step / 2.0)
                anchored_rows = []
                query_float = query.astype(np.float32)
                screen_x, screen_y = (float(value) for value in bounds[:2])
                source_center = np.asarray(source_icon["center"], dtype=np.float64)
                live_center = np.asarray(live_icon["center"], dtype=np.float64)
                for candidate_scale in np.arange(
                    max(minimum_scale, seed_scale - radius),
                    min(maximum_scale, seed_scale + radius) + anchor_step * 0.5,
                    anchor_step,
                ):
                    if was_cancelled():
                        return cancelled_result()
                    translation = live_center - source_center * candidate_scale
                    source_to_query = np.array([
                        [candidate_scale, 0.0, translation[0] - screen_x],
                        [0.0, candidate_scale, translation[1] - screen_y],
                    ], dtype=np.float32)
                    projected_reference = cv2.warpAffine(
                        full_reference, source_to_query, (qw, qh),
                        flags=cv2.INTER_NEAREST,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
                    ).astype(np.float32)
                    projected_valid = cv2.warpAffine(
                        full_valid, source_to_query, (qw, qh),
                        flags=cv2.INTER_NEAREST,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
                    ).astype(np.float32)
                    valid_query = query_float * projected_valid
                    compared_fraction = float(valid_query.sum()) / max(float(query_float.sum()), 1.0)
                    if compared_fraction < 0.45:
                        continue
                    intersection = float((projected_reference * valid_query).sum())
                    reference_count = float((projected_reference * projected_valid).sum())
                    score = min(
                        1.0,
                        (2.0 * intersection)
                        / max(float(valid_query.sum()) + reference_count, 1.0),
                    )
                    source_location = [
                        float((screen_x - translation[0]) / candidate_scale),
                        float((screen_y - translation[1]) / candidate_scale),
                    ]
                    anchored_rows.append({
                        "score": score,
                        "scale": float(candidate_scale),
                        "sourceLocation": source_location,
                        "sourceScaledLocation": [
                            float(screen_x - translation[0]),
                            float(screen_y - translation[1]),
                        ],
                        "translation": translation.tolist(),
                        "comparedFraction": compared_fraction,
                    })
                if anchored_rows:
                    anchored_rows.sort(key=lambda row: row["score"], reverse=True)
                    anchored_best = anchored_rows[0]
                    scale_agreement = abs(anchored_best["scale"] - seed_scale) / max(seed_scale, 0.001)
                    if (
                        anchored_best["score"] >= 0.60
                        # One semantic point fixes translation but cannot prove
                        # scale. Real accepted samples stay within ~5.2%; the
                        # map-04 ghosting incident landed exactly at the former
                        # 12% boundary and visibly doubled every wall edge.
                        and scale_agreement <= 0.06
                        and query_pixels >= 12000
                    ):
                        best = anchored_best
                        distinct_score = float((source_icon or {}).get("secondScore", 0.0))
                        margin = float((source_icon or {}).get("distinctMargin", 0.0))
                        transform_distinct_score = distinct_score
                        transform_margin = margin
                        search_mode = "exit-icon-anchored"
                        exit_anchor = {
                            "sourceScore": float(source_icon["score"]),
                            "sourceMargin": float(source_icon["distinctMargin"]),
                            "sourceCenter": list(source_icon["center"]),
                            "liveScore": float(live_icon["score"]),
                            "liveMargin": float(live_icon["distinctMargin"]),
                            "liveCenter": list(live_icon["center"]),
                            "structureScore": float(anchored_best["score"]),
                            "scaleAgreement": float(scale_agreement),
                            "scaleSeed": float(seed_scale),
                            "scaleSeedSource": seed_source,
                        }
                    elif structure_icon_geometry_verified:
                        # The icon-size seed is quantized in 0.05 template-scale
                        # steps. On a sparse island its own anchored refinement
                        # can therefore miss the 6% cutoff by a fraction even
                        # when the stronger wall fit already projects the same
                        # unique exit onto the live icon. Keep the wall-derived
                        # transform when both its scale and projected icon center
                        # independently agree; this is deliberately unavailable
                        # to automatic cross-map scans.
                        best = structure_best
                        search_mode = "exit-icon-confirmed-structure"
                        exit_anchor = {
                            "sourceScore": float(source_icon["score"]),
                            "sourceMargin": float(source_icon["distinctMargin"]),
                            "sourceCenter": list(source_icon["center"]),
                            "liveScore": float(live_icon["score"]),
                            "liveMargin": float(live_icon["distinctMargin"]),
                            "liveCenter": list(live_icon["center"]),
                            "structureScore": float(structure_best["score"]),
                            "scaleAgreement": float(structure_scale_agreement),
                            "centerProjectionError": structure_icon_error,
                            "scaleSeed": float(icon_scale),
                            "scaleSeedSource": "exit-icon-size-ratio",
                        }
                    else:
                        # Both exit icons are unique, so a structural placement
                        # that cannot agree with their scale/shape is disproved,
                        # not merely "less confident". Never let the relaxed
                        # expanded-corridor rule resurrect that placement.
                        exit_anchor_conflict = {
                            "sourceScore": float(source_icon["score"]),
                            "sourceMargin": float(source_icon["distinctMargin"]),
                            "liveScore": float(live_icon["score"]),
                            "liveMargin": float(live_icon["distinctMargin"]),
                            "structureScore": float(anchored_best["score"]),
                            "scaleAgreement": float(scale_agreement),
                            "scaleSeed": float(seed_scale),
                            "scaleSeedSource": seed_source,
                            "reason": "unique-exit-anchor-disagrees-with-structure",
                        }

        scale = float(best["scale"])
        source_x, source_y = best["sourceScaledLocation"]
        screen_x, screen_y = bounds[:2]
        matrix = np.array([
            [scale, 0.0, screen_x - source_x],
            [0.0, scale, screen_y - source_y],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        # Dice overlap can remain deceptively high when thick, repeated rooms
        # are shifted by several pixels. Measure the visible wall-edge residual
        # directly so an overlay with obvious double edges never reaches the
        # screen. This is independent of map identity and semantic anchors.
        source_to_query = matrix[:2].astype(np.float32).copy()
        source_to_query[0, 2] -= float(screen_x)
        source_to_query[1, 2] -= float(screen_y)
        projected_reference = cv2.warpAffine(
            full_reference.astype(np.uint8), source_to_query, (qw, qh),
            flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        edge_kernel = np.ones((3, 3), np.uint8)
        query_edges = cv2.morphologyEx(
            query.astype(np.uint8), cv2.MORPH_GRADIENT, edge_kernel,
        ) > 0
        projected_edges = cv2.morphologyEx(
            projected_reference, cv2.MORPH_GRADIENT, edge_kernel,
        ) > 0
        edge_count = int(query_edges.sum())
        if edge_count >= 200 and projected_edges.any():
            distance_to_projection = cv2.distanceTransform(
                (~projected_edges).astype(np.uint8), cv2.DIST_L2, 3,
            )
            edge_distances = distance_to_projection[query_edges]
            edge_median = float(np.median(edge_distances))
            edge_p90 = float(np.percentile(edge_distances, 90))
            edge_within_three = float(np.mean(edge_distances <= 3.0))
        else:
            edge_median = edge_p90 = 999.0
            edge_within_three = 0.0
        visual_residual = {
            "queryEdgeCount": edge_count,
            "medianPixels": edge_median,
            "p90Pixels": edge_p90,
            "withinThreePixels": edge_within_three,
        }
        visual_geometry_ok = bool(
            edge_count >= 200
            and edge_median <= 4.5
            and edge_within_three >= 0.45
        )
        projected_width = route.shape[1] * scale
        projected_height = route.shape[0] * scale
        x0, y0 = matrix[0, 2], matrix[1, 2]
        x1, y1 = x0 + projected_width, y0 + projected_height
        fh, fw = frame.shape[:2]
        intersection_area = (
            max(0.0, min(x1, fw) - max(x0, 0.0))
            * max(0.0, min(y1, fh) - max(y0, 0.0))
        )
        box_area = max(1.0, projected_width * projected_height)
        screen_overlap = intersection_area / min(box_area, float(fw * fh))
        plausible = bool(
            minimum_scale < scale < maximum_scale and screen_overlap >= 0.05
        )
        standard_confidence = bool(
            best["score"] >= 0.82
            and margin >= 0.04
            and transform_margin >= 0.02
        )
        expanded_strong = bool(
            best["score"] >= 0.82
            and margin >= 0.018
            and transform_margin >= 0.015
        )
        # A player-confirmed map may expose only a small, dragged island.  Once
        # the search has expanded beyond the side-door crop, accept a slightly
        # softer but still distinct interior winner when there is substantial
        # live structure and most of the projected sheet remains on screen.
        # align_best_partial disables expansion, so this never promotes an
        # automatic cross-map identity.
        expanded_progressive = bool(
            best["score"] >= 0.79
            and margin >= 0.015
            and transform_margin >= 0.012
            and query_pixels >= 20000
            and screen_overlap >= 0.65
        )
        # A large, newly explored floor can touch the full-source boundary and
        # still be unambiguous geometrically.  In that case the raw Dice margin
        # is softened by many near-identical scale samples of the *same* place.
        # Admit it only when the independent edge residual is pixel-precise.
        # This is intentionally stricter than the ordinary expanded path.
        full_floor_precision_confidence = bool(
            boundary_expanded
            and search_mode in {
                "full-floor-expanded", "full-floor-first-fit",
                "full-floor-crop-fallback", "accepted-prior-full-floor",
            }
            and best["score"] >= 0.84
            and margin >= 0.025
            and transform_margin >= 0.025
            and query_pixels >= 30000
            and screen_overlap >= 0.65
            and edge_median <= 1.5
            and edge_p90 <= 6.0
            and edge_within_three >= 0.75
        )
        full_floor_edge_confidence = bool(
            boundary_expanded
            and search_mode in {
                "full-floor-expanded", "full-floor-first-fit",
                "full-floor-crop-fallback", "accepted-prior-full-floor",
            }
            # An explored island may genuinely start at the outermost corridor.
            # Once the search already covers the full source floor there is no
            # larger window left; require strong distinctness and retain the
            # independent wall-edge residual as the final safety gate.
            and (
                (
                    best["score"] >= 0.79
                    and margin >= 0.04
                    and transform_margin >= 0.02
                    and query_pixels >= 20000
                    and screen_overlap >= 0.65
                )
                or full_floor_precision_confidence
            )
        )
        exit_anchor_conflict_overridden = bool(
            full_floor_precision_confidence
            and exit_anchor_conflict is not None
            and structure_icon_consistency is not None
            and float(structure_icon_consistency.get("centerErrorPixels", 999.0)) <= 6.0
            and float(exit_anchor_conflict.get("sourceMargin", 0.0)) >= 0.12
            and float(exit_anchor_conflict.get("liveMargin", 0.0)) >= 0.12
        )
        expanded_confidence = bool(
            boundary_expanded
            and search_mode in {
                "side-door-expanded", "full-floor-expanded",
                "full-floor-first-fit", "full-floor-crop-fallback",
                "accepted-prior-full-floor",
            }
            and (expanded_strong or expanded_progressive)
            and (
                not touches_search_edge(best, search_bounds)
                or full_floor_edge_confidence
            )
        )
        icon_ratio_geometry_ok = bool(
            edge_median <= 3.0 and edge_within_three >= 0.50
        )
        semantic_anchor_strength = bool(
            exit_anchor is not None
            and exit_anchor["structureScore"] >= 0.84
            and exit_anchor["scaleAgreement"] <= 0.03
            and exit_anchor["liveScore"] >= 0.80
            and exit_anchor["liveMargin"] >= 0.18
            and query_pixels >= 20000
        )
        direct_structure_anchor_strength = bool(
            exit_anchor is not None
            and search_mode == "exit-icon-confirmed-structure"
            and (structure_icon_consistency or {}).get("verified", False)
            and edge_median <= 4.2
            and edge_within_three >= 0.42
            and edge_p90 <= 24.0
        )
        exit_anchor_confidence = bool(
            exit_anchor is not None
            and search_mode in {
                "exit-icon-anchored", "exit-icon-confirmed-structure",
            }
            and exit_anchor["structureScore"] >= 0.60
            and exit_anchor["liveScore"] >= 0.72
            and exit_anchor["liveMargin"] >= 0.12
            # Icon-size ratio is an excellent scale seed but is quantized by
            # template size. Require tighter wall agreement than the generic
            # visual gate unless the independent semantic proof is very strong.
            and (
                exit_anchor.get("scaleSeedSource") != "exit-icon-size-ratio"
                or icon_ratio_geometry_ok
                or semantic_anchor_strength
                or direct_structure_anchor_strength
            )
        )
        # Sparse exploration and thick live walls can make the raw edge-distance
        # metric pessimistic. A unique semantic exit plus a high structural fit
        # and near-identical scale is an independent two-signal proof. This
        # admits the current map-12 frame (0.864 structure, 1.8% scale delta)
        # without reopening the map-04 ghost fit (0.759, 12% delta).
        semantic_precision_override = bool(
            exit_anchor_confidence
            and (semantic_anchor_strength or direct_structure_anchor_strength)
        )
        success = bool(
            (
                standard_confidence
                or expanded_confidence
                or exit_anchor_confidence
            )
            and plausible
            and (exit_anchor_conflict is None or exit_anchor_conflict_overridden)
            and (visual_geometry_ok or semantic_precision_override)
        )

        def public_candidate(row: dict | None) -> dict | None:
            if not isinstance(row, dict):
                return None
            source_scaled = np.asarray(
                row.get("sourceScaledLocation"), dtype=np.float64,
            )
            scale_value = float(row.get("scale", 0.0))
            if source_scaled.shape != (2,) or not np.isfinite(
                source_scaled
            ).all() or not np.isfinite(scale_value):
                return None
            candidate_matrix = np.array([
                [scale_value, 0.0, float(bounds[0]) - source_scaled[0]],
                [0.0, scale_value, float(bounds[1]) - source_scaled[1]],
                [0.0, 0.0, 1.0],
            ], dtype=np.float64)
            return {
                "score": float(row["score"]),
                "scale": scale_value,
                "sourceLocation": row["sourceLocation"],
                "sourceScaledLocation": row["sourceScaledLocation"],
                "matrixSourceToClient": candidate_matrix.tolist(),
            }

        transform_other_candidate = next((
            row for row in candidates[1:]
            if (
                np.linalg.norm(
                    np.asarray(row["sourceLocation"])
                    - np.asarray(candidates[0]["sourceLocation"])
                ) >= 35.0
                or abs(
                    float(row["scale"]) - float(candidates[0]["scale"])
                ) / max(float(candidates[0]["scale"]), 0.001) >= 0.04
            )
        ), None)
        return {
            "success": success,
            "reason": (
                "aligned-expanded-partial-structure"
                if success and expanded_confidence else
                "aligned-exit-anchor-partial-structure"
                if success and exit_anchor_confidence else
                "aligned-partial-structure"
                if success else
                "partial-structure-visual-residual"
                if (
                    (standard_confidence or expanded_confidence or exit_anchor_confidence)
                    and plausible
                    and not (visual_geometry_ok or semantic_precision_override)
                ) else "partial-structure-ambiguous"
            ),
            "alignmentMode": "partial-structure-dice",
            "plausibleGeometry": plausible,
            "score": float(best["score"]),
            "templateScore": float(best["score"]),
            "distinctScore": distinct_score,
            "distinctMargin": margin,
            "transformDistinctScore": transform_distinct_score,
            "transformDistinctMargin": transform_margin,
            "queryPixels": query_pixels,
            "scaleSearch": [minimum_scale, maximum_scale],
            "searchMode": search_mode,
            "searchBounds": list(search_bounds),
            "searchCropFallback": search_crop_fallback,
            "boundaryExpanded": boundary_expanded,
            "fullFloorBoundaryAccepted": full_floor_edge_confidence,
            "fullFloorPrecisionAccepted": full_floor_precision_confidence,
            "exitAnchor": exit_anchor,
            "exitAnchorConflict": exit_anchor_conflict,
            "exitAnchorConflictOverridden": exit_anchor_conflict_overridden,
            "structureIconConsistency": structure_icon_consistency,
            "semanticPrecisionOverride": semantic_precision_override,
            "iconRatioGeometryVerified": icon_ratio_geometry_ok,
            "acceptedPriorUsed": bool(
                prior_scale is not None
                and abs(scale - prior_scale) / max(prior_scale, 0.001) <= 0.06
            ),
            "acceptedPriorScale": prior_scale,
            "visualResidual": visual_residual,
            "visibleComponent": component,
            "candidateDiagnostics": [
                {
                    "score": float(row["score"]),
                    "scale": float(row["scale"]),
                    "sourceLocation": row["sourceLocation"],
                    "sourceScaledLocation": row["sourceScaledLocation"],
                    "matrixSourceToClient": public_candidate(row)[
                        "matrixSourceToClient"
                    ],
                }
                for row in candidates[:12]
            ],
            "transformDistinctCandidate": public_candidate(
                transform_other_candidate
            ),
            "scale": scale,
            "anisotropy": 1.0,
            "screenOverlap": float(screen_overlap),
            "inliers": 0,
            "inlierRatio": 0.0,
            "medianError": 0.0,
            "matrixSourceToClient": matrix.tolist(),
        }

    @staticmethod
    def _evaluate_alignment_fit(
        fit: dict,
        reference: _Features,
        query: _Features,
        route_shape: tuple[int, ...],
        frame_shape: tuple[int, ...],
        crop_x: int,
        crop_y: int,
        mode: str,
    ) -> dict:
        fit = dict(fit)
        prepared_matrix = fit.pop("matrix", None)
        if prepared_matrix is None:
            return {
                "success": False, "reason": "no-geometric-model",
                "alignmentMode": mode, "plausibleGeometry": False, **fit,
            }
        source_scale = np.diag([reference.scale, reference.scale, 1.0])
        query_unscale = np.diag([1.0 / query.scale, 1.0 / query.scale, 1.0])
        crop_offset = np.array(
            [[1.0, 0.0, crop_x], [0.0, 1.0, crop_y], [0.0, 0.0, 1.0]],
        )
        matrix = crop_offset @ query_unscale @ prepared_matrix @ source_scale
        rh, rw = route_shape[:2]
        corners = np.float32([[[0, 0], [rw, 0], [rw, rh], [0, rh]]])
        projected = cv2.perspectiveTransform(corners, matrix)[0]
        if not np.isfinite(projected).all():
            return {
                "success": False, "reason": "non-finite-transform",
                "alignmentMode": mode, "plausibleGeometry": False, **fit,
            }
        x0, y0 = projected.min(axis=0)
        x1, y1 = projected.max(axis=0)
        fh, fw = frame_shape[:2]
        intersection = (
            max(0.0, min(float(x1), fw) - max(float(x0), 0.0))
            * max(0.0, min(float(y1), fh) - max(float(y0), 0.0))
        )
        box_area = max(1.0, float(x1 - x0) * float(y1 - y0))
        screen_overlap = intersection / min(box_area, float(fw * fh))
        singular = np.linalg.svd(matrix[:2, :2], compute_uv=False)
        scale = float(np.sqrt(abs(np.linalg.det(matrix[:2, :2]))))
        anisotropy = float(singular.max() / max(singular.min(), 1e-9))
        rotation_degrees = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
        projected_footprint = float(intersection / max(float(fw * fh), 1.0))
        plausible_geometry = bool(
            0.03 <= scale <= 6.0
            and anisotropy <= 1.20
            and screen_overlap >= 0.05
            and abs(rotation_degrees) <= 8.0
            and projected_footprint >= 0.08
        )
        success = bool(
            fit["inliers"] >= 10 and fit["inlierRatio"] >= 0.22
            and fit["medianError"] <= 6.0 and plausible_geometry
        )
        return {
            "success": success,
            "reason": "aligned-current-frame" if success else "low-confidence-or-degenerate",
            "alignmentMode": mode,
            "plausibleGeometry": plausible_geometry,
            **fit,
            "scale": scale,
            "anisotropy": anisotropy,
            "rotationDegrees": rotation_degrees,
            "projectedFootprint": projected_footprint,
            "screenOverlap": screen_overlap,
            "matrixSourceToClient": matrix.tolist(),
        }


def build_colored_route_overlay(
    route_path: Path,
    matrix_source_to_client: list[list[float]] | np.ndarray,
    frame_shape: tuple[int, ...],
) -> tuple[np.ndarray | None, dict]:
    route = imread_unicode(route_path)
    if route is None:
        return None, {"success": False, "reason": "route-image-missing"}
    hsv = cv2.cvtColor(route, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    vivid = (saturation >= 85) & (value >= 120)
    route_hue = (hue <= 12) | (hue >= 165) | ((hue >= 35) & (hue <= 140))
    alpha = np.where(vivid & route_hue, 235, 0).astype(np.uint8)
    alpha = cv2.dilate(alpha, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    source = np.dstack([route, alpha])
    height, width = frame_shape[:2]
    matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
    warped = cv2.warpPerspective(
        source, matrix, (width, height), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
    )
    visible = int((warped[:, :, 3] >= 24).sum())
    coverage = visible / max(width * height, 1)
    if visible < 120 or coverage > 0.22:
        return None, {"success": False, "reason": "route-mask-empty-or-excessive", "coverage": coverage}
    return warped, {"success": True, "reason": "colored-route-layer", "coverage": coverage, "visiblePixels": visible}


def build_annotated_map_overlay(
    route_path: Path,
    matrix_source_to_client: list[list[float]] | np.ndarray,
    frame_shape: tuple[int, ...],
) -> tuple[np.ndarray | None, dict]:
    """Warp map architecture and route ink without the opaque sheet backdrop."""
    route = imread_unicode(route_path)
    if route is None:
        return None, {"success": False, "reason": "route-image-missing"}
    hsv = cv2.cvtColor(route, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    vivid = (saturation >= 82) & (value >= 105)
    route_hue = (hue <= 14) | (hue >= 165) | ((hue >= 35) & (hue <= 140))
    route_ink = vivid & route_hue
    architecture = map_color_mask(route, live=False).astype(bool)

    # Labels inside rooms are useful, but the title/author block around the map
    # is not.  Keep bright neutral pixels only when they touch map structure.
    radius = max(5, round(max(route.shape[:2]) * 0.006))
    radius = radius + 1 if radius % 2 == 0 else radius
    near_map = cv2.dilate(
        (architecture | route_ink).astype(np.uint8),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius, radius)),
    ).astype(bool)
    nearby_labels = (saturation <= 80) & (value >= 175) & near_map

    alpha = np.zeros(route.shape[:2], dtype=np.uint8)
    alpha[architecture] = 150
    alpha[nearby_labels] = 185
    alpha[route_ink] = 235
    alpha = cv2.dilate(alpha, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    source = np.dstack([route, alpha])
    height, width = frame_shape[:2]
    matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
    warped = cv2.warpPerspective(
        source, matrix, (width, height), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
    )
    visible = int((warped[:, :, 3] >= 24).sum())
    structural = int(((warped[:, :, 3] >= 90) & (warped[:, :, 3] < 220)).sum())
    route_pixels = int((warped[:, :, 3] >= 220).sum())
    coverage = visible / max(width * height, 1)
    if visible < 500 or structural < 300 or coverage > 0.40:
        return None, {
            "success": False, "reason": "annotated-map-mask-empty-or-excessive",
            "coverage": coverage, "visiblePixels": visible,
            "structuralPixels": structural, "routePixels": route_pixels,
        }
    return warped, {
        "success": True, "reason": "annotated-map-layer",
        "coverage": coverage, "visiblePixels": visible,
        "structuralPixels": structural, "routePixels": route_pixels,
    }


def build_original_sheet_overlay(
    route_path: Path | np.ndarray,
    matrix_source_to_client: list[list[float]] | np.ndarray,
    frame_shape: tuple[int, ...],
    *,
    minimum_coverage: float = 0.08,
    minimum_visible_pixels: int = 500,
) -> tuple[np.ndarray | None, dict]:
    """Warp the unmodified source sheet; opacity is applied by the window.

    Initial alignment keeps the conservative coverage defaults. Runtime pan
    tracking may lower them because a correctly aligned long sheet can be
    mostly outside the client while the player is dragging it. Every call
    still starts from the original source, so content that re-enters the
    client is never lost to an earlier screen-sized crop.
    """
    route = (
        route_path if isinstance(route_path, np.ndarray)
        else imread_unicode(route_path)
    )
    if route is None:
        return None, {"success": False, "reason": "route-image-missing"}
    if route.ndim == 2:
        source = cv2.cvtColor(route, cv2.COLOR_GRAY2BGRA)
    elif route.ndim == 3 and route.shape[2] == 3:
        alpha = np.full(route.shape[:2], 255, dtype=np.uint8)
        source = np.dstack([route, alpha])
    elif route.ndim == 3 and route.shape[2] == 4:
        # Alternate-author assets are transparent PNGs loaded with
        # IMREAD_UNCHANGED.  Appending another alpha plane produced a five-
        # channel array: geometric validation still passed, but the native
        # LayeredOverlay correctly rejected it because show_bgra accepts BGRA
        # only.  Preserve the author's alpha instead of manufacturing a fifth
        # channel.  Legacy three-channel JPG sheets still receive an opaque
        # alpha plane above.
        source = route.copy()
    else:
        return None, {
            "success": False,
            "reason": "unsupported-route-channel-count",
            "routeShape": list(route.shape),
        }
    height, width = frame_shape[:2]
    matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
    warped = cv2.warpPerspective(
        source, matrix, (width, height), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
    )
    visible = int((warped[:, :, 3] >= 24).sum())
    coverage = visible / max(width * height, 1)
    required_visible = max(1, int(minimum_visible_pixels))
    required_coverage = max(0.0, float(minimum_coverage))
    if visible < required_visible or coverage < required_coverage:
        return None, {
            "success": False, "reason": (
                "original-sheet-too-small-or-outside-screen"
                if coverage < required_coverage else "original-sheet-outside-screen"
            ),
            "coverage": coverage, "visiblePixels": visible,
        }
    return warped, {
        "success": True, "reason": "original-sheet-layer",
        "coverage": coverage, "visiblePixels": visible,
        "sourceUnmodified": True,
    }


def validate_display_alignment(
    fit: dict | None,
    overlay_meta: dict | None,
    confirmed_map_id: str | None,
    candidate_map_id: str | None,
) -> dict:
    """One final, branch-independent gate immediately before native display."""
    if not confirmed_map_id or candidate_map_id != confirmed_map_id:
        return {"success": False, "reason": "confirmed-map-mismatch"}
    if not fit or not fit.get("success"):
        return {"success": False, "reason": (fit or {}).get("reason", "fit-not-successful")}
    if not overlay_meta or not overlay_meta.get("success"):
        return {"success": False, "reason": (overlay_meta or {}).get("reason", "overlay-not-built")}
    matrix = np.asarray(fit.get("matrixSourceToClient"), dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return {"success": False, "reason": "invalid-display-transform"}
    linear = matrix[:2, :2]
    determinant = float(np.linalg.det(linear))
    singular = np.linalg.svd(linear, compute_uv=False)
    scale = float(np.sqrt(abs(determinant)))
    anisotropy = float(singular.max() / max(singular.min(), 1e-9))
    rotation = float(np.degrees(np.arctan2(linear[1, 0], linear[0, 0])))
    coverage = float(overlay_meta.get("coverage", 0.0))
    if (
        determinant <= 0.0
        or not 0.03 <= scale <= 6.0
        or anisotropy > 1.20
        or abs(rotation) > 8.0
        or not 0.08 <= coverage <= 1.0
    ):
        return {
            "success": False,
            "reason": "implausible-display-geometry",
            "scale": scale,
            "anisotropy": anisotropy,
            "rotationDegrees": rotation,
            "coverage": coverage,
        }
    if fit.get("alignmentMode") == "partial-structure-dice":
        residual = fit.get("visualResidual") or {}
        residual_ok = bool(
            int(residual.get("queryEdgeCount", 0)) >= 200
            and float(residual.get("medianPixels", 999.0)) <= 4.5
            and float(residual.get("withinThreePixels", 0.0)) >= 0.45
        )
        if not residual_ok and not fit.get("semanticPrecisionOverride"):
            return {"success": False, "reason": "partial-display-residual-unverified"}
    if fit.get("requiresSelectedRouteCurrentFrameResidual"):
        residual = fit.get("selectedRouteCurrentFrameResidual") or {}
        rendered_profile_verified = bool(
            residual.get("verified") is True
            and residual.get("poseVerifiedAgainstRenderedProfile") is True
            and residual.get("validationBasis")
            == "actual-rendered-profile-structural-mask"
        )
        canonical_geometry_verified = bool(
            residual.get("verified") is True
            and residual.get("poseVerifiedAgainstCanonicalGeometry") is True
            and residual.get("profileRenderDerivedFromCanonical") is True
            and residual.get("validationBasis")
            == "shared-canonical-geometry-current-frame"
            and bool(residual.get("coordinatePackId"))
        )
        if not (rendered_profile_verified or canonical_geometry_verified):
            return {
                "success": False,
                "reason": "selected-route-current-frame-residual-unverified",
                "selectedRouteCurrentFrameResidual": residual,
            }
    return {
        "success": True,
        "reason": "display-geometry-verified",
        "scale": scale,
        "anisotropy": anisotropy,
        "rotationDegrees": rotation,
        "coverage": coverage,
    }


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


def estimate_front_local_geometry(
    frame: np.ndarray | None,
    route: np.ndarray | None,
    baseline_matrix,
    *,
    maximum_dimension: int = 720,
    visible_override: tuple | None = None,
    reference_override: np.ndarray | None = None,
    route_profile_id: str | None = None,
    maximum_relative_scale_correction: float = 0.08,
) -> dict:
    """Estimate an axis-locked local similarity from one current-map ROI.

    The reference and current masks are prepared once. Sparse local patch
    tracking supplies reference-to-current point pairs near the seed; a
    RANSAC pass removes repeated-room mismatches from the support half of the
    pairs. H0 fixes the reviewed linear scale and solves only translation. H1
    solves one uniform scale and translation around the observed point cloud,
    while a disjoint validation half measures whether that extra degree of
    freedom is actually observable. No scale lattice or whole-ROI refiner is
    used here.
    """
    started = time.perf_counter()
    if not isinstance(frame, np.ndarray) or not isinstance(route, np.ndarray):
        return {"success": False, "reason": "front-local-input-missing"}
    matrix = np.asarray(baseline_matrix, dtype=np.float64)
    if (
        matrix.shape != (3, 3)
        or not np.isfinite(matrix).all()
        or abs(float(np.linalg.det(matrix[:2, :2]))) <= 1e-9
    ):
        return {"success": False, "reason": "front-local-invalid-transform"}
    visible = visible_override or visible_map_component(frame)
    if visible is None:
        return {"success": False, "reason": "front-local-visible-component-missing"}
    patch, bounds, component = visible
    query_full = map_color_mask(patch, live=True).astype(np.uint8) * 255
    query_pixels = int(np.count_nonzero(query_full))
    if query_pixels < 1500:
        return {
            "success": False,
            "reason": "front-local-insufficient-current-map",
            "queryPixels": query_pixels,
        }
    reference = (
        reference_override
        if (
            isinstance(reference_override, np.ndarray)
            and reference_override.ndim == 2
            and reference_override.shape == route.shape[:2]
        ) else author_route_structure_mask(route, profile_id=route_profile_id)
    )
    if not isinstance(reference, np.ndarray) or reference.shape != route.shape[:2]:
        return {"success": False, "reason": "front-local-reference-missing"}

    evaluation_scale = min(
        1.0, float(maximum_dimension) / max(query_full.shape),
    )
    query = query_full
    if evaluation_scale < 0.999:
        query = cv2.resize(
            query_full,
            (
                max(1, round(query_full.shape[1] * evaluation_scale)),
                max(1, round(query_full.shape[0] * evaluation_scale)),
            ),
            interpolation=cv2.INTER_NEAREST,
        )
    source_to_patch = matrix[:2].astype(np.float32).copy()
    source_to_patch[0, 2] -= float(bounds[0])
    source_to_patch[1, 2] -= float(bounds[1])
    source_to_patch *= float(evaluation_scale)
    projected = cv2.warpAffine(
        (reference > 0).astype(np.uint8) * 255,
        source_to_patch,
        (query.shape[1], query.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    if not projected.any():
        return {
            "success": False,
            "reason": "front-local-reference-outside-visible-component",
            "queryPixels": query_pixels,
        }
    feature_points = cv2.goodFeaturesToTrack(
        projected,
        maxCorners=320,
        qualityLevel=0.01,
        minDistance=8,
        blockSize=7,
    )
    if feature_points is None or len(feature_points) < 6:
        return {
            "success": False,
            "reason": "front-local-structure-points-missing",
            "queryPixels": query_pixels,
        }
    tracked, status, forward_error = cv2.calcOpticalFlowPyrLK(
        projected, query, feature_points, None,
        winSize=(31, 31), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    if tracked is None or status is None or forward_error is None:
        return {
            "success": False,
            "reason": "front-local-point-tracking-failed",
            "queryPixels": query_pixels,
        }
    backward, backward_status, backward_error = cv2.calcOpticalFlowPyrLK(
        query, projected, tracked, None,
        winSize=(31, 31), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    if backward is None or backward_status is None or backward_error is None:
        return {
            "success": False,
            "reason": "front-local-backtracking-failed",
            "queryPixels": query_pixels,
        }
    query_distance = cv2.distanceTransform(
        (query > 0).astype(np.uint8), cv2.DIST_L2, 3,
    )
    local_radius = max(
        24.0,
        min(96.0, 0.12 * max(query.shape)),
    )
    points = feature_points[:, 0, :].astype(np.float64)
    tracked_points = tracked[:, 0, :].astype(np.float64)
    backward_points = backward[:, 0, :].astype(np.float64)
    flow_error_values = forward_error[:, 0].astype(np.float64)
    backward_error_values = backward_error[:, 0].astype(np.float64)
    forward_backward = np.linalg.norm(
        backward_points - points, axis=1,
    )
    in_bounds = (
        (tracked_points[:, 0] >= 1.0)
        & (tracked_points[:, 1] >= 1.0)
        & (tracked_points[:, 0] < query.shape[1] - 1.0)
        & (tracked_points[:, 1] < query.shape[0] - 1.0)
    )
    integer_points = np.round(tracked_points).astype(np.int32)
    query_support = np.zeros(len(integer_points), dtype=bool)
    query_support[in_bounds] = query_distance[
        integer_points[in_bounds, 1], integer_points[in_bounds, 0]
    ] >= 1.0
    accepted = (
        (status[:, 0] > 0)
        & (backward_status[:, 0] > 0)
        & (flow_error_values <= 100.0)
        & (backward_error_values <= 100.0)
        & (forward_backward <= 2.5)
        & (np.linalg.norm(tracked_points - points, axis=1) <= local_radius)
        & query_support
    )
    initial_count = int(len(points))
    if int(accepted.sum()) < 6:
        return {
            "success": False,
            "reason": "front-local-correspondences-insufficient",
            "queryPixels": query_pixels,
            "initialPointCount": initial_count,
            "acceptedPointCount": int(accepted.sum()),
        }
    points = points[accepted]
    tracked_points = tracked_points[accepted]
    forward_backward = forward_backward[accepted]
    flow_error_values = flow_error_values[accepted]
    source_points_h = np.column_stack((
        points / float(evaluation_scale)
        + np.asarray(bounds[:2], dtype=np.float64),
        np.ones(len(points), dtype=np.float64),
    ))
    source_h = source_points_h @ np.linalg.inv(matrix).T
    source_points = source_h[:, :2] / source_h[:, 2, None]
    observed_points = (
        tracked_points / float(evaluation_scale)
        + np.asarray(bounds[:2], dtype=np.float64)
    )
    if not np.isfinite(source_points).all() or not np.isfinite(observed_points).all():
        return {"success": False, "reason": "front-local-point-conversion-failed"}
    return fit_axis_locked_point_geometry(
        source_points, observed_points, matrix, local_points=points,
        query_shape=query.shape, forward_backward=forward_backward,
        flow_error_values=flow_error_values, initial_count=initial_count,
        component=component, evaluation_scale=evaluation_scale,
        maximum_relative_scale_correction=maximum_relative_scale_correction,
        started=started,
    )


def fit_axis_locked_dense_geometry(previous, current, source_mask, source_structure, current_structure, initial_matrix,
                                   *, pixel_origin=(0, 0)):
    """Bounded photometric H0/H1 proposal, checked on held-out terrain edges.

    This sensor has no pose writer. Images and the initial matrix share one
    cropped pixel coordinate system. Coloured-marker exclusions belong to the
    caller's original source_mask. Training and validation tiles are disjoint.
    """
    ys, xs = np.where(source_mask > 0)
    if len(xs) < 200:
        return {'success': False, 'reason': 'dense-terrain-support-insufficient'}
    # Keep the partition tied to the old pixel coordinates. A current island
    # growing or shrinking must not select a different training/validation set.
    global_x, global_y = xs+pixel_origin[0], ys+pixel_origin[1]
    tiles = ((global_x // 24 + global_y // 24) % 2) == 0
    interior = ((global_x % 24 >= 2) & (global_x % 24 < 22)
                & (global_y % 24 >= 2) & (global_y % 24 < 22))
    train = np.flatnonzero(tiles & interior)
    train = train[::max(1, int(np.ceil(len(train)/4096)))]
    old_edges = cv2.morphologyEx(source_structure.astype(np.uint8), cv2.MORPH_GRADIENT,
                                 np.ones((3, 3), np.uint8)) > 0
    validation = np.flatnonzero((~tiles) & interior & old_edges[ys, xs])
    if len(train) < 100 or len(validation) < 6:
        return {'success': False, 'reason': 'dense-terrain-held-out-support-insufficient'}
    points = np.column_stack([xs, ys]).astype(np.float64)
    center = points[train].mean(axis=0)
    fit_points = points[train]-center
    values = previous[ys[train], xs[train]].astype(np.float64)/255.0
    query = current.astype(np.float32)/255.0
    gx = cv2.Sobel(query, cv2.CV_32F, 1, 0, ksize=3)/8.0
    gy = cv2.Sobel(query, cv2.CV_32F, 0, 1, ksize=3)/8.0

    def sample(image, coordinates):
        return cv2.remap(image, coordinates[:, 0].astype(np.float32).reshape(-1, 1),
                         coordinates[:, 1].astype(np.float32).reshape(-1, 1),
                         cv2.INTER_LINEAR).ravel().astype(np.float64)

    def cost(residual):
        # Robust intensity optimization only; not a display residual threshold.
        return float(np.sum(np.sqrt(1+(residual/.05)**2)-1))

    def solve(free_scale):
        initial = np.asarray(initial_matrix, dtype=np.float64)
        params = np.r_[initial[0, 0] if free_scale else 1.,
                       (initial @ np.r_[center, 1])[:2]-center]
        condition = 0.
        for _ in range(30):
            coordinates = params[0]*fit_points + params[1:] + center
            residual = sample(query, coordinates)-values
            dx, dy = sample(gx, coordinates), sample(gy, coordinates)
            jac = np.column_stack([dx*fit_points[:, 0]+dy*fit_points[:, 1], dx, dy])
            if not free_scale:
                jac = jac[:, 1:]
            weights = (1+(residual/.05)**2)**(-.25)
            weighted = jac*weights[:, None]
            singular = np.linalg.svd(weighted/np.maximum(np.linalg.norm(weighted, axis=0), 1e-9),
                                     compute_uv=False)
            condition = float(singular[-1]/max(singular[0], 1e-9))
            step = np.linalg.lstsq(weighted, -residual*weights, rcond=None)[0]
            if not free_scale:
                step = np.r_[0., step]
            improved = False
            for fraction in (1., .5, .25, .125):
                candidate = params+fraction*step
                trial = sample(query, candidate[0]*fit_points+candidate[1:]+center)-values
                if np.isfinite(candidate).all() and cost(trial) < cost(residual):
                    params = candidate
                    improved = True
                    break
            if not improved or np.linalg.norm(step[1:]) < .001 and abs(step[0]) < 1e-5:
                break
        matrix = np.eye(3)
        matrix[:2, :2] *= params[0]
        matrix[:2, 2] = params[1:]+center-params[0]*center
        return matrix, condition

    h0, _ = solve(False)
    h1, condition = solve(True)
    edges = cv2.morphologyEx(current_structure.astype(np.uint8), cv2.MORPH_GRADIENT,
                            np.ones((3, 3), np.uint8)) > 0
    if not edges.any():
        return {'success': False, 'reason': 'dense-current-edges-missing'}
    distances = cv2.distanceTransform((~edges).astype(np.uint8), cv2.DIST_L2, 3)

    def validate(matrix):
        coordinates = points[validation] @ matrix[:2, :2].T + matrix[:2, 2]
        if (np.any(coordinates < 0) or np.any(coordinates[:, 0] >= current.shape[1]-1)
                or np.any(coordinates[:, 1] >= current.shape[0]-1)):
            return None
        errors = sample(distances, coordinates)
        return _front_local_point_residuals(np.column_stack([errors, np.zeros_like(errors)]),
                                          np.zeros((len(errors), 2)))

    v0, v1 = validate(h0), validate(h1)
    if v0 is None or v1 is None:
        return {'success': False, 'reason': 'dense-held-out-edges-outside-frame'}
    improvement = (v0['rmsPixels']-v1['rmsPixels'])/max(v0['rmsPixels'], 1.)
    observable = bool(condition >= .10 and np.min(np.ptp(points[validation], axis=0)) >= 24.)
    factor = float(h1[0, 0])
    verified = bool(observable and .003 <= abs(factor-1) <= .08 and improvement >= .20
                    and v1['medianPixels'] <= 3. and v1['p90Pixels'] <= 8. and v1['maxPixels'] <= 20.)
    translation_verified = bool(observable and abs(factor-1) < .003
        and v0['medianPixels'] <= 3. and v0['p90Pixels'] <= 8. and v0['maxPixels'] <= 20.)
    mode = 'scale-plus-translation' if verified else 'translation-only' if translation_verified else None
    return {'success': mode is not None, 'mode': mode,
            'reason': 'dense-scale-held-out-verified' if verified else 'dense-translation-held-out-verified' if translation_verified else 'dense-scale-held-out-failed',
            'matrixSourceToClient': h1 if verified else h0 if translation_verified else h1,
            'scaleFactor': factor, 'scaleObservable': observable,
            'geometryCondition': condition, 'improvement': improvement,
            'supportPixelCount': int(len(train)), 'validationEdgeCount': int(len(validation)),
            'h0Validation': v0, 'h1Validation': v1}


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
