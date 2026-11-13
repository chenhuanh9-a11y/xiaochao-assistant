"""Declared metadata-free legacy binaries, without native identity authority."""
from copy import deepcopy
import hashlib
import posixpath
import re

import numpy as np

from author_structure_resources import AuthorStructureResourceStore
from imported_native_base import _reference, _reject, _text
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader
from imported_structure_declarations import MAX_CANVAS_AXIS, MAX_CANVAS_PIXELS
from shared_structure_reference import SharedStructureReference
import star_map_import as files


class ImportedLegacyReferences:
    """Read explicitly retained working drafts alongside a native inventory.

    No typed metadata is added to their archives. The reference metadata below
    comes from the real legacy index and its declared per-floor source canvas.
    The caller still owns the complete identity pool and all ranking policy.
    """

    def __init__(self, view, declaration, *, expected_maps, floors, reader=None):
        if not isinstance(view, PackageView) or not isinstance(declaration, dict):
            _reject('legacy-declaration-invalid')
        self._view, self._root = view, view.root
        self._store = None
        self._reader = ComponentResourceReader(view) if reader is None else reader
        if (not isinstance(self._reader, ComponentResourceReader)
                or self._reader.view.component_ref != view.component_ref
                or self._reader.root.resolve() != self._root.resolve()):
            _reject('legacy-reader-view-mismatch')
        self._profile = declaration.get('profileId')
        _text(self._profile)
        maps = declaration.get('mapIds')
        if (not isinstance(maps, list) or len(maps) != 1 or any(not isinstance(mid, str) for mid in maps)
                or len(set(maps)) != len(maps) or set(maps) != set(expected_maps)):
            _reject('legacy-map-inventory-invalid')
        self._maps = tuple(maps)
        canvases = declaration.get('referenceCanvas')
        if not isinstance(canvases, dict) or set(canvases) != set(floors):
            _reject('legacy-floor-inventory-invalid')
        self._canvases = deepcopy(canvases)
        resource_root = declaration.get('resourceRoot')
        if not isinstance(resource_root, str) or files._relative(resource_root) != resource_root:
            _reject('legacy-root-invalid')
        index_path = _reference(view, declaration.get('index'), '.json')
        self._store = AuthorStructureResourceStore(view.path(index_path), self._root / resource_root,
            read_resource=self._reader, check_current=self.assert_current)
        if self._store.error is not None:
            _reject('legacy-index-invalid')
        wanted = {(self._profile, mid, 'variant-01', floor) for mid in maps for floor in floors}
        if set(self._store.entries) != wanted:
            _reject('legacy-index-inventory-invalid')
        selected = set()
        for key, entry in self._store.entries.items():
            canvas = self._canvases[key[3]]
            if not isinstance(canvas, dict):
                _reject('legacy-canvas-invalid')
            shape = canvas.get('shapeHW')
            if (not isinstance(shape, list) or len(shape) != 2
                    or any(type(axis) is not int or not 1 <= axis <= MAX_CANVAS_AXIS for axis in shape)
                    or shape[0] * shape[1] > MAX_CANVAS_PIXELS
                    or entry.get('sourceShape') != shape
                    or 'sourceLayout' not in canvas or canvas['sourceLayout'] is not None
                    or canvas.get('sourceClass') != 'legacy-clean-working-draft'
                    or canvas.get('staticAcceptance') != 'working-draft-not-batch-accepted'
                    or entry.get('sourceLayout') is not None
                    or entry.get('staticAcceptance') not in (None, 'working-draft-not-batch-accepted')
                    or entry.get('sourceClass') not in (None, 'legacy-clean', 'legacy-clean-working-draft')):
                _reject('legacy-canvas-invalid')
            for field in ('coordinateSpace', 'sourceCommit', 'sourceVersion', 'structureVersionKey', 'baseSnapshot'):
                _text(canvas.get(field))
            if entry.get('sourceCommit') != canvas['sourceCommit']:
                _reject('legacy-source-commit-mismatch')
            relative = entry.get('resourcePath')
            if not isinstance(relative, str) or files._relative(relative) != relative:
                _reject('legacy-product-path-invalid')
            path = _reference(view, posixpath.join(resource_root, relative), '.npz')
            if path != canvas.get('resourcePath') or path.casefold() in selected:
                _reject('legacy-product-binding-invalid')
            selected.add(path.casefold())
            digest = canvas.get('resourceSHA256')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                _reject('legacy-product-digest-invalid')
            if hashlib.sha256(self._reader(view.path(path))).hexdigest() != digest:
                _reject('legacy-product-digest-mismatch')
            product = self._store.load(key[0], key[1], key[3], route_shape=tuple(shape))
            if (product is None or product.clean_mask is None or not np.any(product.clean_mask)
                    or product.known_boundary is None or product.unknown_mask is None
                    or product.native_wall_segments is None):
                _reject('legacy-product-incomplete')
        self.assert_current()

    def assert_current(self):
        try:
            if self._view.root != self._root:
                _reject('legacy-component-root-changed')
        except Exception:
            if self._store is not None:
                self._store._cache.clear()
            raise

    @property
    def bindings(self):
        self.assert_current()
        return {(self._profile, mid, 'variant-01', floor): deepcopy(canvas)
                for mid in self._maps for floor, canvas in self._canvases.items()}

    def read_owner_state(self):
        """RAM owner description; actual legacy reads retain their own checks."""
        store = self._store
        return (id(self), id(self._view), self._root, id(self._reader),
                id(self._reader.view), self._reader.root, id(store),
                None if store is None else (id(store.read_resource), id(store.check_current),
                    store.resource_root, store.index_path, id(store.entries)),
                self._maps, id(self._canvases), self._profile)

    def reference(self, map_id, floor):
        self.assert_current()
        if str(map_id) not in self._maps or str(floor) not in self._canvases:
            return None
        canvas = self._canvases[str(floor)]
        product = self._store.load(self._profile, str(map_id), str(floor),
                                   route_shape=tuple(canvas['shapeHW']))
        if product is None:
            return None
        metadata = dict(product.entry, sourceKind='legacy-shared-binary',
            sourceClass=canvas['sourceClass'], sourceLayout=canvas['sourceLayout'],
            staticAcceptance=canvas['staticAcceptance'], sourceCommit=canvas['sourceCommit'],
            authorizesMapIdentity=False, authorizesStructureValidation=False,
            binding=dict(profile=self._profile, mapId=str(map_id), variant='variant-01', floor=str(floor)),
            canvas=dict(coordinateSpace=canvas['coordinateSpace'], shapeHW=list(canvas['shapeHW']),
                width=canvas['shapeHW'][1], height=canvas['shapeHW'][0], floor=str(floor)),
            **{name: canvas[name] for name in ('sourceVersion', 'structureVersionKey', 'baseSnapshot')})
        result = SharedStructureReference(product.clean_mask, product.validation_mask, metadata,
            self._view.path(canvas['resourcePath']), product.known_boundary, product.unknown_mask,
            product.native_wall_segments, self._root)
        self.assert_current()
        return result
