"""Optional prepared structures owned by one imported base component.

Declarations never authorize identity or pose. Production only decodes saved
arrays; producers and explicit activation additionally compare their derivation.
"""
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
import math
import zipfile
import zlib

import numpy as np

from imported_array_archive import validate_npz_bytes
from imported_resource_packages import FileRef
from imported_resource_reader import decode_resource_json
from imported_structure_declarations import MAX_METADATA_BYTES
import star_map_import as files


INDEX_PATH = 'references/fast-structure/index.json'
INDEX_KIND = 'fast-structure-preparation'
_BINDING = ('profile', 'mapId', 'floor', 'variant')
_FIELDS = frozenset(('distance', 'chains', 'rays', 'pitch', 'shape', 'sourceMetadata'))


def _reject(reason):
    code = 'imported-fast-structure-' + reason
    raise files.ImportRejected(code, code=code)


def decode_prepared(raw, reference):
    """Bound allocation and validate every consumed role before NumPy loads it."""
    headers = validate_npz_bytes(raw)
    if set(headers) != {name + '.npy' for name in _FIELDS}:
        _reject('members-invalid')
    shape = tuple(reference.clean_mask.shape)
    expected = {'distance': (shape, np.dtype('uint16')),
                'pitch': ((), np.dtype('float64')), 'shape': ((2,), np.dtype('int64'))}
    chains_shape, chains_dtype = headers['chains.npy']
    if (len(chains_shape) != 3 or chains_shape[1:] != (3, 2)
            or chains_dtype != np.dtype('float32')):
        _reject('chains-invalid')
    expected['rays'] = ((chains_shape[0], 2, 2), np.dtype('float32'))
    if any(headers[name + '.npy'] != spec for name, spec in expected.items()):
        _reject('roles-invalid')
    metadata_shape, metadata_dtype = headers['sourceMetadata.npy']
    if metadata_shape != () or metadata_dtype.kind != 'U' or metadata_dtype.itemsize > MAX_METADATA_BYTES * 4:
        _reject('metadata-invalid')
    try:
        with np.load(BytesIO(raw), allow_pickle=False) as archive:
            arrays = {name: archive[name] for name in _FIELDS}
        metadata_raw = str(arrays.pop('sourceMetadata').item()).encode('utf-8')
        if len(metadata_raw) > MAX_METADATA_BYTES or decode_resource_json(metadata_raw) != reference.metadata:
            _reject('metadata-mismatch')
        pitch = float(arrays['pitch'].item())
        if (tuple(arrays['shape'].tolist()) != shape or not math.isfinite(pitch) or pitch < 0
                or not np.isfinite(arrays['chains']).all() or not np.isfinite(arrays['rays']).all()):
            _reject('values-invalid')
        from fast_structure_alignment import PreparedStructure
        for array in arrays.values():
            array.setflags(write=False)
        return PreparedStructure(arrays['distance'], arrays['chains'], arrays['rays'], pitch, shape)
    except files.ImportRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, zlib.error, EOFError) as error:
        code = 'imported-fast-structure-arrays-invalid'
        raise files.ImportRejected(code, code=code) from error


