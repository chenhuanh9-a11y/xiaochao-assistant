"""Headless native-group transport and consumers; no private frames or UI."""
import copy
from concurrent.futures import ThreadPoolExecutor
import dis
import hashlib
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
import identityv_daily_assistant as runtime

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


class NativeSimilarityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name))
        self.assertIsNone(self.f.index.error)

    def host(self):
        a = runtime.DailyAssistant.__new__(runtime.DailyAssistant)
        a.native_similarity_state = NativeSimilarityState()
        a._native_similarity_priority = None
        a.engine = SimpleNamespace(native_similarity_index=self.f.index, entry_shared_reference_provider=self.f.provider)
        a.game_mode = 'nightmare'; a.current_floor = '1'; a.capture_generation = 10
        a.active_map_cycle_id = 10; a.current_frame_generation = 1; a.surface_visibility_generation = 2
        a.current_frame = np.zeros((64, 64, 3), np.uint8)
        a.map_is_open = True; a.floor_required = False
        a.gate = SimpleNamespace(confirmed_id=None); a.alignment_core_v2 = AlignmentCoreV2()
        a.logs = []; a._log = lambda event, **values:a.logs.append((event, values))
        return a

    def consume(self, a, proposal, *, shade=0):
        a.current_frame = np.full((64, 64, 3), shade, np.uint8)
        event = dict(kind='ranked', token=a.capture_generation, frame=a.current_frame,
            rankSourceFrameGeneration=a.current_frame_generation,
            rankSourceSurfaceGeneration=a.surface_visibility_generation, ranked=[dict(nativeSimilarity=proposal)])
        a._consume_native_similarity_ranked(event)
        return event

    def poll_to_group_consumer(self, a, event):
        """Run the unchanged poll prefix through real frame binding and group consumption.

        Stop immediately before unrelated temporal-fusion/UI work, rather than
        mock the poll, frame generation, or new consumer under examination.
        """
        class PrefixCompleted(BaseException):
            pass
        a.closed = False; a.events = queue.Queue(); a.events.put(event)
        a.floor_var = SimpleNamespace(set=lambda value:None)
        a.manual_floor = None; a.active_confirmation = None
        a._round_identity_lock_context = lambda *args:None
        for name in ('_reap_cancelled_map_package_helper', '_drain_online_map_package_progress',
                     '_service_display_capture', '_drain_overlay_diagnostics', '_drain_ui_frame_queue',
                     '_report_alignment_failure_sampling_state', '_sync_recognition_progress',
                     '_maybe_open_drag_boundary_calibration', '_update_floor_buttons', '_log_core_chain_event'):
            setattr(a, name, lambda *args, **kw:None)
        with patch.object(runtime, 'fuse_temporal_candidate_evidence', side_effect=PrefixCompleted), \
             patch.object(runtime.tk, 'Tk', side_effect=AssertionError('No Tk')), \
             patch.object(runtime.tk, 'Toplevel', side_effect=AssertionError('No Toplevel')):
            with self.assertRaises(PrefixCompleted):
                a._poll_events()

    def test_real_poll_fresh_rerank_keeps_source_binding_and_creates_group(self):
        a = self.host()
        event = dict(kind='ranked', token=10, cycleId=10, mapOpen=True, floor='1', floorSource='manual',
            frame=a.current_frame.copy(), rankSourceFrameGeneration=1, rankSourceSurfaceGeneration=2,
            ranked=[dict(nativeSimilarity=self.f.propose())])
        self.poll_to_group_consumer(a, event)
        self.assertEqual(a.current_frame_generation, 2)
        self.assertEqual(event['rankSourceFrameGeneration'], 1)  # Never rewrite producer evidence.
        self.assertEqual(a.similarity_group_snapshot()['state'], 'GROUP_LOCKED')

    def test_real_poll_old_rerank_does_not_become_fresh_after_accept(self):
        a = self.host(); a.current_frame_generation = 3
        event = dict(kind='ranked', token=10, cycleId=10, mapOpen=True, floor='1', floorSource='manual',
            frame=a.current_frame.copy(), rankSourceFrameGeneration=1, rankSourceSurfaceGeneration=2,
            ranked=[dict(nativeSimilarity=self.f.propose())])
        self.poll_to_group_consumer(a, event)
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')

    def test_real_poll_old_surface_copy_cannot_be_rebound(self):
        a = self.host()
        event = dict(kind='ranked', token=10, cycleId=10, mapOpen=True, floor='1', floorSource='manual',
            frame=a.current_frame.copy(), rankSourceFrameGeneration=1, rankSourceSurfaceGeneration=1,
            ranked=[dict(nativeSimilarity=self.f.propose())])
        self.poll_to_group_consumer(a, event)
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')
        self.assertEqual(event['rankSourceSurfaceGeneration'], 1)

    def test_real_poll_capture_binds_only_accepted_frame_generation(self):
        a = self.host()
        event = dict(kind='ranked', token=10, cycleId=10, mapOpen=True, floor='1', floorSource='manual',
            frame=a.current_frame.copy(), rankCaptureStartFrameGeneration=1, rankSourceSurfaceGeneration=2,
            ranked=[dict(nativeSimilarity=self.f.propose())])
        self.poll_to_group_consumer(a, event)
        self.assertEqual(event['rankSourceFrameGeneration'], 2)
        self.assertEqual(a.similarity_group_snapshot()['state'], 'GROUP_LOCKED')

    def test_real_floor_switch_clears_evidence_even_if_switching_back_before_rank(self):
        a = self.host(); self.consume(a, self.f.propose())
        a._hide_spatial_route = lambda *args:None
        a._invalidate_floor_switch_work(None, '1', '2', reason='fixture-switch')
        a.current_floor = '2'; a._check_native_similarity_resources()
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')
        self.assertIsNone(a._native_similarity_priority)
        a._invalidate_floor_switch_work(None, '2', '1', reason='fixture-return')
        a.current_floor = '1'
        self.assertEqual(a.native_similarity_state.observations, [])

    def test_real_select_floor_manual_and_restore_automatic_clear_old_evidence(self):
        a = self.host(); self.consume(a, self.f.propose())
        a.pack = self.f.pack; a.gate.selected_id = None
        a.floor_var = SimpleNamespace(set=lambda value:None)
        for name in ('_ui_action_begin', '_ui_action_finish', '_ui_action_fail', '_update_floor_buttons', '_set_status'):
            setattr(a, name, lambda *args, **kw:None)
        reranks = []; a._rerank_current = lambda floor, source:reranks.append((floor, source))
        a.select_floor('2')
        self.assertEqual(a.current_floor, '2')
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')
        self.assertIsNone(a._native_similarity_priority)
        a.select_floor('1')
        self.assertEqual(a.native_similarity_state.observations, [])
        self.consume(a, self.f.propose())
        a._mode_selected_floor = lambda frame:dict(selectedFloor=2)  # Floor sensor IO boundary.
        a.select_floor(None)
        self.assertEqual(a.current_floor, '2')
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')
        self.assertEqual(reranks[-1], ('2', 'automatic'))

    def test_all_pairs_positive_evidence_switches_real_ui_priority_not_identity(self):
        a = self.host()
        self.consume(a, self.f.propose())
        self.assertEqual(a.native_similarity_state.provisional_member_id, IDS[0])
        # Two fresh partial observations accumulate distinct difference points.
        q = np.zeros((6, 8), np.uint8); q[1, 3] = 1
        self.consume(a, self.f.propose(q), shade=40)
        self.assertEqual(a.native_similarity_state.provisional_member_id, IDS[1])
        count = a.similarity_group_snapshot()['positiveSampleCount']
        self.consume(a, self.f.propose(q), shade=40)
        self.assertEqual(a.similarity_group_snapshot()['positiveSampleCount'], count)
        q[2, 3] = 1
        self.consume(a, self.f.propose(q), shade=80)
        self.assertGreater(a.similarity_group_snapshot()['positiveSampleCount'], count)
        self.assertEqual(a._native_similarity_priority[-1], (IDS[1], IDS[0], IDS[2]))
        self.assertIsNone(a.gate.confirmed_id)
        self.assertFalse(a._native_similarity_member_committed(IDS[1], '1', global_verified=True))
        self.assertFalse(a.similarity_group_snapshot()['authorizesIdentity'])
        self.assertEqual(len(self.f.propose(q)['differenceEvidence']['pairs']), 9)

    def test_weak_or_incomplete_competition_does_not_create_group(self):
        self.assertEqual(self.f.propose(supported=IDS[:2])['status'], 'none')
        self.assertEqual(self.f.propose(supported=())['status'], 'none')
        a = self.host(); self.consume(a, self.f.propose(supported=()))
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')

    def test_actual_rank_recovery_consumer_uses_priority_and_keeps_all_three(self):
        a = self.host(); q = np.zeros((6, 8), np.uint8); q[:, 3] = 1
        self.consume(a, self.f.propose(q))
        engine = MapIdentityEngine.__new__(MapIdentityEngine)
        engine.pack = self.f.pack; engine.identity_floors = ('1', '2'); engine.identity_profile_id = 'zhan10-speed'
        engine.native_similarity_index = self.f.index
        engine.native_similarity_priority_provider = lambda:a._native_similarity_priority
        engine.entry_shared_reference_provider = self.f.provider
        engine.entry_identity = SimpleNamespace(rank=lambda *args, **kw:([], dict(complete=True, reason='fixture', elapsedMs=0)),
            geometry=lambda frame:dict(query=q, observationBounds=[0, 0, 8, 6]))
        engine._shared_entry_inventory = lambda floor:None
        calls = []
        def rows():
            return [dict(mapId=m, score=20-i, floor='1', profileId='zhan10-speed', entryKind='side',
                verification=dict(evaluated=False, success=False)) for i, m in enumerate(IDS)]
        def verify(frame, row):
            calls.append(('verify', row['mapId']))
            return dict(success=False, reason='wall-fixture-rejected')
        def refine(frame, row):
            calls.append(('refine', row['mapId']))
            return dict(row, continuousRefinementReason='wall-fixture-rejected')
        engine.side_entry_identity = SimpleNamespace(rank=lambda *args, **kw:(rows(),
            dict(complete=True, verificationComplete=True, competitiveHypotheses=3, identityAmbiguous=True, reason='fixture')),
            verify=verify, refine_candidate=refine)
        engine.side_entry_error = None; engine.entry_advisory = None
        engine._rank_source_structure_candidates = lambda *args, **kw:dict(candidateCount=0)
        result = engine.rank(np.zeros((64, 64, 3), np.uint8), '1')
        self.assertEqual([mid for op, mid in calls if op == 'verify'], [IDS[1], IDS[0], IDS[2]])
        self.assertEqual([mid for op, mid in calls if op == 'refine'], [IDS[1], IDS[0], IDS[2]])
        self.assertFalse(any(row['identityHardGatePassed'] for row in result))
        self.assertEqual(engine.last_difficult_evidence['tilemapSide']['poseRecoveryEvaluated'], 3)
        a._native_similarity_priority = None; calls.clear()
        engine.rank(np.zeros((64, 64, 3), np.uint8), '1')
        self.assertEqual([mid for op, mid in calls if op == 'verify'], list(IDS))
        # A repeated room must not occupy every slot before the preference is
        # consumed. A fourth, unverified competitor must still prevent a lock.
        a._native_similarity_priority = ('nightmare', self.f.index.validate(self.f.group, self.f.provider),
            '1', 'three', (IDS[2], IDS[0], IDS[1]))
        extra = SimpleNamespace(id='nightmare-06', name='six', layers={'1':'x'})
        engine.pack.maps.append(extra); engine.pack.by_id[extra.id] = extra
        repeated = [dict(mapId=m, score=score, floor='1', profileId='zhan10-speed', entryKind='side',
            verification=dict(evaluated=False, success=False))
            for m, score in ((IDS[0],25), (extra.id,25), (IDS[0],24), (IDS[1],24), (IDS[2],24))]
        engine.side_entry_identity.rank = lambda *args, **kw:(copy.deepcopy(repeated),
            dict(complete=True, verificationComplete=True, competitiveHypotheses=5, identityAmbiguous=True, reason='fixture'))
        def one_success(frame, row):
            calls.append(('verify', row['mapId']))
            return dict(success=row['mapId']==IDS[2], evaluated=True, residual=dict(verified=True),
                matrixSourceToClient=np.eye(3).tolist())
        engine.side_entry_identity.verify = one_success
        calls.clear()
        result = engine.rank(np.zeros((64, 64, 3), np.uint8), '1')
        self.assertEqual([mid for op, mid in calls if op == 'verify'][0], IDS[2])
        self.assertEqual(len([mid for op, mid in calls if op == 'verify']), 3)
        self.assertFalse(any(row['identityHardGatePassed'] for row in result))
        self.assertFalse(engine.last_difficult_evidence['tilemapSide']['poseRecoveryEquivalent'])

    def test_fresh_f2_original_hard_gate_and_real_core_commit_are_both_required(self):
        a = self.host(); self.consume(a, self.f.propose())
        a.current_floor = '2'; a.current_frame_generation += 1
        self.consume(a, self.f.propose(winner=IDS[1], floor='2'), shade=120)
        self.assertEqual(a.native_similarity_state.floor, '2')
        self.assertEqual(a.native_similarity_state.positive_samples, {})
        a.gate.confirmed_id = IDS[1]
        self.assertFalse(a._native_similarity_member_committed(IDS[1], '2', global_verified=True))
        core = a.alignment_core_v2
        core.begin_observation(cycle_id=10, map_id=IDS[1], floor='2', frame_generation=2)
        candidate = AlignmentCandidate(IDS[1], '2', np.eye(3), 'initial-structure-v2', 2,
            current_frame_verified=True, residual_verified=True, global_residual_verified=True)
        verdict = core.adjudicate(candidate)
        self.assertTrue(verdict.accepted)
        authorization = core.authorize(verdict)
        self.assertTrue(core.commit(authorization))
        self.assertFalse(a._native_similarity_member_committed(IDS[1], '2', global_verified=False))
        self.assertTrue(a._native_similarity_member_committed(IDS[1], '2', global_verified=True))
        self.assertEqual(a.similarity_group_snapshot()['verifiedMemberId'], IDS[1])
        self.assertIsNone(a._native_similarity_priority)
        self.assertIsNone(core.active_pose.pose_for_floor('1'))

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

    def test_stale_frame_surface_token_and_changed_member_clear_or_reject(self):
        a = self.host(); event = self.consume(a, self.f.propose())
        before = a.similarity_group_snapshot()
        for key, value in [('token', 9), ('rankSourceFrameGeneration', 0), ('rankSourceSurfaceGeneration', 1),
                           ('frame', a.current_frame.copy())]:
            stale = dict(event); stale[key] = value
            a._consume_native_similarity_ranked(stale)
            self.assertEqual(a.similarity_group_snapshot(), before)
        self.f.refs[(IDS[2], '1')].metadata['structureVersionKey'] = 'replaced'
        a._check_native_similarity_resources()
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')
        self.assertIsNone(a._native_similarity_priority)
        self.assertEqual(self.f.propose()['status'], 'unavailable')

    def test_end_round_real_reset_and_capture_generation_reject_late_result(self):
        a = self.host(); event = self.consume(a, self.f.propose())
        a.pack = self.f.pack; a.closed = False; a.round_work_active = True; a.active_confirmation = None
        a.floor_var = SimpleNamespace(set=lambda value:None)
        a._round_identity_lock_context = lambda *args:None
        # Only UI/settings/display IO and unrelated presentation caches are replaced.
        for name in ('_ui_action_begin', '_resolve_route_issue', '_set_sheet_move_mode',
                     '_invalidate_round_group_edge_correction', '_clear_open_cycle_drag', '_clear_route_notification',
                     '_clear_round_layer_group', '_reset_locked_fit_retry', '_update_floor_buttons', 'hide_route',
                     '_hide_round_recovery', '_save_settings', '_refresh_candidates', 'set_expanded', '_set_status'):
            setattr(a, name, lambda *args, **kw:None)
        self.assertTrue(a.end_round(notify=False)['success'])
        self.assertEqual(a.capture_generation, 11)
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')
        self.assertIsNone(a._native_similarity_priority)
        a._consume_native_similarity_ranked(event)
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')

    def test_wrong_floor_resource_and_difference_binding_do_not_reorder(self):
        a = self.host(); self.consume(a, self.f.propose())
        e = MapIdentityEngine.__new__(MapIdentityEngine)
        e.pack = self.f.pack; e.native_similarity_index = self.f.index
        e.entry_shared_reference_provider = self.f.provider
        e.native_similarity_priority_provider = lambda:a._native_similarity_priority
        rows = [dict(mapId=m) for m in IDS]
        self.assertIs(e._native_similarity_prioritize(rows, '2'), rows)
        (self.f.root / self.f.floor['differenceResourcePath']).write_bytes(b'changed')
        self.assertIs(e._native_similarity_prioritize(rows, '1'), rows)
        a._check_native_similarity_resources()
        self.assertEqual(a.similarity_group_snapshot()['state'], 'UNLOCKED')

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


if __name__ == '__main__':
    unittest.main()
