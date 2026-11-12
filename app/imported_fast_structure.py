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


class _PreparedReadBatch:
    """One resource operation's verified compressed bytes, never an array cache."""
    def __init__(self, source, payloads, cancelled, state):
        self.source, self.payloads, self.cancelled = source, payloads, cancelled
        self.state = state
        self._current()

    def _current(self):
        files._check_cancelled(self.cancelled)
        if self.source._read_state() != self.state:
            _reject('component-root-changed')

    def read_resource(self, path):
        self._current()
        return self.payloads[self.source.reader.relative_path(path)]

    def read(self, reference):
        return self.source._read(reference, self._read_many, self._current)

    def _read_many(self, paths):
        return tuple(self.read_resource(path) for path in paths)

    def release(self):
        self.payloads.clear()


class _PreparedReadFailure:
    """Deliver a pre-read's first real error to the original consumer once.

    Actual successful payloads are consumed once, in consumer order. The
    provider keeps its ordinary warm/cold behavior; the common prepared loader
    owns optional fallback, retry ordering and its existing cache.
    """
    def __init__(self, source, error, prefix, cancelled, state):
        self.source, self.error, self.cancelled, self.state = source, error, cancelled, state
        self.prefix = prefix

    def assert_current(self, cancelled=None):
        files._check_cancelled(cancelled)
        self.source.assert_current(self.cancelled)
        if self.source._read_state() != self.state:
            _reject('component-root-changed')
        reader = self.source.reader
        if reader.view is not self.source.view and reader.view.root != reader.root:
            _reject('component-root-changed')

    def _read_many(self, paths):
        self.assert_current()
        result = []
        raw = error = None
        try:
            for path in paths:
                relative = self.source.reader.relative_path(path)
                if relative in self.prefix:
                    raw = self.prefix.pop(relative)
                elif self.error is not None and relative == self.error.resource_file_ref.path:
                    error, self.error = self.error, None
                    raise error
                else:
                    raw = self.source._read_paths((path,), cancelled=self.cancelled)[0]
                result.append(raw)
            self.assert_current()
            return tuple(result)
        finally:
            # A caught optional error retains this frame through its traceback.
            # Only the returned tuple may own successful payloads; abandoned
            # reads and the replayed exception must not remain in frame locals.
            result.clear()
            raw = error = None

    def read_resource(self, path):
        return self._read_many((path,))[0]

    def read(self, reference, *, cancelled=None, strict=False):
        files._check_cancelled(cancelled)
        return self.source._read(reference, self._read_many, self.assert_current, strict=strict)

    def decode(self, raw, reference):
        return self.source.decode(raw, reference)

    def acquire_prepared_use(self, reference, *, cancelled=None):
        from fast_structure_alignment import _acquire_imported_prepared
        return _acquire_imported_prepared(reference, self, cancelled)

    def release(self):
        # Replayed exceptions can keep traceback cycles alive until GC. Their
        # operation must still release staged bytes before the final full read.
        self.prefix.clear()
        self.error = None


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

    def _read_state(self):
        return (id(self.view), id(self.reader), self.root, id(self.reader.view),
                self.index_path, id(self.entries), self.view.component_ref,
                self.reader.view.component_ref, self.reader.root)

    def _read_paths(self, paths, *, cancelled=None, on_payload=None):
        view, reader, root = self.view, self.reader, self.root
        reader_view = reader.view
        options = dict(expected_root=root if reader_view is view else None, cancelled=cancelled)
        if on_payload is not None:
            options['on_payload'] = on_payload
        payloads = reader.read_many(paths, **options)
        if (self.view is not view or self.reader is not reader or self.root != root
                or reader.view is not reader_view):
            _reject('component-root-changed')
        if reader_view is not view:
            self.assert_current(cancelled)
        files._check_cancelled(cancelled)
        return payloads

    def read(self, reference, *, cancelled=None, strict=False):
        return self._read(reference,
            lambda paths: self._read_paths(paths, cancelled=cancelled),
            lambda: self.assert_current(cancelled), strict=strict)

    def _read(self, reference, read_many, assert_current, *, strict=False):
        assert_current()
        reader, root = self.reader, self.root
        # Even an old package without preparations must not reuse a damaged
        # cached reference in the normal solver after this optional miss.
        required = ([root / self.index_path] if self.index_path is not None else [])
        required.append(reference.path)
        binding = reference.metadata.get('binding', {})
        key = (binding.get('profile'), binding.get('mapId'), binding.get('variant'), binding.get('floor'))
        entry = self.entries.get(key)
        if entry is None:
            read_many(required)
            if self.entries:
                _reject('reference-not-declared')
            return None
        reference_ref, prepared_ref = entry
        if reader.relative_path(reference.path) != reference_ref.path:
            read_many(required)
            _reject('reference-mismatch')
        try:
            raw = read_many((*required, root / prepared_ref.path))[-1]
        except (OSError, ValueError) as error:
            if getattr(error, 'resource_file_ref', None) != prepared_ref:
                raise
            # Only the optional preparation may fail open. Recheck the view,
            # index and reference so obsolescence/cancellation never does.
            assert_current()
            read_many(required)
            if strict:
                raise
            return None
        return raw, (str(root), reference_ref, prepared_ref, id(reference))

    def prepare_batch(self, bindings, *, required_paths=(), cancelled=None):
        """Read declared references/preparations once for a bounded static build.

        Declaration failures propagate. A typed/prepared failure is delivered
        once through operation-local callbacks to the original provider/loader,
        preserving cold misses, warm required errors and optional read identity.
        Context errors have no payload attribution and never take this fallback.
        """
        bindings = tuple(bindings)
        required_paths = tuple(required_paths)
        state = self._read_state()
        prefix = {}
        def stage(file_ref, raw):
            prefix[file_ref.path] = raw
        try:
            return self._prepare_batch(bindings, required_paths=required_paths, cancelled=cancelled,
                                       on_payload=stage)
        except (OSError, ValueError) as error:
            failed = getattr(error, 'resource_file_ref', None)
            if failed is None:
                raise
            self.assert_current(cancelled)
            declarations = {self.reader.relative_path(path) for path in required_paths}
            if failed.path in declarations:
                raise
            # Retain the actual successful prefix once, then resume through
            # each original consumer. No other map's cold typed read becomes
            # an eager whole-pool required check on this failure path.
            failure = _PreparedReadFailure(self, error.with_traceback(None), prefix, cancelled, state)
            failure.assert_current()
            return failure

    def _prepare_batch(self, bindings, *, required_paths, cancelled, on_payload=None):
        self.assert_current(cancelled)
        bindings = tuple(bindings)
        state = self._read_state()
        paths = list(required_paths)
        if bindings and self.index_path is not None:
            paths.append(self.root / self.index_path)
        for binding, path in bindings:
            paths.append(path)
            key = (binding['profile'], binding['mapId'], binding['variant'], binding['floor'])
            entry = self.entries.get(key)
            if entry is not None:
                if self.reader.relative_path(path) != entry[0].path:
                    return None  # Keep the ordinary source's mismatch handling.
                paths.append(self.root / entry[1].path)
        paths = tuple(dict.fromkeys(Path(path) for path in paths))
        raw = self._read_paths(paths, cancelled=cancelled, on_payload=on_payload)
        return _PreparedReadBatch(self,
            dict(zip((self.reader.relative_path(path) for path in paths), raw)), cancelled, state)

    def read_many(self, references, *, required_paths=(), cancelled=None):
        """Batch actual-byte checks, preserving each ordinary read's identity."""
        references = tuple(references)
        required_paths = tuple(required_paths)
        bindings = tuple((reference.metadata.get('binding', {}), reference.path)
                         for reference in references)
        # Invalid lookup bindings belong to the original single-read classifier.
        if any(set(binding) != set(_BINDING) for binding, _ in bindings):
            self._read_paths(required_paths, cancelled=cancelled)
            return tuple(self.read(reference, cancelled=cancelled) for reference in references)
        try:
            batch = self._prepare_batch(bindings, required_paths=required_paths, cancelled=cancelled)
        except (OSError, ValueError) as error:
            failed = getattr(error, 'resource_file_ref', None)
            required = {self.reader.relative_path(path) for path in required_paths}
            required.update(self.reader.relative_path(reference.path) for reference in references)
            if self.index_path is not None:
                required.add(self.index_path)
            optional = {self.entries[(binding['profile'], binding['mapId'],
                binding['variant'], binding['floor'])][1] for binding, _ in bindings
                if (binding['profile'], binding['mapId'], binding['variant'], binding['floor']) in self.entries}
            if failed not in optional or failed.path in required:
                raise
            # Preserve the first optional miss, even if a one-shot I/O failure
            # would succeed on retry. The ordinary classifier still rechecks
            # each required input and owns currentness/cancellation handling.
            self.assert_current(cancelled)
            self._read_paths(required_paths, cancelled=cancelled)
            def read_paths(paths):
                if any(self.reader.relative_path(path) == failed.path for path in paths):
                    raise error
                return self._read_paths(paths, cancelled=cancelled)
            return tuple(self._read(reference, read_paths, lambda: self.assert_current(cancelled))
                         for reference in references)
        if batch is None:
            return tuple(self.read(reference, cancelled=cancelled) for reference in references)
        return tuple(batch.read(reference) for reference in references)

    def decode(self, raw, reference):
        return decode_prepared(raw, reference)

    def acquire_prepared_use(self, reference, *, cancelled=None):
        """Let one solve retain a value plus its loader-verified resource binding."""
        from fast_structure_alignment import _acquire_imported_prepared
        return _acquire_imported_prepared(reference, self, cancelled)

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
