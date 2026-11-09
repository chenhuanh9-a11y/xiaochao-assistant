"""Original main ranker on fresh synthetic pixels or provenance-checked full-client recordings."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

import cv2
import numpy as np


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    return value


def save(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(plain(value), f, allow_nan=False, ensure_ascii=False)


def field(record, path):
    for key in path:
        record = record[key]
    return record


def live_input(spec):
    from s0_vertical_controls import observe_s0_map, validated_control_rects
    from s1_floor_observation import detect_s1_floor
    receipt = Path(spec["captureRecord"]).resolve(strict=True)
    if spec.get("line"):
        record = json.loads(receipt.read_text(encoding="utf-8-sig").splitlines()[spec["line"] - 1])
    else:
        record = json.loads(receipt.read_text(encoding="utf-8-sig"))
    captured_text = field(record, spec["timeField"])
    captured = datetime.fromisoformat(captured_text)
    if captured.tzinfo is None or captured.date().isoformat() < "2026-11-05":
        raise ValueError("Capture event predates current floor controls or lacks timezone")
    if field(record, spec["kindField"]) != "live-client-capture":
        raise ValueError("Original event must identify a live client capture")
    image = (receipt.parent / field(record, spec["imageField"])).resolve(strict=True)
    if "cropBounds" in record and record.get("cropProvenance") == "color-component-unvalidated":
        raise ValueError("Component-only crop cannot attest full-client controls")
    frame = cv2.imdecode(np.fromfile(image, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Cannot decode original capture")
    season, mode, floor = spec["season"], spec["mode"], str(spec["floor"])
    if season == "s0":
        opened, read = observe_s0_map(frame, mode, season=season)
        current = (opened.get("detected") is True and opened.get("layoutFamily") == "s0-vertical"
                   and validated_control_rects(read) is not None and str(opened.get("floor")) == floor)
        layout = "s0-vertical"
    else:
        read = detect_s1_floor(frame, season=season, mode=mode)
        current = (read.get("mapOpen") is True and read.get("automaticFloorEligible") is True and
                   read.get("floorSource") == "s1-signed-pixel-column" and str(read.get("selectedFloor")) == floor)
        layout = "s1-signed-pixel-column"
    if not current:
        raise ValueError(f"Current independent floor-control read failed: {read}")
    # datetime is only used for the date/timezone checks. The source can record 100-ns ticks.
    return frame, image, dict(spec=spec, layout=layout, capturedAt=captured_text,
                              pngSha256=hashlib.sha256(image.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser()
    for key in ("repo", "resources", "output"):
        parser.add_argument("--"+key, type=Path, required=True)
    parser.add_argument("--recordings", type=Path, help="JSON array of original capture journal locators")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(args.repo.resolve() / "experiments/qt_quick"), str(args.repo.resolve() / "app")]
    from client_providers import build_engine_factories
    from difficult_entry_identity import entry_corners
    from single_map_structure import explicit_wall_segments
    providers = build_engine_factories(args.resources.resolve())
    cases = []

    def case(name, season, mode, floor, frame, image, origin, *, verification=None, seconds=180):
        engine = providers.get_engine(mode, season)
        entry = engine.entry_identity
        context = entry.geometry(frame)
        measured = entry_corners(frame, context) if context else []
        corners = [{k: n[k] for k in ("point", "rays", "lengths")} for n in measured]
        allowed, gate = engine.front_gate_prefilter.select(frame, floor, entry.geometry)
        del_keys = ("observationMs", "preparationAndObservationMs")
        for key in del_keys:
            gate.pop(key, None)
        rows, diagnostics = entry.rank(frame, floor, budget_seconds=seconds, candidate_map_ids=allowed)
        diagnostics.pop("elapsedMs", None)
        engine._apply_shared_entry_inventory(rows, diagnostics, engine._shared_entry_inventory(floor))
        if any(r["identityVerified"] for r in rows):
            _, passport = engine.side_entry_identity.rank_entry(frame, floor, budget_seconds=seconds,
                                                                candidate_map_ids=allowed)
            for r in rows:
                if r["identityVerified"]:
                    proposed = dict(r, floor=floor, profileId=engine.identity_profile_for_map(r["mapId"]),
                                    matrixSourceToClient=r["verification"]["matrixSourceToClient"])
                    r["entryCrossCheck"] = engine.side_entry_identity.check_entry_evidence(frame, proposed, passport)
                    if r["entryCrossCheck"]["contradicted"]:
                        r["identityVerified"] = r["poseVerified"] = False
        expected = name + "-expected.json"
        save(out / expected, dict(rows=rows, diagnostics=diagnostics, frontGate=gate, corners=corners))
        cases.append(dict(name=name, season=season, mode=mode, floor=floor, image=str(image), origin=origin,
                          capture=verification, seconds=seconds, expected=expected))
        print(name, len(corners), len(rows), diagnostics["reason"], flush=True)

    def synthetic(name, season, mode, floor, frame, seconds=180):
        image = out / (name + ".png")
        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            raise ValueError("PNG encode failed")
        image.write_bytes(encoded.tobytes())
        case(name, season, mode, floor, frame, image, "synthetic", seconds=seconds)

    for season, mode, floor in (("s0","nightmare","1"), ("s0","difficult","1"), ("s1","nightmare","-1")):
        engine = providers.get_engine(mode, season)
        source = next(row for row, *_ in engine.entry_identity.entries if row["floor"] == floor)
        product = engine.qt_shared_provider(engine.identity_profile_for_map(source["mapId"]), source["mapId"], floor)
        mask = product.clean_mask
        # Render the source into a new 1600x1080 BGR canvas. The transform and source map are used
        # only to draw pixels here. The ranker receives the full mode/floor inventory and the PNG.
        h, w = mask.shape
        scale = min(820/w, 550/h)
        affine = np.array([[scale, 0., 740.-w*scale/2.], [0., scale, 565.-h*scale/2.]])
        occupancy = cv2.warpAffine(mask, affine, (1600,1080), flags=cv2.INTER_NEAREST)
        frame = np.repeat(np.where(occupancy[...,None] != 0, 133, 0).astype(np.uint8),3,2)
        synthetic("synthetic-"+season+"-"+mode, season, mode, floor, frame)
    blank = np.zeros((1080,1600,3),np.uint8)
    synthetic("synthetic-black", "s0","nightmare","1",blank)
    synthetic("synthetic-unindexed-floor","s0","nightmare","2",blank)
    synthetic("synthetic-budget","s0","difficult","1",blank,seconds=0)
    for spec in json.loads(args.recordings.read_text(encoding="utf-8-sig")) if args.recordings else []:
        frame, path, proof = live_input(spec)
        case(spec["name"], spec["season"], spec["mode"], str(spec["floor"]), frame, path, "recorded", verification=proof)
    # The split wall removes occupancy on one column. Its two directed faces are measured on adjacent columns.
    clean = np.zeros((96,96),np.uint8); clean[10:86,10:86] = 1
    wall = np.zeros_like(clean); wall[20:76,47] = 1
    supported = cv2.dilate(wall,np.ones((3,3),np.uint8)); supported[64:]=0
    keys = ("axis","sign","normal","lo","hi")
    def spans(obs):
        return [{k:s[k] for k in keys} for s in explicit_wall_segments(clean,wall,obs)]
    affected = dict(old=spans(wall), expanded=spans(supported))
    if affected["old"] == affected["expanded"]:
        raise ValueError("Synthetic separated wall/support inputs failed to exercise the overload.")
    save(out / "wall-expected.json", affected)
    save(out / "index.json", dict(cases=cases, wallExpected="wall-expected.json", numpy=np.__version__,
                                  cv=cv2.__version__, module=str(sys.modules[entry_corners.__module__].__file__)))


if __name__ == "__main__":
    main()
