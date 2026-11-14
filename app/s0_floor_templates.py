"""Read the shipped S0 digit exemplars; never regenerate them on a user's CPU.

The font-only producer is tools/generate_s0_floor_templates.py. Both the client
reference and resource exporter read this same fixed numerical asset.
"""
from functools import lru_cache
from pathlib import Path
import sys

import cv2
import numpy as np

HEIGHT, WIDTH, DIGIT_HEIGHT = 64, 96, 40
TEMPLATE_FAMILY = "synthetic-hershey-plain-v1"
LABELS = (1, 2, -1, -2, 3, 4, 7, 9, -3, -4, -7, -9)


def _center_ink(ink):
    ys, xs = np.where(ink > 20)
    canvas = np.zeros((HEIGHT, WIDTH), np.float32)
    if not len(xs):
        return canvas
    crop = ink[ys.min():ys.max()+1, xs.min():xs.max()+1]
    y, x = (HEIGHT-crop.shape[0])//2, (WIDTH-crop.shape[1])//2
    if min(x, y) >= 0:
        canvas[y:y+crop.shape[0], x:x+crop.shape[1]] = crop
    return canvas


def make_template(label, aspect, thickness, shear, sign_width=.45, sign_gap=.20):
    """Font-only development fixture, also used by the explicit asset producer."""
    big = np.zeros((128, 128), np.uint8)
    cv2.putText(big, str(abs(label)), (16,96), cv2.FONT_HERSHEY_PLAIN,
                3., 255, thickness*3, cv2.LINE_AA)
    ys, xs = np.where(big > 20)
    crop = big[ys.min():ys.max()+1, xs.min():xs.max()+1]
    glyph = cv2.resize(crop, (max(4,round(DIGIT_HEIGHT*aspect)),DIGIT_HEIGHT),
                       interpolation=cv2.INTER_AREA)
    pad = 16
    warped = np.zeros((DIGIT_HEIGHT+pad*2, glyph.shape[1]+pad*2),np.uint8)
    warped[pad:pad+DIGIT_HEIGHT,pad:pad+glyph.shape[1]] = glyph
    warped = cv2.warpAffine(warped, np.float32([[1,shear,-shear*(pad+DIGIT_HEIGHT/2)],
        [0,1,0]]), (warped.shape[1],warped.shape[0]), flags=cv2.INTER_LINEAR)
    image = cv2.GaussianBlur(_center_ink(warped),(0,0),.5)
    out = cv2.warpAffine(image,np.float32([[1,0,12],[0,1,0]]),(WIDTH,HEIGHT))
    if label < 0:
        ys, xs = np.where(out > 40)
        sw, gap = round(DIGIT_HEIGHT*sign_width), round(DIGIT_HEIGHT*sign_gap)
        left = int(xs.min())-gap-sw
        cv2.line(out,(left,HEIGHT//2),(left+sw-1,HEIGHT//2),255,3,cv2.LINE_AA)
    return out


@lru_cache(maxsize=1)
def _bank():
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent
    path = root / 'control-data' / 's0-floor-templates-v1.npz'
    # Missing/broken program data is an installation error. Regeneration here
    # would recreate the CPU-dependent reference that made valid packs fail.
    with np.load(path, allow_pickle=False) as source:
        if set(source.files) != {'vectors', 'labels'}:
            raise ValueError('s0-template-resource-invalid')
        bank, label_array = source['vectors'], source['labels']
    expected_labels = np.repeat(np.array(LABELS, dtype=np.int64),
                                [90 if label < 0 else 30 for label in LABELS])
    if (bank.dtype != np.float32 or bank.shape != (len(expected_labels), HEIGHT * WIDTH)
            or not bank.flags.c_contiguous or not np.isfinite(bank).all()
            or label_array.dtype != np.int64
            or not np.array_equal(label_array, expected_labels)):
        raise ValueError('s0-template-resource-invalid')
    bank.flags.writeable = False
    label_array.flags.writeable = False
    return bank, label_array


def classify_label(patch, *, selected):
    """Digit-anchored gray NCC preserves all soft pixels left of the digit.

    Never crop to a detected sign component: blur could make that component
    disappear and silently turn -1 into 1. All classes compete on the same
    original sign-context pixels, including negative and non-S0 alternatives.
    """
    if patch.size == 0:
        return None
    crop = patch.astype(np.float32)
    signed = np.median(crop)-crop if selected else crop-np.median(crop)
    strength = float(np.percentile(signed,99))
    if strength < 12:
        return None
    ink = np.clip(signed/strength*255,0,255)
    stats = cv2.connectedComponentsWithStats((ink>80).astype(np.uint8),8)[2][1:]
    candidates = [s for s in stats if .3*crop.shape[0] < s[3] < .95*crop.shape[0]]
    if len(candidates) != 1:
        return None
    dx,dy,dw,dh,_ = candidates[0]
    # A clipped stroke can still normalize into a convincing numeral. It
    # cannot prove the neighboring row at a pitch inferred from a dimmed
    # highlight, or that partial row can contradict the complete raw column.
    if dx == 0 or dy == 0 or dx+dw >= crop.shape[1] or dy+dh >= crop.shape[0]:
        return None
    scale = DIGIT_HEIGHT/dh
    matrix = np.float32([[scale,0,60-scale*(dx+(dw-1)/2)],
                         [0,scale,31.5-scale*(dy+(dh-1)/2)]])
    image = cv2.warpAffine(ink,matrix,(WIDTH,HEIGHT),flags=cv2.INTER_LINEAR)
    image = cv2.GaussianBlur(image,(0,0),.5)
    vector = image.ravel()-image.mean()
    vector /= max(1e-9,float(np.linalg.norm(vector)))
    bank, labels = _bank()
    # Explicit contraction avoids a large thread pool for these tiny matches.
    matches = np.einsum('ij,j->i',bank,vector,optimize=False)
    scores = {label:float(matches[labels==label].max()) for label in LABELS}
    # A visible thin minus can lose NCC weight after pixel quantization while
    # the numeral remains crisp. Keep its independent same-polarity geometry
    # rather than allowing the stronger unsigned glyph to erase it.
    marks = [row for row in stats if row[0]+row[2] < dx
        and 0 <= dx-(row[0]+row[2]) <= .6*dh
        and .2*dh <= row[2] <= .9*dh and row[3] <= .5*dh
        and abs(row[1]+row[3]/2-(dy+dh/2)) <= .3*dh]
    signs = [row for row in marks if row[3] <= .25*dh and row[2] >= 2*row[3]]
    # A dot or plus beside 1 is not a minus just because its mass improves
    # the -1 NCC. Keep all signed competitors; e.g. -4 must not be relabelled
    # -1 merely because those are the only two S0 row semantics.
    if len(signs) > 1 or len(signs) != len(marks):
        return None
    eligible = tuple(label for label in LABELS if label<0) if signs else LABELS
    best = max(eligible,key=scores.get)
    return dict(label=best, scores=scores, digitAspect=float(dw/dh),
                digitCenterY=float(dy+(dh-1)/2),
                family=TEMPLATE_FAMILY)
