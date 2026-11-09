"""Original complete display policy oracle. Resource tree is always read-only.

Only explicitly named fixture directories below the new evidence directory are
written. No game frames, recognition seeds or authorization overrides are used.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import shutil

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "experiments/qt_quick"), str(REPO / "app")]
import cv2
import numpy as np
from client_providers import EngineFactories, SharedReferenceProvider
from client_route_preferences import prepare_display_layer, coffin_variant_metadata
from client_route_reprojection import prepare_route_reprojection
from custom_maps import CustomMapLibrary
from base_map_display import BaseMapDisplay
from client_custom_display import CustomGalleryDisplay
from author_route_catalog import AuthorRouteCatalog
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
from optional_author_displays import OptionalAuthorDisplays
from s1_author_displays import S1AuthorDisplays
import generic_author_base
import generic_author_package
from shared_structure_reference import SharedStructureResourceStore
from s1_native_display import S1NativeDisplay


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class Oracle:
    def __init__(self, root, output):
        self.root, self.output = root, output
        self.cases = []
        self.factories = EngineFactories(root)
        self.library, self.base = CustomMapLibrary(root / "assets/custom-maps"), BaseMapDisplay()
        self.custom = CustomGalleryDisplay(SimpleNamespace(base_display_library=lambda: self.library))
        self.factories.set_custom_display(None, base_provider=self.custom.base_layer)

    def case(self, label, engine, season, mode, map_id, floor, preferred, *, root=None, variant="variant-01"):
        floor = str(floor)
        row = dict(label=label, resourceRoot=str(root or self.root), season=season, mode=mode,
                   mapId=map_id, floor=floor, preferred=preferred, variant=variant,
                   referenceToClient=[[1.125, -.075, 23], [.075, 1.125, -17], [0, 0, 1]])
        try:
            # These are the original production entry points, with real providers.
            layer = prepare_display_layer(engine, map_id, floor, preferred, variant_id=variant)
            basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
            canonical = np.asarray(row["referenceToClient"]) @ np.linalg.inv(basis)
            reprojection = prepare_route_reprojection(engine, map_id, floor, preferred, canonical, variant_id=variant)
            pixels = np.asarray(layer["pixels"])
            bridge = np.asarray(layer["matrixDisplayToReference"], np.float64)
            source_client = np.asarray(reprojection["matrixSourceToClient"], np.float64)
            expected = {key: layer.get(key) for key in ("preferredProfileId", "effectiveProfileId", "authorStatus",
                "fallback", "fallbackReason", "variantId", "displayKind", "displayLabel")}
            expected.update(referenceToCanonical=basis.tolist(), canonicalToClient=canonical.tolist(),
                displayToReference=bridge.tolist(), displayToClient=source_client.tolist(),
                pixelSha256=digest(pixels.tobytes()), shape=list(pixels.shape),
                imagePath=layer.get("referencePath"), originalProducerPixelSha256=digest(pixels.tobytes()))
            if layer["displayKind"] == "base-map":
                # Core deliberately returns the source bitmap + an explicit atlas
                # recipe. Runtime owns the original warp/icon pixel producer.
                asset = self.library.floor(map_id, floor)
                raw = cv2.imdecode(np.frombuffer(asset.image_path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
                if raw.ndim == 2:
                    raw = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGRA)
                elif raw.shape[2] == 3:
                    raw = cv2.cvtColor(raw, cv2.COLOR_BGR2BGRA)
                expected.update(displayToReference=(bridge @ asset.to_native).tolist(),
                    displayToClient=(source_client @ asset.to_native).tolist(), imagePath=str(asset.image_path),
                    pixelSha256=digest(raw.tobytes()), shape=list(raw.shape),
                    atlas=dict(imageToNative=asset.to_native.tolist(), nativeShape=list(asset.native_shape),
                        artIncludesExits=self.library.art_includes_exits))
            row.update(prepared=True, expected=expected)
            if preferred == "zhan10-coffin":
                row["variants"] = coffin_variant_metadata(engine, mode, preferred, map_id, floor, season)["options"]
        except (OSError, ValueError, TypeError, KeyError) as error:
            row.update(prepared=False, reason=str(error))
        row["providerErrors"] = dict(getattr(getattr(engine, "qt_optional_displays", None), "errors", {}))
        self.cases.append(row)
        print(label, "=>", row.get("expected", {}).get("displayKind", row.get("reason")), flush=True)

    def actual_resources(self):
        for season in ("s0", "s1"):
            for mode in ("nightmare", "difficult"):
                engine = self.factories.get_engine(mode, season)
                maps = list(engine.pack.maps)
                for item in (maps[:2] + maps[-1:]):
                    for floor in item.layers:
                        for preferred in ("suju", "zhan10-speed", "wang-qingxin", "author-missing", "custom:missing"):
                            self.case(f"actual/{season}/{mode}/{item.id}/{floor}/{preferred}", engine,
                                      season, mode, item.id, floor, preferred)
                if season == "s0" and mode == "nightmare":
                    for variant in ("variant-01", "variant-02"):
                        self.case("actual/coffin/" + variant, engine, season, mode, "nightmare-01", "1",
                                  "zhan10-coffin", variant=variant)

    def fixture(self, name, season="s0", mode="nightmare", map_id="nightmare-01", floor="1"):
        """Actual base declarations/NPZ, synthetic display registration only."""
        install = self.output / "fixtures" / name
        root = install / "_internal"
        for source in self.root.rglob("*.json"):
            target = root / source.relative_to(self.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        original = self.factories.get_engine(mode, season)
        reference = original.qt_shared_provider("shared-structure", map_id, floor)
        relative = reference.path.relative_to(self.root)
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reference.path, root / relative)
        context_root = root if season == "s0" else root / "seasons" / season / mode
        if season == "s1":
            image = Path("seasons") / season / mode / "assets/game-native" / map_id / (floor + ".png")
            (root / image).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / image, root / image)
        catalog_path = root / original.qt_catalog.catalog_path.relative_to(self.root)
        coordinates = root / original.qt_adapters.path.relative_to(self.root)
        return SimpleNamespace(install=install, root=root, context_root=context_root, original=original,
            season=season, mode=mode, map_id=map_id, floor=floor, catalog_path=catalog_path, coordinates=coordinates)

    def fixture_engine(self, fixture):
        original = fixture.original
        engine = copy.copy(original)
        # Production package readers own registration validation; only their
        # explicit display inputs point at synthetic assets under evidence.
        catalog = AuthorRouteCatalog(fixture.catalog_path, asset_base=fixture.context_root)
        adapters = AuthorCoordinateAdapters(fixture.coordinates,
            native_static_context=getattr(original.qt_adapters, "native_static_context", None))
        fallback = OptionalAuthorDisplays(fixture.root, original.pack, catalog, adapters,
            original.qt_shared_provider.versions, mode=fixture.mode) if fixture.season == "s0" else None
        native = SimpleNamespace(root=fixture.context_root, mode=fixture.mode,
            catalog=fallback.catalog if fallback else catalog, adapters=fallback.adapters if fallback else adapters)
        displays = S1AuthorDisplays(fixture.root, native, season=fixture.season, fallback=fallback)
        engine.qt_catalog, engine.qt_adapters, engine.qt_optional_displays = displays.catalog, displays.adapters, displays
        engine.qt_custom_display = engine.qt_base_display = None
        versions = copy.deepcopy(original.qt_shared_provider.versions)
        for version in versions.values():
            version["path"] = fixture.root / version["path"].relative_to(self.root)
        store = SharedStructureResourceStore(fixture.root / original.qt_shared_provider.resources.index_path.relative_to(self.root), fixture.context_root)
        native_context = None
        if fixture.season == "s1":
            native_context = copy.copy(original.qt_native_context)
            native_context.root = fixture.context_root
            native_context.manifest = json.loads((fixture.context_root/"native-map-manifest.json").read_text("utf-8"))
            native_context._declared_files = frozenset(native_context.local(r["path"]) for r in native_context.manifest["files"])
            native_context.adapters, native_context.resources = adapters, store
            native_context._rendered_display = S1NativeDisplay(native_context)
            engine.qt_native_context = native_context
        engine.qt_shared_provider = SharedReferenceProvider(fixture.context_root, store, versions, native=native_context)
        return engine

    def fixture_case(self, fixture, preferred, suffix="", variant="variant-01", expected_kind=None):
        self.case("synthetic/" + fixture.install.name + suffix, self.fixture_engine(fixture), fixture.season,
            fixture.mode, fixture.map_id, fixture.floor, preferred, root=fixture.root, variant=variant)
        if expected_kind is not None:
            assert self.cases[-1].get("expected", {}).get("displayKind") == expected_kind, self.cases[-1]

    @staticmethod
    def image(path, channels=4):
        path.parent.mkdir(parents=True, exist_ok=True)
        y, x = np.mgrid[:29, :37]
        image = np.stack(((x * 7) % 251, (y * 11) % 251, ((x+y)*3) % 251,
            np.where((x+y) % 5, 191, 0)), axis=2).astype(np.uint8)
        if channels == 3:
            image = image[:, :, :3]
        elif channels == 1:
            image = image[:, :, 0]
        ok, raw = cv2.imencode(path.suffix, image)
        assert ok
        path.write_bytes(raw.tobytes())

    def optional_fixture(self, name, mutation=None):
        fixture = self.fixture(name)
        profile = "wang-qingxin"
        relative = "assets/authors/synthetic-optional/1.png"
        self.image(fixture.root / relative)
        version = next(v for k, v in fixture.original.qt_shared_provider.versions.items()
                       if k[1] == fixture.map_id and k[3] == fixture.floor)
        expected, binding = version["expected"], version["binding"]
        raw_versions = json.loads((fixture.root / "references/nightmare-structure-version-bindings.json").read_text("utf-8"))
        source = next(r for r in raw_versions["entries"] if r["mapId"] == fixture.map_id and r["resourceKey"]["floor"] == fixture.floor)
        matrix = [[1.2, .03, 12], [-.04, .95, 7], [0, 0, 1]]
        row = dict(profileId=profile, mapId=fixture.map_id, floor=fixture.floor, nativeResourceKey=source["resourceKey"],
            assetPath=relative, structureVersionKey=expected["structureVersionKey"], sourceVersion=expected["sourceVersion"],
            identityAuthority=False, structureValidationAuthority=False, crossFloorProjectionAllowed=False,
            currentFrameGeometryFitRequired=True, coordinateBridge=dict(baseToNativeIsIdentity=True, authorGeometryErrorUnchanged=True),
            matrixAssetToCanonical=matrix, matrixAssetToBase=matrix, registration=dict(status="confirmed"))
        sidecar = dict(schema="canonical-author-display-bindings-v1", profileId=profile,
            canonicalPackId=fixture.original.qt_adapters.canonical_pack_id,
            coordinateReferenceProfileId=fixture.original.qt_adapters.coordinate_reference_profile_id, entries=[row])
        catalog = dict(schemaVersion=1, profiles=[dict(id=profile, displayName="合成作者", authorName="合成作者", routeFamilyName="测试",
            runtimeEnabled=True, settingsSelectable=True, alignedOverlayEligible=True, maps={fixture.map_id:[dict(id="variant-01",
                displayName="合成注册", identityStatus="display-only", displayPolicy="display-only", layers={fixture.floor:relative})]})])
        if mutation:
            mutation(sidecar, catalog, fixture)
        folder = fixture.root / "references/author-packs" / profile
        write_json(folder / "catalog.json", catalog)
        write_json(folder / "display-bindings.json", sidecar)
        self.fixture_case(fixture, profile)

    def generic_fixture(self, name, *, season="s0", channels=4, extension="png", mutation=None, snapshot=False, extra_floor=None, damage_floor=None, compressed_damage=False):
        map_id, floor = ("nightmare-01", "1") if season == "s0" else ("107001", "-1")
        fixture = self.fixture(name, season, map_id=map_id, floor=floor)
        profile = "author-synthetic"
        contract = generic_author_base.resolve_layer(fixture.install, season, fixture.mode, map_id, floor)
        def record(path, raw):
            return dict(path=path, bytes=len(raw), sha256=digest(raw))
        dependencies = [record(p, (fixture.install / p).read_bytes()) for p in contract["dependencies"]]
        path = generic_author_package.image_path(profile, season, fixture.mode, map_id, floor, extension)
        self.image(fixture.install / path, channels)
        image = (fixture.install / path).read_bytes()
        row = {k:v for k,v in contract.items() if k != "dependencies"}
        row.update(season=season, mode=fixture.mode, mapId=map_id, floor=floor, displayPath=path,
            displaySha256=digest(image), displayBytes=len(image), displayShapeHW=[29,37],
            referenceSha256=next(r["sha256"] for r in dependencies if r["path"] == row["referenceResource"]),
            matrixDisplayToReference=[[.9,.15,10],[-.05,1.3,-8],[0,0,1]])
        if "nativeImageResource" in row:
            row["nativeImageSha256"] = next(r["sha256"] for r in dependencies if r["path"] == row["nativeImageResource"])
        layers = [row]
        if extra_floor is not None:
            reference = fixture.original.qt_shared_provider("shared-structure", map_id, extra_floor)
            target = fixture.root / reference.path.relative_to(self.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reference.path, target)
            extra = generic_author_base.resolve_layer(fixture.install, season, fixture.mode, map_id, extra_floor)
            for dependency in extra["dependencies"]:
                if not any(r["path"] == dependency for r in dependencies):
                    dependencies.append(record(dependency, (fixture.install/dependency).read_bytes()))
            image_path = generic_author_package.image_path(profile, season, fixture.mode, map_id, extra_floor, extension)
            self.image(fixture.install/image_path, channels)
            if compressed_damage:
                broken = bytearray((fixture.install/image_path).read_bytes())
                offset = broken.index(b"IDAT") + 4
                broken[offset:offset+2] = b"\x00\x00"  # Header still valid; selected decompression must fail.
                (fixture.install/image_path).write_bytes(broken)
            extra_image = (fixture.install/image_path).read_bytes()
            extra_row = {k:v for k,v in extra.items() if k != "dependencies"}
            extra_row.update(season=season, mode=fixture.mode, mapId=map_id, floor=extra_floor, displayPath=image_path,
                displaySha256=digest(extra_image), displayBytes=len(extra_image), displayShapeHW=[29,37],
                referenceSha256=next(r["sha256"] for r in dependencies if r["path"] == extra_row["referenceResource"]),
                matrixDisplayToReference=[[1.35,-.05,-21],[.1,.8,37],[0,0,1]])
            layers.append(extra_row)
        document = dict(schema=generic_author_package.BINDINGS_SCHEMA, profileId=profile, season=season,
            authorName="合成作者", displayName="合成完整画布", identityAuthority=False, structureValidationAuthority=False,
            crossFloorProjectionAllowed=False, currentFrameGeometryFitRequired=True, layers=layers)
        if mutation:
            mutation(document, dependencies, fixture)
        bindings = generic_author_package.bindings_path(profile)
        write_json(fixture.install / bindings, document)
        receipt = dict(schema=generic_author_package.SCHEMA, season=season, packageId=profile, revision=1,
            archiveSha256="0"*64, files=[record(r["displayPath"],(fixture.install/r["displayPath"]).read_bytes()) for r in layers]
                +[record(bindings,(fixture.install/bindings).read_bytes())], requiresFiles=dependencies)
        write_json(fixture.install / "work/map-packages" / (profile + ".json"), receipt)
        if snapshot:
            snapshot_id = "1"*32
            directory = fixture.install / "work/map-packages/author-snapshots/versions" / profile / snapshot_id
            for i, owned in enumerate(sorted(receipt["files"], key=lambda r:r["path"])):
                target = directory / "files" / (f"{i:04}" + Path(owned["path"]).suffix)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(fixture.install / owned["path"], target)
                if damage_floor is not None and owned["path"].endswith("/"+damage_floor+"."+extension):
                    target.write_bytes(b"damaged-display-only")
            canonical = json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            manifest = dict(schema="xingtu-author-resource-snapshot-v1", packageId=profile, snapshotId=snapshot_id,
                receipt=receipt, contentId=digest(canonical))
            write_json(directory / "manifest.json", manifest)
            write_json(fixture.install / "work/map-packages/author-snapshots/active" / (profile + ".json"),
                dict(schema="xingtu-author-resource-activation-v1", packageId=profile, season=season,
                    snapshotId=snapshot_id, manifestSha256=digest((directory/"manifest.json").read_bytes()), generation="2"*32))
        self.fixture_case(fixture, profile, expected_kind="author-route" if mutation is None else None)
        if extra_floor is not None:
            other = copy.copy(fixture)
            other.floor = extra_floor
            self.fixture_case(other, profile, suffix="/floor-"+extra_floor,
                expected_kind="shared-structure" if damage_floor == extra_floor or compressed_damage else "author-route")

    def synthetic_registrations(self):
        self.optional_fixture("optional-confirmed")
        self.optional_fixture("optional-unconfirmed", lambda d,c,f:d["entries"][0]["registration"].update(status="pending"))
        self.optional_fixture("optional-wrong-native", lambda d,c,f:d["entries"][0]["nativeResourceKey"].update(structureRawRecordSha256="f"*64))
        self.optional_fixture("optional-wrong-canonical", lambda d,c,f:d.update(canonicalPackId="foreign"))
        self.optional_fixture("optional-rgb-invalid", lambda d,c,f:self.image(f.root/d["entries"][0]["assetPath"],3))
        self.optional_fixture("optional-transparent-valid", lambda d,c,f:self.transparent(f.root/d["entries"][0]["assetPath"]))
        self.optional_fixture("optional-matrix-disagreement", lambda d,c,f:d["entries"][0].update(matrixAssetToBase=np.eye(3).tolist()))
        self.generic_fixture("generic-rgba")
        self.generic_fixture("generic-rgb", channels=3)
        self.generic_fixture("generic-jpeg-gray", channels=1, extension="jpeg")
        self.generic_fixture("generic-s1", season="s1")
        self.generic_fixture("generic-snapshot", snapshot=True)
        self.generic_fixture("generic-independent-floors", extra_floor="2")
        self.generic_fixture("generic-deferred-selected-decode", extra_floor="2", compressed_damage=True)
        self.generic_fixture("generic-snapshot-isolated-damage", snapshot=True, extra_floor="2", damage_floor="2")
        self.generic_fixture("generic-wrong-version", mutation=lambda d,deps,f:d["layers"][0].update(structureVersionKey="foreign"))
        self.generic_fixture("generic-wrong-canonical", mutation=lambda d,deps,f:d["layers"][0].update(coordinateSpace="foreign"))
        self.generic_fixture("generic-short-closure", mutation=lambda d,deps,f:deps.pop())
        self.generic_fixture("generic-reflection", mutation=lambda d,deps,f:d["layers"][0].update(matrixDisplayToReference=[[-1,0,0],[0,1,0],[0,0,1]]))
        self.generic_fixture("generic-projective-invalid", mutation=lambda d,deps,f:d["layers"][0].update(matrixDisplayToReference=[[1,0,0],[0,1,0],[.001,0,1]]))
        self.coffin_fixture("coffin-profile-chart")
        self.coffin_fixture("coffin-narrow-variant", narrow=True)
        self.legacy_unreviewed()
        self.native_failure("native-image-changed", "bytes")
        self.native_failure("native-image-uninventoried", "inventory")
        self.base_author("base-author-bound")
        self.base_author("base-author-evidence-unavailable", unavailable=True)

    @staticmethod
    def transparent(path):
        pixels = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
        pixels[:,:,3] = 0
        path.write_bytes(cv2.imencode(".png", pixels)[1].tobytes())

    def base_author(self, name, unavailable=False):
        fixture = self.fixture(name)
        catalog = json.loads(fixture.catalog_path.read_text("utf-8"))
        variant = next(p for p in catalog["profiles"] if p["id"] == "suju")["maps"][fixture.map_id][0]
        path = fixture.context_root / variant["layers"][fixture.floor]
        self.image(path)
        meta = variant["layerMeta"][fixture.floor]
        pixels = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
        pixels = cv2.resize(pixels, (meta["width"], meta["height"]), interpolation=cv2.INTER_NEAREST)
        raw = cv2.imencode(".png", pixels)[1].tobytes()
        path.write_bytes(raw)
        meta["sha256"] = digest(raw)
        write_json(fixture.catalog_path, catalog)
        if unavailable:
            coordinate = json.loads(fixture.coordinates.read_text("utf-8"))
            coordinate["profiles"]["suju"]["maps"][fixture.map_id]["floors"][fixture.floor]["usableForCurrentFrameRendering"] = False
            write_json(fixture.coordinates, coordinate)
        self.fixture_case(fixture, "suju", expected_kind="shared-structure" if unavailable else "author-route")

    def coffin_fixture(self, name, narrow=False):
        fixture = self.fixture(name)
        raw = json.loads(fixture.catalog_path.read_text("utf-8"))
        raw["profiles"] = [p for p in raw["profiles"] if p["id"] != "zhan10-coffin"]
        variants = []
        for i in (1,2):
            path = f"assets/authors/synthetic-coffin/{i}.png"
            self.image(fixture.root / path)
            if i == 2:
                pixels = cv2.imdecode(np.frombuffer((fixture.root/path).read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
                pixels[:,:,1] = 231
                cv2.imencode(".png", pixels)[1].tofile(str(fixture.root/path))
            variants.append(dict(id=f"variant-0{i}", displayName=f"合成版本{i}", layers={"1":path},
                identityStatus="display-only", displayPolicy="display-only"))
        raw["profiles"].append(dict(id="zhan10-coffin", displayName="合成全棺", authorName="合成作者", routeFamilyName="测试",
            runtimeEnabled=False, settingsSelectable=True, alignedOverlayEligible=True, maps={"nightmare-01":variants}))
        write_json(fixture.catalog_path, raw)
        coordinate = json.loads(fixture.coordinates.read_text("utf-8"))
        evidence = dict(algebraicallyExact=True)
        if narrow:
            evidence["binding"] = dict(variant="variant-01")
        coordinate["profiles"]["zhan10-coffin"] = dict(role="display-only", maps={"nightmare-01":dict(floors={"1":dict(
            usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
            matrixProfileToCanonical=[[1.07,0,19],[0,1.07,-13],[0,0,1]], evidence=evidence)})})
        write_json(fixture.coordinates, coordinate)
        for variant in ("variant-01", "variant-02"):
            self.fixture_case(fixture, "zhan10-coffin", suffix="/"+variant, variant=variant,
                expected_kind="shared-structure" if narrow and variant=="variant-02" else "author-route")

    def legacy_unreviewed(self):
        import s1_author_package as legacy
        fixture = self.fixture("s1-legacy-unreviewed", "s1", map_id="107001", floor="-1")
        profile = legacy.PROFILE_ID
        binding = legacy.bindings_path(profile)
        document = dict(schema=legacy.BINDINGS_SCHEMA, profileId=profile, season="s1", sourceVersion=legacy.SOURCE_VERSION,
            identityAuthority=False, structureValidationAuthority=False, crossFloorProjectionAllowed=False,
            currentFrameGeometryFitRequired=True, layers=[])
        write_json(fixture.install / binding, document)
        files = [dict(path=binding, bytes=len((fixture.install/binding).read_bytes()), sha256=digest((fixture.install/binding).read_bytes()))]
        files += [dict(path=legacy.image_path(mode,mid,floor,profile), bytes=1, sha256="0"*64)
            for mode in legacy.MODES for mid in legacy.layouts(mode) for floor in legacy.FLOORS]
        dependencies = [dict(path=p,bytes=1,sha256="0"*64) for p in sorted(legacy.required_dependencies())]
        write_json(fixture.install/"work/map-packages"/(profile+".json"), dict(schema=legacy.SCHEMA, packageId=profile,
            season="s1", revision=1, archiveSha256="0"*64, files=files, requiresFiles=dependencies))
        self.fixture_case(fixture, profile, expected_kind="game-native")
        assert profile in self.cases[-1]["providerErrors"], "source must reject unreviewed legacy declaration"

    def native_failure(self, name, kind):
        fixture = self.fixture(name, "s1", map_id="107001", floor="-1")
        if kind == "bytes":
            (fixture.context_root/"assets/game-native/107001/-1.png").write_bytes(b"changed")
        else:
            path = fixture.context_root/"native-map-manifest.json"
            document = json.loads(path.read_text("utf-8"))
            document["files"] = [r for r in document["files"] if r["path"] != "assets/game-native/107001/-1.png"]
            write_json(path, document)
        self.fixture_case(fixture, "author-missing", expected_kind="shared-structure")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("resource_root", type=Path)
    parser.add_argument("new_evidence_dir", type=Path)
    parser.add_argument("--synthetic-only", action="store_true")
    args = parser.parse_args()
    root, output = args.resource_root.resolve(), args.new_evidence_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("new empty evidence directory required")
    output.mkdir(parents=True, exist_ok=True)
    oracle = Oracle(root, output)
    if not args.synthetic_only:
        oracle.actual_resources()
    oracle.synthetic_registrations()
    write_json(output / "oracle.json", dict(schema="original-display-selection-v1", resourceRoot=str(root),
        entryPoints=["client_route_preferences.prepare_display_layer", "client_route_reprojection.prepare_route_reprojection"],
        sourceProvider="client_providers.EngineFactories", cases=oracle.cases))
    print(f"WROTE {len(oracle.cases)} cases: {output / 'oracle.json'}", flush=True)


if __name__ == "__main__":
    main()
