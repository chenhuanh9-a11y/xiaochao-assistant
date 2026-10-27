"""Native-footprint group proposals; original identity/pose validators keep authority."""
from dataclasses import dataclass, field
import hashlib
import itertools
import json
from pathlib import Path
import threading
from zipfile import BadZipFile

import numpy as np


class NativeSimilarityGroups:
    def __init__(self, root, pack):
        self.root = Path(root).resolve()
        self.mode = pack.mode
        self.map_ids = frozenset(pack.by_id)
        self.groups = ()
        self.error = None
        self._digests = {}
        self._arrays = {}
        try:
            self.index_path = self.local('references/native-similarity-groups.json')
            payload = json.loads(self.index_path.read_text('utf8'))
            self.index_digest = self._sha(self.index_path)
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
        except (OSError, ValueError, KeyError, TypeError) as error:
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
        reference=provider(member['profile'],member['mapId'],member['floor'])
        metadata=reference.metadata if reference is not None else {}
        if (reference is None or Path(reference.path).resolve()!=self.local(member['resourcePath'])
                or list(reference.shape)!=member['shapeHW']
                or any(metadata.get(k)!=member[k] for k in ('binding','structureVersionKey','sourceVersion','baseSnapshot'))
                or self._sha(reference.path)!=member['resourceSha256']):
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

    def propose(self, rows, entry_rows, diagnostics, floor, provider, geometry):
        """Use original verdicts; no score normalization or hard-gate rewrites."""
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

    def reset(self):
        if threading.get_ident()!=self.owner: raise RuntimeError('Group state belongs to UI owner')
        self.generation += 1;self.state='UNLOCKED';self.group_id=self.resource_key=None
        self.provisional_member_id=self.verified_member_id=self.floor=None
        self.observations.clear();self.positive_samples.clear();self.pending_identity=None;self.member_ids=();self.member_reference=None

    def accept(self, proposal, context, observation_id):
        if threading.get_ident()!=self.owner: raise RuntimeError('Group state belongs to UI owner')
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
        if self.state!='GROUP_LOCKED':return None
        return (mode,self.resource_key,self.floor,self.group_id,
                (self.provisional_member_id,)+tuple(mid for mid in self.member_ids if mid!=self.provisional_member_id))

    def confirm(self,map_id,floor,context):
        if threading.get_ident()!=self.owner: raise RuntimeError('Group state belongs to UI owner')
        if self.pending_identity!=(map_id,str(floor),context):return False
        self.state='MEMBER_VERIFIED';self.verified_member_id=map_id;self.generation+=1
        self.pending_identity=None
        return True

    def snapshot(self):
        return dict(state=self.state,groupId=self.group_id,resourceKey=self.resource_key,
            provisionalMemberId=self.provisional_member_id,verifiedMemberId=self.verified_member_id,
            floor=self.floor,generation=self.generation,observationCount=len(self.observations),
            positiveSampleCount=sum(len(r[0])+len(r[1]) for r in self.positive_samples.values()),
            authorizesIdentity=False,authorizesPose=False)
