"""Explicit producer for the program-owned S0 font bank, never a runtime fallback.

Use the production build environment when intentionally replacing this asset.
Commit the reviewed output; ordinary exports and clients only read that output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from s0_floor_templates import BANK_GRID, BANK_ID, GENERATOR_VERSION, TEMPLATE_FAMILY, make_template


def generate_bank():
    vectors, labels = [], []
    for label, aspect, thickness, shear, sw, gap in BANK_GRID:
        vector = make_template(label, aspect, thickness, shear, sw, gap).ravel()
        vector = vector - vector.mean()
        vector /= max(1e-9, float(np.linalg.norm(vector)))
        vectors.append(vector)
        labels.append(label)
    return np.array(vectors, np.float32), np.array(labels, np.int64)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    vectors, labels = generate_bank()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as target:
        np.savez_compressed(target, vectors=vectors, labels=labels)
    with np.load(args.output, allow_pickle=False) as stored:
        if (stored['vectors'].dtype != vectors.dtype
                or stored['vectors'].tobytes() != vectors.tobytes()
                or not np.array_equal(stored['labels'], labels)):
            raise ValueError('s0-template-asset-roundtrip-mismatch')
    print(json.dumps(dict(generator='tools/generate_s0_floor_templates.py',
        family=TEMPLATE_FAMILY, bankId=BANK_ID, generatorVersion=GENERATOR_VERSION,
        python=platform.python_version(), numpy=np.__version__,
        opencv=cv2.__version__, optimized=cv2.useOptimized(),
        shape=list(vectors.shape), dtype=str(vectors.dtype), labelsDtype=str(labels.dtype)),
        ensure_ascii=False))


if __name__ == '__main__':
    main()
