"""Replay only retained crop observations; never reconstruct a full game frame."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from single_map_structure import current_geometry, current_part, observed_corner_chains, contour_graph


def replay(image, reference, origin, scales=(.75, 1., 1.25)):
    source = contour_graph(reference)
    source_chains = [dict(points=np.array([source[j]['point'] for j in (n['previous'], i, n['next'])]),
                          rays=n['rays']) for i, n in enumerate(source)
                     if all(source[j]['valid'] for j in (n['previous'], i, n['next']))]
    rows = []
    for requested in scales:
        crop = cv2.resize(image, None, fx=requested, fy=requested, interpolation=cv2.INTER_LINEAR)
        h, w = crop.shape[:2]
        sx, sy = w/image.shape[1], h/image.shape[0]
        context = current_geometry(crop, observation_bounds=(0, 0, w, h))
        chains = observed_corner_chains(crop, context) if context is not None else []
        seed_scales = []
        for q in chains:
            for s in source_chains:
                qdelta = q['points'][[0, 2]] - q['points'][1]
                sdelta = s['points'][[0, 2]] - s['points'][1]
                scale = float(np.sum(qdelta*sdelta)/np.sum(sdelta*sdelta))
                if (np.min(np.sum(q['rays']*s['rays'], axis=1)) >= .95 and scale > 0
                        and np.linalg.norm(qdelta-scale*sdelta, axis=1).max() <= 3):
                    seed_scales.append(scale)
        points = np.asarray([c['points'][1] for c in chains[:6]]).reshape(-1, 2)
        # OpenCV resize samples pixel centers: invert that transform before
        # adding the original saved-crop offset in client pixels.
        mapped = (points + .5) / [sx, sy] - .5 + origin
        rows.append(dict(requestedScale=requested, actualScaleXY=[sx, sy], cropShapeHW=[h, w],
            cropLocalObservationBounds=list(context['observationBounds']) if context else None,
            queryPixels=int(np.count_nonzero(context['query'])) if context else 0,
            observedEdgePixels=int(np.count_nonzero(current_part(crop, context, 'observed'))) if context else 0,
            queryChains=len(chains), sourceChains=len(source_chains), ratioSeedCount=len(seed_scales),
            ratioSeedScaleRange=[min(seed_scales), max(seed_scales)] if seed_scales else None,
            exampleCropChainCenters=points.tolist(), exampleClientChainCenters=mapped.tolist()))
    return dict(scope='crop-observation-only', fullFrameReproduction=False, matrixTruthAvailable=False,
        coordinateConvention='opencv-resize-pixel-centers',
        originalCropShapeHW=list(image.shape[:2]), cropOriginClientXY=list(origin), rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--crop', type=int, nargs=4, required=True, metavar=('X', 'Y', 'W', 'H'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image = cv2.imread(str(args.sample))
    if image is None or list(image.shape[:2]) != args.crop[2:][::-1]:
        raise ValueError('crop dimensions do not match saved image')
    with np.load(args.reference, allow_pickle=False) as archive:
        reference = archive['shared_mask']
    result = replay(image, reference, args.crop[:2])
    result.update(sampleSha256=hashlib.sha256(args.sample.read_bytes()).hexdigest(),
                  referenceSha256=hashlib.sha256(args.reference.read_bytes()).hexdigest())
    args.output.write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
