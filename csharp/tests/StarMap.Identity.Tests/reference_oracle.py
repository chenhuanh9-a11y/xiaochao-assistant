"""Offline reference generator. Requires the original project's Python environment."""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def write(path, value):
    def native(v):
        if isinstance(v, np.ndarray):
            return v.tolist()
        if isinstance(v, np.generic):
            return v.item()
        raise TypeError(type(v).__name__)

    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, default=native, allow_nan=False)


def main():
    parser = argparse.ArgumentParser()
    for key in ("repo", "resources", "frame", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    repo, resources = args.repo.resolve(), args.resources.resolve()
    sys.path.insert(0, str(repo / "app"))
    from difficult_side_entry_identity import MapLocalEntryIdentity
    from difficult_entry_identity import MapEntryGeometryIdentity
    from nightmare_candidate import NightmarePack
    from difficult_native_context import DifficultNativeContext
    from difficult_native_identity import NativeDifficultPack
    from s1_native_resources import registration
    from map_identity_engine import verified_entry_candidate, entry_passport_fallback_allowed

    out = args.output.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    expected = out.with_name(out.stem + "-expected")
    expected.mkdir(exist_ok=False)
    frame = cv2.imdecode(np.fromfile(args.frame, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Cannot decode recorded frame.")
    black = np.zeros_like(frame)
    cases = []
    locators = {}
    packs = {}

    def case(name, kind, value, **inputs):
        filename = name + ".json"
        write(expected / filename, value)
        cases.append(dict(name=name, kind=kind, expected=str(Path(expected.name, filename)), **inputs))

    contexts = [("s0", "nightmare"), ("s0", "difficult"), ("s1", "nightmare"), ("s1", "difficult")]
    if (resources / "seasons/s1/difficulty2/native-map-manifest.json").exists():
        contexts.append(("s1", "difficulty2"))
    for season, mode in contexts:
        native = None
        if season == "s1":
            root = resources / "seasons" / season / mode
            interfaces = registration(root, mode)["runtimeInterfaces"]
            pack = NativeDifficultPack(root, interfaces["identityPack"], expected_mode=mode, expected_season=season)
            entries, profile, icon = root / interfaces["identityEntries"], "shared-structure", None
        elif mode == "difficult":
            native = DifficultNativeContext(resources)
            pack = NativeDifficultPack(resources, native.interfaces["identityPack"], display_context=native)
            native.validate_identity_pack(pack)
            entries = native.local(native.interfaces["identityEntries"])
            profile, icon = "shared-structure", native.local(native.interfaces["exitIcon"])
        else:
            pack = NightmarePack(resources / "references/nightmare-map-pack.json")
            entries = resources / "references/nightmare-entries"
            profile, icon = "zhan10-speed", resources / "references/difficult-exit-icon.json"
        profile_for = lambda mid, pack=pack, profile=profile: pack.identity_profiles.get(mid, profile)
        main_entries = MapEntryGeometryIdentity(entries, profile_id=profile, profile_for_map=profile_for)
        if main_entries.error:
            raise ValueError(main_entries.error)
        locator = MapLocalEntryIdentity(entries, mode=mode, profile_id=profile, floors=pack.floors,
                                        profile_for_map=profile_for, exit_icon_path=icon)
        if native:
            native.attach_legacy_entry_metadata(locator)
        locators[season, mode], packs[season, mode] = locator, pack
        ctx = dict(season=season, mode=mode)
        label = season + "-" + mode
        case("catalog-" + label, "catalog", dict(floors=pack.floors, profile=profile, profiles=pack.identity_profiles,
             registeredFloors=sorted(locator.registered_floors), semanticGaps=locator.semantic_gaps,
             sideKeys=[[row["mapId"], row["floor"], row["profileId"]] for row, *_ in locator.entries],
             mainKeys=[[row["mapId"], row["floor"], row["profileId"]] for row, *_ in main_entries.entries],
             passports=locator.passports, coverage=locator.entry_coverage,
             hasPassportError=bool(locator.passport_error), hasLegacySource=hasattr(locator, "legacy_passport_source")), **ctx)
        preflights = [(str(f), False, 20) for f in pack.floors] + [("invalid-floor", False, 20),
                     (str(pack.floors[0]), True, 0)]
        if season == "s0":
            preflights += [(None, False, 20), (None, True, 20)]
        for i, (floor, use_black, seconds) in enumerate(preflights):
            rows, meta = locator.rank_entry(black if use_black else frame, floor=floor, budget_seconds=seconds)
            if rows or meta["reason"] not in ("entry-budget-incomplete", "entry-coverage-incomplete",
                    "entry-passport-unavailable", "entry-unsupported-floor", "entry-no-reliable-exit"):
                raise ValueError(f"Case needs geometry ranking outside the preflight: {meta['reason']}")
            del meta["elapsedMs"]
            case(f"preflight-{label}-{i}", "preflight", dict(needsLocalGeometry=False, diagnostics=meta),
                 floor=floor, black=use_black, seconds=seconds, **ctx)

    locator = locators["s0", "difficult"]
    ctx = dict(season="s0", mode="difficult")
    side = next(p for p in locator.passports if p["family"] == "side" and p["directionKnown"])
    main_entry = next(p for p in locator.passports if p["family"] == "main")
    ident = np.eye(3)
    affine = np.array([[.81, .13, -250.], [-.12, .92, -390.], [0., 0., 1.]])
    row = dict(mapId=side["mapId"], floor=side["floor"], profileId=locator.profile_for_map(side["mapId"]),
               matrixSourceToClient=affine.tolist())
    side_client = affine[:2, :2] @ side["center"] + affine[:2, 2]
    directions = {"S": "下", "N": "上", "W": "左", "E": "右"}
    good = directions[side["outwardDirections"][0]]
    bad = next(v for k, v in directions.items() if k not in side["outwardDirections"])

    def observed(center, direction, reliable=True, direction_reliable=True):
        return dict(icon=dict(center=list(center)), reliable=reliable,
                    direction=dict(reliable=direction_reliable, direction=direction))

    def check(name, candidate, observations=None, black_frame=False):
        observation = None if observations is None else dict(observations=observations)
        value = locator.check_entry_evidence(black if black_frame else frame, candidate, observation)
        case("check-" + name, "check", value, row=candidate, observations=observations, black=black_frame, **ctx)

    check("side-compatible", row, [observed(side_client, good)])
    check("side-opposite", row, [observed(side_client, bad)])
    check("reliability", row, [observed(side_client, bad, False), observed(side_client, bad, True, False)])
    check("mixed-exits", row, [observed(side_client, good), observed([-3000, -3000], bad)])
    check("real-glyphs-under-synthetic-pose", row)
    # A mutable capture buffer can be reused immediately. Retaining one frame's pixels
    # must preserve its observations while a newly captured black frame gets a fresh check.
    case("check-capture-buffer-reuse", "buffer-reuse",
         dict(captured=locator.check_entry_evidence(frame, row), next=locator.check_entry_evidence(black, row)),
         row=row, **ctx)
    check("black", row, black_frame=True)
    check("unknown-other-map", dict(row, mapId="difficult-map-01", profileId=locator.profile_for_map("difficult-map-01")))
    check("incomplete-second-floor", dict(row, floor="2"), [observed(side_client, bad)])
    check("far-from-inventory", row, [observed([-2000, -1000], bad)])
    main_local = next(v for v in main_entry["localInstances"] if v["instance"] == main_entry["sourceEntryId"])
    outline = np.asarray(main_local["outline"]) * main_entry["unit"] + main_entry["center"]
    center = affine[:2, :2] @ main_entry["center"] + affine[:2, 2]
    check("main-center", row, [observed(center, bad)])
    # Measure the inward/outward sign against the original outline and project distances in client pixels.
    # Integerize the translation so the top horizontal edge is representable for the inclusive -6 check.
    m = ident.copy()
    m[:2, 2] = -outline[0] + [360., 180.]
    edge = (outline[0] + outline[1]) * .5 + m[:2, 2]
    for label, delta in (("interior", 2.), ("edge", 0.), ("minus-six", -6.), ("outside", -6.01)):
        check("main-" + label, dict(row, matrixSourceToClient=m.tolist()), [observed(edge + [0., delta], bad)])
    scale = .6
    boundary = np.asarray([80., 120.])
    shift = np.asarray([60., 42.])
    matrix = [[scale, 0., shift[0]], [0., scale, shift[1]], [0., 0., 1.]]
    for label, direction, dx in (("equal", "S", 6.), ("too-far", "S", 6.001), ("opposite", "N", 0.)):
        constraint = dict(door=dict(outwardDirection="S", boundaryCenter=boundary.tolist()),
                          observed=dict(direction=direction, center=(scale*boundary + shift + [dx, 0.]).tolist()), unit=12.)
        check("door-" + label, dict(row, matrixSourceToClient=matrix, sideDoorConstraint=constraint), [])

    # These are deliberately constructed *already-verified-row* inputs to the pure consumer.
    # They are never sent to the original ranker or to a C# pose producer.
    checked = dict(success=True, matrixSourceToClient=ident.tolist(), regions=dict(regionalEdgeVerified=True),
                   residual=dict(verified=True))
    hypothesis = dict(mapId=side["mapId"], floor=side["floor"], profileId=locator.profile_for_map(side["mapId"]),
                      entryEvidence=dict(directionCompatible=True, localGeometryVerified=True, sourceEntryId=side["sourceEntryId"]),
                      verification=checked)
    diagnostics = dict(exitObserved=True, entryObserved=True, complete=True, verificationComplete=True, coverageComplete=True,
                       identityAmbiguous=False, poseAmbiguous=False)

    def select(name, rows, meta):
        value = verified_entry_candidate(rows, meta, "1", profile_id=locator.profile_for_map)
        case("consume-" + name, "selection", value, rows=rows, diagnostics=meta, floor="1", **ctx)

    select("unique", [hypothesis], diagnostics)
    select("no-rows", [], diagnostics)
    select("pose-ambiguous", [hypothesis], dict(diagnostics, poseAmbiguous=True))
    select("two-poses", [hypothesis, dict(hypothesis, verification=dict(checked, matrixSourceToClient=affine.tolist()))], diagnostics)
    select("two-maps", [hypothesis, dict(hypothesis, mapId="difficult-map-01", profileId="shared-structure")], diagnostics)
    select("wrong-profile", [dict(hypothesis, profileId="shared-structure")], diagnostics)
    select("wrong-floor", [dict(hypothesis, floor="2")], diagnostics)
    select("bad-matrix", [dict(hypothesis, verification=dict(checked, matrixSourceToClient=[[1., 2.]]))], diagnostics)
    select("unverified-direction", [dict(hypothesis, entryEvidence=dict(hypothesis["entryEvidence"], directionCompatible=False))], diagnostics)
    select("unverified-residual", [dict(hypothesis, verification=dict(checked, residual=dict(verified=False)))], diagnostics)
    select("ambiguous-identity", [hypothesis], dict(diagnostics, identityAmbiguous=True))
    for key in ("exitObserved", "entryObserved", "complete", "verificationComplete", "coverageComplete"):
        select("missing-" + key, [hypothesis], dict(diagnostics, **{key: False}))
    author_fit = dict(hypothesis, verification=dict(checked, regions={}, validationPolicy="local-fit-and-author"))
    select("regional-required", [author_fit], diagnostics)
    select("first-verified", [author_fit], dict(selectionPolicy="first-verified-entry"))
    select("first-missing-residual", [dict(author_fit, verification=dict(author_fit["verification"], residual={}))],
           dict(selectionPolicy="first-verified-entry"))
    select("author-display-alone", [dict(authorVisible=True, routeAvailable=True)], dict(selectionPolicy="first-verified-entry"))
    for i, reason in enumerate((None, "", "entry-no-reliable-exit", "entry-passport-unavailable", "entry-coverage-incomplete",
                                "entry-family-unresolved", "entry-budget-incomplete", "entry-ambiguous")):
        meta = None if reason is None else dict(reason=reason)
        case(f"fallback-{i}", "fallback", entry_passport_fallback_allowed(meta), diagnostics=meta, **ctx)
    write(out, dict(frame=str(args.frame.resolve()), python=sys.version, numpy=np.__version__, opencv=cv2.__version__,
                    modulePath=str(sys.modules[MapLocalEntryIdentity.__module__].__file__), cases=cases))
    print(json.dumps(dict(cases=len(cases), output=str(out), expected=str(expected))))


if __name__ == "__main__":
    main()
