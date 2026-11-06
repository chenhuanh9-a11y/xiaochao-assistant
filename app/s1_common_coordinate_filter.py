"""Private candidate-independent common-coordinate prototype.
No manual ROI, identity winner, previous-floor pose, GUI, or game-control input.
Provider returns actual typed references. Caller owns round/frame cancellation.
"""
from collections import Counter, OrderedDict
import json
from pathlib import Path
import time
import cv2
import numpy as np

class CommonCoordinateFilter:
    def __init__(self, provider, index, *, mode, resource_key):
        self.provider = provider  # provider(map_id, floor) -> typed reference
        self.index = json.loads(Path(index).read_text('utf-8')) if isinstance(index,(str,Path)) else index
        if self.index.get('schema')!='s1-common-entrance-coordinate-index-v1':raise ValueError('coordinate-index-schema')
        self.mode,self.resource_key=mode,resource_key
        self.entries=self.index['maps'][mode]
        self._cache=OrderedDict()
        self._pool_pending=OrderedDict()

    def _pool(self, ids, floor, cancelled):
        """Resume only immutable resource work; no frame/pose/evidence is cached.

        The runtime owns the single solver lane. Pending jobs are private and
        never returned until every resource product has completed and bound.
        """
        from fast_structure_alignment import prepare, PreparedStructure
        from single_map_structure import contour_graph
        pool_key = (self.resource_key, tuple(sorted(ids)), floor)
        if pool_key in self._cache:
            self._cache.move_to_end(pool_key)
            return None if cancelled() else self._cache[pool_key]
        if cancelled():
            return None
        job = self._pool_pending.get(pool_key)
        if job is None:
            # Pending and complete products share the same three-pool bound.
            # Drop the oldest partial job first, then the oldest complete pool.
            while len(self._pool_pending) + len(self._cache) >= 3:
                if self._pool_pending:
                    self._pool_pending.popitem(last=False)
                else:
                    self._cache.popitem(last=False)
            job = dict(items=[], masks=[], edges=[], fields=[], keep=[], chain_index=0,
                       union_chains=[], union_rays=[], union_seen=set(), union_map=0,
                       union_node=0)
            self._pool_pending[pool_key] = job
        self._pool_pending.move_to_end(pool_key)
        items = job['items']
        for mid in ids[len(items):]:
            if cancelled():
                return None
            entry = self.entries[mid][floor]
            ref = self.provider(mid, floor)
            if ref is None:
                raise ValueError('coordinate-reference-missing:' + mid)
            meta = ref.metadata
            geo = meta.get('sourceGeometry', {})
            if (meta.get('sourceVersion') != self.index['sourceVersion'] or
                geo.get('sourceCatalogSha256') != entry['sourceCatalogSha256'] or
                geo.get('rendererMetadataSha256') != entry['sourceRendererMetadataSha256'] or
                list(ref.clean_mask.shape) != entry['shapeHW']):
                raise ValueError('coordinate-reference-binding-mismatch:' + mid)
            transform = np.asarray(entry['imageToEntranceXZ'], float)
            if not np.array_equal(transform[:2, :2], [[1, 0], [0, -1]]):
                raise ValueError('coordinate-index-axes')
            origin = np.array([transform[0, 2], -transform[1, 2]])
            if not np.array_equal(origin, np.rint(origin)):
                raise ValueError('noninteger-native-origin')
            items.append((mid, (np.asarray(ref.clean_mask) > 0).astype(np.uint8), origin.astype(int)))
        if cancelled():
            return None
        if 'origin' not in job:
            job['origin'] = np.min([item[2] for item in items], axis=0)
            hi = np.max([item[2] + np.array(item[1].shape[::-1]) for item in items], axis=0)
            job['shape'] = tuple((hi - job['origin'])[::-1])
        lo, shape = job['origin'], job['shape']
        masks, edges, fields = job['masks'], job['edges'], job['fields']
        for mid, mask, origin in items[len(fields):]:
            if cancelled():
                return None
            # Commit a complete static layer together. Cancellation after its
            # kernels retains the work for the next current-frame request.
            canvas = np.zeros(shape, np.uint8)
            x, y = origin - lo
            h, w = mask.shape
            canvas[y:y+h, x:x+w] = mask
            edge = cv2.morphologyEx(canvas, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
            field = cv2.distanceTransform(1-edge, cv2.DIST_L2, 3)
            masks.append(canvas); edges.append(edge); fields.append(field)
        if cancelled():
            return None
        if 'common' not in job:
            job['common'], job['shared'] = masks[0].copy(), edges[0].copy()
            job['union_mask'] = masks[0].copy()
            job['merge_index'] = 1
        while job['merge_index'] < len(masks):
            if cancelled():
                return None
            i = job['merge_index']
            np.minimum(job['common'], masks[i], out=job['common'])
            np.minimum(job['shared'], edges[i], out=job['shared'])
            np.maximum(job['union_mask'], masks[i], out=job['union_mask'])
            job['merge_index'] += 1
        common, shared = job['common'], job['shared']
        if cancelled():
            return None
        if 'prepared' not in job:
            job['prepared'] = prepare(common)
        if cancelled():
            return None
        if 'near' not in job:
            job['near'] = cv2.dilate(shared, np.ones((3, 3), np.uint8))
        prepared, near = job['prepared'], job['near']
        while job['chain_index'] < len(prepared.chains):
            if cancelled():
                return None
            i = job['chain_index']; chain = prepared.chains[i]; good = True
            for endpoint in (chain[0], chain[2]):
                xy = np.rint(chain[1] + np.linspace(0, 1, int(np.ceil(np.linalg.norm(endpoint-chain[1])))+1)[:, None] * (endpoint-chain[1])).astype(int)
                if not near[xy[:, 1], xy[:, 0]].all():
                    good = False; break
            if good:
                job['keep'].append(i)
            job['chain_index'] += 1
        if cancelled():
            return None
        if 'distance' not in job:
            job['distance'] = np.rint(np.minimum(cv2.distanceTransform(1-shared, cv2.DIST_L2, 3), 16383.75)*4).astype(np.uint16)
        if floor == '-1':
            while job['union_map'] < len(masks):
                if cancelled():
                    return None
                if 'union_nodes' not in job:
                    job['union_nodes'] = contour_graph(masks[job['union_map']])
                    job['union_node'] = 0
                nodes = job['union_nodes']
                while job['union_node'] < len(nodes):
                    if cancelled():
                        return None
                    node_index = job['union_node']; node = nodes[node_index]
                    indices = [node['previous'], node_index, node['next']]
                    if all(nodes[i]['valid'] for i in indices):
                        chain = np.asarray([nodes[i]['point'] for i in indices], float) + lo
                        chain_key = tuple(chain.ravel())
                        if chain_key not in job['union_seen']:
                            job['union_seen'].add(chain_key)
                            job['union_chains'].append(chain); job['union_rays'].append(node['rays'])
                    job['union_node'] += 1
                del job['union_nodes']
                job['union_map'] += 1
        if cancelled():
            return None
        if 'difference' not in job:
            job['difference'] = cv2.dilate((job['union_mask'] != common).astype(np.uint8), np.ones((31, 31), np.uint8))
        data = dict(ids=tuple(ids), floor=floor, origin=lo, shape=shape,
                    prepared=PreparedStructure(job['distance'], prepared.chains[job['keep']], prepared.rays[job['keep']], prepared.pitch, prepared.shape),
                    fields=fields, unionChains=np.asarray(job['union_chains']).reshape(-1, 3, 2),
                    unionRays=np.asarray(job['union_rays']).reshape(-1, 2, 2), difference=job['difference'])
        # Completed static data is safe to retain even if the owning frame has
        # just expired. It must not authorize that obsolete frame's result.
        self._cache[pool_key] = data
        self._pool_pending.pop(pool_key, None)
        while len(self._cache) > 3:
            self._cache.popitem(last=False)
        return None if cancelled() else data

    @staticmethod
    def _domains_from_common(frame, ctx, prepared, cancelled):
        # The production propose() intentionally returns only its winner. Do
        # not use that API here: examine ALL finite chain-generated positions.
        from fast_structure_alignment import _chain_seeds
        from single_map_structure import observed_corner_chains
        if len(prepared.chains) < 6:
            return []
        query = observed_corner_chains(frame, ctx)
        if len(query) < 6:
            return []
        seeds = _chain_seeds(frame, ctx, prepared, cancelled)
        if not seeds:
            return []  # Includes exhausted proposal budget, never uniqueness.
        domains, seen = [], set()
        for scale, translation in seeds:
            if cancelled():
                return []
            src = prepared.chains
            centers = src[:, 1] * scale + translation
            lengths = np.linalg.norm((src[:, [0, 2]] - src[:, 1, None]) * scale, axis=2)
            pairs = []
            for qi, node in enumerate(query):
                near = np.sum((centers - node['points'][1]) ** 2, axis=1) <= 48 ** 2
                oriented = np.min(np.sum(prepared.rays * node['rays'], axis=2), axis=1) >= .95
                adjacent = np.max(np.abs(lengths - np.linalg.norm(node['points'][[0, 2]] - node['points'][1], axis=1)), axis=1) <= 3
                pairs.extend((qi, int(si)) for si in np.flatnonzero(near & oriented & adjacent))
            key = tuple(pairs)
            if key in seen:
                continue
            seen.add(key)
            domain = CommonCoordinateFilter._domain_from_pairs(src, query, pairs)
            if domain is not None:
                hyp, matrix, evidence = domain
                evidence = dict(evidence, proposalSearch='all-common-chain-seeds',
                                commonSeedCount=len(seeds), perCandidateFreeFit=False)
                domains.append((hyp, matrix, evidence))
        return domains

    @staticmethod
    def _domain_from_pairs(src, query, pairs):
        qc=Counter(q for q,s in pairs);sc=Counter(s for q,s in pairs);pairs=[(q,s) for q,s in pairs if qc[q]==1 and sc[s]==1]
        if len(pairs)<6:return None
        vertices={}
        for q,s in pairs:
            for a,b in zip(src[s],query[q]['points']):
                k=tuple(a);v=tuple(b)
                if k in vertices and vertices[k]!=v:return None
                vertices[k]=v
        source=np.asarray(list(vertices));observed=np.asarray(list(vertices.values()))
        if len(source)<6:return None
        # Fixed-axis isotropic model: any independently corresponding nonzero
        # segment constrains scale. Do not inherit a general-affine 2D SVD gate.
        error=3.;low=.03;high=6.
        for i in range(len(source)):
            for j in range(i):
                for axis in (0,1):
                    ds=source[i,axis]-source[j,axis];dq=observed[i,axis]-observed[j,axis]
                    if abs(ds)<1:
                        if abs(dq)>2*error:return None
                        continue
                    if ds<0:ds,dq=-ds,-dq
                    low=max(low,(dq-2*error)/ds);high=min(high,(dq+2*error)/ds)
        if not 0<low<=high or high/low>1.12:return None
        step=min(.00025,max(.00001,(high-low)/100));hyp=[]
        for s in np.arange(low,high+step*.5,step):
            offsets=observed-s*source;tl=offsets.max(0)-error;th=offsets.min(0)+error
            if np.any(tl>th):continue
            xs=np.linspace(tl[0],th[0],max(2,int(np.ceil((th[0]-tl[0])/.5))+1))
            ys=np.linspace(tl[1],th[1],max(2,int(np.ceil((th[1]-tl[1])/.5))+1))
            hyp.extend((s,x,y) for x in xs for y in ys)
            if len(hyp)>20000:return None
        if not hyp:return None
        hyp=np.asarray(hyp)
        residual=np.mean(np.sum((source[None,:,:]*hyp[:,None,0,None]+hyp[:,None,1:]-observed[None,:,:])**2,axis=2),axis=1)
        scale,tx,ty=hyp[int(np.argmin(residual))]
        matrix=np.array([[scale,0,tx],[0,scale,ty],[0,0,1]],float)
        return np.asarray(hyp),matrix,dict(basis='current-floor-common-chain-vertices',commonChains=len(pairs),commonVertices=len(source),scaleRange=[low,high],pointCoordinateErrorBoundPx=error)

    @staticmethod
    def _entrance_union_domain(frame, ctx, data, anchor, cancelled):
        """Gate first; retain scale intervals from ALL pool geometry, without IDs.
        Each source chain is in the same entrance-relative frame. The union is
        an over-approximation: different chains may come from different maps.
        It can weaken pruning, but cannot secretly select a winning layout.
        """
        from single_map_structure import observed_corner_chains
        source, rays = data['unionChains'], data['unionRays']
        if not len(source):
            return None
        center = np.asarray(anchor['center'], float)
        anchor_error = float(anchor.get('uncertaintyPx', 3.))
        if center.shape != (2,) or not np.isfinite(center).all() or not 0 <= anchor_error <= 20:
            return None
        # A complete observed chain supplies three independently located points.
        # The raster glyph supplies only center, never zoom.
        query = observed_corner_chains(frame, ctx)
        intervals, supporting_queries = [], set()
        tolerance = anchor_error + 3.
        for qi, node in enumerate(query):
            if cancelled():
                return None
            oriented = np.min(np.sum(rays * node['rays'], axis=2), axis=1) >= .95
            for chain in source[oriented]:
                observed = np.asarray(node['points']) - center
                lower, upper = .03, 6.
                for native, measured in zip(chain.ravel(), observed.ravel()):
                    if abs(native) < 1e-9:
                        if abs(measured) > tolerance:
                            upper = -1.; break
                        continue
                    a, b = (measured-tolerance)/native, (measured+tolerance)/native
                    lower, upper = max(lower, min(a,b)), min(upper, max(a,b))
                    if lower > upper:
                        break
                if 0 < lower <= upper:
                    intervals.append((lower, upper)); supporting_queries.add(qi)
        if not intervals:
            return None
        merged = []
        for lower, upper in sorted(intervals):
            if merged and lower <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], upper)
            else:
                merged.append([lower, upper])
        scales = []
        for lower, upper in merged:
            scales.extend(np.linspace(lower, upper, max(2, int(np.ceil((upper-lower)/.00025))+1)))
        offsets = np.linspace(-anchor_error, anchor_error, max(2, int(np.ceil(2*anchor_error)))+1)
        if len(scales)*len(offsets)**2 > 120000:
            return None  # Budget exhaustion is unknown, not uniqueness.
        hyp = np.array([(scale, center[0]+scale*data['origin'][0]+dx,
                         center[1]+scale*data['origin'][1]+dy)
                        for scale in scales for dx in offsets for dy in offsets])
        scale, tx, ty = hyp[len(hyp)//2]
        matrix = np.array([[scale,0.,tx],[0.,scale,ty],[0.,0.,1.]])
        evidence = dict(basis='entrance-plus-all-pool-chain-scale-union',
                        sourceUnionChains=len(source), observedSupportingChains=len(supporting_queries),
                        scaleRange=[merged[0][0],merged[-1][1]], scaleIntervalCount=len(merged),
                        scaleIntervals=merged[:32], pointCoordinateErrorBoundPx=3.,
                        anchorErrorPx=anchor_error, scaleFromGlyph=False,
                        perCandidateFreeFit=False)
        return hyp, matrix, evidence

    def reference_to_client(self, result, map_id, floor):
        """Native clean-mask pixel -> client pixel, for the SAME common pose.
        Returns a feasible representative even when poseAvailable is False.
        It is only a proposal: the SAME current observation and original
        residual validator must accept this exact matrix before display/lock.
        Never substitute an unconstrained per-map fit for this validation.
        Does not refine a candidate pose. A caller must still check frame/round
        ownership, floor, mode and resource key before using this matrix.
        """
        if (not result.get('coordinateVerified') or result.get('floor') != floor or
            result.get('mode') != self.mode or result.get('resourceKey') != self.resource_key or
            map_id not in result.get('evaluatedMapIds', ())):
            return None
        entrance_xz = np.asarray(self.entries[map_id][floor]['imageToEntranceXZ'], float)
        xz_to_renderer = np.diag([1., -1., 1.])
        return np.asarray(result['matrixEntranceRendererToClient'], float) @ xz_to_renderer @ entrance_xz

    @staticmethod
    def _intersect_anchor(domain, anchor, origin):
        """Keep one shared feasible domain satisfying the independent gate."""
        hyp, matrix, evidence = domain
        center = np.asarray(anchor['center'], float)
        error = float(anchor.get('uncertaintyPx', 3.))
        if center.shape != (2,) or not np.isfinite(center).all() or not 0 <= error <= 20:
            return None
        entrance = hyp[:, 1:] - hyp[:, 0, None] * origin
        hyp = hyp[np.max(np.abs(entrance - center), axis=1) <= error + 1e-6]
        if not len(hyp):
            return None
        # Choose an actual feasible member, never an unverified averaged pose.
        nominal = np.array([matrix[0, 0], matrix[0, 2], matrix[1, 2]])
        distance = (hyp[:, 1:] - nominal[1:]) ** 2
        nearest = np.argmin(distance.sum(1) + 1e6 * (hyp[:, 0] - nominal[0]) ** 2)
        scale, tx, ty = hyp[nearest]
        matrix = np.array([[scale, 0., tx], [0., scale, ty], [0., 0., 1.]])
        evidence = dict(evidence, entranceAnchorApplied=True,
                        entranceCenterClient=center.tolist(), anchorErrorPx=error)
        return hyp, matrix, evidence

    def evaluate(self, frame, contexts, pool_ids, floor, *, anchor=None, scale_range=None,
                 scale_basis=None, cancelled=lambda:False):
        """Return contradiction IDs, never infer a new round or silently reset.
        contexts: production current_geometry dictionaries, already current-frame.
        anchor: optional {'center':[x,y], 'uncertaintyPx':3}; supplies no zoom.
        scale_range: explicit current-frame, candidate-independent map scale bound.
        scale_basis must describe its evidence; glyph raster alone is rejected.
        """
        start=time.perf_counter();ids=tuple(sorted(set(pool_ids)))
        def result(reason,**kw):
            return dict(schemaVersion=1,mode=self.mode,resourceKey=self.resource_key,floor=floor,
                        evaluatedMapIds=list(ids),contradictedMapIds=[],unknownMapIds=list(ids),
                        coordinateVerified=False,identityAuthorized=False,reason=reason,
                        elapsedMs=(time.perf_counter()-start)*1000,**kw)
        if not ids or any(mid not in self.entries for mid in ids) or floor not in ('-1','-2','-3','-4'):return result('coordinate-scope-invalid')
        if cancelled():return result('coordinate-work-obsolete')
        try:data=self._pool(ids,floor,cancelled)
        except (KeyError,TypeError,ValueError,OSError) as e:return result('coordinate-resources-unavailable',detail=str(e))
        if data is None or cancelled():return result('coordinate-work-obsolete')
        from single_map_structure import current_part
        from fast_structure_alignment import sample_edges
        solutions=[]
        for ctx in contexts:
            if cancelled():return result('coordinate-work-obsolete')
            if ctx is None:continue
            domain=None
            if anchor is not None and scale_range is not None and scale_basis not in (None,'glyph-raster','winner-map-fit'):
                center=np.asarray(anchor['center'],float);bounds=ctx.get('observationBounds')
                if bounds is None or not (bounds[0]-40<=center[0]<=bounds[2]+40 and bounds[1]-40<=center[1]<=bounds[3]+40):continue
                lower,upper=map(float,scale_range)
                if not 0<lower<=upper or upper/lower>1.12:continue
                error=float(anchor.get('uncertaintyPx',3));steps=np.arange(lower,upper+.000125,.00025)
                hyp=np.array([(s,center[0]+s*data['origin'][0]+dx,center[1]+s*data['origin'][1]+dy) for s in steps for dx in np.arange(-error,error+.01,1) for dy in np.arange(-error,error+.01,1)])
                if len(hyp)>20000:continue
                s=(lower+upper)/2;t=center+s*data['origin'];matrix=np.array([[s,0,t[0]],[0,s,t[1]],[0,0,1]])
                domain=(hyp,matrix,dict(basis='entrance-plus-'+scale_basis,scaleRange=[lower,upper],anchorErrorPx=error))
            if domain is None and anchor is not None and floor == '-1':
                domain = self._entrance_union_domain(frame,ctx,data,anchor,cancelled)
            domains = [domain] if domain is not None else self._domains_from_common(frame,ctx,data['prepared'],cancelled)
            for domain in domains:
                if anchor is not None:
                    domain=self._intersect_anchor(domain,anchor,data['origin'])
                if domain is not None:solutions.append((ctx,*domain))
        if not solutions:return result('current-common-coordinate-unresolved')
        if len({id(solution[0]) for solution in solutions}) > 1:
            return result('current-observation-context-ambiguous')
        # Different scene islands proposing incompatible coordinates stay unknown.
        first=solutions[0];extent=np.array([[0,0,1],[data['shape'][1],data['shape'][0],1]])
        if anchor is None and any(np.max(np.linalg.norm((extent@(s[2]-first[2]).T)[:,:2],axis=1))>6 for s in solutions[1:]):return result('current-common-coordinate-ambiguous')
        ctx,_,matrix,evidence=first
        # Nearby validated domains still contribute their entire uncertainty.
        # Different coordinate basins were rejected above, never silently ranked.
        hyp=np.unique(np.concatenate([solution[1] for solution in solutions]),axis=0)
        if len(hyp)>120000:return result('current-common-coordinate-budget-exhausted')
        evidence=dict(evidence,validatedCommonDomains=len(solutions))
        edge=current_part(frame,ctx,'observed').copy()
        if anchor is not None:
            x,y=map(lambda v:int(round(v)),anchor['center']);edge[max(0,y-20):y+21,max(0,x-20):x+21]=False
        points=sample_edges(edge,500)
        if len(points)<40:return result('current-structure-insufficient')
        checks=[];contradicted=[];h,w=data['shape']
        for mid,field in zip(data['ids'],data['fields']):
            minimum=np.full(len(points),np.inf)
            for startidx in range(0,len(hyp),512):
                if cancelled():return result('coordinate-work-obsolete')
                batch=hyp[startidx:startidx+512];xy=np.rint((points[None,:,:]-batch[:,None,1:])/batch[:,None,0,None]).astype(int)
                ok=(xy[:,:,0]>=0)&(xy[:,:,0]<w)&(xy[:,:,1]>=0)&(xy[:,:,1]<h)
                distances=field[np.clip(xy[:,:,1],0,h-1),np.clip(xy[:,:,0],0,w-1)]*batch[:,None,0];distances[~ok]=10000
                minimum=np.minimum(minimum,distances.min(0))
            bad=points[minimum>8.]  # >6px plus 2px parameter/raster cushion
            spread=np.ptp(bad,axis=0) if len(bad) else np.zeros(2)
            explicit=bool(len(bad)>=12 and np.min(spread)>=12 and np.max(spread)>=24)
            if explicit:contradicted.append(mid)
            checks.append(dict(mapId=mid,unavoidableMissCount=int(len(bad)),sampleCount=len(points),unavoidableSpanPx=spread.tolist(),explicitContradiction=explicit))
        if cancelled():return result('coordinate-work-obsolete')
        if len(contradicted)==len(ids):return result('coordinate-or-catalogue-conflict',candidateChecks=checks)
        projected = extent[None,:,:2]*hyp[:,None,0,None] + hyp[:,None,1:]
        pose_available = bool(np.max(np.linalg.norm(projected-(extent@matrix.T)[:,:2],axis=2)) <= 6.)
        shift=np.eye(3);shift[:2,2]=-data['origin'];common=matrix@shift
        return dict(schemaVersion=1,mode=self.mode,resourceKey=self.resource_key,floor=floor,evaluatedMapIds=list(ids),
                    contradictedMapIds=contradicted,unknownMapIds=[i for i in ids if i not in contradicted],coordinateVerified=True,poseAvailable=pose_available,
                    matrixEntranceRendererToClient=common.tolist(),selectedContextIndex=next(i for i,c in enumerate(contexts) if c is ctx),coordinateEvidence=dict(evidence,sharedHypothesisCount=len(hyp),perCandidateFreeFit=False),
                    candidateChecks=checks,identityAuthorized=False,reason='same-coordinate-positive-contradictions-only',elapsedMs=(time.perf_counter()-start)*1000)
