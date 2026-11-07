"""Headless native-group transport and consumers; no private frames or UI."""
import copy
from concurrent.futures import ThreadPoolExecutor
import dis
import hashlib
import gzip
import itertools
import json
import queue
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from native_similarity_groups import NativeSimilarityGroups, NativeSimilarityState
from map_identity_engine import MapIdentityEngine
from alignment_core_v2 import AlignmentCoreV2, AlignmentCandidate

IDS = ('nightmare-04', 'nightmare-12', 'nightmare-19')


class Fixture:
    """Three explicit binary footprints, with two positive points per member."""
    def __init__(self, root):
        self.root = root
        self.pack = SimpleNamespace(mode='nightmare', maps=[SimpleNamespace(id=m, name=m, layers={'1': 'x', '2': 'y'}) for m in IDS])
        self.pack.by_id = {m.id: m for m in self.pack.maps}
        self.refs = {}
        members = []
        arrays = {}
        for i, mid in enumerate(IDS):
            mask = np.zeros((6, 8), np.uint8)
            mask[1:3, 1 + 2*i] = 1
            arrays[f'member_{i}'] = mask
            for floor in ('1', '2'):
                metadata = dict(binding=dict(profile='zhan10-speed', mapId=mid, floor=floor, variant='variant-01'),
                    structureVersionKey=f'{mid}@fixture/{floor}', sourceVersion='current', baseSnapshot='base')
                path = root / 'references' / f'{mid}-F{floor}.npz'
                path.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(path, shared_mask=mask, metadata=json.dumps(metadata))
                self.refs[(mid, floor)] = SimpleNamespace(path=path, shape=mask.shape, metadata=metadata)
            ref = self.refs[(mid, '1')]
            members.append(dict(mapId=mid, profile='zhan10-speed', floor='1', variant='variant-01',
                array=f'member_{i}', resourcePath=ref.path.relative_to(root).as_posix(),
                resourceSha256=self.sha(ref.path), shapeHW=list(ref.shape),
                matrixNativeToGroup=np.eye(3).tolist(), **ref.metadata))
        pairs = []
        for i, j in itertools.combinations(range(3), 2):
            a, b = arrays[f'member_{i}'], arrays[f'member_{j}']
            an, bn = f'{i}_{j}_a', f'{i}_{j}_b'
            arrays[an], arrays[bn] = (a & (1-b)), (b & (1-a))
            pairs.append(dict(memberA=IDS[i], memberB=IDS[j], aOnlyArray=an, bOnlyArray=bn))
        arrays['union'] = np.maximum.reduce([arrays[f'member_{i}'] for i in range(3)])
        arrays['common'] = np.minimum.reduce([arrays[f'member_{i}'] for i in range(3)])
        difference = root / 'references' / 'differences.npz'
        np.savez_compressed(difference, **arrays)
        self.floor = dict(floor='1', profile='zhan10-speed', variant='variant-01',
            canvas=dict(shapeHW=[6, 8]), members=members, pairs=pairs,
            differenceResourcePath='references/differences.npz', differenceSha256=self.sha(difference),
            unionArray='union', commonArray='common', evidenceKind='positive-native-footprint-only')
        self.group = dict(groupId='three', mode='nightmare', memberIds=list(IDS), floors=[self.floor])
        (root / 'references/native-similarity-groups.json').write_text(json.dumps(dict(
            schemaVersion=1, resourceKind='native-similarity-groups', groups=[self.group])), encoding='utf8')
        self.index = NativeSimilarityGroups(root, self.pack)

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def provider(self, profile, mid, floor):
        return self.refs.get((mid, str(floor))) if profile == 'zhan10-speed' else None

    def propose(self, query=None, winner=None, floor='1', supported=IDS):
        rows = [dict(mapId=m, identityHardGatePassed=(m == winner)) for m in IDS]
        entries = [dict(mapId=m, score=40-i, verification=dict(
            regions=dict(regionalEdgeVerified=True), residual=dict(verified=True),
            entryWalls=dict(verified=False), matrixSourceToClient=np.eye(3).tolist()))
            for i, m in enumerate(supported)]
        query = np.zeros((6, 8), np.uint8) if query is None else query
        return self.index.propose(rows, entries, dict(complete=True, entryInventoryComplete=True),
            floor, self.provider, lambda:dict(query=query, observationBounds=[0, 0, 8, 6]))


