"""Portable synthetic S0 nightmare source tree for native-base consumers.

The fixture provides structurally consistent source maps only. It contains no
registered similarity groups or expected solver decisions and is not game data.
"""
from __future__ import annotations

import base64
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]

import export_native_base_package as exporter
import imported_resource_archive as archives
from imported_native_base import decode_native_base
from imported_resource_packages import PackageView
from side_entry_search import write_index


_DEFAULT_IDS = tuple(f"nightmare-{number:02}" for number in range(1, 27))
_PACKAGE_ID = "fixture-s0n-group-base"
_RESOURCE_VERSION = "fixture-v1"
_SOURCE_VERSION = "fixture-s0-nightmare-v1"
_BASE_SNAPSHOT = "fixture-s0-nightmare-base-v1"


def _json_bytes(value):
    return exporter._encoded(value)


def _structure_mask(map_number: int, floor_index: int) -> np.ndarray:
    """Make small deterministic binary references with map/floor variation."""
    mask = np.zeros((8, 8), np.uint8)
    mask[1, 1:7] = 1
    mask[6, 1:7] = 1
    mask[1:7, 1] = 1
    mask[1:7, 6] = 1
    x = 2 + ((map_number + floor_index) % 3)
    y = 2 + ((map_number * 2 + floor_index) % 3)
    mask[2:6, x] = 1
    mask[y, 2:6] = 1
    # Retain a nonempty shared centre while avoiding an all-identical map pool.
    mask[(map_number + floor_index) % 4 + 2, (map_number * 3 + floor_index) % 4 + 2] = 1
    return mask


