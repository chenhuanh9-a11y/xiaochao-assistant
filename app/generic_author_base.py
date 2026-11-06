"""Read-only base contracts for display packages, independent of author identity.

Providers are trusted application code, never registered by imported JSON. The
installation root is the parent of ``_internal``. No missing floor is synthesized.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

VERSION_FIELDS = ('structureVersionKey', 'baseSnapshot', 'sourceVersion')
_PROVIDERS = {}


def register_provider(season, mode, resolver):
    """Application startup seam for future resource providers (not ZIP metadata)."""
    if not re.fullmatch(r's[0-9]+', season) or not re.fullmatch(r'[a-z0-9-]+', mode):
        raise ValueError('invalid provider context')
    if (season, mode) in _PROVIDERS or not callable(resolver):
        raise ValueError('duplicate or invalid base provider')
    _PROVIDERS[season, mode] = resolver


def _path(root, relative):
    # Reuse lexical Windows path/reparse validation before resolving. Checking
    # is_symlink alone would accept an in-root directory junction.
    from star_map_import import _target
    path = _target(root, relative)
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('hard-linked base resource')
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('base resource missing or outside provider root')
    return path


def _json(root, relative):
    path = _path(root, relative)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('base declaration too large')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('base declaration must be an object')
    return value


def _one(rows, predicate):
    matches = [row for row in rows if predicate(row)]
    if len(matches) != 1:
        raise ValueError('base binding missing or ambiguous')
    return matches[0]


def _resolve(root, season, mode, mid, floor):
    prefix = f'_internal/seasons/{season}/{mode}' if season != 's0' else '_internal'
    base = root / prefix
    dependencies = set()
    def read(relative):
        dependencies.add(relative)
        return _json(base, relative)

    manifest = None
    if season == 's1':
        from s1_native_resources import registration, MODE_LAYOUTS, S1_FLOORS
        if mid not in MODE_LAYOUTS[mode] or floor not in S1_FLOORS:
            raise ValueError('unknown S1 map or floor')
        manifest = registration(base, mode)
        dependencies.add('native-map-manifest.json')
        interfaces = manifest['runtimeInterfaces']
        source_relative = interfaces['identityPack']
    elif mode == 'difficult':
        manifest = read('difficult-native-manifest.json')
        interfaces = manifest['runtimeInterfaces']
        source_relative = interfaces['sourceManifest']
    else:
        interfaces = dict(typedIndex='references/nightmare-shared-structure-index.json',
                          coordinatePack='references/nightmare-canonical-coordinate-pack.json')
        source_relative = 'references/nightmare-map-pack.json'
    source = read(source_relative)
    if source.get('mode', mode) != mode or source.get('season', season) != season:
        raise ValueError('base source context mismatch')
    source_row = _one(source['maps'], lambda r: r.get('id') == mid)
    # S0 nightmare declares floors per map in layers; native difficult uses
    # per-map versioned floors. Neither declaration invents a missing layer.
    source_layers = source_row.get('floors' if season == 's0' and mode == 'difficult' else 'layers', {})
    if (not isinstance(source_layers, dict) or floor not in source_layers
            or ('floors' in source and floor not in source['floors'])):
        raise ValueError('base source floor unavailable')
    index = read(interfaces['typedIndex'])
    if index.get('schemaVersion') != 1 or index.get('resourceKind') != 'shared-structure':
        raise ValueError('invalid base structure index')
    version_path = index['structureVersionBindings']
    if 'versionBindings' in interfaces and interfaces['versionBindings'] != version_path:
        raise ValueError('base registry interface mismatch')
    registry = read(version_path)
    if registry.get('schemaVersion') != 1 or registry.get('resourceKind') != 'native-structure-version-bindings':
        raise ValueError('invalid base version registry')
    # Historical S0 nightmare typed products use the pack's identity profile
    # (zhan10-speed) even though their coordinate provider is shared-structure.
    profile = source_row.get('identityProfileId', 'shared-structure') if season == 's0' and mode == 'nightmare' else 'shared-structure'
    if not isinstance(profile, str) or not profile:
        raise ValueError('base identity profile unavailable')
    binding = dict(profile=profile, mapId=mid, floor=floor, variant='variant-01')
    entry = _one(index['entries'], lambda r: r.get('mapId') == mid and r.get('floor') == floor)
    if any(entry.get(k) != v for k, v in binding.items()):
        raise ValueError('base structure identity mismatch')
    version = {k: entry[k] for k in VERSION_FIELDS}
    if any(not isinstance(v, str) or not v for v in version.values()):
        raise ValueError('base version identity unavailable')
    reference = entry['resourcePath']
    if season == 's0' and mode == 'difficult':
        source_floor = source_layers[floor]
        if (not isinstance(source_floor, dict) or source_floor.get('referenceResource') != reference
                or any(source_floor.get(k) != v for k, v in version.items())):
            raise ValueError('base source floor binding mismatch')
    dependencies.add(reference)
    reg = _one(registry['entries'], lambda r: r.get('mapId') == mid and r.get('resourceKey', {}).get('floor') == floor)
    key = reg['resourceKey']
    if (version['structureVersionKey'] != f"{key['layoutId']}@{key['structureRawRecordSha256']}/{floor}"
            or reg.get('sourceVersion') != version['sourceVersion']
            or reg.get('sharedLookup') != dict(binding, resourcePath=reference)
            or reg.get('typedReference') != reference
            or source_row.get('sourceLayout') != key['layoutId']
            or source_row.get('structureContentSha256') != key['structureRawRecordSha256']
            or source_row.get('sourceVersion') != version['sourceVersion']):
        raise ValueError('base registry/source mismatch')
    coordinates = read(interfaces['coordinatePack'])
    policy = coordinates.get('policy', {})
    if (coordinates.get('schema') != 'identityv-nightmare-canonical-coordinate-pack-v1'
            or coordinates.get('coordinateReferenceProfileId') != 'shared-structure'
            or policy.get('requiresCurrentFrameGeometryFit') is not True
            or policy.get('authorizesMapIdentity') is not False
            or policy.get('authorizesCrossFloorProjection') is not False):
        raise ValueError('invalid base coordinate contract')
    canvas = coordinates['maps'][mid]['floors'][floor]
    if (canvas.get('referenceResource') != reference
            or any(canvas.get(k) != version[k] for k in ('structureVersionKey', 'sourceVersion'))
            or ('baseSnapshot' in canvas and canvas['baseSnapshot'] != version['baseSnapshot'])):
        raise ValueError('base coordinate version mismatch')
    # The typed product owns shape. S0 index entries do not necessarily carry
    # geometry dimensions; neither an author nor a guessed floor can supply them.
    import numpy as np
    with np.load(_path(base, reference), allow_pickle=False) as archive:
        metadata = json.loads(str(archive['metadata'].item()))
    typed_canvas = metadata['canvas']
    shape = typed_canvas['shapeHW']
    if (not isinstance(shape, list) or len(shape) != 2
            or any(type(n) is not int or n <= 0 or n > 8192 for n in shape)
            or shape[0] * shape[1] > 16 * 1024 * 1024
            or metadata.get('binding') != binding
            or any(metadata.get(k) != v for k, v in version.items())
            or metadata.get('sourceKind') != 'game-rendered-shared-structure'
            or not isinstance(typed_canvas.get('coordinateSpace'), str)
            or not typed_canvas['coordinateSpace']
            or typed_canvas['coordinateSpace'] != canvas.get('coordinateSpace')
            or [canvas.get('coordinateHeight'), canvas.get('coordinateWidth')] != shape):
        raise ValueError('base typed canvas mismatch')
    result = dict(version, coordinateSpace=typed_canvas['coordinateSpace'],
                  referenceShapeHW=shape, referenceResource=f'{prefix}/{reference}')
    if season == 's1':
        native = f'assets/game-native/{mid}/{floor}.png'
        dependencies.add(native)
        result['nativeImageResource'] = f'{prefix}/{native}'
        if manifest['sourceVersion'] != version['sourceVersion']:
            raise ValueError('base manifest version mismatch')
        inventory = manifest['files']
        for relative in dependencies - {'native-map-manifest.json'}:
            row = _one(inventory, lambda r: r.get('path') == relative)
            data = _path(base, relative).read_bytes()
            if row.get('bytes') != len(data) or row.get('sha256') != hashlib.sha256(data).hexdigest():
                raise ValueError('base inventory hash mismatch')
    for relative in dependencies:
        _path(base, relative)
    result['dependencies'] = sorted(f'{prefix}/{p}' for p in dependencies)
    return result


def resolve_layer(root, season, mode, map_id, floor):
    """Resolve a real installed layer; raise ValueError on missing/invalid data."""
    if not all(isinstance(v, str) and v for v in (season, mode, map_id, floor)):
        raise ValueError('base layer identity must use nonempty strings')
    provider = _PROVIDERS.get((season, mode))
    if provider is None:
        raise ValueError('unsupported base resource provider')
    try:
        return provider(Path(root).absolute(), season, mode, map_id, floor)
    except (OSError, KeyError, TypeError, AttributeError, IndexError, zipfile.BadZipFile) as error:
        raise ValueError('base layer contract unavailable') from error


for _mode in ('nightmare', 'difficult'):
    register_provider('s0', _mode, _resolve)
for _mode in ('nightmare', 'difficult', 'difficulty2'):
    register_provider('s1', _mode, _resolve)