class ConditionFixture(Fixture):
    """Finite front windows with explicitly controlled current pose evidence."""
    def __init__(self,root):
        super().__init__(root)
        features=[];references=[]
        points=np.array([[0,0],[30,0],[30,30],[0,30],[0,90],[30,90]])
        for i,mid in enumerate(IDS):
            for floor in ('1','2'):
                ref=self.refs[(mid,floor)]
                mask=np.zeros((128,128),np.uint8);mask[:31,:31]=1
                mask[60:71,30*i:30*i+10]=1;mask[80:91,30*i:30*i+10]=1
                ref.shape=mask.shape
                np.savez_compressed(ref.path,shared_mask=mask,metadata=json.dumps(ref.metadata))
            ref=self.refs[(mid,'1')];product=ref.path.relative_to(root).as_posix()
            chains=[dict(points=[points[(j-1)%6].tolist(),point.tolist(),points[(j+1)%6].tolist()],
                rays=[[1,0],[0,1]]) for j,point in enumerate(points)]
            features.append(dict(mapId=mid,floor='1',unit=60.,center=[0,0],outline=[[0,0],[30,0]],
                chains=chains,sourceProduct=product))
            references.append(dict(mode='nightmare',mapId=mid,floor='1',referenceId=f'nightmare/{mid}/1',
                resourcePath=product,resourceSha256=self.sha(ref.path),shapeHW=list(ref.shape),
                features={},**ref.metadata))
        path=root/'references/entries.json'
        path.write_text(json.dumps(dict(schemaVersion=1,entries=features)),encoding='utf8')
        for i,r in enumerate(references):
            r['features']['front']=dict(path='references/entries.json',sha256=self.sha(path),rowIndex=i,
                unit=60.,sourceProduct=features[i]['sourceProduct'])
        did='front-domain'
        domain=dict(domainId=did,source=f'nightmare/{IDS[0]}/1',kind='front',sourceAnchorIndex=-1,anchorPointIds=[0,1],
            unit=60.,centerReference=[0,0],sourceReferenceToCondition=np.eye(3).tolist())
        poses=[dict(poseId=m,domainId=did,target=f'nightmare/{m}/1',targetReferenceToCondition=np.eye(3).tolist(),
            windows=[],exactEquivalentSeeds=[]) for m in IDS]
        conditions=[dict(conditionId=i,domainId=did,source=IDS[0],kind='front',mode='nightmare',floor='1',
            radiusUnits=radius,poseIds=list(members),members=list(members),
            completeForeignMapCompetition=True,sameMapAlternativePoses='UNKNOWN')
            for i,radius,members in ((0,1,IDS),(1,2,IDS[:2]))]
        sample_sets=[[],[[1,90]],[[31,90]],[[1,70]],[[61,90]],[[31,70]],[[61,70]]]
        arrays=np.asarray([p for sample in sample_sets for p in sample],np.int32)
        offsets=np.cumsum([0]+[len(s) for s in sample_sets],dtype=np.int64)
        sample_path=root/'references/samples.npz';np.savez_compressed(sample_path,points=arrays,offsets=offsets)
        comparisons=[]
        for cid,pairs in ((0,((IDS[0],IDS[1],1,2),(IDS[0],IDS[2],3,4),(IDS[1],IDS[2],5,6))),
                          (1,((IDS[0],IDS[1],1,2),))):
            for a,b,sa,sb in pairs:
                comparisons.append(dict(conditionId=cid,domainId=did,poseA=a,poseB=b,
                    differenceScope='post-entry-positive-structure',observationBounds=[0,0,127,127],
                    minimumPositiveDistancePixels=3.0,
                    distanceEvaluationSpace='original-source-and-target-positive-pixel-centers',
                    persistedSampleDistanceChecked=True,
                    aOnly=dict(sampleSetId=sa,positivePixels=1),bOnly=dict(sampleSetId=sb,positivePixels=1)))
        self.payload=dict(schemaVersion=2,resourceKind='native-similarity-conditions',references=references,
            domains=[domain],poses=poses,conditions=conditions,comparisons=comparisons,
            samples=dict(resourcePath='references/samples.npz',sha256=self.sha(sample_path),
                pointsArray='points',offsetsArray='offsets'))
        self.write()

    def write(self,compressed=False):
        path=self.root/'references/native-similarity-groups.json'
        raw=json.dumps(self.payload).encode('utf8')
        if compressed:path.with_suffix('.json.gz').write_bytes(gzip.compress(raw,mtime=0))
        else:path.write_bytes(raw)
        self.index=NativeSimilarityGroups(self.root,self.pack)

    def front_rows(self,members=IDS,expanded=False):
        return [dict(mapId=m,floor='1',profileId='zhan10-speed',score=20,
            sourceProduct=self.refs[(m,'1')].path.relative_to(self.root).as_posix(),
            hypothesis=dict(scale=1.,translation=[0,0],pairs=[[i,i] for i in range(6 if expanded else 4)]),
            verification=dict(regions=dict(regionalEdgeVerified=True),residual=dict(verified=True),entryWalls=dict(verified=False),
                matrixSourceToClient=np.eye(3).tolist())) for m in members]

    def current_side(self,members=IDS[:2],verified=True):
        return [dict(mapId=m,floor='1',profileId='zhan10-speed',score=20,
            sourceProduct=self.refs[(m,'1')].path.relative_to(self.root).as_posix(),
            verification=dict(success=verified,residual=dict(verified=verified),
                matrixSourceToClient=np.eye(3).tolist())) for m in members]

    def propose_condition(self,q,*,front=None,side=(),prior=None,deadline=None,rows=()):
        return self.index.propose(rows,self.front_rows() if front is None else front,
            dict(complete=True,entryInventoryComplete=True),'1',self.provider,
            lambda:dict(query=q,observationBounds=[0,0,q.shape[1],q.shape[0]]),
            side_rows=side,side_diagnostics=dict(complete=True),prior=prior,deadline=deadline)


