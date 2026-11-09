"""Original production entry strategy, with independent current-control admission for >=2026-11-05 captures."""
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import numpy as np

# The pinned private Python uses an isolated ._pth and does not implicitly
# expose the script directory. Bind the helper to this exact checkout file.
_sibling_spec = importlib.util.spec_from_file_location(
    "side_entry_structure_oracle", Path(__file__).resolve().with_name("reference_oracle.py"))
_sibling = importlib.util.module_from_spec(_sibling_spec)
_sibling_spec.loader.exec_module(_sibling)
plain, save = _sibling.plain, _sibling.save
_fixture_spec = importlib.util.spec_from_file_location(
    "side_entry_synthetic_resources", Path(__file__).resolve().with_name("synthetic_passport_resources.py"))
_fixtures = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixtures)


def clean(value):
    value = plain(value)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if not k.endswith("Ms") and k != "spansMs"}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def main():
    p = argparse.ArgumentParser()
    for key in ("repo", "resources", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--recordings", type=Path)
    p.add_argument("--watch", type=Path, help="Physical G capture directory; sample oldest/middle/newest current open frames without using recognition verdicts")
    p.add_argument("--recording-count", type=int, default=3)
    p.add_argument("--synthetic-only", action="store_true", help="Bounded new typed-fixture probe only; never represents the full production/live replay")
    a = p.parse_args()
    if a.synthetic_only and (a.recordings or a.watch):
        p.error("--synthetic-only cannot omit explicitly requested recordings/watch inputs")
    if np.__version__ != "2.2.6":
        raise RuntimeError("Use original NumPy 2.2.6 runtime")
    repo, resources, out = a.repo.resolve(), a.resources.resolve(), a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(repo / "experiments/qt_quick"), str(repo / "app")]
    from client_providers import build_engine_factories
    from map_identity_engine import verified_entry_candidate, entry_passport_fallback_allowed
    providers = build_engine_factories(resources)
    cases, mismatches, inventory, positives = [], [], [], []
    fixture_locators = {}

    def result(rows, meta, turns=()):
        return dict(rows=rows, diagnostics=meta, turns=turns)

    def case(name, season, mode, floor, frame, image, origin, operation="entry", proof=None, seconds=180, fixture=None, **inputs):
        engine = providers.get_engine(mode, season) if fixture is None else None
        locator = engine.side_entry_identity if engine is not None else fixture_locators[mode]
        profile_for = engine.identity_profile_for_map if engine is not None else lambda mid: fixture["profile"]
        if operation == "entry":
            rows, meta = locator.rank_entry(frame, floor, budget_seconds=seconds)
            actual = dict(**result(rows, meta), consumer=verified_entry_candidate(rows, meta, floor, profile_id=profile_for),
                          fallbackAllowed=entry_passport_fallback_allowed(meta))
            if meta.get("reason") == "entry-first-verified" and actual["consumer"] is None:
                mismatches.append(dict(name=name, observed=meta["reason"], candidates=[dict(mapId=r["mapId"], sourceEntryId=r["sourceEntryId"],
                    localGeometryVerified=r["entryEvidence"]["localGeometryVerified"], success=r["verification"]["success"],
                    policy=r["verification"].get("validationPolicy"), regional=r["verification"].get("regions"),
                    residualVerified=(r["verification"].get("residual") or {}).get("verified")) for r in rows]))
        elif operation == "strategy":
            if engine is None:
                raise ValueError("Synthetic passport worlds exercise original rank_entry, not a substituted engine strategy")
            # Actual engine entry point; no mock/replacement solver. The
            # independent source-family advisory branch runs too but is not
            # granted registered entrance identity or included in this result.
            ranked = engine.rank(frame, floor=floor, limit=len(engine.pack.maps), budget_seconds=seconds)
            evidence = engine.last_difficult_evidence
            if evidence is None:
                raise RuntimeError("Production engine did not produce registered entry evidence")
            path = evidence["recognitionPath"]
            side_rows, side_meta = evidence["tilemapSideCandidates"], evidence["tilemapSide"]
            side = None
            if path["sideProducer"] != "not-run":
                nodes = locator._observe(frame)[1] if path["sideProducer"] == "local-structure" else []
                side = result(side_rows, side_meta, [{k: n[k] for k in ("point", "rays", "lengths", "next")} for n in nodes])
            winner = next((r["difficultEntry"] for r in ranked if r.get("reason") == "side-current-identity-verified"), None)
            gate = engine.front_gate_prefilter.select(frame, floor, engine.entry_identity.geometry)[1]
            actual = dict(passport=result(evidence["entryPassportCandidates"], evidence["entryPassport"]),
                mainRows=evidence["tilemapCandidates"], mainDiagnostics=evidence["tilemapEntry"], side=side, consumer=winner,
                diagnostics={k: path[k] for k in ("passportSelected", "fallbackAllowed", "passportUnresolved", "sideProducer", "sideWinnerMapId")}, frontGate=gate)
        elif operation == "verify-existing":
            actual = locator.verify_existing_pose(frame, inputs["mapId"], floor, inputs["matrix"], observation=inputs.get("observation"))
        elif operation == "verify-candidate":
            candidate = inputs["candidate"].copy()
            candidate["verification"] = locator.verify(frame, candidate)
            actual = dict(candidate=candidate, consumer=verified_entry_candidate([candidate], dict(selectionPolicy="first-verified-entry"),
                floor, profile_id=profile_for))
            if candidate["verification"].get("success") and actual["consumer"] is None:
                mismatches.append(dict(name=name, scope="supplied local-room proof validator seam only; not a rank/search result",
                    mapId=candidate["mapId"], sourceEntryId=candidate["sourceEntryId"], verification=candidate["verification"]))
        else:
            raise ValueError(operation)
        expected = name + "-expected.json"
        save(out / expected, clean(actual))
        cases.append(dict(name=name, season=season, mode=mode, floor=floor, image=str(image), origin=origin, capture=proof,
                          seconds=seconds, operation=operation, expected=expected,
                          **(dict(resourceRoot=str(fixture["root"]), resourceScope="new-synthetic-typed-passport-world") if fixture else {}), **inputs))
        checked = (actual.get("candidate") or {}).get("verification") or actual
        print(json.dumps(dict(name=name, operation=operation, rankReason=(actual.get("diagnostics") or {}).get("reason"),
            verifiedRows=sum(bool(r.get("verification", {}).get("success")) for r in actual.get("rows", [])),
            verificationSuccess=checked.get("success"), consumerSelected=bool(actual.get("consumer")))), flush=True)
        return actual

    def synthetic(name, season, mode, floor, frame, operation="entry", seconds=180, **inputs):
        image = out / (name + ".png")
        ok, png = cv2.imencode(".png", frame)
        if not ok:
            raise ValueError("PNG encode failed")
        image.write_bytes(png.tobytes())
        return case(name, season, mode, floor, frame, image, "synthetic", operation, seconds=seconds, **inputs)

    # The emitting map and transform draw fixture pixels only. Both ranking
    # operations receive their full source inventory and no map/pose answer.
    for mode in (() if a.synthetic_only else ("nightmare", "difficult")):
        engine = providers.get_engine(mode, "s0"); locator = engine.side_entry_identity
        eligible = [i for i in locator.passports if i["family"] == "side" and i["directionKnown"] and len(i["knownWallLines"]) >= 6]
        inventory.append(dict(mode=mode, passports=len(locator.passports), sidePassports=sum(i["family"] == "side" for i in locator.passports),
            eligibleSidePassports=len(eligible), coverage=len(locator.entry_coverage), completeCoverage=sum(bool(c["complete"]) for c in locator.entry_coverage),
            passportError=locator.passport_error, structureGraphs=len(locator.entries), semanticGapCount=len(locator.semantic_gaps),
            semanticGapFields=sorted({field for gap in locator.semantic_gaps for field in gap.get("missing", [])})))
        save(out / ("resource-inventory-" + mode + ".json"), clean(inventory[-1]))
        # Current resources deliberately contain no nightmare passports, and
        # difficult's legacy passports do not remove its native-file error.
        # Keep those exact capability-gap paths in the numerical cases.
        item = eligible[0] if eligible else max((row for row, *_ in locator.entries if row["floor"] == "2"), key=lambda row: len(row["turns"]))
        floor, mid = item["floor"], item["mapId"]
        product = engine.qt_shared_provider(engine.identity_profile_for_map(mid), mid, floor)
        h, w = product.clean_mask.shape
        scale = min(820 / w, 520 / h)
        pose = np.array([[scale, 0., 820 - w * scale / 2], [0., scale, 560 - h * scale / 2], [0., 0., 1.]])
        occupancy = cv2.warpAffine(product.clean_mask, pose[:2], (1600, 1080), flags=cv2.INTER_NEAREST)
        frame = np.repeat(np.where(occupancy[..., None] != 0, 133, 0).astype(np.uint8), 3, 2)
        no_icon = frame.copy()
        if eligible and locator.exit_icon_path is not None:
            raw = json.loads(Path(locator.exit_icon_path).read_text("utf8"))["exitIcon"]
            icon = np.frombuffer(base64.b64decode(raw["pixels"]), np.uint8).reshape(raw["height"], raw["width"])
            anchor = pose[:2, :2] @ item["center"] + pose[:2, 2]
            x, y = np.rint(anchor - [icon.shape[1] / 2, icon.shape[0] / 2]).astype(int)
            if 0 <= x <= frame.shape[1] - icon.shape[1] and 0 <= y <= frame.shape[0] - icon.shape[0]:
                frame[y:y + icon.shape[0], x:x + icon.shape[1]] = icon[..., None]
        synthetic("synthetic-real-resource-gap-" + mode, "s0", mode, floor, frame)
        synthetic("synthetic-strategy-" + mode, "s0", mode, floor, frame, "strategy")
        synthetic("synthetic-existing-" + mode, "s0", mode, floor, no_icon, "verify-existing", mapId=mid, matrix=pose.tolist())
        # A shifted caller-owned pose remains an independently checked
        # negative; it does not enter ranking as a search hint.
        moved = pose.copy(); moved[0, 2] += 44
        synthetic("synthetic-existing-wrong-" + mode, "s0", mode, floor, no_icon, "verify-existing", mapId=mid, matrix=moved.tolist())
    # These are separate, newly authored synthetic worlds. Their complete
    # coverage belongs only to their one registered test map; production
    # resource gaps above remain unchanged and remain in the case inventory.
    for mode in ("nightmare", "difficult"):
        fixture = _fixtures.create(out / ("synthetic-resources-" + mode), resources / "references/difficult-exit-icon.json", mode)
        fixture_locators[mode] = _fixtures.locator(fixture)
        pose = np.array([[.5, 0., 450.], [0., .5, 200.], [0., 0., 1.]])
        occupancy = cv2.warpAffine(fixture["mask"], pose[:2], (1600, 1080), flags=cv2.INTER_NEAREST)
        no_icon = np.repeat(np.where(occupancy[..., None] != 0, 133, 0).astype(np.uint8), 3, 2)
        frame = no_icon.copy()
        icon_data = json.loads(fixture["icon"].read_text("utf8"))["exitIcon"]
        icon = np.frombuffer(base64.b64decode(icon_data["pixels"]), np.uint8).reshape(icon_data["height"], icon_data["width"])
        center = pose[:2, :2] @ fixture["passport"]["center"] + pose[:2, 2]
        x, y = np.rint(center - [icon.shape[1] / 2, icon.shape[0] / 2]).astype(int)
        frame[y:y + icon.shape[0], x:x + icon.shape[1]] = icon[..., None]
        actual = synthetic("synthetic-new-passport-positive-" + mode, "s0", mode, fixture["floor"], frame, fixture=fixture)
        positives.append(dict(name="synthetic-new-passport-positive-" + mode, reason=actual["diagnostics"]["reason"],
            rows=len(actual["rows"]), verifiedRows=sum(bool(r["verification"].get("success")) for r in actual["rows"]),
            roomProofRows=sum(bool(r["entryEvidence"].get("localGeometryVerified")) for r in actual["rows"])))
        mid, floor = fixture["mapId"], fixture["floor"]
        synthetic("synthetic-new-existing-" + mode, "s0", mode, floor, no_icon, "verify-existing", fixture=fixture, mapId=mid, matrix=pose.tolist())
        candidate = dict(mapId=mid, floor=floor, profileId=fixture["profile"], sourceEntryId=fixture["passport"]["sourceEntryId"],
            matrixSourceToClient=pose.tolist(), identityVerified=False,
            entryEvidence=dict(sourceEntryId=fixture["passport"]["sourceEntryId"], localGeometryVerified=True, directionCompatible=True))
        synthetic("synthetic-local-fit-policy-" + mode, "s0", mode, floor, no_icon, "verify-candidate", fixture=fixture, candidate=candidate)
    if not a.synthetic_only:
        blank = np.zeros((1080, 1600, 3), np.uint8)
        synthetic("synthetic-passport-blank", "s0", "nightmare", "2", blank)
        synthetic("synthetic-passport-budget", "s0", "nightmare", "2", blank, seconds=0)
        synthetic("synthetic-passport-s1-gap", "s1", "nightmare", "-1", blank)
    specs = json.loads(a.recordings.read_text("utf-8-sig")) if a.recordings else []
    if a.watch:
        available = []
        for path in sorted(a.watch.resolve().glob("event-*/capture.json")):
            data = json.loads(path.read_text("utf-8-sig")); label = data.get("userLabel") or {}
            obs = (data.get("observations") or {}).get("s0/nightmare") or {}
            if label.get("Season") == "s0" and label.get("Mode") == "nightmare" and obs.get("mapOpen") and obs.get("floorId") is not None:
                available.append((path, str(obs["floorId"])))
        if not available:
            raise ValueError("No current user-labeled open-map watch captures to independently admit")
        indexes = np.unique(np.rint(np.linspace(0, len(available) - 1, min(a.recording_count, len(available)))).astype(int))
        for i in indexes:
            path, floor = available[i]
            specs.append(dict(name="recorded-g-" + path.parent.name, season="s0", mode="nightmare", floor=floor,
                captureRecord=str(path), timeField=["capturedAt"], imageField=["samplePath"], kindField=["evidenceKind"]))
    helper_spec = importlib.util.spec_from_file_location("main_fixture_admission", repo / "csharp/tests/StarMap.MainEntry.Tests/reference_oracle.py")
    helper = importlib.util.module_from_spec(helper_spec); helper_spec.loader.exec_module(helper)
    for spec in specs:
        frame, image, proof = helper.live_input(spec)
        for operation in ("entry", "strategy"):
            case(spec["name"] + "-" + operation, spec["season"], spec["mode"], str(spec["floor"]), frame, image, "recorded", operation, proof)
    save(out / "mismatch-evidence.json", clean(mismatches))
    save(out / "resource-inventory.json", clean(inventory))
    save(out / "positive-fixture-status.json", clean(positives))
    save(out / "recording-specs.json", specs)
    save(out / "index.json", dict(cases=cases, numpy=np.__version__, cv=cv2.__version__,
        module=str(sys.modules["difficult_side_entry_identity"].__file__),
        syntheticOnly=a.synthetic_only,
        scope=("Bounded synthetic typed-fixture probe only; no full strategy/live coverage. " if a.synthetic_only else "Original entry rank, engine registered strategy, fixed-pose validation and consumers. ") + "Wide-budget replay is not a real-time performance result."))
    if not all(p["reason"] == "entry-first-verified" and p["verifiedRows"] > 0 and p["roomProofRows"] > 0 for p in positives):
        raise RuntimeError("New synthetic passport worlds did not prove both required measured-room/validator positives; preserve all cases and investigate positive-fixture-status.json")


if __name__ == "__main__":
    main()
