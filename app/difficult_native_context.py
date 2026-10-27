"""Difficult recognition stays legacy; this context owns native fit/display data."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import cv2
import numpy as np

from author_route_catalog import AuthorRouteCatalog
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
from nightmare_candidate import imread_unicode
from shared_structure_reference import SharedStructureResourceStore
from shared_structure_reference import SharedStructureReference
from author_structure_resources import AuthorStructureResourceStore


class DifficultNativeContext:
    def __init__(self, root, identity_pack=None):
        self.root = Path(root).resolve()
        manifest = self.read('difficult-native-manifest.json')
        interfaces = manifest['runtimeInterfaces']
        self.interfaces = interfaces
        source_path = self.local(interfaces['sourceManifest'])
        source = self.read(interfaces['sourceManifest'])
        if source.get('mode') != 'difficult':
            raise ValueError('native difficult source mode mismatch')
        self.neutral_relative = interfaces['neutralDisplay']
        self.resources = SharedStructureResourceStore(self.local(interfaces['typedIndex']), self.root)
        if self.resources.error:
            raise ValueError(self.resources.error)
        if self.read(interfaces['typedIndex']).get('structureVersionBindings') != interfaces['versionBindings']:
            raise ValueError('native version registry interface mismatch')
        self.catalog = AuthorRouteCatalog(self.local(interfaces['authorCatalog']), asset_base=self.root)
        self.adapters = AuthorCoordinateAdapters(self.local(interfaces['coordinatePack']))
        self.version_pack = SimpleNamespace(by_id={row['id']: row for row in source['maps']},
            floors=tuple(map(str, source['floors'])), identity_profiles=source['identityProfiles'],
            manifest_path=source_path)
        if identity_pack is not None and not set(self.version_pack.by_id).issubset(identity_pack.by_id):
            raise ValueError('native declarations reference unknown difficult identities')
        self.unbound_layers = source.get('pendingOriginalBindings', [])
        self.legacy = interfaces.get('legacyClean')
        self.legacy_bindings = {}
        self._legacy_cache = {}
        self.legacy_resources = None
        if self.legacy:
            self.legacy_resources = AuthorStructureResourceStore(self.local(self.legacy['index']),
                self.local(self.legacy['resourceRoot']))
            if self.legacy_resources.error:
                raise ValueError(self.legacy_resources.error)
            for map_id in self.legacy['mapIds']:
                for floor, canvas in self.legacy['referenceCanvas'].items():
                    key = (self.legacy['profileId'], map_id, 'variant-01', str(floor))
                    entry = self.legacy_resources.entries.get(key)
                    if (entry is None or entry.get('sourceShape') != canvas.get('shapeHW')
                            or entry.get('sourceCommit') != canvas.get('sourceCommit')
                            or canvas.get('sourceLayout') is not None
                            or canvas.get('sourceClass') != 'legacy-clean-working-draft'):
                        raise ValueError('legacy-clean source/canvas declaration mismatch')
                    self.legacy_bindings[key] = dict(canvas)
        bridges = self.read(interfaces['coordinateBridges'])
        index_digest = (hashlib.sha256((identity_pack.tilemap_root/'index.json').read_bytes()).hexdigest()
                        if identity_pack is not None else None)
        self._bridges = {}
        for map_id, item in bridges['maps'].items():
            for floor, row in item['floors'].items():
                key = ('shared-structure', str(map_id), 'variant-01', str(floor))
                entry = self.resources.entries.get(key)
                old = identity_pack.tilemap_entries.get((str(map_id), str(floor))) if identity_pack is not None else None
                if (entry is None
                        or row.get('mapId') != map_id or str(row.get('floor')) != floor
                        or row.get('authorizesMapIdentity') is not False
                        or row.get('authorizesCrossFloorProjection') is not False
                        or row.get('requiresFreshNativeGeometryValidation') is not True
                        or any(row.get(name) != entry.get(name)
                               for name in ('structureVersionKey', 'sourceVersion', 'baseSnapshot'))):
                    raise ValueError(f'legacy/native coordinate binding mismatch: {map_id}/{floor}')
                if identity_pack is not None and (old is None
                        or row.get('legacyIndexSourceSha256') != index_digest
                        or row.get('legacyRouteResource') != old.get('routeResource')
                        or row.get('legacyRouteShapeHW') != old.get('sourceShape')):
                    raise ValueError(f'legacy identity resource mismatch: {map_id}/{floor}')
                matrix = self.matrix(row['matrixLegacyRouteToNative'])
                inverse = self.matrix(row['matrixNativeToLegacyRoute'])
                if not np.allclose(matrix @ inverse, np.eye(3), atol=1e-9, rtol=0):
                    raise ValueError('native bridge inverse mismatch')
                old_basis = self.matrix(row['legacyMatrixRouteToFloorLocal'])
                if old is not None and not np.array_equal(old_basis, self.matrix(old['matrixAuthorToMap'])):
                    raise ValueError('legacy floor-local basis mismatch')
                if not np.allclose(self.matrix(row['matrixLegacyFloorLocalToNative']) @ old_basis,
                                   matrix, atol=1e-9, rtol=0):
                    raise ValueError('legacy route/floor-local bridge mismatch')
                matrix.setflags(write=False)
                self._bridges[(map_id, floor)] = matrix
        expected = {(key[1], key[3]) for key in self.resources.entries}
        if not expected or set(self._bridges) != expected:
            raise ValueError('native bridge coverage differs from typed resources')
        self._full = {}
        for row in self.read(interfaces['fullReferences'])['entries']:
            if (row.get('role') != 'full-reference' or row['mapId'] not in
                    (set(self.version_pack.by_id) | {key[1] for key in self.legacy_bindings})):
                raise ValueError('invalid native full-reference role/identity')
            if row['mapId'] in self._full:
                raise ValueError('duplicate native full-reference')
            self._full[row['mapId']] = self.local(row['resourcePath'])
        self._display_paths = set()
        for map_id, floor in (set(self._bridges) | {(key[1],key[3]) for key in self.legacy_bindings}):
            path = self.catalog.route_path('zhan10-speed', map_id, floor, include_inactive=True)
            if path is not None:
                self._display_paths.add(path.resolve())

    def validate_identity_pack(self, pack):
        expected = {(key[1],key[3]):self.local(entry['resourcePath'])
                    for key,entry in self.resources.entries.items()}
        expected.update({(key[1],key[3]):self.local(canvas['resourcePath'])
                         for key,canvas in self.legacy_bindings.items()})
        actual = {(item.id,str(floor)):Path(path).resolve()
                  for item in pack.maps for floor,path in item.layers.items() if path is not None}
        unavailable = {(item.id,str(floor)) for item in pack.maps for floor,path in item.layers.items()
                       if path is None}
        original_missing = {(str(row['mapId']),str(row['floor'])) for row in self.unbound_layers}
        declared_missing = original_missing - {(key[1],key[3]) for key in self.legacy_bindings}
        declared_ids = set(self.version_pack.by_id) | {row[0] for row in original_missing}
        expected_profiles = {map_id:'shared-structure' for map_id in self.version_pack.by_id}
        expected_profiles.update({key[1]:key[0] for key in self.legacy_bindings})
        if (actual != expected or unavailable != declared_missing or set(pack.by_id) != declared_ids
                or pack.identity_profiles != expected_profiles):
            raise ValueError('native identity pack differs from typed structure inventory')
        for key in self.legacy_bindings:
            if self.legacy_reference(key[1],key[3]) is None:
                raise ValueError('declared legacy-clean competitor is unavailable')

    def local(self, relative):
        path = (self.root / str(relative)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('native resource escapes declared root')
        return path

    def read(self, relative):
        return json.loads(self.local(relative).read_text(encoding='utf-8'))

    @staticmethod
    def matrix(raw):
        matrix = np.asarray(raw, dtype=np.float64)
        if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
                or not np.array_equal(matrix[2], [0, 0, 1])
                or abs(np.linalg.det(matrix[:2, :2])) <= 1e-12):
            raise ValueError('invalid native coordinate bridge')
        return matrix

    def validate_versions(self, versions):
        if set(versions) != set(self.resources.entries):
            raise ValueError('native version coverage mismatch')
        failures = [f'{key[1]}/{key[3]}: {row["sharedError"]}'
                    for key, row in versions.items() if row.get('sharedError')]
        if failures:
            raise ValueError('; '.join(failures[:3]))

    def legacy_route_to_native(self, map_id, floor):
        return self._bridges.get((str(map_id), str(floor)))

    def has_reference_binding(self, map_id, floor):
        return ((str(map_id),str(floor)) in self._bridges or
                any(key[1] == str(map_id) and key[3] == str(floor) for key in self.legacy_bindings))

    def legacy_reference(self, map_id, floor):
        key = next((key for key in self.legacy_bindings
                    if key[1] == str(map_id) and key[3] == str(floor)), None)
        if key is None:
            return None
        if key in self._legacy_cache:
            return self._legacy_cache[key]
        canvas = self.legacy_bindings[key]
        path = self.local(canvas['resourcePath'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != canvas['resourceSHA256']:
            raise ValueError('legacy-clean binary differs from declared original source')
        product = self.legacy_resources.load(key[0],key[1],key[3],route_shape=tuple(canvas['shapeHW']))
        if product is None:
            return None
        metadata = dict(product.entry, sourceKind='legacy-shared-binary',
            sourceClass=canvas['sourceClass'], sourceLayout=None,
            binding=dict(profile=key[0],mapId=key[1],variant=key[2],floor=key[3]),
            canvas=dict(coordinateSpace=canvas['coordinateSpace'],shapeHW=canvas['shapeHW'],
                width=canvas['shapeHW'][1],height=canvas['shapeHW'][0],floor=key[3]),
            **{name:canvas[name] for name in ('structureVersionKey','sourceVersion','baseSnapshot')})
        reference = SharedStructureReference(product.clean_mask,product.validation_mask,metadata,path,
            product.known_boundary,product.unknown_mask,product.native_wall_segments,self.root)
        self._legacy_cache[key] = reference
        return reference

    def attach_legacy_entry_metadata(self, locator):
        if not self.legacy or locator is None:
            return
        payload = self.read(self.legacy['passports'])
        if ((payload.get('schemaVersion'),payload.get('mode'),payload.get('profileId')) !=
                (1,'difficult',self.legacy['profileId'])):
            raise ValueError('legacy-clean entry metadata is incompatible')
        for row in payload['entries'] + payload['coverage']:
            if row['mapId'] not in self.legacy['mapIds']:
                raise ValueError('legacy entry metadata expands its declared candidate scope')
        locator.passports.extend(payload['entries'])
        locator.entry_coverage.extend(payload['coverage'])
        # Keep the original missing-native-passport error. Two known legacy
        # passports do not establish native/global coverage. The existing
        # per-candidate cross-check can still use those real known records.
        locator.legacy_passport_source = str(self.local(self.legacy['passports']))

    def full_reference(self, map_id):
        return self._full.get(str(map_id))

    def display_pixels(self, path, role):
        path = Path(path).resolve()
        if role not in {'full-reference', 'minimap', 'aligned-overlay'}:
            raise ValueError('unknown native display role')
        allowed = set(self._full.values()) if role == 'full-reference' else self._display_paths
        if path not in allowed:
            raise ValueError('native display resource role mismatch')
        pixels = imread_unicode(path, cv2.IMREAD_UNCHANGED)
        if pixels is None or pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4:
            raise ValueError('native display requires real BGRA pixels')
        return pixels
