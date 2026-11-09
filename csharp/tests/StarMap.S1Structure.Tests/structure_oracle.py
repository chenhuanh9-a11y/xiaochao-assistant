"""Unchanged production S1 scanner replay; annotations/results never enter the solver."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np


def plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def save(path, value):
    path.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    for key in ("repo", "resources", "index", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--cases", nargs="*")
    args = parser.parse_args()
    repo, output = args.repo.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(repo / "experiments/qt_quick"), str(repo / "app")]
    from client_providers import build_engine_factories
    from client_live_runtime import _AlignmentHost
    from s1_floor_observation import detect_s1_floor
    from s1_structure_selection import StructureCandidateScanner
    if cv2.__version__ != "4.12.0" or np.__version__ != "2.2.6":
        raise RuntimeError("Use original OpenCV 4.12.0 / NumPy 2.2.6 runtime")
    factories = build_engine_factories(args.resources.resolve())
    rows = json.loads(args.index.read_text(encoding="utf-8"))["samples"]
    selected = set(args.cases or ())
    cases = []
    active_fits = None
    original = _AlignmentHost.fit

    def observed_fit(self, *positional, **keywords):
        result = original(self, *positional, **keywords)
        if active_fits is not None:
            active_fits.append(dict(mapId=positional[1], floor=positional[2], result=plain(result)))
        return result

    # Passive observer: calls the original fit with identical inputs, returns
    # the original object, and supplies no prior, map shortlist or ground truth.
    _AlignmentHost.fit = observed_fit
    try:
        for row in rows:
            if row["season"] != "s1" or selected and row["id"] not in selected:
                continue
            if datetime.fromisoformat(row["capturedAt"]).astimezone(timezone.utc) < datetime(2026, 11, 5, tzinfo=timezone.utc):
                raise ValueError("Pre-2026-11-05 capture rejected")
            frame = cv2.imdecode(np.fromfile(row["image"], np.uint8), cv2.IMREAD_COLOR)
            observation = detect_s1_floor(frame, season="s1", mode=row["mode"])
            floor = observation.get("selectedFloor")
            dest = output / row["id"]
            dest.mkdir()
            save(dest / "observation.json", observation)
            case = dict(id=row["id"], image=row["image"], mode=row["mode"], capturedAt=row["capturedAt"],
                        floor=str(floor) if floor is not None else None, scope="unchanged-original-production-scanner")
            if not observation.get("observedFloorValidated") or floor is None or observation.get("mapOpen") is not True:
                case["status"] = "current-map-or-floor-unresolved"
                cases.append(case)
                save(dest / "input.json", case)
                continue
            engine = factories.get_engine(row["mode"], "s1")
            partials = []
            active_fits = []
            started = time.perf_counter()
            report = engine.qt_s1_structure_scan(frame, floor=str(floor), floor_source="current-frame-verified",
                on_progress=lambda report: partials.append(plain(report)), collect_fit_diagnostics=True)
            case.update(status=report["status"], elapsedSeconds=time.perf_counter() - started,
                        supported=report["supportedMapIds"], checked=report["checkedMapIds"], resourceKey=report["resourceKey"])
            save(dest / "input.json", case)
            save(dest / "report.json", report)
            save(dest / "fits.json", active_fits)
            save(dest / "partials.json", partials)
            save(dest / "rows.json", StructureCandidateScanner.rank_rows(report, engine.pack))
            active_fits = None
            cases.append(case)
            print(json.dumps({k: case[k] for k in ("id", "mode", "floor", "supported", "elapsedSeconds")}), flush=True)
    finally:
        _AlignmentHost.fit = original
    save(output / "index.json", cases)
    save(output / "summary.json", dict(count=len(cases), cases=cases,
        scope="Real >=2026-11-05 capture bytes; full selected mode catalog; no identity truth, pose seed, native input or live acceptance"))


if __name__ == "__main__":
    main()