def settle_resource_validation(host, *, invalid=False):
    """Wait for real background file validation; never substitute a verdict."""
    import time
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ready = host._check_native_similarity_resources()
        if (ready and not invalid) or not host.native_similarity_state.group_id:
            return ready
        time.sleep(.002)
    raise AssertionError('resource validation did not settle')


class NativeSimilarityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name))
        self.assertIsNone(self.f.index.error)














    def test_priority_cannot_drop_successful_or_unfinished_required_rival(self):
        e = MapIdentityEngine.__new__(MapIdentityEngine)
        e.pack = self.f.pack; e.identity_floors = ('1', '2'); e.identity_profile_id = 'zhan10-speed'
        e.native_similarity_index = self.f.index; e.entry_shared_reference_provider = self.f.provider
        hint = ('nightmare',self.f.index.validate(self.f.group,self.f.provider),'1','three',(IDS[2],IDS[0],IDS[1]))
        e.native_similarity_priority_provider = lambda:hint
        e.entry_identity = SimpleNamespace(rank=lambda *args, **kw:([], dict(complete=True, reason='fixture', elapsedMs=0)))
        e._shared_entry_inventory = lambda floor:None
        e.side_entry_error = None; e.entry_advisory = None
        e._rank_source_structure_candidates = lambda *args, **kw:dict(candidateCount=0)
        def row(mid, score, success=False, evaluated=True, pose=0):
            return dict(mapId=mid, score=score, floor='1', profileId='zhan10-speed', entryKind='side', pose=pose,
                competitionComplete=True, poseAmbiguous=False, hypothesis=dict(chainCount=8, extentGrid=[3,3]),
                verification=dict(success=success, evaluated=evaluated, residual=dict(verified=True),
                    matrixSourceToClient=np.eye(3).tolist()))
        source = [row(IDS[0],25),row(IDS[1],25,True),row(IDS[2],20,evaluated=False),row(IDS[2],19,evaluated=False,pose=1)]
        diag = dict(complete=True, verificationComplete=True, competitiveHypotheses=2, identityAmbiguous=True, reason='fixture')
        refine_calls = []
        def verify(frame, candidate):
            return dict(candidate['verification'], evaluated=True, success=candidate['pose']==0)
        def refine(frame, candidate):
            refine_calls.append(candidate['mapId'])
            return dict(candidate, continuousRefinementReason='completed-rejection')
        e.side_entry_identity = SimpleNamespace(rank=lambda *args, **kw:(copy.deepcopy(source),dict(diag)),
            verify=verify, refine_candidate=refine)
        frame = np.zeros((64,64,3),np.uint8)
        # Original no-hint competition and priority must both retain 12's success.
        hint_saved, hint = hint, None
        baseline = e.rank(frame,'1')
        self.assertFalse(any(r['identityHardGatePassed'] for r in baseline))
        hint = hint_saved
        self.assertFalse(any(r['identityHardGatePassed'] for r in e.rank(frame,'1')))
        # Initial failed verify alone cannot discard 12: its original recovery
        # still owes a refinement even though the new work slots omit it.
        source[1]['verification']['success'] = False
        self.assertFalse(any(r['identityHardGatePassed'] for r in e.rank(frame,'1')))
        self.assertIn(IDS[1], e.last_difficult_evidence['tilemapSide']['groupPriorityUnverifiedCompetitors'])
        # A contradiction at the initial pose is not a completed rejection:
        # the omitted original refinement may still change that pose.
        source[1]['entryCrossCheck'] = dict(contradicted=True)
        self.assertFalse(any(r['identityHardGatePassed'] for r in e.rank(frame,'1')))
        source[1].pop('entryCrossCheck')
        # A genuinely completed rejection removes that outstanding path.
        source[1]['continuousRefinementReason'] = 'completed-rejection'
        result = e.rank(frame,'1')
        self.assertEqual([r['mapId'] for r in result if r['identityHardGatePassed']], [IDS[2]])
        clock = [100.]
        original = e.native_similarity_index.propose
        def measured(*args, **kwargs):
            clock[0] += .125
            return original(*args, **kwargs)
        with patch('map_identity_engine.time.perf_counter', side_effect=lambda:clock[0]), \
             patch.object(e.native_similarity_index, 'propose', side_effect=measured):
            result = e.rank(frame,'1')
        self.assertEqual(result[0]['difficultRecognition']['elapsedMs'], 125.)




    def test_cached_samples_are_point_for_point_equivalent_and_reused(self):
        q = np.zeros((6, 8), np.uint8); q[:, 3] = 1
        poses = {mid:np.eye(3) for mid in IDS}
        context = dict(query=q, observationBounds=[0, 0, 8, 6])
        with patch.object(self.f.index, '_samples', wraps=self.f.index._samples) as sample:
            first = self.f.index.differences(self.f.floor, poses, context)
            self.assertEqual(sample.call_count, 6)
            second = self.f.index.differences(self.f.floor, poses, context)
            self.assertEqual(sample.call_count, 6)
        self.assertEqual(first, second)
        # Independent original formula: sample all six masks afresh per view.
        with np.load(self.f.root / self.f.floor['differenceResourcePath']) as archive:
            for reading in first['pairs']:
                pair = next(p for p in self.f.floor['pairs']
                    if (p['memberA'],p['memberB']) == (reading['memberA'],reading['memberB']))
                for side, key in (('a','aOnlyArray'), ('b','bOnlyArray')):
                    y, x = np.nonzero(archive[pair[key]])
                    take = np.linspace(0,len(x)-1,min(512,len(x))).astype(int)
                    hits = q[y[take],x[take]] != 0
                    expected = dict(observed=int(hits.sum()), samples=len(take),
                        fraction=float(hits.sum())/len(take) if len(take) else 0.,
                        observedIndices=np.flatnonzero(hits).tolist())
                    self.assertEqual(reading[side], expected)
        values = np.arange(256, dtype=np.uint8)
        for sample in (values[:2], values[:3], values, values[:0]):
            self.assertEqual(bool(np.isin(sample,(0,1)).all()), not bool(sample.size and sample.max()>1))

    def test_overlapping_rank_proposals_never_observe_half_a_cache_entry(self):
        entered, release = threading.Event(), threading.Event()
        original = self.f.index._samples
        once = threading.Lock()
        first = True
        def paused(mask):
            nonlocal first
            with once:
                wait = first
                first = False
            if wait:
                entered.set()
                if not release.wait(5):
                    raise AssertionError('sample fixture timed out')
            return original(mask)
        with patch.object(self.f.index, '_samples', side_effect=paused), ThreadPoolExecutor(2) as pool:
            initial = pool.submit(self.f.propose)
            self.assertTrue(entered.wait(3))
            try:
                overlapping = pool.submit(self.f.propose).result(timeout=3)
            finally:
                release.set()
            self.assertEqual(initial.result(timeout=3)['status'], 'group-candidate')
            self.assertEqual(overlapping['status'], 'group-candidate')

    def test_digest_cache_is_published_once_after_local_insertion(self):
        """Schedule actual propose/SHA bytecodes; replace no cache or result."""
        code = NativeSimilarityGroups._sha.__code__
        instructions = {i.offset:i.opname for i in dis.get_instructions(code)}
        b_ready, a_iterating, b_finished = (threading.Event() for _ in range(3))
        target = self.f.refs[(IDS[0], '1')].path
        results = {}
        def trace(frame, event, arg):
            if frame.f_code is code:
                frame.f_trace_opcodes = True
                if event != 'opcode' or frame.f_locals.get('path') != target:
                    return trace
                operation = instructions.get(frame.f_lasti)
                role = threading.current_thread().name
                if role == 'digest-B' and operation == 'STORE_SUBSCR' and not b_ready.is_set():
                    b_ready.set()
                    if not a_iterating.wait(3):
                        raise AssertionError('A iterator not reached')
                elif role == 'digest-A' and operation == 'FOR_ITER' and not a_iterating.is_set():
                    a_iterating.set()
                    if not b_finished.wait(3):
                        raise AssertionError('B insertion not completed')
            return trace
        def run(role):
            sys.settrace(trace)
            try:
                results[role] = self.f.propose()['status']
            except Exception as error:
                results[role] = error
            finally:
                sys.settrace(None)
                if role == 'digest-B':b_finished.set()
        b = threading.Thread(target=run, args=('digest-B',), name='digest-B')
        a = threading.Thread(target=run, args=('digest-A',), name='digest-A')
        b.start()
        try:
            self.assertTrue(b_ready.wait(3))
            a.start()
            a.join(6); b.join(6)
            self.assertFalse(a.is_alive() or b.is_alive())
            self.assertEqual(results['digest-A'], 'group-candidate')
            self.assertEqual(results['digest-B'], 'group-candidate')
        finally:
            a_iterating.set(); b_finished.set()
            if a.ident is not None:a.join(3)
            b.join(3)


class NativeFrontConditionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.f=ConditionFixture(Path(self.temp.name))
        self.assertIsNone(self.f.index.error)

    def test_nested_established_family_excludes_unestablished_member_and_pose(self):
        self.f.payload['conditions'][1]['poseIds']=list(IDS[1:])
        self.f.payload['conditions'][1]['members']=list(IDS[1:])
        comparison=self.f.payload['comparisons'][-1]
        comparison.update(poseA=IDS[1],poseB=IDS[2])
        comparison['aOnly']['sampleSetId']=5;comparison['bOnly']['sampleSetId']=6
        self.f.write()
        state=NativeSimilarityState()
        first=self.f.propose_condition(np.zeros((40,40),np.uint8),front=self.f.front_rows(IDS[1:]))
        state.accept(first,('nightmare',10,'1',1,2),'nested-entry')
        self.assertIn(state.provisional_member_id,IDS[1:])
        self.assertEqual(state.member_ids,IDS[1:])
        self.assertEqual(set(state.condition_domains['front-domain']['poses']),set(IDS[1:]))
        # A later valid nested condition must also narrow an already-established
        # wider family, including recovery requests and required pose pairs.
        wide=NativeSimilarityState()
        wide.accept(self.f.propose_condition(np.zeros((40,40),np.uint8)),
            ('nightmare',10,'1',1,2),'wide')
        self.assertEqual(wide.provisional_member_id,IDS[0])
        wide.accept(first,('nightmare',10,'1',2,2),'nested')
        self.assertEqual(wide.member_ids,IDS[1:])
        self.assertIn(wide.provisional_member_id,IDS[1:])
        state=wide
        q=np.zeros((128,128),np.uint8);q[70,31]=1
        second=self.f.propose_condition(q,front=[],side=self.f.current_side(IDS[1:]),prior=state.context_snapshot())
        state.accept(second,('nightmare',10,'1',2,2),'nested-current')
        self.assertEqual(state.priority('nightmare')[-1],IDS[1:])
        engine=MapIdentityEngine.__new__(MapIdentityEngine)
        engine.pack=self.f.pack;engine.native_similarity_index=self.f.index
        engine.entry_shared_reference_provider=self.f.provider
        engine.entry_identity=SimpleNamespace(geometry=lambda frame:dict(query=q,observationBounds=[0,0,128,128]))
        calls=[]
        def local(frame,floor,*,map_id,budget_seconds):
            calls.append(map_id)
            return self.f.current_side((map_id,)),dict(complete=True,verificationComplete=True)
        engine.side_entry_identity=SimpleNamespace(rank=local)
        engine.native_similarity_recover_observation(np.zeros((128,128,3),np.uint8),'1',
            prior=state.context_snapshot(),deadline=__import__('time').perf_counter()+1)
        self.assertEqual(calls,list(IDS[1:]))


    def test_front_small_to_large_window_keeps_points_after_entrance_leaves_view(self):
        state=NativeSimilarityState();q=np.zeros((40,40),np.uint8);q[:31,:31]=1
        first=self.f.propose_condition(q)
        self.assertEqual({o['conditionId'] for o in first['conditionObservations']},{'0','1'})
        state.accept(first,('nightmare',10,'1',1,2),'first')
        before=state.snapshot()['positiveSampleCount']
        self.assertEqual(before,0);self.assertIsNone(state.priority('nightmare'))
        self.assertEqual(state.provisional_member_id,IDS[0])
        # The smaller AB condition still has required rival C outside it.
        # Keep the complete ABC entrance, without establishing the AB subset.
        self.assertEqual(state.condition_domains['front-domain']['established'],{'0'})
        # Entrance is no longer observed. This frame has only a different
        # positive point and independently verified current ordinary poses.
        q=np.zeros((96,96),np.uint8);q[70,1]=1
        second=self.f.propose_condition(q,front=[],side=self.f.current_side(),prior=state.context_snapshot())
        self.assertEqual({o['conditionId'] for o in second['conditionObservations']},{'0'})
        state.accept(second,('nightmare',10,'1',2,2),'second')
        self.assertGreater(state.snapshot()['positiveSampleCount'],before)
        before=state.snapshot()['positiveSampleCount']
        self.assertIsNone(state.priority('nightmare'))
        q=np.zeros((128,128),np.uint8);q[90,1]=1
        third=self.f.propose_condition(q,front=[],side=self.f.current_side(),prior=state.context_snapshot())
        state.accept(third,('nightmare',10,'1',3,2),'third')
        self.assertGreater(state.snapshot()['positiveSampleCount'],before)
        self.assertEqual(state.priority('nightmare')[-1][0],IDS[0])
        self.assertEqual(state.snapshot()['domainCount'],1)
        self.assertIsNone(state.verified_member_id)


    def test_explicit_entry_contradiction_cannot_establish_a_provisional_group(self):
        front=self.f.front_rows()
        for row in front:
            row.update(entryCrossCheck=dict(contradicted=True),identityVerified=False,poseVerified=False)
        proposal=self.f.propose_condition(np.zeros((40,40),np.uint8),front=front)
        state=NativeSimilarityState();state.accept(proposal,('nightmare',10,'1',1,2),'contradicted')
        self.assertEqual(state.state,'UNLOCKED')
        self.assertIsNone(state.provisional_member_id)

    def test_missing_member_outside_rival_and_untrusted_current_pose_stay_unknown(self):
        q=np.ones((40,40),np.uint8)
        missing=self.f.propose_condition(q,front=self.f.front_rows(IDS[:2]))
        state=NativeSimilarityState();state.accept(missing,('nightmare',10,'1',1,2),'partial')
        self.assertFalse(state.snapshot()['conditionComparisonComplete'])
        self.assertIsNone(state.priority('nightmare'))
        q=np.zeros((96,96),np.uint8);q[1,1]=1
        outside=self.f.propose_condition(q,front=[],side=self.f.current_side(IDS),prior=state.context_snapshot())
        state.accept(outside,('nightmare',10,'1',2,2),'outside')
        self.assertFalse(state.snapshot()['conditionComparisonComplete'])
        self.assertIsNone(state.priority('nightmare'))
        prior=state.context_snapshot();count=state.snapshot()['positiveSampleCount']
        unreliable=self.f.propose_condition(q,front=[],side=self.f.current_side(verified=False),prior=prior)
        state.accept(unreliable,('nightmare',10,'1',3,2),'no-pose')
        self.assertEqual(state.snapshot()['positiveSampleCount'],count)
        self.assertEqual(state.snapshot()['domainCount'],1)
        self.assertIsNone(state.priority('nightmare'))

    def test_unique_original_winner_bypasses_group_budget_without_authorizing_display(self):
        import time
        rows=[dict(mapId=IDS[1],identityHardGatePassed=True)]
        with patch.object(self.f.index.condition_index,'match',side_effect=AssertionError('No group scan')):
            result=self.f.propose_condition(np.zeros((40,40),np.uint8),rows=rows,deadline=time.perf_counter()-1)
        self.assertTrue(result['originalIdentityHardGatePassed'])
        self.assertEqual(result['candidateMemberId'],IDS[1])
        self.assertEqual(result['conditionObservations'],[])
        self.assertFalse(result['authorizesIdentity']);self.assertFalse(result['authorizesPose'])

    def test_expired_budget_is_incomplete_without_cold_sample_decode(self):
        import time
        with patch.object(self.f.index.condition_index,'samples',side_effect=AssertionError('No late decode')):
            result=self.f.propose_condition(np.zeros((40,40),np.uint8),deadline=time.perf_counter()-1)
        self.assertFalse(result['complete']);self.assertEqual(result['conditionObservations'],[])

    def test_compressed_precedence_corruption_and_plain_v2_compatibility(self):
        self.assertIsNotNone(self.f.index.condition_index)
        self.f.write(compressed=True)
        self.assertTrue(str(self.f.index.index_path).endswith('.json.gz'))
        (self.f.root/'references/native-similarity-groups.json').write_text('old invalid plain',encoding='utf8')
        good=NativeSimilarityGroups(self.f.root,self.f.pack)
        self.assertIsNone(good.error)
        compressed=self.f.root/'references/native-similarity-groups.json.gz'
        compressed.write_bytes(compressed.read_bytes()[:15])
        bad=NativeSimilarityGroups(self.f.root,self.f.pack)
        self.assertIsNotNone(bad.error);self.assertIsNone(bad.condition_index)

    def test_invalid_deflate_is_unavailable_without_plain_fallback(self):
        self.f.write(compressed=True)
        path=self.f.root/'references/native-similarity-groups.json.gz'
        data=bytearray(path.read_bytes());data[10]=(data[10]&0xf8)|6;path.write_bytes(data)
        bad=NativeSimilarityGroups(self.f.root,self.f.pack)
        self.assertIsNotNone(bad.error)
        self.assertIsNone(bad.condition_index)
        self.assertEqual(bad.index_path,path)

    def test_untrusted_or_post_match_expired_views_do_not_decode_samples(self):
        index=self.f.index.condition_index
        front=self.f.front_rows()
        for row in front:row['verification']['regions']['regionalEdgeVerified']=False
        with patch.object(index,'samples',side_effect=AssertionError('No unusable sample decode')):
            result=self.f.propose_condition(np.zeros((40,40),np.uint8),front=front)
        self.assertEqual(result['conditionObservations'],[])
        clock=[0.];original=index.match
        def expired_match(*args):
            matched=original(*args);clock[0]=2.;return matched
        with patch('native_similarity_groups.time.perf_counter',side_effect=lambda:clock[0]), \
             patch.object(index,'match',side_effect=expired_match), \
             patch.object(index,'samples',side_effect=AssertionError('No decode after match expiry')):
            result=self.f.propose_condition(np.zeros((40,40),np.uint8),deadline=1.)
        self.assertFalse(result['complete'])


if __name__ == '__main__':
    unittest.main()
