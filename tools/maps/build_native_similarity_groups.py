"""Produce author-independent comparison resources from bound native footprints.

Only explicit world bounds and renderer pixel geometry define the common grid.
The output is positive footprint evidence, never a semantic wall/visibility mask.

The output directory is a self-contained candidate with only the selected native
references. It is NOT a complete resource package or a directory overlay: its
shared structure index is deliberately restricted to those references. To use a
candidate with a complete matching base, add only the new group JSON and group
NPZ files listed by the CLI, verify the listed reference dependencies, and keep
the base's full shared structure index unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path, PureWindowsPath
import shutil
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'app'))
from shared_structure_reference import SharedStructureResourceStore


INDEX_NAME = 'references/native-similarity-groups.json'
SHARED_INDEX_NAME = 'references/nightmare-shared-structure-index.json'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_geometry(reference):
    """Validate a direct renderer product and return its world-to-pixel scale."""
    metadata = reference.metadata
    geometry = metadata.get('sourceGeometry') or {}
    canvas = metadata.get('canvas') or {}
    binding = metadata.get('binding') or {}
    if (metadata.get('sourceKind') != 'game-rendered-shared-structure'
            or metadata.get('authorPixelsUsedForStructure') is not False
            or metadata.get('authorValidationUsed') is not False
            or canvas.get('axes') != 'x-right,y-down'
            or canvas.get('floor') != binding.get('floor')
            or canvas.get('layout') != geometry.get('layout')
            or canvas.get('worldFloor') != geometry.get('worldFloor')):
        raise ValueError('native source binding/geometry invalid')
    matrix = np.asarray(geometry.get('matrixRendererPixelToProduct'), dtype=float)
    if matrix.shape != (3, 3) or not np.array_equal(matrix, np.eye(3)):
        raise ValueError('only direct native renderer products are supported')
    shape = list(reference.shape)
    if (geometry.get('nativeShapeHW') != shape or canvas.get('shapeHW') != shape
            or [canvas.get('height'), canvas.get('width')] != shape):
        raise ValueError('native canvas shape mismatch')
    bounds = geometry.get('bounds') or {}
    try:
        x0, y0, x1, y1 = [float(bounds[key]) for key in ('minX', 'minY', 'maxX', 'maxY')]
        scale_x, scale_y = shape[1] / (x1 - x0), shape[0] / (y1 - y0)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError('invalid world bounds') from error
    if (not np.isfinite([x0, y0, x1, y1, scale_x, scale_y]).all()
            or scale_x <= 0 or scale_y <= 0 or not np.isclose(scale_x, scale_y, rtol=0, atol=1e-10)):
        raise ValueError('world bounds must define isotropic native pixels')
    if not metadata.get('structureVersionKey') or not metadata.get('sourceVersion') or not metadata.get('baseSnapshot'):
        raise ValueError('missing source version binding')
    return geometry, scale_x


def _integer(value):
    rounded = round(value)
    if not np.isfinite(value) or abs(value - rounded) > 1e-7:
        raise ValueError('native world bridge requires integer pixel coordinates')
    return rounded


def _validate_windows_output_path(relative):
    # Win32 aliases are not removed by Path.resolve(): a trailing dot/space
    # can overwrite another product, and ':' selects an alternate data stream.
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
                *[prefix + digit for prefix in ('COM', 'LPT') for digit in '123456789¹²³']}
    for part in PureWindowsPath(relative).parts:
        if (part.endswith(('.', ' ')) or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                or part.split('.')[0].rstrip(' ').upper() in reserved):
            raise ValueError(f'invalid Windows output path component: {part}')


def _validate_output_targets(output_root, relative_paths):
    """Resolve the complete write set before any candidate file is created."""
    targets = set()
    for relative in relative_paths:
        _validate_windows_output_path(relative)
        target = (output_root / relative).resolve()
        if not target.is_relative_to(output_root):
            raise ValueError(f'output target outside candidate root: {relative}')
        if any(target == other or target in other.parents or other in target.parents for other in targets):
            raise ValueError(f'output target collision: {relative}')
        targets.add(target)


def build_group(source_root, output_root, *, member_ids, profile, floor='1',
                variant='variant-01', mode='nightmare', group_id='native-04-12-19',
                shared_index=SHARED_INDEX_NAME):
    source_root, output_root = Path(source_root).resolve(), Path(output_root).resolve()
    shared_index = Path(shared_index)
    if shared_index.is_absolute() or '..' in shared_index.parts:
        raise ValueError('shared index must be relative to the resource root')
    _validate_windows_output_path(shared_index)
    if not floor or floor in ('.', '..') or '/' in floor or '\\' in floor:
        raise ValueError('floor must be a single binding token')
    if source_root == output_root or output_root.is_relative_to(source_root):
        raise ValueError('output must be independent of the read-only source root')
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError('output root must be empty; preserve previous candidates')
    member_ids = tuple(member_ids)
    if len(member_ids) < 2 or len(set(member_ids)) != len(member_ids):
        raise ValueError('at least two distinct explicit members required')
    if not group_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in group_id):
        raise ValueError('invalid group id')
    store = SharedStructureResourceStore(source_root / shared_index, source_root)
    if store.error:
        raise ValueError(store.error)
    references, entries, geometries, scales = [], [], [], []
    for map_id in member_ids:
        key = (profile, map_id, variant, floor)
        entry = store.entries.get(key)
        if entry is not None and isinstance(entry.get('resourcePath'), str):
            _validate_windows_output_path(entry['resourcePath'])
        reference = store.load(profile, map_id, floor, variant=variant)
        if entry is None or reference is None:
            raise ValueError(f'missing or invalid native binding: {key}')
        geometry, scale = native_geometry(reference)
        for name in ('structureVersionKey', 'sourceVersion', 'baseSnapshot'):
            if entry.get(name) != reference.metadata[name]:
                raise ValueError(f'stale shared index {name}: {map_id}/{floor}')
        if not str(reference.metadata['structureVersionKey']).endswith('/' + floor):
            raise ValueError('structureVersionKey floor mismatch')
        references.append(reference); entries.append(entry)
        geometries.append(geometry); scales.append(scale)
    if not np.allclose(scales, scales[0], rtol=0, atol=1e-10):
        raise ValueError('member pixel scales differ')
    if len({g['worldFloor'] for g in geometries}) != 1:
        raise ValueError('member world floors differ')
    scale = scales[0]
    bounds = dict(minX=min(g['bounds']['minX'] for g in geometries),
                  minY=min(g['bounds']['minY'] for g in geometries),
                  maxX=max(g['bounds']['maxX'] for g in geometries),
                  maxY=max(g['bounds']['maxY'] for g in geometries))
    shape = (_integer((bounds['maxY'] - bounds['minY']) * scale),
             _integer((bounds['maxX'] - bounds['minX']) * scale))
    arrays, members = {}, []
    for index, (reference, entry, geometry) in enumerate(zip(references, entries, geometries)):
        dx = _integer((geometry['bounds']['minX'] - bounds['minX']) * scale)
        dy = _integer((bounds['maxY'] - geometry['bounds']['maxY']) * scale)
        h, w = reference.shape
        occupancy = np.zeros(shape, dtype=np.uint8)
        occupancy[dy:dy+h, dx:dx+w] = reference.clean_mask
        array = f'member_{index}'
        arrays[array] = occupancy
        metadata = reference.metadata
        members.append(dict(mapId=entry['mapId'], binding=dict(metadata['binding']),
            profile=profile, floor=floor, variant=variant, array=array,
            resourcePath=entry['resourcePath'], resourceSha256=sha256(reference.path),
            structureVersionKey=metadata['structureVersionKey'], sourceVersion=metadata['sourceVersion'],
            baseSnapshot=metadata['baseSnapshot'], shapeHW=list(reference.shape),
            sourceGeometry=geometry, matrixNativeToGroup=[[1., 0., float(dx)], [0., 1., float(dy)], [0., 0., 1.]]))
    stack = np.stack([arrays[row['array']] > 0 for row in members])
    arrays['union_mask'] = np.any(stack, axis=0).astype(np.uint8)
    arrays['common_mask'] = np.all(stack, axis=0).astype(np.uint8)
    pairs = []
    for a, b in itertools.combinations(range(len(members)), 2):
        a_name, b_name = f'pair_{a}_{b}_a_only', f'pair_{a}_{b}_b_only'
        arrays[a_name] = (stack[a] & ~stack[b]).astype(np.uint8)
        arrays[b_name] = (stack[b] & ~stack[a]).astype(np.uint8)
        pairs.append(dict(memberA=member_ids[a], memberB=member_ids[b],
                          aOnlyArray=a_name, bOnlyArray=b_name))
    relative = f'references/native-similarity/{group_id}-{floor}.npz'
    _validate_output_targets(output_root, [INDEX_NAME, shared_index, relative,
        *[entry['resourcePath'] for entry in entries]])
    # Validate all sources and geometry before creating any candidate output.
    output_root.mkdir(parents=True, exist_ok=True)
    for reference, entry in zip(references, entries):
        target = output_root / entry['resourcePath']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reference.path, target)
        if sha256(target) != sha256(reference.path):
            raise ValueError('copied resource readback mismatch')
    target = output_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(target, **arrays)
    with np.load(target, allow_pickle=False) as archive:
        if set(archive.files) != set(arrays) or any(not np.array_equal(archive[k], v) for k, v in arrays.items()):
            raise ValueError('difference archive readback mismatch')
    floor_row = dict(floor=floor, profile=profile, variant=variant,
        canvas=dict(shapeHW=list(shape), worldBounds=bounds, pixelsPerWorldUnit=scale,
                    worldFloor=geometries[0]['worldFloor'], axes='x-right,y-down'),
        members=members, pairs=pairs, differenceResourcePath=relative,
        differenceSha256=sha256(target), unionArray='union_mask', commonArray='common_mask',
        evidenceKind='positive-native-footprint-only')
    payload = dict(schemaVersion=1, resourceKind='native-similarity-groups',
        groups=[dict(groupId=group_id, mode=mode, memberIds=list(member_ids), floors=[floor_row])])
    shared = dict(schemaVersion=1, resourceKind='shared-structure', entries=entries)
    for name, value in ((INDEX_NAME, payload), (shared_index, shared)):
        path = output_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if json.loads(path.read_text(encoding='utf-8')) != value:
            raise ValueError('index readback mismatch')
    return payload


def delivery_summary(payload, output_root, shared_index=SHARED_INDEX_NAME):
    """Describe the candidate's two new products separately from base dependencies."""
    floors = [floor for group in payload['groups'] for floor in group['floors']]
    dependencies = [
        {key: member[key] for key in ('resourcePath', 'resourceSha256', 'binding',
            'structureVersionKey', 'sourceVersion', 'baseSnapshot')}
        for floor in floors for member in floor['members']]
    return dict(index=str(Path(output_root) / INDEX_NAME),
        groups=[dict(groupId=g['groupId'], memberIds=g['memberIds'], floors=[f['floor'] for f in g['floors']])
                for g in payload['groups']],
        candidateKind='self-contained-selected-reference-candidate',
        candidateIsFullPackageReplacement=False,
        completeBaseIntegration=dict(
            newFiles=[INDEX_NAME, *[floor['differenceResourcePath'] for floor in floors]],
            referenceDependencies=dependencies,
            preserveBaseFiles=[Path(shared_index).as_posix()],
            candidateOnlyFiles=[Path(shared_index).as_posix(), *[row['resourcePath'] for row in dependencies]],
            instructions='Add only newFiles to a matching complete base. Verify referenceDependencies. '
                'Never overlay the whole candidate directory or replace the base full shared structure index.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_root', type=Path)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--member', action='append', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--floor', default='1')
    parser.add_argument('--variant', default='variant-01')
    parser.add_argument('--mode', default='nightmare')
    parser.add_argument('--group-id', required=True)
    parser.add_argument('--shared-index', default=SHARED_INDEX_NAME)
    args = parser.parse_args()
    payload = build_group(args.source_root, args.output_root, member_ids=args.member,
        profile=args.profile, floor=args.floor, variant=args.variant, mode=args.mode,
        group_id=args.group_id, shared_index=args.shared_index)
    print(json.dumps(delivery_summary(payload, args.output_root, args.shared_index), ensure_ascii=False))


if __name__ == '__main__':
    main()
