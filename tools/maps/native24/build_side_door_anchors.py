"""Export geometric F2 doorway anchors from raw layouts and room paths.

    python build_side_door_anchors.py --resources <_internal> \
        --exports <literal-exports.json> --output <new-side-door-anchors.json>

No room semantics, icon offset or passports are synthesized. The output belongs
beside nightmare-entries/side-entries.json in a new resource stage.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def read(path):
    return Path(path).read_text('utf-8-sig')


def literal(value):
    if isinstance(value, dict) and value.get('kind') == 'dict':
        return {str(k): literal(v) for k, v in value['entries']}
    if isinstance(value, list):
        return [literal(v) for v in value]
    return value


def world_voxel(cell, position, rotation):
    x, y, z = cell
    a, b = ((x, z), (z, -x-1), (-x-1, -z-1), (-z-1, x))[rotation % 4]
    return [a+position[0], y+position[1], b+position[2]]


def build(resources, exports_path):
    resources = Path(resources)
    exports = json.loads(read(exports_path))
    layouts = literal(exports['levelLayouts']['exports']['data'])
    rooms = literal(exports['roomDefinitions']['exports']['data'])
    pack = json.loads(read(resources / 'references/nightmare-map-pack.json'))
    index = json.loads(read(resources / 'references/nightmare-shared-structure-index.json'))
    bindings = {(r['mapId'], str(r['floor'])): r for r in index['entries']}
    rows = []
    for registered in pack['maps']:
        mid, layout = registered['id'], str(registered['sourceLayout'])
        raw, binding = layouts[layout], bindings[mid, '2']
        record = dict(room_data=raw['room_data'], door=raw['door'])
        # Verify the existing shared resource identity; do not invent a new key.
        digest = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        version = f'{layout}@{digest}/2'
        assert binding['structureVersionKey'] == version, mid
        with np.load(resources / binding['resourcePath'], allow_pickle=False) as resource:
            meta = json.loads(str(resource['metadata'].item()))
        assert meta['structureVersionKey'] == version and meta['sourceVersion'] == pack['sourceVersion']
        assert meta['canvas']['worldFloor'] == 3 and meta['sourceGeometry']['targetPixelsPerVoxel'] == 60
        bounds = meta['sourceGeometry']['bounds']
        doors = []
        for door_id, (room_index, slot) in raw['door'].items():
            config, position, rotation = raw['room_data'][room_index]
            definition = rooms[str(config)]
            path = definition['room_paths'][slot]
            inside, outside = [world_voxel(p, position, rotation) for p in path]
            if inside[1] != 3:
                continue
            assert outside[1] == 3
            assert path[0] in definition['room_voxels'] and path[1] not in definition['room_voxels']
            dx, dz = outside[0]-inside[0], outside[2]-inside[2]
            direction = {(1, 0): 'E', (-1, 0): 'W', (0, 1): 'N', (0, -1): 'S'}[dx, dz]
            boundary = [(inside[0]+outside[0]+1)*30-bounds['minX']*60/128,
                        -(inside[2]+outside[2]+1)*30-bounds['minY']*60/128]
            assert all(0 <= v < limit for v, limit in zip(boundary, meta['canvas']['shapeHW'][::-1]))
            doors.append(dict(mapId=mid, floor='2', layoutId=layout,
                structureVersionKey=version, sourceVersion=meta['sourceVersion'],
                boundaryCenter=boundary, outwardDirection=direction,
                sourceDoorId=str(door_id), roomIndex=room_index, doorSlot=slot,
                worldVoxelInside=inside, worldVoxelOutside=outside))
        assert len(doors) == 1, (mid, 'requires exactly one source F2 side door', len(doors))
        rows.extend(doors)
    assert len(rows) == len(pack['maps']) == 24
    return dict(schemaVersion=1, mode='nightmare', profileId='zhan10-speed',
                coordinateSpace='per-map F2 native pixels; geometric boundary, not icon center', entries=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--exports', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.resources, args.exports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps(dict(maps=len(result['entries']), output=str(args.output))))