class S0NightmareGroupSourceFixture:
    """Build a genuine producer input with a complete three-floor map pool."""

    def __init__(self, root, ids=None):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.sourceRoot = self.root / "source"
        self.sourceRoot.mkdir(parents=True, exist_ok=True)
        self.output = self.root / "s0-nightmare-group-base.xingtu-base"
        chosen = tuple(_DEFAULT_IDS if ids is None else ids)
        if (not chosen or any(not isinstance(item, str) or item not in _DEFAULT_IDS
                              for item in chosen)
                or len(chosen) != len(set(chosen))):
            raise ValueError("fixture-map-ids-must-be-unique-s0-nightmare-members")
        self.ids = chosen
        self.inventory = self.root / "source-inventory.json"
        self._build()
        self.refresh_inventory()

    def _write_bytes(self, relative, raw):
        path = self.sourceRoot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def _write_json(self, relative, value):
        return self._write_bytes(relative, _json_bytes(value))

    def _build(self):
        typed_rows, version_rows, neutral_rows = [], [], []
        map_rows, coordinate_maps, shared_maps = [], {}, {}
        main_entries, local_entries, door_rows = [], [], []
        prepared_entries = []
        doors = {}

        for map_id in self.ids:
            map_number = int(map_id.rsplit("-", 1)[1])
            layout_id = "fixture-layout-" + map_id
            raw_hash = hashlib.sha256(("synthetic-structure:" + map_id).encode("ascii")).hexdigest()
            coordinate_floors, profile_floors, layers = {}, {}, {}
            local_by_floor = {}

            for floor_index, floor in enumerate(exporter.FLOORS):
                resource = f"{exporter.ENTRIES}/{map_id}-{floor}.npz"
                binding = dict(profile=exporter.PROFILE, mapId=map_id,
                               floor=floor, variant="variant-01")
                structure_key = f"{layout_id}@{raw_hash}/{floor}"
                canvas = dict(width=8, height=8, shapeHW=[8, 8], layout=layout_id,
                              floor=floor,
                              coordinateSpace=f"synthetic-s0-nightmare:{map_id}:{floor}")
                common = dict(structureVersionKey=structure_key,
                              sourceVersion=_SOURCE_VERSION, baseSnapshot=_BASE_SNAPSHOT)
                metadata = dict(sourceKind="game-rendered-shared-structure", binding=binding,
                                canvas=canvas,
                                roles=dict(clean="shared_mask", validation="shared_mask"),
                                **common)
                archive = BytesIO()
                np.savez_compressed(archive, metadata=np.asarray(json.dumps(
                    metadata, ensure_ascii=False, sort_keys=True)),
                    shared_mask=_structure_mask(map_number, floor_index))
                self._write_bytes(resource, archive.getvalue())

                typed_rows.append(dict(binding, resourcePath=resource, **common))
                neutral_rows.append(dict(mapId=map_id, floor=floor, binding=binding,
                    referenceResource=resource, canvas=canvas, **common))
                layers[floor] = resource
                resource_key = dict(layoutId=layout_id,
                                    structureRawRecordSha256=raw_hash, floor=floor)
                version_rows.append(dict(mapId=map_id, resourceKey=resource_key,
                    sourceVersion=_SOURCE_VERSION, typedReference=resource,
                    sharedLookup=dict(binding, resourcePath=resource),
                    packReference=exporter.PACK, adapterReference=exporter.COORDINATES,
                    neutralDisplay=dict(index=exporter.NEUTRAL,
                                        baseResourceKey=resource_key),
                    authorDisplay=dict(resource="assets/author.png",
                        baseResourceKey=resource_key,
                        registration="producer-provenance-only")))
                coordinate_floors[floor] = dict(referenceResource=resource,
                    structureVersionKey=structure_key, sourceVersion=_SOURCE_VERSION)
                profile_floors[floor] = dict(structureVersionKey=structure_key,
                    matrixProfileToCanonical=np.eye(3).tolist(),
                    usableForCurrentFrameRendering=True,
                    crossFloorProjectionAllowed=False,
                    evidence=dict(algebraicallyExact=True))

                points = np.asarray([[1., 1.], [6., 1.], [6., 6.], [1., 6.]], dtype=np.float64)
                rays = np.asarray([[[1., 0.], [0., 1.]]] * 4, dtype=np.float64)
                pairs = np.asarray([[0, 1], [1, 2], [2, 3], [3, 0]], dtype=np.int64)
                local = dict(mapId=map_id, floor=floor, profileId=exporter.PROFILE,
                    variantId="variant-01", unit=12.,
                    sourceKind="game-rendered-shared-structure",
                    material="fixture-synthetic-stone", region="fixture-region",
                    sourceProduct=Path(resource).name,
                    structureResource=Path(resource).name,
                    sourceIndex="../nightmare-shared-structure-index.json",
                    canvas=canvas,
                    turns=[dict(point=point.tolist(), rays=ray.tolist(), next=int(pair[1]))
                           for point, ray, pair in zip(points, rays, pairs)],
                    gates=[], **common)
                local_entries.append(local)
                local_by_floor[floor] = local
                prepared_entries.append((local, points, rays, pairs))

                if floor == "2":
                    door = dict(mapId=map_id, floor="2", layoutId=layout_id,
                        structureVersionKey=structure_key, sourceVersion=_SOURCE_VERSION,
                        boundaryCenter=[3., 7.], outwardDirection="S")
                    door_rows.append(door)
                    doors[map_id] = door

            coordinate_maps[map_id] = dict(floors=coordinate_floors)
            shared_maps[map_id] = dict(floors=profile_floors)
            map_rows.append(dict(id=map_id, name="Synthetic " + map_id,
                direction="unknown", doorCount=0, shape="fixture",
                resourceBase="resource-root", sourceLayout=layout_id,
                structureContentSha256=raw_hash, sourceVersion=_SOURCE_VERSION,
                identityProfileId=exporter.PROFILE, routeProfileId="fixture-author",
                oneFlow="assets/author.png", layers=layers,
                sideDoorCrop=[0, 0, 8, 8], targetGuid="synthetic-" + map_id,
                classificationKnown=False))

            main = dict(local_by_floor["1"], kind="main", center=[4., 4.],
                outline=[[1., 1.], [6., 1.], [6., 6.], [1., 6.]],
                chains=[dict(points=[[1., 1.], [3., 3.], [6., 6.]],
                             rays=[[1., 0.], [0., 1.]])])
            main_entries.append(main)

        self._write_json(exporter.PACK, dict(schemaVersion=2, season="s0", mode="nightmare",
            packId="fixture-native-s0-nightmare", defaultEntryFloor=1,
            sourceVersion=_SOURCE_VERSION, maps=map_rows))
        self._write_json(exporter.INDEX, dict(schemaVersion=1,
            resourceKind="shared-structure", entries=typed_rows,
            structureVersionBindings=exporter.VERSIONS))
        self._write_json(exporter.VERSIONS, dict(schemaVersion=1,
            resourceKind="native-structure-version-bindings", entries=version_rows))
        self._write_json(exporter.NEUTRAL, dict(schemaVersion=1,
            resourceKind="typed-shared-structure-display", indexPath=exporter.INDEX,
            entries=neutral_rows))
        self._write_json(exporter.COORDINATES, dict(
            schema="identityv-nightmare-canonical-coordinate-pack-v1",
            canonicalPackId="fixture-s0-nightmare-canonical",
            coordinateReferenceProfileId="shared-structure",
            referenceProvider=dict(indexPath=exporter.INDEX,
                lookupBindings=[{key: row[key] for key in
                    ("profile", "mapId", "floor", "variant")} for row in typed_rows]),
            policy=dict(requiresCurrentFrameGeometryFit=True,
                        authorizesMapIdentity=False,
                        authorizesCrossFloorProjection=False),
            maps=coordinate_maps,
            profiles={"shared-structure": dict(maps=shared_maps),
                      "fixture-author": dict(maps={})},
            canonicalCrossFloorSearchCandidates={}))
        entries_root = exporter.ENTRIES
        self._write_json(entries_root + "/entries.json", dict(schemaVersion=1,
            season="s0", mode="nightmare", profileId=exporter.PROFILE,
            entries=main_entries, semanticGaps=["fixture-semantic-gap"]))
        self._write_json(entries_root + "/side-entries.json", dict(schemaVersion=1,
            season="s0", mode="nightmare", profileId=exporter.PROFILE,
            entries=local_entries, semanticGaps=["fixture-semantic-gap"]))
        self._write_json(entries_root + "/entry-passports.json", dict(schemaVersion=1,
            mode="nightmare", profileId=exporter.PROFILE,
            entries=[], coverage=[], semanticGaps=["fixture-passports-unavailable"]))
        self._write_json(entries_root + "/side-door-anchors.json", dict(schemaVersion=1,
            mode="nightmare", profileId=exporter.PROFILE, entries=door_rows))
        write_index(self.sourceRoot / entries_root / "side-entry-search.npz",
                     prepared_entries, doors)
        self._write_json(exporter.EXIT, dict(
            exitIcon=dict(width=8, height=8,
                pixels=base64.b64encode(np.arange(64, dtype=np.uint8).tobytes()).decode("ascii")),
            sideDoors=[dict(mapId="fixture-only-source-descriptor")]))
        # Source-only assets exercise the producer's existing display filter;
        # they are not recognition groups or part of the exported base.
        self._write_bytes("assets/author.png", b"synthetic-author-display-only")

    def refresh_inventory(self):
        files = {}
        for path in sorted(item for item in self.sourceRoot.rglob("*") if item.is_file()):
            raw = path.read_bytes()
            files[path.relative_to(self.sourceRoot).as_posix()] = dict(
                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.inventory.write_bytes(_json_bytes(dict(files=files)))

    def export(self, **kwargs):
        fixed = dict(package_id=_PACKAGE_ID, resource_version=_RESOURCE_VERSION,
                     season="s0", mode="nightmare")
        for key, value in fixed.items():
            if key in kwargs and kwargs[key] != value:
                raise ValueError("fixture-export-context-is-fixed")
        kwargs.setdefault("source_inventory", self.inventory)
        return exporter.export_package(self.sourceRoot, self.output, **fixed, **kwargs)


def _roundtrip_fixture(root, ids=None):
    fixture = S0NightmareGroupSourceFixture(root, ids=ids)
    result = fixture.export()
    installed = fixture.root / "installed"
    inspected = archives.inspect_archive(fixture.output)
    saved = archives.save_archive(fixture.output, installed,
                                  expected_archive_sha256=inspected.archive_sha256)
    base_ref = next(ref for ref in saved.component_refs if ref.component_id == "base")
    decoded = decode_native_base(PackageView(installed, base_ref),
                                 season="s0", mode="nightmare")
    assert result["mapCount"] == len(fixture.ids)
    assert result["typedLayerCount"] == len(fixture.ids) * 3
    assert len(decoded.pack.maps) == len(fixture.ids)
    assert len(decoded.resources.entries) == len(fixture.ids) * 3
    assert len(decoded.entry_identity.entries) == len(fixture.ids)
    assert len(decoded.side_entry_identity.entries) == len(fixture.ids) * 3
    assert set(decoded.pack.by_id) == set(fixture.ids)
    return dict(maps=len(decoded.pack.maps), typedLayers=len(decoded.resources.entries),
                mainEntries=len(decoded.entry_identity.entries),
                localEntries=len(decoded.side_entry_identity.entries),
                synthetic=True, gameAcceptance=False)


if __name__ == "__main__":
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="s0-nightmare-group-fixture-"))
    print(json.dumps(_roundtrip_fixture(root), sort_keys=True))
