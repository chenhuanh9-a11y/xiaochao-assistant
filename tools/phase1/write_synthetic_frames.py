"""Write synthetic recorded frames for the phase-1 grid slice.

The frames are geometric fixtures, not game captures. They contain no window
titles, account names, or other programs' data. Re-running this script
recreates csharp/fixtures from the constants below.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "csharp" / "fixtures"


def distance_field(height: int, width: int) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    values = np.abs(xx - width // 3) + np.abs(yy - height // 4)
    return np.minimum(values, 65535).astype("<u2")


def rectangle_perimeter(height: int, width: int, top: int, left: int, bottom: int, right: int) -> np.ndarray:
    edge = np.zeros((height, width), np.uint8)
    edge[top, left:right + 1] = 1
    edge[bottom, left:right + 1] = 1
    edge[top:bottom + 1, left] = 1
    edge[top:bottom + 1, right] = 1
    return edge


def write_frame(name: str, edge: np.ndarray, distance: np.ndarray, scale: float, prior: dict | None) -> None:
    directory = FIXTURES / name
    directory.mkdir(parents=True, exist_ok=True)
    edge.astype(np.uint8).tofile(directory / "edge.u8")
    np.ascontiguousarray(distance.astype("<u2")).tofile(directory / "distance.u16")
    manifest = {
        "format": "xingtu-recorded-frame-v1",
        "id": name,
        "synthetic": True,
        "note": "Synthetic geometry for the phase-1 grid slice. Not a game capture.",
        "edgeWidth": int(edge.shape[1]),
        "edgeHeight": int(edge.shape[0]),
        "preparedWidth": int(distance.shape[1]),
        "preparedHeight": int(distance.shape[0]),
        "scale": scale,
        "prior": prior,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(name, "edge", edge.shape, "nonzero", int(np.count_nonzero(edge)), "distance", distance.shape)


def global_translation_count(points: np.ndarray, prepared_shape: tuple[int, int], scale: float) -> int:
    reference = points / scale
    median = np.quantile(reference, 0.5, axis=0)
    hi = np.asarray(prepared_shape[::-1]) - median
    stride = max(3.0, 5.0 / scale)
    xs = np.arange(-median[0], hi[0] + stride, stride)
    ys = np.arange(-median[1], hi[1] + stride, stride)
    return int(len(xs) * len(ys))


def main() -> None:
    black = np.zeros((8, 8), np.uint8)
    write_frame("black-frame", black, distance_field(8, 8), 1.0, None)

    prior_edge = rectangle_perimeter(48, 64, 10, 8, 29, 40)
    write_frame("prior-local", prior_edge, distance_field(32, 32), 1.0, {"x": 4.0, "y": -8.0})

    dense = np.ones((96, 96), np.uint8)
    # Importing the real sampler keeps the fixture inside the large-grid branch
    # of _grid (at least 4096 translations, at most 350000).
    import sys
    sys.path.insert(0, str(ROOT / "app"))
    from fast_structure_alignment import sample_edges
    coarse = sample_edges(dense, 96)
    chosen = None
    for side in range(160, 320, 4):
        count = global_translation_count(coarse, (side, side), 2.0)
        if 4096 <= count <= 350000:
            chosen = (side, count)
            break
    if chosen is None:
        raise SystemExit("no prepared size landed in the large-grid branch")
    side, count = chosen
    write_frame("global-large", dense, distance_field(side, side), 2.0, None)
    print("global-large translations", count)


if __name__ == "__main__":
    main()
