"""Version-bound room/door routing; shared pixels remain the editor coordinate frame.

This is static 2D planning evidence, not complete interior collision or live door state.
No occupied-height node or unknown transfer is admitted as a connection.
"""
from __future__ import annotations
import hashlib
import heapq
import json
import math
import re
import sys
from pathlib import Path
import numpy as np
import cv2

DATA = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'references' / 'route-topology-pilot.json'

def mask_hash(mask):
    return hashlib.sha256(np.ascontiguousarray(mask > 0, dtype=np.uint8).tobytes()).hexdigest()

def simplify(points):
    """Remove only forward collinear vertices; retain bends, diagonal arms and returns."""
    out = []
    for p in points:
        p = tuple(p)
        if out and p == out[-1]:
            continue
        while len(out) >= 2:
            a, b = out[-2:]
            u, v = (b[0]-a[0], b[1]-a[1]), (p[0]-b[0], p[1]-b[1])
            if abs(u[0]*v[1]-u[1]*v[0]) > 1e-6 or u[0]*v[0]+u[1]*v[1] <= 0:
                break
            out.pop()
        out.append(p)
    return out

def _catalog():
    try:
        if DATA.is_file():
            data = json.loads(DATA.read_text(encoding='utf-8'))
        else:
            # A code data module is collected by PyInstaller without broadening
            # the build's resource collection to private references directories.
            from route_topology_pilot_data import PILOT
            data = PILOT
        return data if data.get('schema') == 'custom-route-topology-v1' else {}
    except (OSError, ValueError, ImportError):
        return {}

def display_name(map_id, fallback):
    mapped = _catalog().get('displayNames', {}).get(str(map_id), {}).get('name')
    if mapped:
        return mapped
    match = re.fullmatch(r'nightmare-(\d+)', str(map_id))
    return f'地图 {int(match.group(1)):02d}' if match else fallback

def for_mask(mask):
    digest = mask_hash(mask)
    matches = [f for f in _catalog().get('floors', []) if f.get('maskSha256') == digest
               and tuple(f.get('shape', [])) == tuple(mask.shape)]
    return Topology(mask, matches[0]) if len(matches) == 1 else None

class Topology:
    def __init__(self, mask, data):
        self.mask = mask > 0
        self.data = data
        self.points = [tuple(p) for p in data['points']]
        self.room_ids = data['pointRooms']
        self.edges = [[] for _ in self.points]
        self.diagonal_edges = []
        for a, b, kind in data['links']:
            if kind not in ('room-grid', 'source-door', 'source-diagonal'):
                raise ValueError('Unsupported topology edge')
            p, q = self.points[a], self.points[b]
            if not self.line_on_mask(p, q):
                continue
            weight = math.dist(p, q)
            self.edges[a].append((b, weight)); self.edges[b].append((a, weight))
            if kind == 'source-diagonal':
                self.diagonal_edges.append((a,b))

    def line_on_mask(self, a, b):
        x0, y0 = map(round, a); x1, y1 = map(round, b)
        l, r, t, bot = min(x0,x1), max(x0,x1), min(y0,y1), max(y0,y1)
        if l < 0 or t < 0 or r >= self.mask.shape[1] or bot >= self.mask.shape[0]:
            return False
        line = np.zeros((bot-t+1, r-l+1), np.uint8)
        cv2.line(line, (x0-l,y0-t), (x1-l,y1-t), 1, 1)
        return not np.any((line > 0) & ~self.mask[t:bot+1,l:r+1])

    def anchors(self, point):
        """Only connect a free waypoint within its own source cell/diagonal arm.

        Avoid nearest-node snapping across a room wall. Exported room ownership
        is explicit; geometrically adjacent rooms do not create graph edges.
        """
        x, y = point; pitch = self.data['registration']['pixelsPerTile']
        diagonal = {}
        for a,b in self.diagonal_edges:
            p,q = self.points[a],self.points[b]; dx,dy=q[0]-p[0],q[1]-p[1]
            length2=dx*dx+dy*dy
            if length2 == 0:
                continue
            ratio=((x-p[0])*dx+(y-p[1])*dy)/length2
            projection=(p[0]+ratio*dx,p[1]+ratio*dy)
            if (0 <= ratio <= 1 and math.dist(point,projection) <= pitch/2
                    and self.line_on_mask(point,p) and self.line_on_mask(point,q)):
                diagonal[a]=math.dist(point,p);diagonal[b]=math.dist(point,q)
        if diagonal and len({self.room_ids[i] for i in diagonal}) == 1:
            return list(diagonal.items())
        result = []
        for i, (px,py) in enumerate(self.points):
            if abs(px-x) <= pitch/2 and abs(py-y) <= pitch/2 and self.line_on_mask(point,(px,py)):
                result.append((i, math.dist(point,(px,py))))
        # Ambiguous overlap is closed, rather than inventing a bridge.
        if not result:
            return []
        nearest = min(d for _,d in result)
        rooms = {self.room_ids[i] for i,d in result if abs(d-nearest) < 1e-6}
        return [(i,d) for i,d in result if self.room_ids[i] in rooms] if len(rooms) == 1 else []

    def contains(self, point):
        h,w = self.mask.shape
        try:
            return (0 <= point[0] < w and 0 <= point[1] < h
                    and bool(self.mask[int(point[1]), int(point[0])]) and bool(self.anchors(point)))
        except (TypeError, ValueError, OverflowError):
            return False

    def snap(self, point):
        candidates = self.anchors(point)
        projections = []
        for a,b in self.diagonal_edges:
            p,q=self.points[a],self.points[b];dx,dy=q[0]-p[0],q[1]-p[1];length2=dx*dx+dy*dy
            if length2 == 0:
                continue
            ratio=((point[0]-p[0])*dx+(point[1]-p[1])*dy)/length2
            projected=(round(p[0]+ratio*dx),round(p[1]+ratio*dy))
            if (0 <= ratio <= 1 and math.dist(point,projected) <= self.data['registration']['pixelsPerTile']/2
                    and self.line_on_mask(point,projected) and self.line_on_mask(projected,p)
                    and self.line_on_mask(projected,q)):
                projections.append(projected)
        if projections and candidates:
            return min(projections,key=lambda p:math.dist(point,p))
        return self.points[min(candidates, key=lambda item:item[1])[0]] if candidates else None

    def path(self, start, goal):
        starts, ends = self.anchors(start), dict(self.anchors(goal))
        if not starts or not ends:
            return None
        dist = dict(starts); prev = {}; queue = [(v,i) for i,v in starts]; heapq.heapify(queue)
        finish = None; best = math.inf
        while queue:
            cost, i = heapq.heappop(queue)
            if cost != dist[i] or cost > best:
                continue
            if i in ends and cost+ends[i] < best:
                finish, best = i, cost+ends[i]
            for j, weight in self.edges[i]:
                value = cost+weight
                if value < dist.get(j, math.inf):
                    dist[j] = value; prev[j] = i; heapq.heappush(queue,(value,j))
        if finish is None:
            return None
        path = [finish]
        while path[-1] in prev:
            path.append(prev[path[-1]])
        result = simplify([tuple(start), *[self.points[i] for i in reversed(path)], tuple(goal)])
        return result if all(self.line_on_mask(a,b) for a,b in zip(result,result[1:])) else None
