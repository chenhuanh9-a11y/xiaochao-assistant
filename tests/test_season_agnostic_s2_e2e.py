"""End-to-end tests for zero-code new season (S2/S3) export, import, UI, controls & recognition.

Uses synthetic masks and isolated temp directories; does not claim real-game acceptance.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from fast_structure_alignment import prepare
from floor_contract import (FloorDefinition, clear_dynamic_floor_contracts, floor_contract,
                            floor_display_label, register_floor_contract)
from floor_observation_policy import cursor_in_floor_column, game_floor_control_hit
from generic_structure_index import GenericStructureIndex
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from nightmare_candidate import map_color_mask
from s1_floor_observation import bind_s1_control_geometry
from season_context import (clear_dynamic_seasons, context_key, get_context,
                            normalize_season, register_season, season_label, season_options)


def _poly_mask(points, shape=(240, 320)):
    mask = np.zeros(shape, np.uint8)
    cv2.fillPoly(mask, [np.array(points, np.int32)], 1)
    return mask


def _frame_from_mask(mask):
    frame = np.full((*mask.shape, 3), 25, np.uint8)
    frame[mask > 0] = [80, 95, 112]
    return frame


def _write_shared_npz(path: Path, clean_mask: np.ndarray, *, map_id: str, floor: str, mode: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = clean_mask.shape
    clean_u8 = (clean_mask > 0).astype(np.uint8)
    unknown = np.zeros_like(clean_u8)
    known = cv2.morphologyEx(clean_u8, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    metadata = dict(
        schemaVersion=2,
        mapId=map_id,
        floor=str(floor),
        mode=mode,
        profileId="shared-structure",
        variantId="variant-01",
        canvas=dict(shapeHW=[int(h), int(w)]),
        sourceKind="game-rendered-shared-structure",
        sourceGeometry=dict(unit=20.0),
    )
    np.savez_compressed(
        path,
        clean_mask=clean_u8,
        validation_mask=clean_u8,
        unknown_mask=unknown,
        known_boundary=known,
        metadata=np.array(json.dumps(metadata, sort_keys=True)),
    )
    return metadata


def _build_synthetic_s2_source(root: Path, *, season="s2", mode="nightmare", floors=("-1", "-2", "-3"),
                               season_name="极寒迷宫"):
    """Build a canonical season source tree ready for export_original_package."""
    from nightmare_author_coordinate_adapter import CANONICAL_SCHEMA

    season_dir = root / "seasons" / season / mode
    source_version = f"{season}-fixture-v1"
    shapes = {
        "s2-map-alpha": {
            "-1": [(40, 40), (260, 40), (260, 100), (190, 100), (190, 190), (40, 190)],
            "-2": [(50, 50), (270, 50), (270, 180), (130, 180), (130, 120), (50, 120)],
            "-3": [(30, 40), (250, 40), (250, 170), (160, 170), (160, 110), (30, 110)],
        },
        "s2-map-beta": {
            "-1": [(40, 40), (130, 40), (130, 90), (270, 90), (270, 200), (180, 200), (180, 150), (40, 150)],
            "-2": [(60, 30), (260, 30), (260, 90), (200, 90), (200, 190), (60, 190)],
            "-3": [(40, 50), (220, 50), (220, 120), (280, 120), (280, 195), (40, 195)],
        },
    }

    typed_rows, version_rows, neutral_rows = [], [], []
    pack_maps, coordinate_maps, shared_maps = [], {}, {}
    identity_profiles = {}
    main_entries, local_entries = [], []

    for mid, by_floor in shapes.items():
        raw_hash = hashlib.sha256((f"{season}-structure:" + mid).encode()).hexdigest()
        identity_profiles[mid] = "shared-structure"
        layers = {}
        coordinate_floors, profile_floors = {}, {}
        for fl in floors:
            mask = _poly_mask(by_floor[fl])
            h, w = mask.shape
            resource = f"references/structures/{mid}-{fl}.npz"
            binding = dict(profile="shared-structure", mapId=mid, floor=fl, variant="variant-01")
            version_key = f"{mid}@{raw_hash}/{fl}"
            base_snapshot = f"fixture-base-{season}-v1"
            canvas = dict(width=int(w), height=int(h), shapeHW=[int(h), int(w)], layout=mid, floor=fl,
                          coordinateSpace=f"fixture-native:{mid}:{fl}")
            common = dict(structureVersionKey=version_key, sourceVersion=source_version,
                          baseSnapshot=base_snapshot)
            metadata = dict(sourceKind="game-rendered-shared-structure", binding=binding,
                            canvas=canvas, roles=dict(clean="shared_mask", validation="shared_mask"), **common)
            target = season_dir / resource
            target.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(target, metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
                                shared_mask=mask.astype(np.uint8))

            prepared = prepare(mask)
            fast_path = f"references/fast-structure/{Path(resource).stem}-fast.npz"
            target_fast = season_dir / fast_path
            target_fast.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(target_fast, distance=prepared.distance, chains=prepared.chains,
                                rays=prepared.rays, pitch=prepared.pitch, shape=prepared.shape,
                                sourceMetadata=np.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True)))

            typed_rows.append(dict(binding, resourcePath=resource, **common))
            neutral_rows.append(dict(mapId=mid, floor=fl, binding=binding,
                                     referenceResource=resource, canvas=canvas, **common))
            layers[fl] = resource
            resource_key = dict(layoutId=mid, structureRawRecordSha256=raw_hash, floor=fl)
            version_rows.append(dict(mapId=mid, resourceKey=resource_key,
                                     typedReference=resource, sharedLookup=dict(binding, resourcePath=resource),
                                     neutralDisplay=dict(baseResourceKey=resource_key), **common))
            coordinate_floors[fl] = dict(referenceResource=resource, sourceVersion=source_version,
                                         structureVersionKey=version_key, coordinateWidth=int(w), coordinateHeight=int(h),
                                         matrixCanonicalToLegacyRecognition=np.eye(3).tolist())
            profile_floors[fl] = dict(referenceResource=resource, sourceVersion=source_version,
                                      structureVersionKey=version_key, matrixProfileToCanonical=np.eye(3).tolist(),
                                      usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
                                      evidence=dict(algebraicallyExact=True))

            filename = Path(resource).name
            entry_common = dict(
                mapId=mid, floor=fl, profileId="shared-structure", variantId="variant-01",
                sourceKind="game-rendered-shared-structure", sourceVersion=source_version,
                structureVersionKey=version_key, baseSnapshot=base_snapshot,
                unit=20.0, sourceProduct=filename, structureResource="../structures/" + filename,
                sourceIndex="../shared-structure-index.json", canvas=canvas, gates=[],
                knownWallSupportUnavailable=True,
            )
            turns = [dict(point=point, rays=[[1., 0.], [0., 1.]], next=(idx + 1) % 4)
                     for idx, point in enumerate(([40., 40.], [180., 40.], [180., 180.], [40., 180.]))]
            local_entries.append(dict(entry_common, region="fixture-region",
                                      featureRole="local-contour-proposals-not-semantic-side-exits",
                                      extractorEnvironment=dict(opencv=cv2.__version__), turns=turns))
            if fl == floors[0]:
                main_entries.append(dict(entry_common, material="fixture-stone", kind="main",
                                         sourceKind="game-entry-room-proposal",
                                         proposalOnly=True, authorizesPose=False, authorizesIdentity=False,
                                         center=[120., 120.], outline=[[40., 40.], [180., 40.], [180., 180.], [40., 180.]],
                                         chains=[dict(points=[[40., 40.], [100., 40.], [100., 100.]],
                                                      rays=[[1., 0.], [0., 1.]])]))
        coordinate_maps[mid] = dict(floors=coordinate_floors)
        shared_maps[mid] = dict(floors=profile_floors)
        pack_maps.append(dict(
            id=mid, name="Fixture " + mid, direction="unknown", doorCount=0,
            shape="native", sourceLayout=mid, sourceVersion=source_version,
            structureContentSha256=raw_hash, identityProfileId="shared-structure",
            routeProfileId="shared-structure", oneFlow=None, sideDoorCrop=[0, 0, 320, 240],
            layers=layers,
        ))

    def _dump(rel, obj):
        target = season_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    _dump("references/map-pack.json", dict(
        schemaVersion=1, season=season, mode=mode, packId=f"fixture-{season}-{mode}",
        displayName=f"Fixture {season.upper()} {mode}", floors=list(floors),
        defaultEntryFloor=int(floors[0]), identityProfiles=identity_profiles, maps=pack_maps))
    _dump("references/shared-structure-index.json", dict(
        schemaVersion=1, resourceKind="shared-structure",
        structureVersionBindings="references/structure-version-bindings.json", entries=typed_rows))
    _dump("references/structure-version-bindings.json", dict(
        schemaVersion=1, resourceKind="native-structure-version-bindings", entries=version_rows))
    _dump("references/structure-display.json", dict(
        schemaVersion=1, resourceKind="typed-shared-structure-display",
        indexPath="references/shared-structure-index.json", entries=neutral_rows))
    _dump("references/canonical-coordinate-pack.json", dict(
        schema=CANONICAL_SCHEMA, canonicalPackId=f"fixture-{season}-{mode}-canonical",
        coordinateReferenceProfileId="shared-structure",
        policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False,
                    authorizesCrossFloorProjection=False),
        maps=coordinate_maps, profiles={"shared-structure": dict(maps=shared_maps)},
        canonicalCrossFloorSearchCandidates={}))
    _dump("references/entries/entries.json", dict(
        schemaVersion=1, season=season, mode=mode, profileId="shared-structure",
        entries=main_entries, semanticGaps=[]))
    _dump("references/entries/side-entries.json", dict(
        schemaVersion=1, season=season, mode=mode, profileId="shared-structure",
        entries=local_entries, semanticGaps=[]))
    _dump("references/entries/entry-passports.json", dict(
        schemaVersion=1, season=season, mode=mode, profileId="shared-structure",
        entries=[], coverage=[]))
    _dump("native-map-manifest.json", dict(
        schemaVersion=1, season=season, seasonName=season_name, mode=mode,
        floors=list(floors), defaultEntryFloor=int(floors[0]),
        runtimeInterfaces=dict(
            identityPack="references/map-pack.json",
            typedIndex="references/shared-structure-index.json",
            coordinatePack="references/canonical-coordinate-pack.json",
            neutralDisplay="references/structure-display.json",
            identityEntries="references/entries",
            identityProfile="shared-structure",
        ),
    ))
    return shapes


class SeasonAgnosticS2Tests(unittest.TestCase):
    def tearDown(self):
        clear_dynamic_seasons()
        clear_dynamic_floor_contracts()

    def test_dynamic_season_registration_and_floor_contracts(self):
        with self.assertRaises(ValueError):
            normalize_season("s9")

        register_season("s2", name="极寒迷宫", modes=("nightmare", "difficult"))
        self.assertEqual(normalize_season("s2"), "s2")
        self.assertEqual(season_label("s2"), "S2 · 极寒迷宫")
        self.assertTrue(get_context("s2:nightmare").registered)
        opts = season_options()
        self.assertIn("s2", [row["id"] for row in opts])
        self.assertEqual(next(row for row in opts if row["id"] == "s2")["label"], "S2 · 极寒迷宫")

        # 3-floor S2 and 5-floor S3 contracts both register cleanly.
        c2 = register_floor_contract("s2", "nightmare", [(-1, "-1"), (-2, "-2"), (-3, "-3")])
        c3 = register_floor_contract("s3", "difficult", [(1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5")])
        self.assertEqual(floor_contract("nightmare", season="s2"), c2)
        self.assertEqual(floor_contract("difficult", season="s3"), c3)
        self.assertEqual([floor_display_label(f) for f in c2.floor_ids], ["B1", "B2", "B3"])
        self.assertEqual([floor_display_label(f) for f in c3.floor_ids], ["F1", "F2", "F3", "F4", "F5"])

    def test_multi_floor_s2_and_s3_mouse_click_geometry_binding(self):
        register_season("s2", name="三层测试")
        register_floor_contract("s2", "nightmare", [(-1, "-1"), (-2, "-2"), (-3, "-3")])
        points3 = [[0.86, 0.20 + i * 0.09] for i in range(3)]
        read3 = dict(
            season="s2", mode="nightmare", reason="s1-floor-observed", selectedFloor=-2,
            observedFloorValidated=True, automaticFloorEligible=True,
            controlAxis="vertical", observedOrder=[-1, -2, -3], mapOpen=True,
            centerPoints=points3, centers=points3,
            labelEvidence=[dict(floor=fl, negative=True) for fl in (-1, -2, -3)],
            controlBounds=[1600, 180, 1700, 440],
        )
        bound3 = bind_s1_control_geometry(read3, "nightmare", (1920, 1080), map_open=True, season="s2")
        self.assertTrue(bound3["controlGeometryVerified"])
        self.assertEqual(bound3["season"], "s2")
        self.assertEqual(len(bound3["controlRects"]), 3)
        bounds = (0, 0, 1920, 1080)
        for fl, (cx, cy) in zip((-1, -2, -3), points3):
            self.assertEqual(game_floor_control_hit((cx * 1920, cy * 1080), bounds, bound3), fl)
        # Gap between -1 and -2 is not a floor hit, but IS inside floor column (blocks drag).
        self.assertIsNone(game_floor_control_hit((0.86 * 1920, 0.245 * 1080), bounds, bound3))
        self.assertTrue(cursor_in_floor_column((0.86 * 1920, 0.245 * 1080), bounds, bound3))

    def test_generic_structure_index_ranks_matching_map_first_without_gate_icon(self):
        mask_a = _poly_mask([(40, 40), (260, 40), (260, 100), (190, 100), (190, 190), (40, 190)])
        mask_b = _poly_mask([(40, 40), (130, 40), (130, 90), (270, 90), (270, 200), (180, 200), (180, 150), (40, 150)])
        mask_c = _poly_mask([(30, 30), (280, 30), (280, 200), (30, 200)])
        index = GenericStructureIndex.from_prepared({
            "-1": {"alpha": prepare(mask_a), "beta": prepare(mask_b), "gamma": prepare(mask_c)},
        })
        for target_id, mask in (("alpha", mask_a), ("beta", mask_b)):
            frame = _frame_from_mask(mask)
            context = dict(query=map_color_mask(frame, live=True),
                           observationBounds=(0, 0, frame.shape[1], frame.shape[0]))
            ordered = index.shortlist(frame, "-1", ("gamma", "beta", "alpha"), context=context)
            self.assertEqual(ordered[0], target_id, (target_id, ordered))

    def test_zero_code_s2_package_export_import_activation_and_engine_build(self):
        from export_original_qt_base import export_original_package
        from client_imported_admission import admit_import, available_base_contexts
        from client_imported_activation import activate_context
        from client_imported_providers import ImportedEngineFactories, SUPPORTED_CAPABILITIES
        from client_imported_resources import _activation_refs, _context_metadata
        from client_paths import RuntimePaths

        with tempfile.TemporaryDirectory(prefix="xingtu-s2-e2e-") as folder:
            base_dir = Path(folder)
            source_root = base_dir / "source"
            _build_synthetic_s2_source(source_root, season="s2", mode="nightmare",
                                       floors=("-1", "-2", "-3"), season_name="极寒迷宫")
            archive = base_dir / "s2-nightmare-base.zip"
            exported = export_original_package(
                source_root, archive, package_id="xingtu-s2-nightmare",
                resource_version="1.0.0", season="s2", mode="nightmare",
                season_name="极寒迷宫")
            self.assertEqual(exported["season"], "s2")
            self.assertEqual(exported["mode"], "nightmare")
            self.assertTrue(exported["semanticValidated"])
            self.assertEqual(exported["mapCount"], 2)

            # Clear dynamic state to prove fresh import + activation restores S2 from package alone!
            clear_dynamic_seasons()
            clear_dynamic_floor_contracts()

            install_root = base_dir / "install"
            paths = RuntimePaths(install_root, False)
            resolver = ResourceResolver(paths.imported_resource_root,
                                        supported_capabilities=SUPPORTED_CAPABILITIES)
            saved = save_archive(archive, paths.imported_resource_root)
            from imported_resource_packages import PackageView
            views = [PackageView(paths.imported_resource_root, ref) for ref in saved.component_refs]
            admitted = admit_import(views, resolver)
            self.assertEqual(len(admitted), 1)
            season, mode, base_ref, controls_ref = admitted[0]
            self.assertEqual((season, mode), ("s2", "nightmare"))

            activated = activate_context(resolver, paths, base_ref, controls_ref,
                                         season="s2", mode="nightmare")
            self.assertEqual(activated["context"], "s2:nightmare")

            avail = available_base_contexts(resolver.snapshot())
            self.assertIn("s2:nightmare", avail)
            self.assertEqual(season_label("s2"), "S2 · 极寒迷宫")
            self.assertEqual(floor_contract("nightmare", season="s2").floor_ids, ("-1", "-2", "-3"))

            # Verify ImportedResourceService metadata & activation button generation works for S2!
            meta = _context_metadata("s2", "nightmare")
            self.assertEqual(meta["label"], "启用 S2 噩梦")
            self.assertEqual(meta["objectSuffix"], "-s2-nightmare")

            # Verify ImportedEngineFactories builds a live S2 engine with generic scanner & fingerprint index!
            factories = ImportedEngineFactories(resolver, paths)
            engine = factories.get_engine("nightmare", "s2")
            self.assertEqual(engine.qt_season, "s2")
            self.assertEqual(tuple(engine.identity_floors), ("-1", "-2", "-3"))
            self.assertTrue(callable(getattr(engine, "qt_s1_structure_scan", None)))
            self.assertTrue(callable(getattr(engine, "qt_generic_structure_shortlist", None)))


if __name__ == "__main__":
    unittest.main()
