"""Difficult two-button UI observer; independent of map identity/entrance kind.

Locate the recorded two-button controls in client pixels, at candidate UI scales.
Viewport dimensions propose scales; only current glyph evidence selects one.
"""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np


def glyph_mask(image):
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    return (gray >= 155).astype(np.uint8) * 255


class DifficultMapUI:
    def __init__(self, templates: Path):
        with np.load(templates, allow_pickle=False) as data:
            self.one = data["one"].copy()
            self.two = data["two"].copy()
            self.source_size = data["source_size"].tolist()

    def observe(self, frame):
        h, w = frame.shape[:2]
        if min(h, w) < 120:
            return dict(mapOpen=None, floor=None, reason="client-too-small-for-controls",
                        size=[w, h], entryKind="unknown")
        # Width- and height-driven HUDs, and fixed-pixel windowed HUDs. These
        # are search hypotheses, not declarations of supported resolutions.
        scales = sorted({round(w / self.source_size[0], 4),
                         round(h / self.source_size[1], 4), 1.0})
        observations = [self._observe_scale(frame, scale) for scale in scales]
        opened = [r for r in observations if r["mapOpen"] is True]
        if opened:
            floors = {r["floor"] for r in opened if r["floor"] is not None}
            best = max(opened, key=lambda r: sum(g["score"] for g in r["glyphs"]))
            if len(floors) > 1:
                best = dict(best, floor=None, reason="two-floor-scale-ambiguous")
            return best
        uncertain = [r for r in observations if r["mapOpen"] is None]
        return (uncertain or observations)[0]

    def _observe_scale(self, frame, scale):
        h, w = frame.shape[:2]
        # Search a client-relative band, so moving the pair or changing aspect
        # ratio does not move it outside a hard-coded 2560px rectangle.
        x0, y0 = max(round(w * .50), round(w - 1000 * scale)), 0
        x1, y1 = min(round(w * .97), round(w - 150 * scale)), min(h, round(180 * scale))
        if x1 <= x0 or y1 <= y0:
            return dict(mapOpen=None, floor=None, entryKind="unknown",
                        reason="client-too-small-for-controls")
        crop = frame[y0:y1, x0:x1, :3]
        normalized = cv2.resize(crop, (max(1, round(crop.shape[1] / scale)),
                                      max(1, round(crop.shape[0] / scale))),
                                interpolation=cv2.INTER_LINEAR)
        sx, sy = crop.shape[1] / normalized.shape[1], crop.shape[0] / normalized.shape[0]
        region = glyph_mask(normalized)
        if any(region.shape[0] < t.shape[0] or region.shape[1] < t.shape[1]
               for t in (self.one, self.two)):
            return dict(mapOpen=None, floor=None, entryKind="unknown",
                        reason="client-too-small-for-controls")
        matches = []
        for template in (self.one, self.two):
            scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(scores)
            matches.append({"score": float(score),
                "center": [point[0] + template.shape[1] / 2,
                           point[1] + template.shape[0] / 2]})
        a, b = matches
        spacing = b["center"][0] - a["center"][0]
        # The selected floor tile adds a bright texture over the digit.  In
        # reviewed real frames the selected glyph can therefore score much
        # lower than the unselected one (for example 0.44 versus 0.76), while
        # the pair geometry remains stable.  Keep the stronger glyph strict,
        # accept the textured glyph at a bounded lower score, and retain the
        # spacing/alignment gates as the structural evidence.
        valid = (
            max(a["score"], b["score"]) >= .70
            and min(a["score"], b["score"]) >= .35
            and 72 <= spacing <= 108
            and abs(a["center"][1]-b["center"][1]) <= 12
        )
        result = {"mapOpen": bool(valid), "floor": None,
                  "glyphs": [dict(m, center=[x0 + m["center"][0]*sx,
                                              y0 + m["center"][1]*sy]) for m in matches],
                  "controlScale": [sx, sy], "size": [w, h],
                  "entryKind": "unknown", "reason": "two-floor-glyphs-visible" if valid else "two-floor-controls-not-visible"}
        if not valid:
            # During fade-in the controls exist below the binary brightness
            # cutoff. Contrast-normalized gray matches are only a reason to
            # retain uncertainty, never evidence to select a floor or display.
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
            weak = []
            for template in (self.one, self.two):
                _, score, _, point = cv2.minMaxLoc(
                    cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))
                weak.append({"score": float(score), "center": [
                    point[0]+template.shape[1]/2, point[1]+template.shape[0]/2]})
            # Faint or partial control evidence retains uncertainty, never a
            # floor. No prior observation or repeated-frame waiting is used.
            # A lone digit in the central countdown is not HUD evidence.
            # Single-glyph uncertainty stays in the right-side control area;
            # the wider search above requires the complete ordered pair.
            plausible_control = any(
                m["score"] >= .65 and x0 + m["center"][0]*sx >= .65*w
                and 40 <= m["center"][1] <= 175 for m in weak)
            result["weakGlyphs"] = [dict(m, center=[x0+m["center"][0]*sx,
                                                    y0+m["center"][1]*sy]) for m in weak]
            if plausible_control:
                result.update(mapOpen=None, reason="faint-two-floor-controls-uncertain")
            return result
        means = []
        for match in matches:
            cx, cy = (round(v) for v in match["center"])
            patch = normalized[max(0, cy-22):cy+22, max(0, cx-38):max(0, cx-20)]
            if patch.shape[:2] != (44, 18):
                result["reason"] = "two-floor-highlight-clipped"
                return result
            means.append(float(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).mean()))
        difference = abs(means[0]-means[1])
        result["highlightMeans"] = means
        result["highlightDifference"] = difference
        if max(means) >= 65 and difference >= 15:
            result["floor"] = "1" if means[0] > means[1] else "2"
        else:
            result["reason"] = "two-floor-highlight-ambiguous"
        return result
