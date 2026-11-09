"""Compare the shipping Python grid slice with the C# port.

Strict comparison: canonical structure bytes and point bytes, SHA-256.
No score tolerance and no threshold edits. NumPy must be 2.2.6, the version
pinned in requirements.txt. sample_edges and _grid are imported from
app/fast_structure_alignment.py. OpenCV is imported by that module and is not
called on this slice.

Usage, from the repository root, after a Release build:

    dotnet build csharp/StarMap.Linux.sln -c Release
    python3 tools/phase1/compare_grid_slice.py
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

if np.__version__ != "2.2.6":
    sys.exit(f"numpy {np.__version__} != 2.2.6 (requirements.txt)")

import fast_structure_alignment as fast  # noqa: E402

DLL = ROOT / "csharp" / "src" / "StarMap.Cli" / "bin" / "Release" / "net10.0" / "StarMap.Cli.dll"
FIXTURES = ("black-frame", "prior-local", "global-large")
FINE_LIMIT = 300
COARSE_LIMIT = 96
MINIMUM_POINTS = 40


def structure_bytes(reason: str, gate_count: int, points: np.ndarray, rows: list) -> bytes:
    reason_code = 1 if reason == "fast-insufficient-edges" else 2
    buffer = bytearray()
    buffer += b"XGRID1\x00\x01"
    buffer += struct.pack("<iiii", reason_code, int(gate_count), int(len(points)), int(len(rows)))
    for x, y in np.asarray(points, dtype=np.float32).reshape(-1, 2):
        buffer += struct.pack("<ff", np.float32(x), np.float32(y))
    for score, scale, translation in rows:
        buffer += struct.pack(
            "<dddd", float(score), float(scale), float(translation[0]), float(translation[1]))
    return bytes(buffer)


def python_slice(frame_dir: Path, cancel: bool) -> dict:
    manifest = json.loads((frame_dir / "manifest.json").read_text(encoding="utf-8"))
    edge = np.fromfile(frame_dir / "edge.u8", dtype=np.uint8).reshape(
        manifest["edgeHeight"], manifest["edgeWidth"])
    distance = np.fromfile(frame_dir / "distance.u16", dtype="<u2").reshape(
        manifest["preparedHeight"], manifest["preparedWidth"])
    fine = fast.sample_edges(edge, FINE_LIMIT)
    if len(fine) < MINIMUM_POINTS:
        points = fine
        rows: list = []
        reason = "fast-insufficient-edges"
        cancelled = False
    else:
        points = fast.sample_edges(edge, COARSE_LIMIT)
        prior = None
        if manifest.get("prior") is not None:
            prior = np.zeros((2, 3), np.float64)
            prior[0, 2] = float(manifest["prior"]["x"])
            prior[1, 2] = float(manifest["prior"]["y"])
        rows = fast._grid(
            points,
            fast.PreparedStructure(
                distance,
                np.zeros((0, 3, 2), np.float32),
                np.zeros((0, 2, 2), np.float32),
                0.0,
                distance.shape,
            ),
            float(manifest["scale"]),
            prior,
            (lambda: True) if cancel else None,
        )
        reason = "grid"
        cancelled = bool(cancel and len(rows) == 0)
    payload = structure_bytes(reason, len(fine), points, rows)
    point_bytes = b"".join(
        struct.pack("<ff", np.float32(x), np.float32(y))
        for x, y in np.asarray(points, dtype=np.float32).reshape(-1, 2))
    return {
        "reason": reason,
        "gatePointCount": int(len(fine)),
        "coarsePointCount": 0 if reason != "grid" else int(len(points)),
        "rowCount": len(rows),
        "cancelled": cancelled,
        "pointsSha256": hashlib.sha256(point_bytes).hexdigest(),
        "structureSha256": hashlib.sha256(payload).hexdigest(),
        "rows": [(float(score), float(scale), float(translation[0]), float(translation[1]))
                 for score, scale, translation in rows],
    }


def csharp_slice(frame_dir: Path, cancel: bool) -> dict:
    if not DLL.is_file():
        sys.exit(f"missing {DLL.name}; build csharp/StarMap.Linux.sln -c Release first")
    command = ["dotnet", str(DLL), "--frame", str(frame_dir)]
    if cancel:
        command.append("--cancel")
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        sys.exit(f"csharp exit {completed.returncode}")
    if str(frame_dir.resolve()) in completed.stdout or str(frame_dir) in completed.stdout:
        sys.exit("csharp stdout included the frame path")
    payload = json.loads(completed.stdout)
    if "\\" in str(payload.get("frameId", "")) or "/" in str(payload.get("frameId", "")):
        sys.exit("csharp printed a path as frameId")
    return payload


def compare_one(name: str, cancel: bool) -> None:
    directory = ROOT / "csharp" / "fixtures" / name
    python = python_slice(directory, cancel)
    csharp = csharp_slice(directory, cancel)
    fields = ("reason", "gatePointCount", "coarsePointCount", "rowCount",
              "pointsSha256", "structureSha256", "cancelled")
    mismatches = [field for field in fields if python[field] != csharp[field]]
    label = name + (" --cancel" if cancel else "")
    if mismatches:
        print("FAIL", label, mismatches)
        print(" python", {key: python[key] for key in fields})
        print(" csharp", {key: csharp[key] for key in fields})
        print(" python rows", python["rows"][:4])
        print(" csharp rows", csharp["rows"][:4])
        sys.exit(1)
    print("ok", label, "rows", python["rowCount"], "structure", python["structureSha256"])


def main() -> None:
    print("numpy", np.__version__)
    print("command: dotnet", DLL)
    for name in FIXTURES:
        compare_one(name, False)
    compare_one("prior-local", True)
    print("parity-ok", len(FIXTURES) + 1, "cases")


if __name__ == "__main__":
    main()
