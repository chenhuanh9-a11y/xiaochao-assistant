"""Run the unchanged production side ranker; source map/pose only draw fresh fixture pixels."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

import cv2
import numpy as np


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [plain(v) for v in value]
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    return value


def save(path, value):
    with path.open("x", encoding="utf8") as target:
        json.dump(plain(value), target, ensure_ascii=False, allow_nan=False)


def main():
    p = argparse.ArgumentParser()
    for name in ("repo", "resources", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--recordings", type=Path)
    args = p.parse_args()
    if np.__version__ != "2.2.6":
        raise RuntimeError("Use the original pinned NumPy 2.2.6 runtime")
    repo, resources, out = args.repo.resolve(), args.resources.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(repo / "experiments/qt_quick"), str(repo / "app")]
    from client_providers import build_engine_factories
    from difficult_side_entry_identity import MapLocalEntryIdentity
    providers = build_engine_factories(resources)
    cases = []
    positive_modes = set()

    def case(name, season, mode, floor, frame, image, origin, capture=None, seconds=180):
        engine = providers.get_engine(mode, season)
        locator = engine.side_entry_identity
        # The source selects a mode/provider, not a map or a known pose. Every
        # registered map/floor in that mode competes on these current pixels.
        context, nodes, pairs = locator._observe(frame)
        rows, meta = locator.rank(frame, floor, budget_seconds=seconds)
        for key in ("elapsedMs", "sideOptionalDoorMs"):
            meta.pop(key, None)
        turns = [{k: n[k] for k in ("point", "rays", "lengths", "next")} for n in nodes] if seconds > 0 else []
        expected = name + "-expected.json"
        save(out / expected, dict(rows=rows, diagnostics=meta, turns=turns))
        cases.append(dict(name=name, season=season, mode=mode, floor=floor, image=str(image), origin=origin,
                          capture=capture, seconds=seconds, expected=expected))
        if name.endswith("-whole") and any((r.get("verification") or {}).get("success") for r in rows):
            positive_modes.add((season, mode))
        print(name, len(nodes), len(rows), meta["reason"],
              sum(bool((r.get("verification") or {}).get("success")) for r in rows), flush=True)

    def synthetic(name, season, mode, floor, frame, seconds=180):
        path = out / (name + ".png")
        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            raise ValueError("PNG encoding failed")
        path.write_bytes(encoded.tobytes())
        case(name, season, mode, floor, frame, path, "synthetic", seconds=seconds)

    for season, mode, floor in (("s0", "nightmare", "2"), ("s0", "difficult", "1")):
        engine = providers.get_engine(mode, season)
        locator = engine.side_entry_identity
        # A full native contour, plus a second independently rendered scale,
        # exercises prepared F2 and ordinary difficult search without feeding
        # the generating source identity or pose to either solver.
        source = max((item for item, *_ in locator.entries if item["floor"] == floor), key=lambda item: len(item["turns"]))
        product = engine.qt_shared_provider(engine.identity_profile_for_map(source["mapId"]), source["mapId"], floor)
        for suffix, ratio in (("whole", 1.), ("smaller", .73)):
            mask = product.clean_mask
            h, w = mask.shape
            scale = min(760 / w, 490 / h) * ratio
            matrix = np.array([[scale, 0., 800 - w * scale / 2], [0., scale, 560 - h * scale / 2]])
            occupancy = cv2.warpAffine(mask, matrix, (1600, 1080), flags=cv2.INTER_NEAREST)
            frame = np.repeat(np.where(occupancy[..., None] != 0, 133, 0).astype(np.uint8), 3, 2)
            synthetic("synthetic-" + season + "-" + mode + "-" + suffix, season, mode, floor, frame)
    blank = np.zeros((1080, 1600, 3), np.uint8)
    synthetic("synthetic-black", "s0", "nightmare", "2", blank)
    synthetic("synthetic-budget", "s0", "difficult", "1", blank, seconds=0)
    synthetic("synthetic-unsupported-floor", "s0", "nightmare", "-4", blank)
    synthetic("synthetic-source-gap", "s1", "nightmare", "-1", blank)
    if args.recordings:
        # Reuse the provenance and independent current floor-control reader;
        # it is a fixture admission helper, not a replacement side solver.
        path = repo / "csharp/tests/StarMap.MainEntry.Tests/reference_oracle.py"
        spec = importlib.util.spec_from_file_location("main_fixture_admission", path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        for recording in json.loads(args.recordings.read_text("utf-8-sig")):
            frame, image, capture = helper.live_input(recording)
            case(recording["name"], recording["season"], recording["mode"], str(recording["floor"]),
                 frame, image, "recorded", capture=capture)
    save(out / "index.json", dict(cases=cases, numpy=np.__version__, cv=cv2.__version__,
         module=str(sys.modules[MapLocalEntryIdentity.__module__].__file__),
         scope="Complete original production side search and fixed-pose verification; no runtime/session authorization"))
    if positive_modes != {("s0", "nightmare"), ("s0", "difficult")}:
        raise RuntimeError("Fresh whole-source fixtures did not establish both positive modes; preserve outputs and investigate: "
                           + repr(sorted(positive_modes)))


if __name__ == "__main__":
    main()
