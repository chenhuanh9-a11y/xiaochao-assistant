"""Source-independent S1 treasure-room exclusion, shared by both difficulties.

Port of Android SeasonOptionalRoom.kt at aa8712c (double lobe and cleft round
head). Shape, halo, parent join, ambiguity and retained-evidence thresholds
are unchanged. This module supplies neither map identity nor a pose.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
import math

import cv2
import numpy as np


_DOUBLE = ((0, .34), (.13, .13), (.29, .07), (.40, .30), (.56, 0),
           (.76, .02), (1, .25), (1, .75), (.85, .96), (.65, 1),
           (.52, .92), (.40, .71), (.20, .86), (0, .66))
_ROUND = ((0, .34), (.08, .21), (.25, .05), (.48, 0), (.72, .04),
          (.90, .14), (1, .27), (.98, .39), (.94, .50), (.98, .61),
          (1, .73), (.90, .86), (.72, .96), (.48, 1), (.25, .95),
          (.08, .79), (0, .66))


def _probes(outline, rows):
    y, x = np.mgrid[:rows, :32]
    x, y = (x + .5) / 32, (y + .5) / rows
    inside = np.zeros(x.shape, bool)
    previous = outline[-1]
    for point in outline:
        if previous[1] != point[1]:
            inside ^= (((point[1] > y) != (previous[1] > y)) &
                       (x < (previous[0] - point[0]) * (y - point[1]) /
                        (previous[1] - point[1]) + point[0]))
        previous = point
    return inside


_PROBES = (_probes(_DOUBLE, 16), _probes(_ROUND, 32))


@dataclass(frozen=True)
class Core:
    query: np.ndarray
    recovery: np.ndarray | None
    removed_pixels: int = 0
    regions: tuple = ()


@dataclass(frozen=True)
class _Box:
    left: int
    top: int
    right: int
    bottom: int
    score: float
    neck: tuple | None = None

    @property
    def region(self):
        return self.left, self.top, self.right, self.bottom


def _round_int(value):
    # Kotlin roundToInt rounds a tie toward positive infinity.
    return np.floor(value + .5).astype(np.int32)


@lru_cache(maxsize=128)
def _sizes(length, round_head):
    if round_head:
        sizes = [(h, math.floor(h * a + .5), cap)
                 for h in range(max(30, length * 3), 181, 2)
                 for a in (.90, .95, 1.) for cap in (.27, .73)]
    else:
        sizes = [(h, math.floor(h * a + .5), .5)
                 for h in range(max(30, int(length * 1.6)), min(160, int(length * 2.5)) + 1, 2)
                 for a in (1.8, 1.9, 2., 2.1)]
    return np.asarray(sizes, dtype=np.float64).reshape(-1, 3).T


def _matches(query, round_head, cancelled):
    height, width = query.shape
    expected = _PROBES[int(round_head)]
    rows = expected.shape[0]
    expected_count = int(expected.sum())
    matches = []
    for turn in range(4):
        view = np.rot90(query != 0, -turn)
        h, w = view.shape

        def box(left, top, right, bottom, score, neck=None):
            def point(x, y):
                return ((x, y), (y, height - 1 - x),
                        (width - 1 - x, height - 1 - y), (width - 1 - y, x))[turn]
            ax, ay = point(left, top)
            bx, by = point(right - 1, bottom - 1)
            return _Box(min(ax, bx), min(ay, by), max(ax, bx) + 1, max(ay, by) + 1, score, neck)

        edge = view[:, :-1] & ~view[:, 1:]
        pad = 3 if round_head else 5
        for x in np.flatnonzero(edge[pad:h-pad].any(axis=0)):
            x = int(x)
            if cancelled and cancelled():
                return []
            if not ((32 if round_head else 8) <= x < w - pad):
                continue
            changes = np.diff(np.pad(edge[pad:h-pad, x].astype(np.int8), (1, 1)))
            for start, end in zip(np.flatnonzero(changes == 1) + pad,
                                  np.flatnonzero(changes == -1) + pad):
                length = int(end - start)
                if not ((2 <= length <= 32) if round_head else (12 <= length <= 90)):
                    continue
                bh, bw, cap = _sizes(length, round_head)
                bh, bw = bh.astype(np.int32), bw.astype(np.int32)
                top = _round_int((start + end - 1) / 2. - bh * cap)
                left = x - bw + 1
                margin = np.maximum(2, bh // 60) if round_head else np.maximum(3, bh // 12)
                legal = ((left >= bh // 2 + margin) if round_head else (left >= margin + 3))
                legal &= (top >= margin) & (top + bh + margin < h) & (x + margin < w)
                bh, bw, top, left, margin = [a[legal] for a in (bh, bw, top, left, margin)]
                if not len(bh):
                    continue
                if round_head:
                    def at(px, py):
                        return view[top + _round_int(py * (bh - 1)), left + _round_int(px * (bw - 1))]
                    cheap = (at(.5, .5) & at(.5, .15) & at(.5, .85) & ~at(.975, .5) & at(.90, .5)
                             & ~at(.04, .04) & ~at(.96, .04) & ~at(.04, .96) & ~at(.96, .96))
                    bh, bw, top, left, margin = [a[cheap] for a in (bh, bw, top, left, margin)]
                if not len(bh):
                    continue
                yy = top[:, None] + ((np.arange(rows) + .5) * bh[:, None] / rows).astype(np.int32)
                xx = left[:, None] + ((np.arange(32) + .5) * bw[:, None] / 32).astype(np.int32)
                actual = view[yy[:, :, None], xx[:, None, :]]
                both = (actual & expected).sum(axis=(1, 2))
                actual_count = actual.sum(axis=(1, 2))
                union = expected_count + actual_count - both
                rates = (.96, .965, .94) if round_head else (.90, .95, .89)
                passing = (both >= expected_count * rates[0]) & (both >= actual_count * rates[1]) & (both >= union * rates[2])
                for i in np.flatnonzero(passing):
                    hh, ll, tt, mm = (int(a[i]) for a in (bh, left, top, margin))
                    halo = view[tt-mm:tt+hh+mm, ll-mm:x+mm+1]
                    attachment = halo[:, 0].copy()
                    neck_start, neck_end = mm + int(hh * .30), mm + int(hh * .70) + 1
                    neck_count = int(attachment[neck_start:neck_end].sum())
                    attachment[neck_start:neck_end] = False
                    if halo[0].any() or halo[-1].any() or halo[:, -1].any() or attachment.any():
                        continue
                    score = float(both[i] / union[i])
                    if not round_head:
                        if hh * .15 <= neck_count <= hh * .50:
                            matches.append(box(ll-mm, tt-mm, x+mm+1, tt+hh+mm, score))
                        continue
                    neck_x = ll - mm
                    occupied = np.flatnonzero(view[tt:tt+hh, neck_x]) + tt
                    if not len(occupied):
                        continue
                    nt, nb = int(occupied[0]), int(occupied[-1]) + 1
                    nh = nb - nt
                    if len(occupied) != nh or not hh * .18 <= nh <= hh * .40:
                        continue
                    middle = (nt + nb - 1) // 2
                    neck_top, neck_bottom, stop, parent = nt, nb, neck_x, False
                    while stop > ll - hh // 2 and stop > mm:
                        next_x = stop - 1
                        if not view[middle, next_x]:
                            break
                        a, b = middle, middle + 1
                        while a > tt-mm and view[a-1, next_x]:
                            a -= 1
                        while b < tt+hh+mm and view[b, next_x]:
                            b += 1
                        span = b - a
                        if span > hh * .42:
                            parent = span >= nh * 1.35
                            break
                        if (span < hh * .18 or a <= tt-mm or b >= tt+hh+mm
                                or view[tt-mm:a, next_x].any() or view[b:tt+hh+mm, next_x].any()):
                            break
                        neck_top, neck_bottom = min(neck_top, a), max(neck_bottom, b)
                        stop -= 1
                    if not parent or ll-stop < hh * .12 or stop <= ll-hh//2 or stop <= mm:
                        continue
                    neck = box(stop, neck_top, ll-mm, neck_bottom, score).region
                    matches.append(box(ll-mm, tt-mm, x+mm+1, tt+hh+mm, score, neck))
    return matches


def core(query, recovery=None, *, cancelled=None):
    """Return immutable-input masks; ambiguous/incomplete/ordinary rooms stay intact."""
    unchanged = Core(query, recovery)
    if (not isinstance(query, np.ndarray) or query.ndim != 2 or not query.size
            or recovery is not None and recovery.shape != query.shape
            or cancelled and cancelled()):
        return unchanged
    matches = _matches(query, False, cancelled) + _matches(query, True, cancelled)
    if not matches or cancelled and cancelled():
        return unchanged
    best = max(matches, key=lambda b: b.score)
    for candidate in matches:
        overlap = max(0, min(candidate.right, best.right) - max(candidate.left, best.left)) * max(
            0, min(candidate.bottom, best.bottom) - max(candidate.top, best.top))
        if overlap < min((candidate.right-candidate.left) * (candidate.bottom-candidate.top),
                         (best.right-best.left) * (best.bottom-best.top)) * .8:
            return unchanged
    regions = (best.region,) + ((best.neck,) if best.neck is not None else ())
    removed = sum(int(np.count_nonzero(query[t:b, l:r])) for l, t, r, b in regions)
    if not removed or removed * (4 if best.neck is not None else 5) > np.count_nonzero(query):
        return unchanged

    def exclude(mask):
        result = mask.copy()
        for l, t, r, b in regions:
            result[t:b, l:r] = 0
        return result

    return Core(exclude(query), exclude(recovery) if recovery is not None else None, removed, regions)


class Observation:
    """One synchronous frame/scan; only bounds-keyed rectangles are cached."""
    def __init__(self, frame):
        self.frame = frame
        self._regions = {}
        self._floor = None
        self.removed_pixels = 0

    def _silhouette(self, bounds):
        # Android's neutral body/lightness evidence is used only to recognize
        # the appendage. PC geometry and its original color/edge validators
        # continue to consume their own masks. Measure the backdrop on the
        # capture, never on a candidate or its projected reference.
        if self._floor is None:
            high, low = self.frame.max(axis=2), self.frame.min(axis=2)
            light = (high.astype(np.uint16) + low) // 2
            histogram = np.bincount(light.ravel(), minlength=256)
            total = light.size
            initial = max(38, int(np.searchsorted(histogram.cumsum(), (total+1)//2)) + 20)
            background = int(histogram[:initial].sum())
            floor = initial
            if background and background * 4 >= total * 3:
                floor = max(initial, int(np.searchsorted(histogram[:initial].cumsum(), (background*9+9)//10)) + 20)
            self._floor = floor
        x0, y0, x1, y1 = bounds
        patch = self.frame[y0:y1, x0:x1]
        rh, rw = patch.shape[:2]
        # Bound either client orientation without stretching the silhouette.
        scale = min(1., 960. / max(rw, rh))
        sw, sh = max(1, math.floor(rw * scale + .5)), max(1, math.floor(rh * scale + .5))
        if (sw, sh) != (rw, rh):
            patch = cv2.resize(patch, (sw, sh), interpolation=cv2.INTER_NEAREST)
        high, low = patch.max(axis=2), patch.min(axis=2)
        light = (high.astype(np.uint16) + low) // 2
        raster = ((light >= self._floor) & (light <= 178) & (high-low <= 72)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(raster, connectivity=4)
        if count > 1:
            raster = (labels == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
        return raster

    def filter(self, mask, bounds, cancelled):
        bounds = tuple(map(int, bounds))
        x0, y0, x1, y1 = bounds
        h, w = self.frame.shape[:2]
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise ValueError('optional-room-bounds-outside-frame')
        full = mask.shape == (h, w)
        if not full and mask.shape != (y1-y0, x1-x0):
            raise ValueError('optional-room-mask-context-mismatch')
        if bounds not in self._regions:
            raster = self._silhouette(bounds)
            sh, sw = raster.shape
            rh, rw = y1-y0, x1-x0
            prepared = core(raster, cancelled=cancelled)
            if cancelled and cancelled():
                return mask
            regions = tuple((x0+math.ceil(l*rw/sw), y0+math.ceil(t*rh/sh),
                             x0+math.ceil(r*rw/sw), y0+math.ceil(b*rh/sh))
                            for l, t, r, b in prepared.regions)
            self._regions[bounds] = regions
        regions = self._regions[bounds]
        if not regions:
            return mask
        result = mask.copy()
        ox, oy = (0, 0) if full else (x0, y0)
        for l, t, r, b in regions:
            result[max(t, y0)-oy:min(b, y1)-oy, max(l, x0)-ox:min(r, x1)-ox] = 0
        self.removed_pixels = max(self.removed_pixels, int(np.count_nonzero(mask)) - int(np.count_nonzero(result)))
        return result


_CURRENT = ContextVar('season_optional_room_observation', default=None)


@contextmanager
def observation_scope(frame, *, enabled, observation=None, cancelled=None):
    """Bind exclusion to the exact S1 frame; reset on return, error or cancellation."""
    active = _CURRENT.get()
    owner = None
    if enabled:
        owner = observation or (active[0] if active is not None and active[0].frame is frame else Observation(frame))
        if owner.frame is not frame:
            raise ValueError('optional-room-frame-context-mismatch')
    token = _CURRENT.set((owner, cancelled) if owner is not None else None)
    try:
        yield owner
    finally:
        _CURRENT.reset(token)


def filter_query(frame, mask, bounds):
    active = _CURRENT.get()
    if active is None or active[0].frame is not frame:
        return mask
    return active[0].filter(mask, bounds, active[1])
