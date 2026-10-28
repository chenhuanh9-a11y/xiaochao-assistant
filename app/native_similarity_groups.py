"""Native-footprint group proposals; original identity/pose validators keep authority."""
from dataclasses import dataclass, field
import gzip
import hashlib
import itertools
import json
from pathlib import Path
import threading
import time
import zlib
from zipfile import BadZipFile

import numpy as np


class NativeSimilarityGroups:
    def __init__(self, root, pack, *, binding_provider=None):
        self.root = Path(root).resolve()
        self.mode = pack.mode
        self.map_ids = frozenset(pack.by_id)
        self.binding_provider=binding_provider
        self.groups = ()
        self.error = None
        self._digests = {}
        self._arrays = {}
        self.condition_index = None
        try:
            compressed=self.local('references/native-similarity-groups.json.gz')
            self.index_path=(compressed if compressed.exists() else
                self.local('references/native-similarity-groups.json'))
            if self.index_path==compressed:
                with gzip.open(self.index_path,'rt',encoding='utf8') as stream:payload=json.load(stream)
            else:payload=json.loads(self.index_path.read_text('utf8'))
            self.index_digest = self._sha(self.index_path)
            if (payload.get('schemaVersion'),payload.get('resourceKind')) == (2,'native-similarity-conditions'):
                self.condition_index = NativeConditionIndex(self,payload)
                return
            if (payload.get('schemaVersion'), payload.get('resourceKind')) != (1, 'native-similarity-groups'):
                raise ValueError('similarity-index-schema')
            groups = []; claimed = set()
            for group in payload['groups']:
                if group['mode'] != self.mode:
                    continue
                members = tuple(group['memberIds'])
                if len(members) < 2 or len(set(members)) != len(members) or not set(members) <= self.map_ids or claimed.intersection(members):
                    raise ValueError('similarity-members-incompatible')
                claimed.update(members)
                for floor in group['floors']:
                    if {r['mapId'] for r in floor['members']} != set(members):
                        raise ValueError('similarity-floor-members-incomplete')
                    expected = {frozenset(pair) for pair in itertools.combinations(members, 2)}
                    actual = [frozenset((p['memberA'], p['memberB'])) for p in floor['pairs']]
                    if set(actual) != expected or len(actual) != len(expected):
                        raise ValueError('similarity-pairs-incomplete')
                    if floor['evidenceKind'] != 'positive-native-footprint-only':
                        raise ValueError('similarity-channel-role')
                groups.append(group)
            self.groups = tuple(groups)
        except (OSError, ValueError, KeyError, TypeError, EOFError, zlib.error) as error:
            self.error = str(error)

    def local(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('similarity-path-outside-root')
        return path

    def _sha(self, path):
        stat = path.stat(); key = (path, stat.st_size, stat.st_mtime_ns, stat.st_ino)
        value = self._digests.get(key)
        if value is None:
            value = hashlib.sha256(path.read_bytes()).hexdigest()
            updated = {k:v for k,v in self._digests.items() if k[0] != path}
            updated[key] = value
            self._digests = updated
        return value

    def group_for(self, map_id):
        return next((g for g in self.groups if map_id in g['memberIds']), None)

    def validate(self, group, provider):
        """Bind every declared member, including currently unobserved rivals."""
        if provider is None:
            raise ValueError('similarity-shared-provider-unavailable')
        if self._sha(self.index_path) != self.index_digest:
            raise ValueError('similarity-index-changed')
        for floor in group['floors']:
            for member in floor['members']:
                self.validate_reference(member,provider)
            path = self.local(floor['differenceResourcePath'])
            if self._sha(path) != floor['differenceSha256']:
                raise ValueError('similarity-differences-changed')
        return hashlib.sha256(json.dumps(group, sort_keys=True).encode('utf8')).hexdigest()

    def validate_reference(self,member,provider):
        reference=(self.binding_provider or provider)(member['profile'],member['mapId'],member['floor'])
        metadata=(reference.get('metadata',{}) if isinstance(reference,dict) else
            reference.metadata if reference is not None else {})
        path=(reference.get('path') if isinstance(reference,dict) else getattr(reference,'path',None))
        shape=(reference.get('shape') if isinstance(reference,dict) else getattr(reference,'shape',()))
        if (reference is None or Path(path).resolve()!=self.local(member['resourcePath'])
                or list(shape)!=member['shapeHW']
                or any(metadata.get(k)!=member[k] for k in ('binding','structureVersionKey','sourceVersion','baseSnapshot'))
                or self._sha(Path(path))!=member['resourceSha256']):
            raise ValueError('similarity-member-binding-changed')

    def arrays(self, floor):
        path = self.local(floor['differenceResourcePath'])
        digest = self._sha(path)
        if digest != floor['differenceSha256']:
            raise ValueError('similarity-differences-changed')
        key = (path, digest)
        if key not in self._arrays:
            with np.load(path, allow_pickle=False) as archive:
                names = [r['array'] for r in floor['members']] + [floor['unionArray'], floor['commonArray']]
                names += [p[k] for p in floor['pairs'] for k in ('aOnlyArray','bOnlyArray')]
                arrays = {name:archive[name] for name in names}
            if any(list(a.shape) != floor['canvas']['shapeHW'] or a.dtype != np.uint8
                    or (a.size and a.max()>1) for a in arrays.values()):
                raise ValueError('similarity-array-incompatible')
            # Sampling is a property of the bound resource, not of a pose.
            samples = {name:self._samples(mask) for name,mask in arrays.items()
                if any(name==pair[k] for pair in floor['pairs'] for k in ('aOnlyArray','bOnlyArray'))}
            # Rerank workers may overlap. Publish one complete immutable-by-
            # convention entry; another worker can recompute but never see
            # arrays without their matching sample coordinates.
            self._arrays[key] = (arrays,samples)
        return self._arrays[key][0]

    @staticmethod
    def _samples(mask):
        y,x = np.nonzero(mask)
        take = np.linspace(0,len(x)-1,min(512,len(x))).astype(int)
        return np.column_stack((x[take],y[take],np.ones(len(take))))

    @staticmethod
    def _support(points, matrix, context):
        if not len(points):
            return dict(observed=0, samples=0, fraction=0.,observedIndices=[])
        # A computational sample bound, never an identity acceptance threshold.
        points = points @ matrix.T
        points = points[:,:2]/points[:,2:3]
        bounds = context['observationBounds']; query = context['query']
        xy = np.rint(points-np.asarray(bounds[:2])).astype(int)
        valid = (xy[:,0]>=0)&(xy[:,1]>=0)&(xy[:,0]<query.shape[1])&(xy[:,1]<query.shape[0])
        hits = np.zeros(len(points),bool)
        hits[valid] = query[xy[valid,1],xy[valid,0]] != 0
        observed = int(hits.sum())
        return dict(observed=observed, samples=len(points), fraction=observed/len(points),
                    observedIndices=np.flatnonzero(hits).tolist())

    def differences(self, floor, poses, context):
        """All unordered pairs and all supported poses; absent pixels are not vetoes."""
        self.arrays(floor); readings = []; champions = []
        path=self.local(floor['differenceResourcePath'])
        samples=self._arrays[(path,self._sha(path))][1]
        for member in floor['members']:
            if member['mapId'] not in poses:
                continue
            matrix = np.asarray(poses[member['mapId']],float) @ np.linalg.inv(np.asarray(member['matrixNativeToGroup'],float))
            if matrix.shape != (3,3) or not np.isfinite(matrix).all():
                continue
            wins = {r['mapId']:set() for r in floor['members']}
            for pair in floor['pairs']:
                a,b = pair['memberA'],pair['memberB']
                sa = self._support(samples[pair['aOnlyArray']],matrix,context)
                sb = self._support(samples[pair['bOnlyArray']],matrix,context)
                readings.append(dict(poseMemberId=member['mapId'],memberA=a,memberB=b,a=sa,b=sb))
                if sa['observed'] and sa['fraction'] > sb['fraction']: wins[a].add(b)
                if sb['observed'] and sb['fraction'] > sa['fraction']: wins[b].add(a)
            champions.append(next((mid for mid,rivals in wins.items() if len(rivals)==len(wins)-1),None))
        winner = champions[0] if champions and all(value==champions[0] for value in champions) else None
        return dict(candidateMemberId=winner,pairs=readings,positiveEvidenceOnly=True,
                    authorizesIdentity=False,authorizesPose=False)

    def propose(self, rows, entry_rows, diagnostics, floor, provider, geometry,
                *, side_rows=(), side_diagnostics=None, deadline=None, prior=None,required_map_ids=None):
        """Use original verdicts; no score normalization or hard-gate rewrites."""
        if self.condition_index is not None:
            return self.condition_index.propose(rows,entry_rows,diagnostics,side_rows,
                side_diagnostics or {},str(floor),provider,geometry,deadline,prior,required_map_ids)
        verified = [r for r in rows if r.get('identityHardGatePassed') is True]
        winner = verified[0]['mapId'] if len(verified)==1 else None
        group = self.group_for(winner) if winner else None
        supported = {}
        for row in entry_rows:
            fit = row.get('verification') or {}
            if ((fit.get('regions') or {}).get('regionalEdgeVerified') is True
                    and (fit.get('residual') or {}).get('verified') is True
                    and fit.get('matrixSourceToClient') is not None):
                supported[row['mapId']] = row
        if group is None:
            group = next((g for g in self.groups if set(supported)==set(g['memberIds'])),None)
        if group is None:
            return dict(status='none',reason=self.error or 'no-supported-native-group')
        try:
            resource_key = self.validate(group,provider)
            result = dict(mode=self.mode,groupId=group['groupId'],memberIds=list(group['memberIds']),floor=str(floor),
                resourceKey=resource_key,authorizesIdentity=False,authorizesPose=False)
            if winner:
                reference = provider(self._member_profile(group,winner),winner,str(floor))
                if reference is None:
                    raise ValueError('similarity-winning-floor-unavailable')
                binding = {k:reference.metadata.get(k) for k in ('binding','structureVersionKey','sourceVersion','baseSnapshot')}
                binding.update(mapId=winner,floor=str(floor),profile=self._member_profile(group,winner),
                    resourcePath=Path(reference.path).relative_to(self.root).as_posix(),
                    resourceSha256=self._sha(reference.path),shapeHW=list(reference.shape))
                return dict(result,status='identity-pending',candidateMemberId=winner,
                    originalIdentityHardGatePassed=True,winnerReference=binding)
            declared = next((r for r in group['floors'] if str(r['floor'])==str(floor)),None)
            if declared is None or diagnostics.get('complete') is not True or diagnostics.get('entryInventoryComplete') is not True:
                return dict(status='none',reason='similarity-competition-incomplete')
            poses = {mid:r['verification']['matrixSourceToClient'] for mid,r in supported.items()}
            difference = self.differences(declared,poses,geometry())
            best = max(supported,key=lambda mid:float(supported[mid]['score']))
            return dict(result,status='group-candidate',candidateMemberId=difference['candidateMemberId'] or best,
                differenceEvidence=difference,supportedMemberIds=list(supported),originalIdentityHardGatePassed=False)
        except (OSError,ValueError,KeyError,TypeError,BadZipFile,np.linalg.LinAlgError) as error:
            return dict(status='unavailable',reason=str(error),groupId=group['groupId'])

    @staticmethod
    def _member_profile(group,map_id):
        return next(r['profile'] for f in group['floors'] for r in f['members'] if r['mapId']==map_id)

    def validate_state(self,state,provider):
        if self.condition_index is not None:
            self.condition_index.validate_domains(state.condition_domains,provider)
            if state.resource_key != self.index_digest:raise ValueError('similarity-index-changed')
        else:
            group=next((g for g in self.groups if g['groupId']==state.group_id),None)
            if group is None or self.validate(group,provider)!=state.resource_key:
                raise ValueError('similarity-context-changed')
        if state.member_reference is not None:self.validate_reference(state.member_reference,provider)


class NativeConditionIndex:
    """Finite local comparisons, indexed by original source feature IDs.

    Conditions describe static positive observations, never identity authority.
    Every matrix maps a real reference domain (including legacy cropped R) to
    the declared local comparison canvas. No world bridge is inferred.
    """
    def __init__(self,owner,payload):
        self.owner=owner;self.mode=owner.mode
        self.references={r['referenceId']:dict(r,profile=r['binding']['profile'])
            for r in payload['references'] if r['mode']==self.mode}
        if not {r['mapId'] for r in self.references.values()}<=owner.map_ids:
            raise ValueError('similarity-reference-outside-pack')
        self.domains={d['domainId']:d for d in payload['domains']
            if d['source'] in self.references and d['kind']=='front'}
        self.poses={str(p['poseId']):p for p in payload['poses'] if p['domainId'] in self.domains}
        self.conditions={str(c['conditionId']):c for c in payload['conditions'] if c['domainId'] in self.domains}
        self.comparisons={};self.domain_conditions={};self.lookup={};self.domain_references={}
        self.sample_spec=payload['samples'];self.sample_bundle=None
        self._feature_cache={};self._feature_files={};self._matrices={};self._sample_views={}
        for pid,pose in self.poses.items():
            reference=self.references[pose['target']];domain=self.domains[pose['domainId']]
            source=self.references[domain['source']]
            if reference['floor']!=source['floor']:raise ValueError('similarity-cross-floor-pose')
            matrix=np.asarray(pose['targetReferenceToCondition'],float)
            if (matrix.shape!=(3,3) or not np.isfinite(matrix).all() or matrix[0,0]<=0
                    or matrix[0,0]!=matrix[1,1] or matrix[0,1]!=0 or matrix[1,0]!=0
                    or not np.array_equal(matrix[2],(0,0,1))):
                raise ValueError('similarity-local-reference-matrix')
            self._matrices[pid]=matrix
            self.domain_references.setdefault(pose['domainId'],set()).add(pose['target'])
            feature=self.feature(pose['target'],domain['kind'])
            for point in np.flatnonzero(feature['main']):
                self.lookup.setdefault((pose['target'],'front',int(point)),set()).add(pid)
        for cid,condition in self.conditions.items():
            if condition['mode']!=self.mode:raise ValueError('similarity-condition-mode')
            if any(str(p) not in self.poses for p in condition['poseIds']):
                raise ValueError('similarity-condition-pose-missing')
            self.domain_conditions.setdefault(condition['domainId'],[]).append(cid)
        for comparison in payload['comparisons']:
            cid=str(comparison['conditionId'])
            if cid in self.conditions:
                bounds=comparison.get('observationBounds')
                if (comparison.get('differenceScope')!='post-entry-positive-structure'
                        or bounds is None or len(bounds)!=4 or not np.isfinite(bounds).all()
                        or bounds[2]<bounds[0] or bounds[3]<bounds[1]
                        or comparison.get('minimumPositiveDistancePixels')!=3.0
                        or comparison.get('distanceEvaluationSpace')!='original-source-and-target-positive-pixel-centers'
                        or comparison.get('persistedSampleDistanceChecked') is not True):
                    raise ValueError('similarity-condition-requires-post-entry-comparison-domain')
                if any(str(comparison[k]) not in self.poses for k in ('poseA','poseB')):
                    raise ValueError('similarity-comparison-pose-missing')
                self.comparisons.setdefault(cid,[]).append(comparison)

    def feature(self,reference_id,kind):
        key=(reference_id,kind)
        if key in self._feature_cache:return self._feature_cache[key]
        reference=self.references[reference_id];declaration=reference['features'][kind]
        path=self.owner.local(declaration['path'])
        digest=self.owner._sha(path)
        if digest!=declaration['sha256']:raise ValueError('similarity-feature-changed')
        file_key=(path,digest)
        payload=self._feature_files.get(file_key)
        if payload is None:
            payload=json.loads(path.read_text('utf8'))
            self._feature_files[file_key]=payload
        if not 0<=declaration['rowIndex']<len(payload['entries']):raise ValueError('similarity-feature-row-index')
        row=payload['entries'][declaration['rowIndex']]
        if row['mapId']!=reference['mapId'] or str(row['floor'])!=reference['floor'] or row['unit']!=declaration['unit']:
            raise ValueError('similarity-feature-binding-changed')
        if kind=='front':
            points=np.asarray([c['points'][1] for c in row['chains']],float)
            outline=np.asarray(row['outline'],float)
            main=np.linalg.norm(points[:,None]-outline[None],axis=2).min(axis=1)<=3
            edges=np.empty((0,2),int)
        else:
            points=np.asarray([t['point'] for t in row['turns']],float)
            main=np.zeros(len(points),bool)
            edges=np.asarray([(i,t['next']) for i,t in enumerate(row['turns'])
                if 0<=t.get('next',-1)<len(points)],int).reshape(-1,2)
        result=dict(points=points,main=main,edges=edges,row=row,declaration=declaration)
        self._feature_cache[key]=result
        return result

    def validate_domains(self,domain_ids,provider):
        if self.owner._sha(self.owner.index_path)!=self.owner.index_digest:
            raise ValueError('similarity-index-changed')
        reference_ids=set()
        for did in domain_ids:
            if did not in self.domains:raise ValueError('similarity-domain-changed')
            reference_ids.update(self.domain_references[did])
        for rid in reference_ids:
            reference=self.references[rid];self.owner.validate_reference(reference,provider)
            for feature in reference['features'].values():
                if self.owner._sha(self.owner.local(feature['path']))!=feature['sha256']:
                    raise ValueError('similarity-feature-changed')
        if self.owner._sha(self.owner.local(self.sample_spec['resourcePath']))!=self.sample_spec['sha256']:
            raise ValueError('similarity-samples-changed')

    def samples(self,sample_id):
        spec=self.sample_spec;path=self.owner.local(spec['resourcePath'])
        digest=self.owner._sha(path)
        if digest!=spec['sha256']:raise ValueError('similarity-samples-changed')
        bundle=self.sample_bundle
        if bundle is None or bundle[0]!=digest:
            with np.load(path,allow_pickle=False) as archive:
                points=archive[spec['pointsArray']];offsets=archive[spec['offsetsArray']]
            if (points.dtype!=np.int32 or points.ndim!=2 or points.shape[1]!=2
                    or offsets.dtype!=np.int64 or offsets.ndim!=1 or len(offsets)<2
                    or offsets[0]!=0 or offsets[-1]!=len(points) or np.any(np.diff(offsets)<0)):
                raise ValueError('similarity-sample-arrays')
            bundle=(digest,points,offsets)
            self.sample_bundle=bundle
        _,points,offsets=bundle;i=int(sample_id)
        if i<0 or i+1>=len(offsets):raise ValueError('similarity-sample-id')
        result=points[offsets[i]:offsets[i+1]]
        if len(result)>512:raise ValueError('similarity-sample-count')
        return result

    @staticmethod
    def _matrix(row):
        checked=row.get('verification') or {}
        value=checked.get('matrixSourceToClient')
        if value is None:value=row.get('matrixSourceToClient')
        if value is None and 'scale' in (row.get('hypothesis') or {}):
            h=row['hypothesis'];x,y=h['translation'];s=h['scale']
            value=[[s,0,x],[0,s,y],[0,0,1]]
        matrix=np.asarray(value,float)
        return matrix if matrix.shape==(3,3) and np.isfinite(matrix).all() else None

    def match(self,front_rows,side_rows,floor,deadline):
        matches={}
        for kind,rows in (('front',front_rows),):
            for row in rows:
                if time.perf_counter()>=deadline:return matches,False
                if (row.get('entryCrossCheck') or {}).get('contradicted') is True:continue
                rid=f"{self.mode}/{row['mapId']}/{floor}"
                if rid not in self.references:continue
                pairs=np.asarray((row.get('hypothesis') or {}).get('pairs',[]),int)
                matrix=self._matrix(row)
                if pairs.ndim!=2 or pairs.shape[1]!=2 or matrix is None:continue
                feature=self.feature(rid,kind);hits=set(map(int,pairs[:,1]))
                if hits and (min(hits)<0 or max(hits)>=len(feature['points'])):continue
                if row.get('sourceProduct')!=feature['row'].get('sourceProduct'):continue
                candidates=set()
                votes={}
                for point in hits:
                    for pid in self.lookup.get((rid,kind,point),()):votes[pid]=votes.get(pid,0)+1
                candidates={pid for pid,count in votes.items() if count>=2}
                # These are the original recovery/front proposal score bounds,
                # not a new identity threshold. Every verdict stays advisory.
                checked=row.get('verification') or {}
                eligible=(float(row.get('score',0))>=10
                    and (checked.get('regions') or {}).get('regionalEdgeVerified') is True
                    and (checked.get('residual') or {}).get('verified') is True)
                for pid in candidates:
                    if time.perf_counter()>=deadline:return matches,False
                    pose=self.poses[pid];did=pose['domainId']
                    view=matrix@np.linalg.inv(self._matrices[pid])
                    matches.setdefault(did,[]).append(dict(poseId=pid,matrix=view,
                        matchedFeatureIds=hits,eligible=eligible,mapId=row['mapId'],referenceId=rid))
        return matches,True

    def local_witness(self,view,condition):
        if view.get('continuation'):
            return str(condition['conditionId']) in view['establishedConditions']
        pose=self.poses[view['poseId']];feature=self.feature(view['referenceId'],'front')
        window=next((w for w in pose['windows'] if w['radiusUnits']==condition['radiusUnits']),None)
        if window is not None:
            eligible=set(window['targetFeatureIds'])
        else:
            points=np.column_stack((feature['points'],np.ones(len(feature['points']))))@self._matrices[view['poseId']].T
            eligible=set(np.flatnonzero(np.max(np.abs(points[:,:2]),axis=1)<=condition['radiusUnits']*60+1e-8))
        hits=view['matchedFeatureIds']&eligible
        # Original entry retrieval requires four unique chains, two on the
        # main entry. This only locates that witness; no rank point is removed
        # and no identity/verifier threshold is changed.
        return len(hits)>=4 and sum(bool(feature['main'][i]) for i in hits)>=2

    def propose(self,rows,front_rows,front_diagnostics,side_rows,side_diagnostics,floor,provider,geometry,deadline,prior=None,required_map_ids=None):
        deadline=float('inf') if deadline is None else deadline
        result=dict(status='conditions',mode=self.mode,floor=floor,groupId='conditions',
            resourceKey=self.owner.index_digest,conditionObservations=[],complete=False,
            candidateMemberId=None,memberIds=[],originalIdentityHardGatePassed=False,
            authorizesIdentity=False,authorizesPose=False,reason='condition-budget-incomplete')
        try:
            verified=[r for r in rows if r.get('identityHardGatePassed') is True]
            if len(verified)==1:
                winner=verified[0]['mapId'];rid=f'{self.mode}/{winner}/{floor}'
                if rid in self.references:
                    # An original unique verdict does not need a static group
                    # search. The UI checks final wrapper flags, resource
                    # binding and the real current/global core commit.
                    return dict(result,complete=True,reason='original-identity-pending',
                        candidateMemberId=winner,memberIds=[winner],
                        originalIdentityHardGatePassed=True,winnerReference=self.references[rid])
            if time.perf_counter()>=deadline:return result
            matched,complete=self.match(front_rows,side_rows,floor,deadline)
            if not complete:return result
            # A previously observed front entrance can be outside the next
            # viewport. Only a freshly verified ordinary structure pose may
            # extend that same reference domain; never reuse a previous H.
            established={}
            if prior and tuple(prior[:3])==(self.mode,floor,self.owner.index_digest):
                established={did:set(cids) for did,cids in prior[3]}
                for did,cids in established.items():
                    if did not in self.domains:continue
                    for row in side_rows:
                        if time.perf_counter()>=deadline:return result
                        if (row.get('entryCrossCheck') or {}).get('contradicted') is True:continue
                        checked=row.get('verification') or {}
                        if not (checked.get('success') is True and (checked.get('residual') or {}).get('verified') is True):continue
                        rid=f"{self.mode}/{row['mapId']}/{floor}";matrix=self._matrix(row)
                        if matrix is None:continue
                        reference=self.references.get(rid)
                        if (reference is None or row.get('profileId')!=reference['profile']
                                or row.get('sourceProduct')!=reference['features'].get('side',{}).get('sourceProduct',reference['resourcePath'])):continue
                        for pid,p in self.poses.items():
                            if p['domainId']!=did or p['target']!=rid:continue
                            if any(v['poseId']==pid for v in matched.get(did,())):continue
                            matched.setdefault(did,[]).append(dict(poseId=pid,
                                matrix=matrix@np.linalg.inv(self._matrices[pid]),eligible=True,
                                matchedFeatureIds=set(),mapId=row['mapId'],continuation=True,
                                establishedConditions=cids))
            if time.perf_counter()>=deadline:return result
            matched={did:views for did,views in matched.items() if any(
                v['eligible'] and str(v['poseId']) in set(map(str,self.conditions[cid]['poseIds']))
                and self.local_witness(v,self.conditions[cid])
                for cid in self.domain_conditions[did] for v in views)}
            if time.perf_counter()>=deadline:return result
            context=geometry() if matched else None
            if matched and context is None:
                return dict(result,reason='condition-current-geometry-unavailable')
            # Validate once before using the bound packed samples. No masks are
            # rebuilt on the rank path, and all following work shares its deadline.
            if time.perf_counter()>=deadline:return result
            if matched:self.samples(0)
            outside=({r['mapId'] for r in [*front_rows,*side_rows]}
                if required_map_ids is None else set(required_map_ids))
            for did,views in matched.items():
                if time.perf_counter()>=deadline:return result
                # Do not decode the entire potential domain before knowing
                # whether this observation can use any of its conditions.
                duplicated=any(sum(v['poseId']==pid for v in views)>1 for pid in {v['poseId'] for v in views})
                for cid in self.domain_conditions[did]:
                    if time.perf_counter()>=deadline:return result
                    condition=self.conditions[cid];pose_ids=set(map(str,condition['poseIds']))
                    current=[v for v in views if v['poseId'] in pose_ids and v['eligible']
                        and self.local_witness(v,condition)]
                    if not current:continue
                    self.validate_domains((did,),provider)
                    members=sorted({self.references[self.poses[p]['target']]['mapId'] for p in pose_ids})
                    observation=dict(domainId=did,conditionId=cid,radiusUnits=condition['radiusUnits'],
                        memberIds=members,poseMaps={p:self.references[self.poses[p]['target']]['mapId'] for p in pose_ids},
                        activeViews=sorted({v['poseId'] for v in current}),pairs=[],
                        expectedPoseIds=sorted(pose_ids),
                        entryEstablished=cid in established.get(did,set()),
                        completeForeignMapCompetition=condition['completeForeignMapCompetition'],
                        currentCompetitionComplete=(side_diagnostics.get('complete') is True
                            if condition['kind']=='side' else front_diagnostics.get('complete') is True
                            and front_diagnostics.get('entryInventoryComplete') is True),
                        ambiguousViews=duplicated,outsideCompetitors=sorted(outside-set(members)),
                        coverage='partial-positive-observation; unseen window pixels are unknown',
                        sameMapAlternativePoses=condition.get('sameMapAlternativePoses','UNKNOWN'))
                    result['conditionObservations'].append(observation)
                    if duplicated:continue
                    contradictory=False
                    for pair in self.comparisons.get(cid,()):
                        if time.perf_counter()>=deadline:return result
                        a,b=str(pair['poseA']),str(pair['poseB'])
                        winners=[]
                        for view in current:
                            if time.perf_counter()>=deadline:return result
                            record=dict(poseA=a,poseB=b,viewerPoseId=view['poseId'])
                            for side in ('a','b'):
                                sid=int(pair[side+'Only']['sampleSetId']);key=(self.sample_bundle[0],sid)
                                cached=self._sample_views.get(key)
                                if cached is None:
                                    _,points,offsets=self.sample_bundle
                                    if sid<0 or sid+1>=len(offsets):raise ValueError('similarity-sample-id')
                                    xy=points[offsets[sid]:offsets[sid+1]]
                                    if len(xy)>512:raise ValueError('similarity-sample-count')
                                    cached=(tuple(map(tuple,xy.tolist())),np.column_stack((xy,np.ones(len(xy)))))
                                    self._sample_views[key]=cached
                                universe,points=cached
                                support=NativeSimilarityGroups._support(points,view['matrix'],context)
                                record[side]=dict(samples=universe,
                                    observed=tuple(universe[i] for i in support['observedIndices']))
                            observation['pairs'].append(record)
                            fa=len(record['a']['observed'])/max(1,len(record['a']['samples']))
                            fb=len(record['b']['observed'])/max(1,len(record['b']['samples']))
                            if fa>fb:winners.append(a)
                            if fb>fa:winners.append(b)
                        if len(set(winners))>1:contradictory=True
                    observation['conflictingViews']=contradictory
            if time.perf_counter()>=deadline:return result
            result.update(complete=True,reason='finite-conditions-evaluated',
                memberIds=sorted({mid for c in result['conditionObservations'] for mid in c['memberIds']}))
            return result
        except (OSError,ValueError,KeyError,TypeError,BadZipFile,np.linalg.LinAlgError) as error:
            return dict(result,complete=False,reason=str(error))

    def prioritize(self,rows,hint,provider):
        mode,key,floor,domains,members=hint
        if mode!=self.mode or key!=self.owner.index_digest:return rows
        if any(self.references[self.domains[d]['source']]['floor']!=str(floor) for d in domains):return rows
        self.validate_domains(domains,provider)
        order={mid:i for i,mid in enumerate(members)}
        return sorted(rows,key=lambda row:order.get(row['mapId'],len(order)))


@dataclass
class NativeSimilarityState:
    owner: int = field(default_factory=threading.get_ident)
    generation: int = 0
    state: str = 'UNLOCKED'
    group_id: str | None = None
    resource_key: str | None = None
    provisional_member_id: str | None = None
    verified_member_id: str | None = None
    floor: str | None = None
    observations: list = field(default_factory=list)
    positive_samples: dict = field(default_factory=dict)
    member_ids: tuple = ()
    pending_identity: tuple | None = None
    member_reference: dict | None = None
    condition_domains: dict = field(default_factory=dict)
    condition_mode: str | None = None
    condition_active: tuple = ()
    condition_priority: tuple | None = None
    condition_complete: bool = False
    condition_reason: str | None = None

    def reset(self):
        if threading.get_ident()!=self.owner: raise RuntimeError('Group state belongs to UI owner')
        self.generation += 1;self.state='UNLOCKED';self.group_id=self.resource_key=None
        self.provisional_member_id=self.verified_member_id=self.floor=None
        self.observations.clear();self.positive_samples.clear();self.pending_identity=None;self.member_ids=();self.member_reference=None
        self.condition_domains.clear();self.condition_mode=None;self.condition_active=()
        self.condition_priority=None;self.condition_complete=False;self.condition_reason=None

    def accept(self, proposal, context, observation_id):
        if threading.get_ident()!=self.owner: raise RuntimeError('Group state belongs to UI owner')
        if proposal.get('status')=='conditions':
            return self._accept_conditions(proposal,context,observation_id)
        if proposal.get('status') not in {'group-candidate','identity-pending'}:
            self.reset();return self.snapshot()
        if (self.group_id,self.resource_key,self.floor)!=(proposal['groupId'],proposal['resourceKey'],proposal['floor']):
            self.reset()
        old = self.provisional_member_id
        self.group_id=proposal['groupId'];self.resource_key=proposal['resourceKey'];self.floor=proposal['floor']
        self.state='GROUP_LOCKED';self.verified_member_id=None
        self.member_ids=tuple(proposal['memberIds'])
        candidate=proposal['candidateMemberId']
        if observation_id not in self.observations:
            for pair in (proposal.get('differenceEvidence') or {}).get('pairs',()):
                key=(pair['poseMemberId'],pair['memberA'],pair['memberB'])
                record=self.positive_samples.setdefault(key,[set(),set(),pair['a']['samples'],pair['b']['samples']])
                record[0].update(pair['a']['observedIndices']);record[1].update(pair['b']['observedIndices'])
            self.observations=(self.observations+[observation_id])[-64:]
        positive=self._positive_candidate()
        if positive and not proposal.get('originalIdentityHardGatePassed'):
            candidate=positive
        # Positive observations only reorder safe proposal work, never validate identity.
        if old is None or positive or proposal.get('originalIdentityHardGatePassed'):
            if old!=candidate:self.generation+=1
            self.provisional_member_id=candidate
        self.pending_identity=(candidate,proposal['floor'],context) if proposal.get('originalIdentityHardGatePassed') else None
        self.member_reference=proposal.get('winnerReference') if self.pending_identity else None
        return self.snapshot()

    def _positive_candidate(self):
        champions=[]
        for pose in {key[0] for key in self.positive_samples}:
            wins={mid:set() for mid in self.member_ids}
            for (source,a,b),(sa,sb,na,nb) in self.positive_samples.items():
                if source!=pose:continue
                fa,fb=len(sa)/max(1,na),len(sb)/max(1,nb)
                if sa and fa>fb:wins[a].add(b)
                if sb and fb>fa:wins[b].add(a)
            champions.append(next((mid for mid,beaten in wins.items() if len(beaten)==len(wins)-1),None))
        return champions[0] if champions and champions[0] and all(c==champions[0] for c in champions) else None

    def priority(self,mode):
        if self.condition_mode is not None:
            return self.condition_priority
        if self.state!='GROUP_LOCKED':return None
        return (mode,self.resource_key,self.floor,self.group_id,
                (self.provisional_member_id,)+tuple(mid for mid in self.member_ids if mid!=self.provisional_member_id))

    def context_snapshot(self):
        if self.condition_mode is None or not self.condition_domains:return None
        domains=tuple((did,tuple(sorted(d['established']))) for did,d in self.condition_domains.items() if d['established'])
        return (self.condition_mode,self.floor,self.resource_key,domains) if domains else None

    def confirm(self,map_id,floor,context):
        if threading.get_ident()!=self.owner: raise RuntimeError('Group state belongs to UI owner')
        if self.pending_identity!=(map_id,str(floor),context):return False
        self.state='MEMBER_VERIFIED';self.verified_member_id=map_id;self.generation+=1
        self.pending_identity=None
        return True

    def snapshot(self):
        result=dict(state=self.state,groupId=self.group_id,resourceKey=self.resource_key,
            provisionalMemberId=self.provisional_member_id,verifiedMemberId=self.verified_member_id,
            floor=self.floor,generation=self.generation,observationCount=len(self.observations),
            positiveSampleCount=sum(len(r[0])+len(r[1]) for r in self.positive_samples.values()),
            authorizesIdentity=False,authorizesPose=False)
        if self.condition_mode is not None:
            result.update(domainCount=len(self.condition_domains),activeDomainIds=list(self.condition_active),
                conditionComparisonComplete=self.condition_complete,conditionReason=self.condition_reason,
                positiveSampleCount=sum(len(r['a'][1])+len(r['b'][1]) for d in self.condition_domains.values()
                    for r in d['pairs'].values()))
        return result

    def _accept_conditions(self,proposal,context,observation_id):
        scope=(proposal['mode'],proposal['floor'],proposal['resourceKey'])
        if (self.condition_mode,self.floor,self.resource_key)!=scope:self.reset()
        self.condition_mode,self.floor,self.resource_key=scope
        self.group_id='conditions';self.condition_priority=None;self.verified_member_id=None
        if observation_id not in self.observations:self.observations=(self.observations+[observation_id])[-64:]
        active=set();records=proposal['conditionObservations']
        for observation in records:
            did=observation['domainId']
            domain=self.condition_domains.setdefault(did,dict(poses={},members=set(),conditions=set(),
                pairs={},established=set(),established_bindings={}))
            domain['conditions'].add(observation['conditionId'])
            active.add(did)
            if (observation['completeForeignMapCompetition'] and observation['currentCompetitionComplete']
                    and not observation['ambiguousViews'] and not observation.get('conflictingViews')
                    and not observation['outsideCompetitors']
                    and set(observation['activeViews'])==set(observation['expectedPoseIds'])):
                domain['established'].add(observation['conditionId'])
                domain['established_bindings'][observation['conditionId']]=dict(
                    poses=dict(observation['poseMaps']),members=set(observation['memberIds']))
            if observation['ambiguousViews'] or observation.get('conflictingViews'):continue
            for pair in observation['pairs']:
                key=(pair['poseA'],pair['poseB'],'reference-domain')
                record=domain['pairs'].setdefault(key,dict(a=[set(),set()],b=[set(),set()]))
                for side in ('a','b'):
                    # Domain coordinates, not radius-specific sample ordinals.
                    # The union denominator keeps a repeated point idempotent.
                    record[side][0].update(map(tuple,pair[side]['samples']))
                    record[side][1].update(map(tuple,pair[side]['observed']))
        cohorts=[]
        for domain in self.condition_domains.values():
            bindings=list(domain['established_bindings'].values())
            domain['members']=set.intersection(*(b['members'] for b in bindings)) if bindings else set()
            domain['poses']={p:m for b in bindings for p,m in b['poses'].items() if m in domain['members']}
            if bindings:cohorts.append(domain['members'])
        eligible=set.intersection(*cohorts) if cohorts else set()
        self.condition_active=tuple(sorted(active))
        self.member_ids=tuple(sorted(eligible))
        self.condition_complete=bool(proposal['complete'] and records and all(
            o['completeForeignMapCompetition'] and o['currentCompetitionComplete']
            and not o['ambiguousViews'] and not o.get('conflictingViews') and not o['outsideCompetitors']
            and (o.get('entryEstablished') and bool(o['activeViews']) or
                set(o['activeViews'])==set(o['expectedPoseIds'])
                and len({o['poseMaps'][p] for p in o['activeViews']})>=2)
            for o in records))
        winners=[self._condition_winner(self.condition_domains[did]) for did in self.condition_active]
        positive=winners[0] if winners and winners[0] and all(w==winners[0] for w in winners) else None
        if positive not in eligible:positive=None
        old=self.provisional_member_id
        candidate=(proposal.get('candidateMemberId') if proposal.get('originalIdentityHardGatePassed')
            else positive if self.condition_complete else None)
        if candidate is None and self.provisional_member_id not in eligible:
            # A complete entrance family authorizes a provisional choice, not
            # a unique identity verdict. Pixels still need the normal fit gate.
            candidate=min(eligible) if eligible else None
        if candidate is not None:self.provisional_member_id=candidate
        if old!=self.provisional_member_id:self.generation+=1
        self.pending_identity=(candidate,self.floor,context) if proposal.get('originalIdentityHardGatePassed') else None
        self.member_reference=proposal.get('winnerReference') if self.pending_identity else None
        self.state='GROUP_LOCKED' if self.condition_domains or self.pending_identity else 'UNLOCKED'
        if positive and self.condition_complete:
            order=(positive,)+tuple(m for m in self.member_ids if m!=positive)
            self.condition_priority=(self.condition_mode,self.resource_key,self.floor,self.condition_active,order)
        self.condition_reason=('positive-consensus' if self.condition_priority else
            'original-identity-pending' if self.pending_identity else
            'condition-incomplete' if not self.condition_complete else 'conflicting-or-unobserved-positive-evidence')
        established=any(d['established'] for d in self.condition_domains.values())
        self.group_id='conditions' if established or self.pending_identity else None
        self.state='GROUP_LOCKED' if self.group_id else 'UNLOCKED'
        return self.snapshot()

    @staticmethod
    def _condition_winner(domain):
        maps=domain['poses'];members=domain['members'];wins={m:set() for m in members}
        for a,b in itertools.combinations(sorted(members),2):
            required={(pa,pb) for pa,ma in maps.items() for pb,mb in maps.items() if ma==a and mb==b}
            votes=[];covered=set()
            for (pa,pb,viewer),record in domain['pairs'].items():
                if pa not in maps or pb not in maps:continue
                ma,mb=maps[pa],maps[pb]
                if {ma,mb}!={a,b}:continue
                covered.add((pa,pb) if ma==a else (pb,pa))
                na,ha=record['a'];nb,hb=record['b']
                fa,fb=len(ha)/max(1,len(na)),len(hb)/max(1,len(nb))
                votes.append(ma if ha and fa>fb else mb if hb and fb>fa else None)
            if required<=covered and votes and votes[0] and all(v==votes[0] for v in votes):
                winner=votes[0];wins[winner].add(b if winner==a else a)
        return next((m for m,rivals in wins.items() if len(rivals)==len(members)-1 and len(members)>1),None)
