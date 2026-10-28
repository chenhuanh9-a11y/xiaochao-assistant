"""Current-frame entrance ports, before per-map search; unknown stays unknown."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np

ROOM_DIRECTIONS = {
    10201: frozenset(('top', 'left', 'right')),
    10202: frozenset(('left', 'right')),
    10203: frozenset(('top', 'left')),
    10204: frozenset(('top', 'right')),
}


def entrance_anchors(nodes):
    """Measure the entrance's lower vestibule (1.5 by 1 native cells).

    Both stem sides and their outward shoulders must be measured corners.
    A room clipped by fog is not extended to an inferred rectangle.
    """
    anchors = []
    for left in nodes:
        p = left['point']
        # OpenCV exterior contour: upper stem -> lower left -> lower right.
        if np.linalg.norm(left['rays'] - [[0, -1], [1, 0]], axis=1).max() > .09:
            continue
        for right in nodes:
            q = right['point']
            if np.linalg.norm(right['rays'] - [[-1, 0], [0, -1]], axis=1).max() > .09:
                continue
            unit = (q[0]-p[0])/1.5
            if unit < 16 or abs(q[1]-p[1]) > 2:
                continue
            width = q[0]-p[0]
            # The exit glyph can split the bottom edge into two measured
            # segments. Their combined coverage still has to span the base.
            if (max(left['lengths'][1], right['lengths'][0]) > width+2
                    or left['lengths'][1]+right['lengths'][0] < width*.8):
                continue
            if max(abs(left['lengths'][0]/unit-1), abs(right['lengths'][1]/unit-1)) > .12:
                continue
            top_left = p + left['rays'][0]*left['lengths'][0]
            top_right = q + right['rays'][1]*right['lengths'][1]
            shoulders = []
            for expected, rays in ((top_left, [[-1, 0], [0, 1]]),
                                   (top_right, [[0, 1], [1, 0]])):
                shoulders.append(any(np.linalg.norm(n['point']-expected) <= 2
                    and np.linalg.norm(n['rays']-rays, axis=1).max() <= .09
                    and n['lengths'].min() >= unit*.4 for n in nodes))
            if all(shoulders):
                anchors.append(dict(center=[float((p[0]+q[0])/2), float((p[1]+q[1])/2)], unit=float(unit)))
    return anchors


def _internal_wall_support(frame, port, normal, tangent, unit):
    """A bright wall can remain inside a merged grey/brown floor mask."""
    radius=max(3, int(round(unit*.12)))
    along=np.linspace(-.28, .28, 11)*unit
    across=np.arange(-radius-3, radius+4)
    samples=np.rint(port+along[:,None,None]*tangent+across[None,:,None]*normal).astype(int)
    samples[...,0]=np.clip(samples[...,0],0,frame.shape[1]-1)
    samples[...,1]=np.clip(samples[...,1],0,frame.shape[0]-1)
    pixels=frame[samples[...,1],samples[...,0],:3].astype(float)
    gray=pixels@np.array([.114,.587,.299])
    ridge=np.minimum(gray[:,3:-3]-gray[:,:-6],gray[:,3:-3]-gray[:,6:])
    return float(np.max(np.mean(ridge>=4,axis=0)))


def port_states(frame, query, observed, anchor, nodes):
    center = np.asarray(anchor['center']); unit = anchor['unit']
    states = {}; evidence = {}
    for name, offset, normal in (('left', [-1.5, -1.5], [-1, 0]),
                                 ('right', [1.5, -1.5], [1, 0]),
                                 ('top', [0, -4], [0, -1])):
        normal = np.asarray(normal); tangent = normal[::-1]
        port = center + unit*np.asarray(offset)
        along = np.linspace(-.28, .28, 11)*unit
        across = np.linspace(-.3, .3, 11)*unit
        points = np.rint(port + along[:, None, None]*tangent + across[None, :, None]*normal).astype(int)
        if (points[..., 0].min() < 3 or points[..., 1].min() < 3
                or points[..., 0].max() >= query.shape[1]-3 or points[..., 1].max() >= query.shape[0]-3):
            states[name] = 'unknown'; continue
        pixels = query[points[..., 1], points[..., 0]]
        interior = float(pixels[:, :3].mean()); exterior = float(pixels[:, -3:].mean())
        # A real closed wall must have independent sharp-edge support across
        # most of the port; dark/unexplored pixels alone never close a door.
        boundary = np.rint(port + along[:, None]*tangent).astype(int)
        support = []
        for point in boundary:
            x, y = point
            support.append(bool(observed[y-3:y+4, x-3:x+4].any()))
        wall = float(np.mean(support))
        state = ('open' if float(pixels.mean()) >= .9 and min(interior, exterior) >= .9
                 else 'closed' if interior >= .9 and exterior <= .1 and wall >= .9
                 else 'unknown')
        if name in ('left', 'right') and state == 'open':
            offset, rays = (([-1.5, -2], [[0, -1], [-1, 0]]) if name == 'left'
                            else ([1.5, -2], [[1, 0], [0, -1]]))
            expected = center+unit*np.asarray(offset)
            if not any(np.linalg.norm(n['point']-expected) <= 3
                       and np.linalg.norm(n['rays']-rays, axis=1).max() <= .09 for n in nodes):
                state = 'unknown'
        if name == 'top' and state == 'open':
            # A brown room behind a solid top wall can share floor-mask
            # pixels. Require the measured neck's two inward shoulders,
            # not just floor colour on both sides of an internal wall.
            neck = []
            for offset, rays in (([-.5, -4], [[0, -1], [-1, 0]]),
                                 ([.5, -4], [[1, 0], [0, -1]])):
                expected = center+unit*np.asarray(offset)
                neck.append(any(np.linalg.norm(n['point']-expected) <= 3
                    and np.linalg.norm(n['rays']-rays, axis=1).max() <= .09 for n in nodes))
            if not all(neck):
                state = 'unknown'
        ridge = _internal_wall_support(frame, port, normal, tangent, unit) if state == 'open' else 0.
        if ridge >= .8:
            # A wall or coloured door marker crossing a port is unresolved;
            # it cannot authorize an opening or prove a missing branch.
            state = 'unknown'
        states[name] = state
        evidence[name] = dict(interior=interior, exterior=exterior, wallSupport=wall, internalWallSupport=ridge)
    return states, evidence


def observe(frame, context):
    from difficult_entry_identity import entry_corners
    from single_map_structure import current_part
    started = time.perf_counter()
    report = dict(automaticGateDetection=True, reason='entrance-not-observed', states={}, applied=False)
    if context is not None:
        nodes = entry_corners(frame, context)
        anchors = entrance_anchors(nodes)
        extra_bounds = context.get('otherObservationBounds', ())
        if not anchors and extra_bounds:
            # Disconnected explored islands can leave the entrance outside
            # the largest component. This observation must not replace the
            # alignment solver's own component/query.
            from nightmare_candidate import map_color_mask
            for bounds in extra_bounds[:3]:
                x0, y0, x1, y1 = bounds
                patch = frame[y0:y1, x0:x1]
                query = cv2.dilate(map_color_mask(patch, live=True), np.ones((3, 3), np.uint8))
                local = dict(query=query, observationBounds=(0, 0, x1-x0, y1-y0))
                local_nodes = entry_corners(patch, local)
                local_anchors = entrance_anchors(local_nodes)
                if local_anchors:
                    context, frame, nodes, anchors = local, patch, local_nodes, local_anchors
                    report.update(includedDisconnectedEntrance=True, observationOrigin=[x0, y0])
                    break
        report['anchorCount'] = len(anchors)
        if len(anchors) == 1:
            states, evidence = port_states(frame, context['query'], current_part(frame, context, 'observed'), anchors[0], nodes)
            report.update(reason='current-frame-entrance-ports', states=states, evidence=evidence, anchor=anchors[0])
    report['observationMs'] = (time.perf_counter()-started)*1000
    return report


class FrontGatePrefilter:
    def __init__(self, pack, entries):
        self.enabled = pack.mode == 'nightmare' and '--disable-front-gate-prefilter' not in sys.argv
        self.ids = frozenset(str(m.id) for m in pack.maps)
        self.profiles = {}
        for item, *_ in entries:
            if str(item.get('floor')) != '1':
                continue
            room = item.get('entryRoomSource') or {}
            self.profiles[str(item['mapId'])] = (ROOM_DIRECTIONS.get(room.get('roomConfigId'))
                if room.get('rotation') == 1 else None)

    def select(self, frame, floor, geometry_provider):
        if not self.enabled or str(floor) != '1':
            return None, dict(automaticGateDetection=True, applied=False, reason='unsupported-mode-or-floor')
        started=time.perf_counter()
        report = observe(frame, geometry_provider(frame))
        report.update(registeredCount=len(self.ids), selectedCount=len(self.ids),
                      preparationAndObservationMs=(time.perf_counter()-started)*1000)
        states = report['states']
        opened = {d for d, state in states.items() if state == 'open'}
        closed = {d for d, state in states.items() if state == 'closed'}
        # No positive doorway or contradictory port count means unresolved.
        if not opened or len(closed) > 1:
            return None, report
        allowed = frozenset(mid for mid in self.ids if self.profiles.get(mid) is None
            or (opened <= self.profiles[mid] and not closed.intersection(self.profiles[mid])))
        if not allowed:
            report['reason'] = 'no-compatible-layout'
            return None, report
        report.update(applied=len(allowed) < len(self.ids), selectedCount=len(allowed),
                      registeredCount=len(self.ids), selectedMapIds=sorted(allowed))
        return (allowed if report['applied'] else None), report


def record(report):
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    try:
        folder = root/'work'
        folder.mkdir(exist_ok=True)
        with (folder/'front-gate-observation.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(dict(time=datetime.now().astimezone().isoformat(), **report), ensure_ascii=False)+'\n')
    except OSError:
        pass