class ImportedFastStructureSource:
    """No array cache: the common fast solver owns its existing bounded cache."""
    def __init__(self, view, reader, *, interfaces, versions):
        self.view, self.reader, self.root = view, reader, view.root
        self.index_path = interfaces.get('fastStructureIndex')
        self.entries = MappingProxyType({})
        if self.index_path is None:
            self.gaps = ('fast-prepared-not-provided',)
            return
        payload = reader.json(self.index_path)
        if (not isinstance(payload, dict) or set(payload) != {'schemaVersion', 'resourceKind', 'entries'}
                or type(payload['schemaVersion']) is not int or payload['schemaVersion'] != 1
                or payload['resourceKind'] != INDEX_KIND or not isinstance(payload['entries'], list)
                or len(payload['entries']) != len(versions)):
            _reject('index-invalid')
        result, prepared_paths = {}, set()
        inventory = view.inventory
        for row in payload['entries']:
            if (not isinstance(row, dict) or set(row) != {'binding', 'referenceResource', 'preparedResource'}
                    or not isinstance(row['binding'], dict) or set(row['binding']) != set(_BINDING)
                    or any(not isinstance(row['binding'][name], str) for name in _BINDING)):
                _reject('binding-invalid')
            binding = row['binding']
            # Shared version keys use profile/map/variant/floor order.
            key = (binding['profile'], binding['mapId'], binding['variant'], binding['floor'])
            version = versions.get(key)
            if version is None or key in result or binding != version['binding']:
                _reject('binding-mismatch')
            reference = FileRef(view.component_ref, row['referenceResource'])
            prepared = FileRef(view.component_ref, row['preparedResource'])
            if (Path(reference.path).suffix.casefold() != '.npz' or Path(prepared.path).suffix.casefold() != '.npz'
                    or reference.path == prepared.path or prepared.path.casefold() in prepared_paths
                    or prepared.path not in inventory
                    or view.path(reference.path).resolve() != version['path']):
                _reject('resource-binding-invalid')
            prepared_paths.add(prepared.path.casefold())
            result[key] = (reference, prepared)
        if set(result) != set(versions):
            _reject('inventory-incomplete')
        self.entries = MappingProxyType(result)
        self.gaps = ()
        self.assert_current()

    def assert_current(self, cancelled=None):
        files._check_cancelled(cancelled)
        if self.view.root != self.root:
            _reject('component-root-changed')
        files._check_cancelled(cancelled)

    def read(self, reference, *, cancelled=None, strict=False):
        self.assert_current(cancelled)
        if self.index_path is not None:
            self.reader(self.view.path(self.index_path))
        # Even an old package without preparations must not reuse a damaged
        # cached reference in the normal solver after this optional miss.
        self.reader(reference.path)
        binding = reference.metadata.get('binding', {})
        key = (binding.get('profile'), binding.get('mapId'), binding.get('variant'), binding.get('floor'))
        entry = self.entries.get(key)
        if entry is None:
            if self.entries:
                _reject('reference-not-declared')
            self.assert_current(cancelled)
            return None
        reference_ref, prepared_ref = entry
        if self.view.path(reference_ref.path).resolve() != Path(reference.path).resolve():
            _reject('reference-mismatch')
        try:
            raw = self.reader(self.view.path(prepared_ref.path))
        except (OSError, ValueError):
            # Only the optional preparation may fail open. Recheck the view,
            # index and reference so obsolescence/cancellation never does.
            self.assert_current(cancelled)
            self.reader(self.view.path(self.index_path))
            self.reader(reference.path)
            self.assert_current(cancelled)
            if strict:
                raise
            return None
        self.assert_current(cancelled)
        return raw, (str(self.root), reference_ref, prepared_ref, id(reference))

    def decode(self, raw, reference):
        return decode_prepared(raw, reference)

    def validate_all(self, provider, *, compare_derived=True, cancelled=None):
        """Explicit resource operation, never ordinary factory or frame work."""
        from fast_structure_alignment import prepare
        self.assert_current(cancelled)
        for profile, map_id, variant, floor in self.entries:
            self.assert_current(cancelled)
            reference = provider(profile, map_id, floor)
            if reference is None:
                _reject('reference-unavailable')
            raw, _ = self.read(reference, cancelled=cancelled, strict=True)
            prepared = self.decode(raw, reference)
            self.assert_current(cancelled)
            if compare_derived:
                actual = prepare(reference.clean_mask)
                self.assert_current(cancelled)
                if (prepared.shape != actual.shape or prepared.pitch != actual.pitch
                        or any(not np.array_equal(getattr(prepared, name), getattr(actual, name))
                               for name in ('distance', 'chains', 'rays'))):
                    _reject('derived-mismatch')
            self.read(reference, cancelled=cancelled, strict=True)
        self.assert_current(cancelled)
