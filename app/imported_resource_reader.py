"""Component-bound content reads for existing recognition resource decoders.

This callback owns file identity and generic JSON/NPZ resource limits. The base
decoder still owns safe map IDs, dependency aliases, field semantics and the
current context. Installing this callback alone does not admit a base package.
"""
import math
from pathlib import Path

import star_map_import as files
from imported_array_archive import validate_npz_bytes


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
        # The immutable view pins this manifest. Cached declarations cannot
        # authorize bytes: every content read still calls view.read again.
        self._inventory = view.inventory

    def __call__(self, path):
        try:
            relative = Path(path).relative_to(self.root).as_posix()
        except (TypeError, ValueError) as error:
            raise files.ImportRejected('imported-reader-path-outside-component',
                                       code='imported-reader-path-outside-component') from error
        extension = Path(relative).suffix.casefold()
        row = self._inventory.get(relative)
        if extension == '.json' and row is not None and row['bytes'] > MAX_JSON_BYTES:
            _reject('json-size')
        raw = self.view.read(relative)
        if extension == '.json':
            decode_resource_json(raw)
        elif extension == '.npz':
            validate_npz_bytes(raw)
        return raw

    def json(self, relative):
        return decode_resource_json(self(self.view.path(relative)))
