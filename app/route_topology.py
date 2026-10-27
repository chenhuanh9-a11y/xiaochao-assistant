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
        self.axis_edges = []
        for a, b, kind in data['links']:
            if kind not in ('room-grid', 'source-door', 'source-diagonal'):
                raise ValueError('Unsupported topology edge')
            p, q = self.points[a], self.points[b]
            if kind != 'source-diagonal' and p[0] != q[0] and p[1] != q[1]:
                raise ValueError('Ordinary topology edges must be axis aligned')
            if not self.line_on_mask(p, q):
                continue
            weight = math.dist(p, q)
            self.edges[a].append((b, weight)); self.edges[b].append((a, weight))
            if kind == 'source-diagonal':
                self.diagonal_edges.append((a,b))
            elif kind == 'room-grid' and self.room_ids[a] == self.room_ids[b]:
                self.axis_edges.append((a,b))

    def line_on_mask(self, a, b):
        x0, y0 = map(round, a); x1, y1 = map(round, b)
        l, r, t, bot = min(x0,x1), max(x0,x1), min(y0,y1), max(y0,y1)
        if l < 0 or t < 0 or r >= self.mask.shape[1] or bot >= self.mask.shape[0]:
            return False
        line = np.zeros((bot-t+1, r-l+1), np.uint8)
        cv2.line(line, (x0-l,y0-t), (x1-l,y1-t), 1, 1)
        return not np.any((line > 0) & ~self.mask[t:bot+1,l:r+1])

    def _attachments(self, point):
        """Explicit, room-owned endpoint paths: orthogonal except source diagonals.

        Project onto real room edges, connecting to either end. This splits an
        edge conceptually, avoiding both diagonal tails and a forced backtrack
        through the nearest cell centre. Door edges are never endpoint shortcuts.
        """
        x, y = point; pitch = self.data['registration']['pixelsPerTile']; options=[]
        for a,b in self.diagonal_edges+self.axis_edges:
            p,q = self.points[a],self.points[b]; dx,dy=q[0]-p[0],q[1]-p[1]
            length2=dx*dx+dy*dy
            if length2 == 0:
                continue
            ratio=((x-p[0])*dx+(y-p[1])*dy)/length2
            projection=(p[0]+ratio*dx,p[1]+ratio*dy)
            if not(0 <= ratio <= 1 and math.dist(point,projection) <= pitch/2):
                continue
            diagonal=(a,b) in self.diagonal_edges
            lead=None
            if diagonal:
                # Retain the exact source slope. An off-axis waypoint joins its
                # projection orthogonally instead of slanting to a distant node.
                for bend in [(projection[0],y),(x,projection[1])]:
                    candidate=simplify([tuple(point),bend,projection])
                    if all(self.line_on_mask(u,v) for u,v in zip(candidate,candidate[1:])):
                        lead=candidate;break
                if lead is None:
                    continue
            for i in (a,b):
                path=simplify([*(lead or [tuple(point),projection]),self.points[i]])
                if all(self.line_on_mask(u,v) for u,v in zip(path,path[1:])):
                    options.append((i,sum(math.dist(u,v) for u,v in zip(path,path[1:])),path,math.dist(point,projection)))
        for i, (px,py) in enumerate(self.points):
            if abs(px-x) > pitch/2 or abs(py-y) > pitch/2:
                continue
            for bend in [(px,y),(x,py)]:
                path=simplify([tuple(point),bend,(px,py)])
                if all(self.line_on_mask(u,v) for u,v in zip(path,path[1:])):
                    options.append((i,sum(math.dist(u,v) for u,v in zip(path,path[1:])),path,math.dist(point,(px,py))))
                    break
        # Ambiguous overlap is closed, rather than inventing a bridge.
        if not options:
            return {}
        nearest=min(o[3] for o in options)
        rooms={self.room_ids[i] for i,_,_,d in options if abs(d-nearest)<1e-6}
        if len(rooms)!=1:
            return {}
        result={}
        for i,cost,path,_ in options:
            if self.room_ids[i] in rooms and cost < result.get(i,(math.inf,[]))[0]:
                result[i]=(cost,path)
        return result

    def anchors(self, point):
        return [(i,cost) for i,(cost,_) in self._attachments(point).items()]

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
        starts, ends = self._attachments(start), self._attachments(goal)
        if not starts or not ends:
            return None
        dist = {i:v[0] for i,v in starts.items()}; prev = {}; queue = [(v,i) for i,v in dist.items()]; heapq.heapify(queue)
        finish = None; best = math.inf
        while queue:
            cost, i = heapq.heappop(queue)
            if cost != dist[i] or cost > best:
                continue
            if i in ends and cost+ends[i][0] < best:
                finish, best = i, cost+ends[i][0]
            for j, weight in self.edges[i]:
                value = cost+weight
                if value < dist.get(j, math.inf):
                    dist[j] = value; prev[j] = i; heapq.heappush(queue,(value,j))
        if finish is None:
            return None
        path = [finish]
        while path[-1] in prev:
            path.append(prev[path[-1]])
        result = simplify([*starts[path[-1]][1], *[self.points[i] for i in reversed(path)], *reversed(ends[finish][1])])
        return result if all(self.line_on_mask(a,b) for a,b in zip(result,result[1:])) else None
