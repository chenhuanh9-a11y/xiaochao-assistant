"""Component-bound content reads for existing recognition resource decoders.

This callback owns file identity and generic JSON/NPZ resource limits. The base
decoder still owns safe map IDs, dependency aliases, field semantics and the
current context. Installing this callback alone does not admit a base package.
"""
import math
from pathlib import Path

import star_map_import as files
from imported_array_archive import validate_npz_bytes
from imported_resource_packages import FileRef


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 2_000_000


def _reject(reason):
    code = 'imported-reader-' + reason
    raise files.ImportRejected(code, code=code)


def decode_resource_json(raw):
    if len(raw) > MAX_JSON_BYTES:
        _reject('json-size')
    try:
        value = files._json(raw)
    except (ValueError, RecursionError) as error:
        raise files.ImportRejected('imported-reader-json-invalid', code='imported-reader-json-invalid') from error
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _reject('json-complexity')
        if isinstance(item, float) and not math.isfinite(item):
            _reject('json-nonfinite')
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        # Account for queued children before copying them onto the work stack.
        if nodes + len(pending) + len(children) > MAX_JSON_NODES:
            _reject('json-complexity')
        pending.extend((child, depth + 1) for child in children)
    return value


class ComponentResourceReader:
    def __init__(self, view):
        self.view = view
        self.root = view.root
        # Cache the canonical directory once. Windows may return a short-name
        # root from PackageView and a long-name path from a consumer calling
        # Path.resolve(); both identify the same directory.
        self._canonical_root = self.root.resolve()
        # The immutable view pins this manifest. Cached declarations cannot
        # authorize bytes: single and batch reads still enter the actual view.
        self._inventory = view.inventory

    def relative_path(self, path):
        try:
            candidate = Path(path)
            if not candidate.is_absolute():
                raise ValueError('resource path must be absolute')
            try:
                relative_path = candidate.relative_to(self.root)
            except ValueError:
                # Resolve only the mismatched candidate path. Check its
                # lexical ancestors first so a symlink/junction cannot be
                # normalized into the component boundary.
                files._no_links(candidate)
                relative_path = candidate.resolve().relative_to(self._canonical_root)
            relative = relative_path.as_posix()
        except files.ImportRejected:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise files.ImportRejected('imported-reader-path-outside-component',
                                       code='imported-reader-path-outside-component') from error
        return relative

    def _check_size(self, relative):
        extension = Path(relative).suffix.casefold()
        row = self._inventory.get(relative)
        if extension == '.json' and row is not None and row['bytes'] > MAX_JSON_BYTES:
            _reject('json-size')
        return extension

    @staticmethod
    def _check_payload(raw, extension):
        if extension == '.json':
            decode_resource_json(raw)
        elif extension == '.npz':
            validate_npz_bytes(raw)
        return raw

    def __call__(self, path):
        relative = self.relative_path(path)
        extension = self._check_size(relative)
        raw = self._check_payload(self.view.read(relative), extension)
        self._read_succeeded((relative,))
        return raw

    def _read_succeeded(self, relatives):
        pass

    def read_many(self, paths, *, expected_root=None, cancelled=None, on_payload=None):
        """Stage preflighted payloads locally; return only after the view proof."""
        relatives = tuple(self.relative_path(path) for path in paths)
        extensions = tuple(self._check_size(relative) for relative in relatives)
        ref = self.view.component_ref
        def validate(payloads):
            for relative, raw, extension in zip(relatives, payloads, extensions):
                try:
                    self._check_payload(raw, extension)
                except (OSError, ValueError) as error:
                    error.resource_file_ref = FileRef(ref, relative)
                    raise
        if on_payload is None:
            result = self.view.read_many(relatives, expected_root=expected_root,
                                         cancelled=cancelled, validate_payloads=validate)
        else:
            by_relative = dict(zip(relatives, extensions))
            def stage(file_ref, raw):
                try:
                    self._check_payload(raw, by_relative[file_ref.path])
                except (OSError, ValueError) as error:
                    error.resource_file_ref = file_ref
                    raise
                # A caller's callback is outside payload classification. It may
                # stage bytes, but cannot grant completed-batch availability.
                on_payload(file_ref, raw)
            result = self.view.read_many(relatives, expected_root=expected_root,
                                         cancelled=cancelled, on_payload=stage)
        self._read_succeeded(relatives)
        return result

    def json(self, relative):
        return decode_resource_json(self(self.view.path(relative)))

    def _owner_state(self):
        return id(self.view), self.root, self._canonical_root, id(self._inventory)

    def for_admission(self, scope):
        """A short-lived reader for unpublished construction, never a live callback."""
        if scope.owner_view is not self.view or scope.root != self.root:
            _reject('scope-owner-mismatch')
        scoped = ComponentResourceReader(scope)
        scope.register_owner(self._owner_state)
        scope.register_owner(scoped._owner_state)
        return scoped

    def recording_for_admission(self, scope):
        """Keep actual live proofs while recording private construction reads."""
        if scope.owner_view is not self.view or scope.root != self.root:
            _reject('scope-owner-mismatch')
        reader = _AdmissionLiveReader(self, scope)
        scope.register_owner(reader._owner_state)
        return reader


class _AdmissionLiveReader(ComponentResourceReader):
    """A private observer of successful live reads, detached before publishing."""
    def __init__(self, reader, scope):
        # Reuse only this reader's already checked declarations. Every read
        # below still enters its original actual PackageView and preflight.
        self.view, self.root = reader.view, reader.root
        self._canonical_root, self._inventory = reader._canonical_root, reader._inventory
        self._scope, self._ref = scope, scope.component_ref

    def _owner_state(self):
        return super()._owner_state(), id(self._scope), self._ref

    def _read_succeeded(self, relatives):
        for relative in relatives:
            self._scope.record_consumed(FileRef(self._ref, relative))
