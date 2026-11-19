"""Candidate-independent screen glyph anchor. No map IDs, world pose or display authority.

The sole template is an offline crop from one pinned native resource, reused for
all frames. A positive result is only a glyph observation. Unknown is deliberately
not a hard gate: a caller may continue its existing structure-based fallback.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import json
import math
from typing import Optional

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class LocatorConfig:
    # Raster support of the 60x60 glyph-only crop, not world/map zoom.
    min_raster_px: int = 19
    max_raster_px: int = 45
    coarse_step_px: int = 4
    peaks_per_scale: int = 3
    max_spatial_proposals: int = 12
    min_score: float = 0.84
    min_distinct_margin: float = 0.12
    near_peak_drop: float = 0.10

    def __post_init__(self):
        if not (8 <= self.min_raster_px <= self.max_raster_px <= 256):
            raise ValueError('Expected raster support range within 8..256 pixels')
        if self.coarse_step_px != 4:
            raise ValueError('This profile is validated only for 4-pixel coarse spacing')
        if self.peaks_per_scale < 2 or self.max_spatial_proposals < 2:
            raise ValueError('Ambiguity assessment requires at least two proposals')
        if not (0 < self.min_score <= 1 and 0 < self.min_distinct_margin <= 1):
            raise ValueError('Invalid evidence thresholds')


@dataclass(frozen=True)
class GateAnchor:
    status: str
    reason: str
    center_px: Optional[tuple[float, float]]
    localization_uncertainty_px: Optional[float]
    center_support_bounds_px: Optional[tuple[float, float, float, float]]
    glyph_raster_size_px: Optional[tuple[int, int]]
    raster_support_range_px: Optional[tuple[int, int]]
    score: Optional[float]
    distinct_second_score: Optional[float]
    score_margin: Optional[float]
    template_sha256: str
    profile_sha256: str
    diagnostics: dict
    # Deliberately no confidence probability, map ID, world scale, rotation,
    # authorization, identity or map-open state.
    allow_structure_fallback: bool = True

    def to_dict(self):
        return asdict(self)


class GateAnchorLocator:
    """Reusable immutable template cache; detect accepts only frame pixels.

    Input is uint8 gray or BGR/BGRA. Returned coordinates refer to that input's
    pixel origin. No cached screen position or per-map template may be supplied.
    OpenCV thread settings are left to the host, not changed globally here.
    """
    def __init__(self, config: LocatorConfig | None = None,
                 asset_dir: str | Path = HERE / 'assets', *, read_resource=None):
        self.config = config or LocatorConfig()
        asset_dir = Path(asset_dir)
        manifest_path = asset_dir / 'template_manifest.json'
        glyph_path = asset_dir / 'native_glyph_template.png'
        manifest = json.loads(manifest_path.read_text() if read_resource is None else read_resource(manifest_path))
        data = glyph_path.read_bytes() if read_resource is None else read_resource(glyph_path)
        self.template_sha256 = hashlib.sha256(data).hexdigest()
        if self.template_sha256 != manifest['template_sha256']:
            raise ValueError('Glyph template digest does not match offline provenance')
        bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None or list(bgr.shape[:2][::-1]) != manifest['template_size_px']:
            raise ValueError('Invalid glyph template shape')
        self._template = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if hashlib.sha256(self._template.tobytes()).hexdigest() != manifest['gray_pixels_sha256']:
            raise ValueError('Decoded grayscale glyph differs from pinned calibration')
        self._fine = {w: cv2.resize(self._template, (w, w), interpolation=cv2.INTER_AREA)
                      for w in range(self.config.min_raster_px, self.config.max_raster_px + 1)}
        widths = list(range(self.config.min_raster_px, self.config.max_raster_px + 1, 4))
        if widths[-1] != self.config.max_raster_px:
            widths.append(self.config.max_raster_px)
        self._coarse = [(w, cv2.resize(self._template, (max(4, round(w/2)),) * 2,
                                      interpolation=cv2.INTER_AREA)) for w in widths]
        profile = dict(algorithm='half-resolution-NCC-local-full-resolution-v1',
                       config=asdict(self.config), template_sha256=self.template_sha256)
        self.profile_sha256 = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()

    def _unknown(self, reason, diagnostics, score=None, second=None, margin=None):
        return GateAnchor('unknown', reason, None, None, None, None, None,
                          score, second, margin, self.template_sha256,
                          self.profile_sha256, diagnostics)

    def detect(self, frame: np.ndarray, *, cancelled=lambda: False) -> GateAnchor:
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8:
            raise ValueError('Frame must be a uint8 numpy array')
        if frame.ndim == 2:
            gray = frame
        elif frame.ndim == 3 and frame.shape[2] in (3, 4):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY if frame.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
        else:
            raise ValueError('Expected gray, BGR or BGRA pixels')
        cfg = self.config
        diag = {'full_frame_coarse_scans': 0, 'spatial_proposals': 0, 'fine_scans': 0,
                'template_raster_size_px': [60, 60],
                'raster_size_is_map_zoom': False,
                'uncertainty_kind': 'near-peak pixel support, not a calibrated probability',
                'profile_validated_on_limited_static_samples': True}
        if cancelled():
            return self._unknown('gate-work-obsolete', diag)
        if min(gray.shape) < cfg.min_raster_px:
            return self._unknown('frame_too_small', diag)
        # Use exact factor 2; for odd dimensions the last pixel is discarded only
        # at proposal time. Full-resolution verification still sees those pixels.
        small = cv2.resize(gray[:gray.shape[0]//2*2, :gray.shape[1]//2*2],
                           (gray.shape[1]//2, gray.shape[0]//2), interpolation=cv2.INTER_AREA)
        proposals = []
        for width, templ in self._coarse:
            if cancelled():
                return self._unknown('gate-work-obsolete', diag)
            if min(small.shape) < len(templ):
                continue
            response = cv2.matchTemplate(small, templ, cv2.TM_CCOEFF_NORMED)
            diag['full_frame_coarse_scans'] += 1
            for _ in range(cfg.peaks_per_scale):
                _, score, _, (x, y) = cv2.minMaxLoc(response)
                if score < 0:
                    break
                n = len(templ)
                proposals.append((float(score), 2*x+n, 2*y+n, width))
                response[max(0, y-n):y+n+1, max(0, x-n):x+n+1] = -1
        proposals.sort(key=lambda r: -r[0])
        distinct = []
        for proposal in proposals:
            if any(math.hypot(proposal[1]-old[1], proposal[2]-old[2]) <
                   max(12, min(proposal[3], old[3]) * .65) for old in distinct):
                continue
            distinct.append(proposal)
            if len(distinct) >= cfg.max_spatial_proposals:
                break
        diag['spatial_proposals'] = len(distinct)
        fine = []
        # Small local refinement, identical fixed work budget whether 1 or 44
        # catalogue maps are available, or their assets are decoded late.
        for _, cx, cy, width in distinct:
            pad = max(42, width+4)
            x0, y0 = max(0, cx-pad), max(0, cy-pad)
            patch = gray[y0:min(gray.shape[0], cy+pad+1), x0:min(gray.shape[1], cx+pad+1)]
            for w in range(max(cfg.min_raster_px, width-4), min(cfg.max_raster_px, width+4)+1):
                if cancelled():
                    return self._unknown('gate-work-obsolete', diag)
                if min(patch.shape) < w:
                    continue
                response = cv2.matchTemplate(patch, self._fine[w], cv2.TM_CCOEFF_NORMED)
                _, score, _, (x, y) = cv2.minMaxLoc(response)
                fine.append((float(score), float(x0+x+w/2), float(y0+y+w/2), w))
                diag['fine_scans'] += 1
        if not fine:
            return self._unknown('no_valid_proposals', diag)
        fine.sort(key=lambda r: -r[0])
        best = fine[0]
        alternatives = [r for r in fine if math.hypot(r[1]-best[1], r[2]-best[2]) > max(12, best[3])]
        second = alternatives[0][0] if alternatives else None
        margin = best[0] - second if second is not None else None
        diag['best_candidate_for_diagnostics_only'] = {'center_px': list(best[1:3]), 'raster_px': best[3]}
        # Never publish a usable center on unknown, even though diagnostics retain
        # the best candidate for debugging false positives and lack of coverage.
        if best[0] < cfg.min_score:
            return self._unknown('insufficient_glyph_evidence', diag, best[0], second, margin)
        if second is None:
            return self._unknown('insufficient_ambiguity_coverage', diag, best[0], second, margin)
        if margin < cfg.min_distinct_margin:
            return self._unknown('ambiguous_distinct_glyphs', diag, best[0], second, margin)
        near = [r for r in fine if r[0] >= best[0]-cfg.near_peak_drop and
                math.hypot(r[1]-best[1], r[2]-best[2]) <= max(5, best[3]/4)]
        # Pixel quantization and competing nearby raster fits. This is an
        # empirical support envelope, not statistical coverage or global error.
        bounds = (min(r[1] for r in near)-.5, min(r[2] for r in near)-.5,
                  max(r[1] for r in near)+.5, max(r[2] for r in near)+.5)
        uncertainty = max(1.0, max(math.hypot(x-best[1], y-best[2]) for x in (bounds[0],bounds[2]) for y in (bounds[1],bounds[3])))
        return GateAnchor('located', 'glyph_observed', best[1:3], uncertainty, bounds,
                          (best[3], best[3]), (min(r[3] for r in near), max(r[3] for r in near)),
                          best[0], second, margin, self.template_sha256, self.profile_sha256, diag)
