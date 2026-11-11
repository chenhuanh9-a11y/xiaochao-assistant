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
        return self._check_payload(self.view.read(relative), extension)

    def read_many(self, paths, *, expected_root=None, cancelled=None):
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
        return self.view.read_many(relatives, expected_root=expected_root,
                                   cancelled=cancelled, validate_payloads=validate)

    def json(self, relative):
        return decode_resource_json(self(self.view.path(relative)))
