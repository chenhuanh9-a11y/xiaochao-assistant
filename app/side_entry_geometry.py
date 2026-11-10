"""Optional source doorway geometry; no map identity, pose or session state."""
import json
from pathlib import Path

import numpy as np


def _bounded_list(value, maximum, label):
    """Bound source-derived work before a decoder creates numeric arrays."""
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('entry-resource-complexity: ' + label)
    return value


def _bounded_points(value, maximum, label):
    for point in _bounded_list(value, maximum, label):
        if not isinstance(point, list) or len(point) != 2 or any(isinstance(v, (list, dict)) for v in point):
            raise ValueError('invalid entry coordinates: ' + label)
    return value


def _bounded_wall_lines(value, maximum, label):
    for line in _bounded_list(value, maximum, label):
        if any(isinstance(line[key], (list, dict)) for key in ('axis', 'sign', 'normal', 'lo', 'hi')):
            raise ValueError('invalid entry wall coordinates: ' + label)
    return value


def load_side_doors(root, entries, mode, profile, *, read_resource=None, typed_index_path=None):
    """Enable only a complete, version-matched F2 source inventory.

    The geometric boundary is deliberately separate from icon-centred legacy
    passports. Missing/stale optional data leaves ordinary structure search on.
    """
    if mode != 'nightmare':
        return {}, 'not-nightmare'
    try:
        root = Path(root)
        data_path = root / 'side-door-anchors.json'
        index_path = (Path(typed_index_path) if typed_index_path is not None else
                      root.parent / 'nightmare-shared-structure-index.json')
        data = json.loads(data_path.read_text('utf8') if read_resource is None else read_resource(data_path))
        index = json.loads(index_path.read_text('utf8') if read_resource is None else read_resource(index_path))
        if read_resource is not None:
            _bounded_list(data['entries'], 256, 'side doors')
            _bounded_list(index['entries'], 1024, 'typed bindings')
            for door in data['entries']:
                _bounded_points([door['boundaryCenter']], 1, 'side-door center')
        if (data['schemaVersion'], data['mode'], data['profileId']) != (1, mode, profile):
            raise ValueError('side-door mode/profile mismatch')
        targets = {item['mapId']: item for item, *_ in entries if str(item['floor']) == '2'}
        bindings = {r['mapId']: r for r in index['entries']
                    if r['profile'] == profile and str(r['floor']) == '2'}
        doors = {r['mapId']: r for r in data['entries']}
        if not targets or len(doors) != len(data['entries']) or set(doors) != set(targets):
            raise ValueError('side-door inventory incomplete')
        for mid, door in doors.items():
            item, binding = targets[mid], bindings[mid]
            if (str(door['floor']) != '2' or door['structureVersionKey'] != binding['structureVersionKey']
                    or door['sourceVersion'] != binding['sourceVersion']
                    or Path(binding['resourcePath']).name != item['sourceProduct']
                    or door['layoutId'] != item['canvas']['layout']
                    or door['outwardDirection'] not in ('N', 'S', 'W', 'E')):
                raise ValueError('side-door source mismatch: ' + mid)
            center = np.asarray(door['boundaryCenter'], dtype=float)
            if (center.shape != (2,) or not np.isfinite(center).all() or np.any(center < 0)
                    or np.any(center >= [item['canvas']['width'], item['canvas']['height']])):
                raise ValueError('side-door boundary outside canvas: ' + mid)
        return doors, None
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {}, str(error)


def observed_side_boundary(observations):
    """Require one current icon AND its independently measured local walls.

    Whole-visible-envelope direction is insufficient for a doorway coordinate.
    Multiple exits remain unknown instead of choosing the highest icon score.
    """
    found = []
    cardinal = {'左': 'W', '右': 'E', '上': 'N', '下': 'S'}
    for observation in observations:
        direction = observation.get('direction') or {}
        if not (observation.get('reliable') and direction.get('reliable')
                and direction.get('localRectangleAccepted')):
            continue
        name = cardinal.get(direction.get('direction'))
        if name is None:
            continue
        x0, y0, x1, y1 = direction['rectangleBounds']
        centers = {'E': [x1, (y0+y1)/2], 'W': [x0, (y0+y1)/2],
                   'N': [(x0+x1)/2, y0], 'S': [(x0+x1)/2, y1]}
        found.append(dict(center=centers[name], direction=name,
                          iconScore=observation['icon']['score']))
    return found[0] if len(found) == 1 else None


def boundary_matches(door, observed, scale, translation, unit):
    """Same 0.2-cell / six-pixel entrance tolerance as existing passports."""
    scale = np.asarray(scale)
    return (door['outwardDirection'] == observed['direction'] and
            np.linalg.norm(scale[..., None]*np.asarray(door['boundaryCenter'])+translation-observed['center'], axis=-1)
            <= np.maximum(6., scale*unit*.2))
