"""Replay the current Python runtime observation on recorded client pixels and new drawings."""
import argparse
import ast
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import cv2
import numpy as np


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")


def capture(record):
    record = record.resolve(strict=True)
    event = json.loads(record.read_text(encoding="utf-8-sig"))
    stamp = event["capturedAt"]
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if (when.utcoffset() is None or when.date() < date(2026, 11, 5)
            or event.get("evidenceKind") != "live-client-capture"
            or event.get("cropProvenance") == "color-component-unvalidated"
            or not event.get("imageScope", "").startswith("Full physical target client area only")):
        raise ValueError("A dated, full-client capture event is required")
    image = (record.parent / event["samplePath"]).resolve(strict=True)
    if not image.is_relative_to(record.parent):
        raise ValueError("Capture path outside the record directory")
    encoded = image.read_bytes()
    frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    meta = event["Metadata"]
    if frame is None or meta["Before"] != meta["After"] or tuple(frame.shape[1::-1]) != (
            meta["Before"]["Width"], meta["Before"]["Height"]):
        raise ValueError("Capture geometry disagrees with original pixels")
    return frame, image, dict(record=str(record), capturedAt=stamp, pngSha256=hashlib.sha256(encoded).hexdigest())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--capture-record", required=True, type=Path)
    p.add_argument("--season", default="s0", choices=["s0", "s1"])
    p.add_argument("--mode", default="nightmare", choices=["nightmare", "difficult", "difficulty2"])
    args = p.parse_args()
    repo = args.repo.resolve(strict=True)
    sys.path[:0] = [str(repo / "app"), str(repo / "experiments" / "qt_quick"), str(repo / "tests")]
    import client_live_runtime
    import floor_contract
    import floor_control_region
    import map_interface_observation
    import s0_vertical_controls
    import s1_floor_observation
    from test_s1_floor_observation import mathematical_frame, draw_column
    from test_s1_floor_localization import centered_hud, relocated
    from test_s1_preparation_robustness import bright_frame
    # The S0 drawing lives in a Qt editor test module. Use its source function and SIZE
    # declaration directly so a synthetic PNG does not require the editor's Qt bindings.
    drawing_source = repo / "experiments" / "qt_quick" / "test_client_floor_region.py"
    tree = ast.parse(drawing_source.read_text(encoding="utf-8"), filename=str(drawing_source))
    drawing = [n for n in tree.body if
        (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SIZE" for t in n.targets))
        or (isinstance(n, ast.FunctionDef) and n.name == "column")]
    if len(drawing) != 2:
        raise RuntimeError("Could not identify the current S0 synthetic column producer")
    namespace = dict(cv2=cv2, np=np)
    exec(compile(ast.Module(body=drawing, type_ignores=[]), str(drawing_source), "exec"), namespace)
    column = namespace["column"]

    originals = {m.__name__: str(Path(m.__file__).resolve()) for m in (client_live_runtime, floor_contract,
        floor_control_region, map_interface_observation, s0_vertical_controls, s1_floor_observation)}
    if not all(Path(path).is_relative_to(repo) for path in originals.values()):
        raise RuntimeError("An original runtime module came from another checkout")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Evidence output must be new or empty")
    cases = []

    def observe(name, frame, season="s0", mode="nightmare", use="Unavailable", settings=None,
                recorded_image=None, proof=None):
        settings = settings or {}
        # Real _observe uses the designated engine capability and writes session/diagnostic sinks.
        # The test supplies capability selection only; all pixel readers, map gates and binding are original.
        host = SimpleNamespace(_season=season, _mode=mode, _settings=settings, _diagnostics=None,
            _manual_alignment=use == "ManualMap",
            _s1_floor_observer_available=lambda engine: use == "ManualMap",
            _s1_candidate_available=lambda engine: use == "StructureCandidate",
            _floor_contract=lambda: floor_contract.floor_contract(mode, season=season),
            _remember_control_observation=lambda *args: None,
            _recognition_diagnostic=lambda *args, **kwargs: None)
        result, floor, read = client_live_runtime.LiveRuntime._observe(host, SimpleNamespace(
            pack=SimpleNamespace(mode=mode)), frame)
        expected = dict(mapObservation=result, floorId=floor, floorRead=read,
            mapOpen=result.get("mapOpen"), selectedFloor=read.get("selectedFloor"), confidence=read.get("confidence"),
            automaticEligible=read.get("automaticFloorEligible", result.get("mapOpen") is True and
                read.get("controlGeometryVerified") is True and read.get("selectedFloor") is not None))
        if recorded_image is None:
            image = out / (name + ".png")
            image.write_bytes(cv2.imencode(".png", frame)[1].tobytes())
        else:
            image = recorded_image
        save(out / (name + ".expected.json"), expected)
        case = dict(name=name, image=str(image), origin="recorded" if proof else "synthetic",
            season=season, mode=mode, use=use, settings=settings, expected=name + ".expected.json")
        if proof:
            case["capture"] = proof
        cases.append(case)

    frame, image, proof = capture(args.capture_record)
    observe("recorded-current-client", frame, args.season, args.mode,
        use="StructureCandidate" if args.season == "s1" else "Unavailable", recorded_image=image, proof=proof)

    def s0(order, selected, **kw):
        img = column(order, selected, **kw)
        img[70:122, 1800:1870] = 150
        img[670:740, 1820:1870] = 150
        img[930:990, 1600:1700] = 150
        return img

    for mode, order in (("nightmare", (2, 1, -1)), ("difficult", (2, 1))):
        for value in order:
            observe(f"synthetic-s0-{mode}-{value}", s0(order, value), mode=mode)
    observe("synthetic-s0-reversed", s0((-1, 1, 2), 1))
    observe("synthetic-s0-unsigned", s0((2, 1, 1), 2))
    observe("synthetic-s0-black", np.zeros((750, 1334, 3), np.uint8))
    moved = s0((2, 1), 1, center_x=1340)
    observe("synthetic-s0-auto-expanded", moved, mode="difficult")
    observe("synthetic-s0-fixed", moved, mode="difficult", settings={"floorControlMode": "fixed"})
    observe("synthetic-s0-region", moved, mode="difficult", settings={"floorControlMode": "region",
        "floorControlRegion": {"clientSize": [1920, 1080], "rect": [.62, .25, .15, .27]}})
    fixed = s0((2, 1), 1)
    box = [.62, .25, .15, .27]
    for suffix, pixels, region in (
        ("scalar", fixed, "bad"), ("array", fixed, []),
        ("rect-item", fixed, {"rect": [None, .25, .15, .27], "clientSize": [1920, 1080]}),
        ("size-item", fixed, {"rect": box, "clientSize": [[], 1080]}),
        ("fraction-text-size", fixed, {"rect": box, "clientSize": ["1920.9", "1080.1"]}),
        ("numeric-text", moved, {"rect": [".62", "0.25", ".15", ".27"], "clientSize": ["1920", "1080"]}),
        ("fractional-size", moved, {"rect": box, "clientSize": [16.9, 9.99]}),
    ):
        observe("synthetic-s0-raw-region-" + suffix, pixels, mode="difficult",
            settings={"floorControlMode": "region", "floorControlRegion": region})
    observe("synthetic-s0-unknown-mode", fixed, mode="difficulty2")
    observe("synthetic-unknown-season", fixed, season="unregistered")
    observe("synthetic-s1-unknown-mode", fixed, season="s1", mode="unregistered")
    observe("synthetic-s0-dim", cv2.convertScaleAbs(s0((2, 1, -1), -1), alpha=.6))
    for index, value in enumerate((-1, -2, -3, -4)):
        observe(f"synthetic-s1-signed-{value}", mathematical_frame((value,)), "s1",
            ("nightmare", "difficult", "difficulty2", "nightmare")[index],
            ("StructureCandidate", "ManualMap")[index % 2])
    for name, img in (("unsigned", mathematical_frame(missing_sign=-3)), ("unhighlighted", mathematical_frame(())),
                      ("multi-highlight", mathematical_frame((-1, -3))), ("anchors-missing", mathematical_frame(anchors=False)),
                      ("shapes", bright_frame((-3,))), ("dim", cv2.convertScaleAbs(mathematical_frame((-4,)), alpha=.65))):
        observe("synthetic-s1-" + name, img, "s1", use="StructureCandidate")
    twice = mathematical_frame((-2,))
    draw_column(twice, selected=(-2,), offset=-90)
    observe("synthetic-s1-two-columns", twice, "s1", use="ManualMap")
    observe("synthetic-s1-relocated-column-alone", relocated(scale=.75), "s1", use="StructureCandidate")
    observe("synthetic-s1-centered-ultrawide", centered_hud((2560, 1080), -3), "s1", use="StructureCandidate")
    observe("synthetic-s1-unavailable", mathematical_frame(), "s1")
    save(out / "index.json", dict(originalModules=originals, cv2=cv2.__version__, numpy=np.__version__, cases=cases))


if __name__ == "__main__":
    main()
