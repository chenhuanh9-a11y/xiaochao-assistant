"""Real background runtime -> Qt -> info toast; synthetic capture/fit, not game QA."""
import copy
import os
from dataclasses import replace
from pathlib import Path
import numpy as np
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from client_coordinator import LiveImages
from s1_structure_selection import StructureCandidateScanner
import test_s1_native_candidate_qml as candidate_qml
import test_client_recognition as qml


def evidence(ids, floor='-1', bad=(), **extra):
    return dict(mode='nightmare', resourceKey='fixture', floor=floor,
        coordinateVerified=True, evaluatedMapIds=list(ids), contradictedMapIds=list(bad),
        unknownMapIds=[i for i in ids if i not in bad],
        reason='same-coordinate-positive-contradictions-only', **extra)


def all_conflict(ids, floor='-1'):
    result = evidence(ids, floor)
    result.update(coordinateVerified=False, reason='coordinate-or-catalogue-conflict',
        candidateChecks=[dict(mapId=mid, explicitContradiction=True) for mid in ids])
    return result


def computed_all_conflict(ids, floor='-1'):
    """Real wall pixels and comparator; only resources/domain are fixtures."""
    from s1_common_coordinate_filter import CommonCoordinateFilter
    observer = CommonCoordinateFilter.__new__(CommonCoordinateFilter)
    observer.mode, observer.resource_key = 'nightmare', 'fixture'
    observer.entries = {mid: {} for mid in ids}
    data = dict(ids=ids, prepared=object(), shape=(96, 96), origin=np.zeros(2),
        fields=[np.full((96, 96), 20., dtype=float) for _ in ids])
    observer._pool = lambda *args: data
    observer._domains_from_common = lambda *args: [(np.array([[1., 0., 0.]]), np.eye(3), {})]
    frame = np.full((96, 96, 3), 25, dtype=np.uint8)
    frame[20:80, 20:80] = 112
    query = np.zeros((96, 96), dtype=np.uint8)
    query[20:80, 20:80] = 1
    return observer.evaluate(frame, [dict(query=query, observationBounds=(0, 0, 96, 96))],
        ids, floor, cancelled=lambda: False)


class PoolConflictEvidence(unittest.TestCase):
    def pool(self):
        from s1_private_experiment import PrivateRoundPool
        pool = PrivateRoundPool('fixture-scope', ('a', 'b', 'c'))
        pool.observe(evidence(pool.remaining, bad=['b', 'c']))
        self.assertTrue(pool.accept_material('a', coordinate_current=True))
        return pool

    def test_real_comparator_all_conflict_proves_recovery_without_verified_flag(self):
        pool = self.pool()
        report = computed_all_conflict(pool.remaining)
        self.assertFalse(report['coordinateVerified'])
        self.assertEqual(report['contradictedMapIds'], [])
        self.assertTrue(all(row['explicitContradiction'] is True for row in report['candidateChecks']))
        self.assertEqual(pool.observe(report), 'private-contradiction-recovery')
        self.assertEqual(pool.remaining, ('a', 'b', 'c'))
        self.assertTrue(pool.recovery_pending)

    def test_incomplete_duplicate_wrong_or_unproven_reports_preserve_identity(self):
        malformed = [
            dict(reason='coordinate-or-catalogue-conflict'),
            dict(all_conflict(['a']), candidateChecks=[]),
            dict(all_conflict(['a']), candidateChecks=[dict(mapId='a', explicitContradiction=False)]),
            dict(all_conflict(['a']), candidateChecks=[dict(mapId='a', explicitContradiction=1)]),
            dict(all_conflict(['a']), candidateChecks=[dict(mapId='foreign', explicitContradiction=True)]),
            dict(all_conflict(['a']), candidateChecks=[dict(mapId='a', explicitContradiction=True)] * 2),
            dict(all_conflict(['a']), evaluatedMapIds=['a', 'a']),
            dict(all_conflict(['a']), evaluatedMapIds=['foreign']),
            dict(all_conflict(['a']), evaluatedMapIds=[['a']]),
            dict(evidence(['a'], bad=['a']), coordinateVerified=False),
            dict(evidence(['a'], bad=['a']), evaluatedMapIds=['a', 'a']),
            dict(evidence(['a'], bad=['a']), evaluatedMapIds=['a', 'b']),
            dict(evidence(['a'], bad=['a']), contradictedMapIds=['foreign']),
        ]
        for report in malformed:
            with self.subTest(report=report):
                pool = self.pool(); before = pool.snapshot()
                self.assertEqual(pool.observe(report), 'private-coordinate-unknown')
                self.assertEqual(pool.snapshot(), before)

    def test_complete_unique_checks_required_for_multi_candidate_conflict(self):
        from s1_private_experiment import PrivateRoundPool
        pool = PrivateRoundPool('fixture', ('a', 'b'))
        report = all_conflict(['a', 'b'])
        report['candidateChecks'] = [dict(mapId='a', explicitContradiction=True)] * 2
        self.assertEqual(pool.observe(report), 'private-coordinate-unknown')
        self.assertEqual(pool.remaining, ('a', 'b'))

    def test_transition_telemetry_survives_privacy_filter(self):
        from diagnostic_privacy import safe_record
        pool = self.pool()
        row = dict(pool.snapshot(), privatePreviousLockMapId='106001', privateResourceDigest='f'*64)
        self.assertEqual(safe_record(row)['privatePoolId'], pool.pool_id)
        self.assertEqual(safe_record(row)['privatePoolRevision'], pool.revision)
        self.assertEqual(safe_record(row)['privatePreviousLockMapId'], '106001')


class StructuralConflictNotice(unittest.TestCase):
    def setUp(self):
        candidate_qml.CandidateQmlContracts.setUp(self)
        self.producer.registry.capability['observedFloors'] = (-1, -2, -3, -4)
        self.coordinator.images = LiveImages()
        self.coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
        self.coordinator._sync_reference_payload = lambda row: None
        self.coordinator._pending_context = None
        self.events, self.notices, self.plans = [], [], []
        self.hook = lambda row: None
        def event(row):
            self.events.append(row)
            self.coordinator.runtimeEvent.emit(row)
            self.hook(row)
        self.runtime.set_event_callback(event)
        self.coordinator.notificationRequested.connect(lambda *row: self.notices.append(row))
        self.entered, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        self.reports = 0
        self.result = lambda ids, floor: computed_all_conflict(ids, floor)
        def prepare(frame, ids, floor, *, cancelled, stage=None):
            self.reports += 1
            if self.reports == 1:
                return evidence(ids, floor, ['b'])
            if self.reports == 2:
                self.entered.set()
                if not self.release.wait(4): raise TimeoutError('fixture-background-release')
                return self.result(ids, floor)
            return evidence(ids, floor)
        self.producer.registry.engine.qt_s1_private_coordinate_prepare = prepare
        def scan(frame, **kw):
            self.plans.append(kw.copy())
            return StructureCandidateScanner(self.producer.registry.catalog,
                lambda *a, **k: copy.deepcopy(self.producer.fit)).scan(frame, **kw)
        self.producer.registry.engine.qt_s1_structure_scan = scan

    def settle(self):
        until = time.monotonic() + 5
        while (self.runtime.private_evidence_busy or self.session.busy) and time.monotonic() < until:
            qml.APP.processEvents(); time.sleep(.002)
        qml.APP.processEvents()
        self.assertFalse(self.runtime.private_evidence_busy)
        self.assertFalse(self.session.busy)

    def lock(self):
        self.producer.show(); self.settle()
        self.runtime.dispatch('close_map'); qml.APP.processEvents()
        self.locked_payload = self.producer.show()
        self.assertTrue(self.entered.wait(3))
        self.session.refreshRuntime(); qml.APP.processEvents()
        self.runtime.acknowledge_render_material(self.locked_payload['token'])
        self.assertEqual(self.runtime._s1_private_pool.locked_id, 'a')
        self.events.clear(); self.notices.clear()
        return self.runtime._s1_private_pool

    def conflict_notices(self):
        return [row for row in self.notices if row[0] == '结构矛盾，恢复识别']

    def test_background_conflict_reaches_info_and_revokes_old_material(self):
        pool = self.lock()
        self.release.set(); self.settle()
        self.assertEqual(pool.remaining, ('a', 'b'))
        self.assertFalse(pool.locked_id)
        self.assertEqual(len(self.conflict_notices()), 1, self.notices)
        self.assertEqual(self.conflict_notices()[0][2], 'info')
        self.assertIsNone(self.runtime.render_payload(self.locked_payload['token']))
        self.assertFalse(self.coordinator._aligned)
        diagnostic = next(e['recognitionDiagnostics'] for e in reversed(self.events)
            if e['event'] == 'recognition-diagnostic' and
            e['recognitionDiagnostics']['stage'] == 's1-round-evidence')
        self.assertEqual(diagnostic['privatePoolId'], pool.pool_id)
        self.assertEqual(diagnostic['privatePoolRevision'], pool.revision)
        self.assertTrue(diagnostic['candidateChecks'][0]['explicitContradiction'])
        self.assertGreater(diagnostic['candidateChecks'][0]['unavoidableMissCount'], 0)
        from diagnostic_privacy import safe_record
        persisted = safe_record(diagnostic)
        self.assertEqual(persisted['candidateChecks'], diagnostic['candidateChecks'])
        self.assertEqual(persisted['privatePoolId'], pool.pool_id)
        self.assertEqual(persisted['privatePoolRevision'], pool.revision)

    def test_unknown_generic_unlock_never_notifies_conflict_or_unlock(self):
        self.lock()
        self.result = lambda ids, floor: dict(evidence(ids, floor), coordinateVerified=False)
        self.release.set(); self.settle()
        self.assertEqual(self.runtime._s1_private_pool.locked_id, 'a')
        self.assertEqual(self.notices, [])
        pool = self.runtime._s1_private_pool
        pool.restore()
        self.runtime._notify('private-experimental-unlocked', reason='private-current-fit-unknown',
            privateResourceDigest='fixture', **pool.snapshot())
        qml.APP.processEvents()
        self.assertEqual(self.notices, [])

    def test_closed_background_notice_once_and_next_fresh_scan_has_every_candidate(self):
        pool = self.lock()
        self.assertIsNotNone(self.runtime._s1_candidate_pose_hint)
        self.runtime.dispatch('close_map'); qml.APP.processEvents()
        self.release.set(); self.settle()
        self.assertEqual(len(self.conflict_notices()), 1)
        notice = next(e for e in self.events if e['event'] == 'private-experimental-unlocked')
        for _ in range(5): self.coordinator.runtimeEvent.emit(notice)
        qml.APP.processEvents()
        self.assertEqual(len(self.conflict_notices()), 1)
        self.assertFalse(self.runtime.snapshot()['mapOpen'])
        self.assertIsNone(self.runtime._s1_candidate_pose_hint)
        self.assertIsNone(self.runtime._s1_candidate_preference)
        self.plans.clear()
        result = self.runtime.dispatch('capture_now'); self.settle()
        self.assertTrue(result['success'], result)
        self.assertEqual(self.plans[0]['planned_map_ids'], ('a', 'b'))
        self.assertIsNone(self.plans[0]['first_material_committed'])
        self.assertNotIn('pose_priors', self.plans[0])
        self.assertEqual(self.runtime.snapshot()['structureEvidence']['currentUnscannedMapIds'], [])
        self.assertFalse(pool.recovery_pending)
        self.assertFalse(pool.locked_id)

    def test_current_open_recovery_uses_existing_probe_fresh_capture_path(self):
        self.lock(); self.release.set(); self.settle()
        self.plans.clear(); count = self.producer.capture.count
        result = self.runtime.dispatch('observe_candidate_floor'); self.settle()
        self.assertTrue(result['success'], result)
        self.assertGreater(self.producer.capture.count, count)
        self.assertEqual(self.plans[0]['planned_map_ids'], ('a', 'b'))
        self.assertEqual(self.runtime.snapshot()['structureEvidence']['currentUnscannedMapIds'], [])
        self.assertIsNotNone(self.producer.current_payload())
        self.assertNotEqual(self.producer.current_payload()['token'], self.locked_payload['token'])

    def test_pool_revision_and_replaced_pool_drop_queued_notice(self):
        old = self.lock()
        # Let the background emit without pumping the Qt consumer.
        self.release.set()
        worker = self.runtime._s1_private_jobs._thread
        if worker: worker.join(3)
        event = next(e for e in self.events if e['event'] == 'private-experimental-unlocked')
        old.restore()  # A later authority change supersedes this event.
        qml.APP.processEvents(); self.assertEqual(self.conflict_notices(), [])
        self.runtime._clear_private_pool()
        self.runtime.dispatch('capture_now'); self.settle()
        self.assertIsNot(self.runtime._s1_private_pool, old)
        self.coordinator.runtimeEvent.emit(event); qml.APP.processEvents()
        self.assertEqual(self.conflict_notices(), [])

    def test_round_end_drops_pending_background_and_queued_event(self):
        old = self.lock(); revision = old.revision
        self.runtime.dispatch('end_round')
        self.release.set(); self.settle()
        self.assertEqual(old.revision, revision)
        self.assertEqual(self.conflict_notices(), [])
        self.assertIsNone(self.runtime._s1_private_pool)
        self.assertFalse(any(e['event'] == 'private-experimental-unlocked' for e in self.events))

    def test_scope_and_old_revision_changes_reject_background_evidence(self):
        # These use the real queue/commit guard, not a hand-built UI snapshot.
        for boundary in ('resource', 'generation', 'geometry', 'capture-rejected', 'revision'):
            with self.subTest(boundary=boundary):
                fx = StructuralConflictNotice(); fx.setUp()
                try:
                    old = fx.lock(); before = old.revision
                    if boundary == 'resource':
                        fx.runtime._engine = object()
                    elif boundary == 'generation':
                        fx.producer.capture.generation += 1
                    elif boundary == 'geometry':
                        fx.producer.capture.frame = np.zeros((640, 960, 3), np.uint8)
                    elif boundary == 'capture-rejected':
                        fx.producer.capture.is_current = lambda *a, **k: False
                    else:
                        old.revision += 1
                    fx.release.set(); fx.settle()
                    self.assertEqual(old.locked_id, 'a')
                    self.assertEqual(old.revision, before + (boundary == 'revision'))
                    self.assertEqual(fx.conflict_notices(), [])
                finally: fx.doCleanups()

    def test_wrong_evidence_resource_and_floor_do_not_unlock(self):
        self.lock()
        self.result = lambda ids, floor: dict(all_conflict(ids, floor), resourceKey='old-resource', floor='-2')
        self.release.set(); self.settle()
        self.assertEqual(self.runtime._s1_private_pool.locked_id, 'a')
        self.assertEqual(self.notices, [])

    def test_ordinary_pool_shrink_has_no_notice_and_unknown_does_not_lose_id(self):
        self.producer.show(); self.settle()
        self.assertEqual(self.runtime._s1_private_pool.remaining, ('a',))
        self.assertFalse(any(n[0] in ('结构矛盾，恢复识别', '恢复识别') for n in self.notices))
        self.result = lambda ids, floor: dict(evidence(ids, floor), coordinateVerified=False,
            reason='current-common-coordinate-unresolved')
        self.runtime.dispatch('close_map'); self.producer.show()
        self.assertTrue(self.entered.wait(3)); qml.APP.processEvents(); self.notices.clear()
        self.release.set(); self.settle()
        self.assertEqual(self.runtime._s1_private_pool.locked_id, 'a')
        self.assertEqual(self.notices, [])
        self.producer.fit = dict(success=False, reason='fixture-fit-failure')
        result = self.runtime.dispatch('capture_now'); self.settle()
        self.assertFalse(result['success'])
        self.assertEqual(self.runtime._s1_private_pool.locked_id, 'a')
        self.assertEqual(self.conflict_notices(), [])

    def test_old_material_commit_cannot_revive_pose_and_notice_does_not_cancel_owner(self):
        self.lock()
        material_entered, material_release = threading.Event(), threading.Event()
        self.addCleanup(material_release.set)
        original = self.runtime._display_layer
        def delayed(*args, **kwargs):
            material_entered.set()
            if not material_release.wait(4): raise TimeoutError('fixture-material-release')
            return original(*args, **kwargs)
        problems = []; self.coordinator.errorReported.connect(problems.append)
        with patch.object(self.runtime, '_display_layer', side_effect=delayed), patch.object(
                self.session, 'refreshRuntime', wraps=self.session.refreshRuntime) as refresh:
            self.session.dispatch('capture_now')
            self.assertTrue(material_entered.wait(3))
            owner, ticket = self.runtime._stream_owner, self.runtime._s1_structure_ticket
            self.release.set()
            until = time.monotonic() + 3
            while self.runtime.private_evidence_busy and time.monotonic() < until:
                qml.APP.processEvents(); time.sleep(.002)
            qml.APP.processEvents()
            self.assertEqual(len(self.conflict_notices()), 1)
            self.assertTrue(self.session.busy)
            self.assertTrue(all(not job.cancelled.is_set() for job, _ in self.session._jobs.values()))
            self.assertIs(self.runtime._stream_owner, owner)
            self.assertEqual(self.runtime._s1_structure_ticket, ticket)
            refresh.assert_not_called()
            material_release.set(); self.settle()
        self.assertIsNone(self.producer.current_payload())
        self.assertEqual(self.session.snapshot['lastReceipt']['reason'], 'private-contradiction-recovery')
        self.assertEqual(problems, [])
        self.assertEqual(len(self.conflict_notices()), 1)
        self.assertFalse(self.runtime.acknowledge_render_material(self.locked_payload['token'])['success'])

    def test_notification_callback_successor_keeps_its_ticket_and_material(self):
        pool = self.lock()
        successor = []
        def reenter(event):
            if event['event'] == 'private-experimental-unlocked':
                successor.append(self.runtime.dispatch('capture_now'))
                successor.append((self.runtime._s1_structure_ticket, self.producer.current_payload()['token']))
        self.hook = reenter
        self.release.set(); self.settle()
        self.assertTrue(successor[0]['success'], successor)
        ticket, token = successor[1]
        self.assertEqual(self.runtime._s1_structure_ticket, ticket)
        self.assertEqual(self.producer.current_payload()['token'], token)
        self.assertTrue(self.runtime.acknowledge_render_material(token)['success'] or
            self.runtime._remember_proposals[token]['accepted'])
        self.assertIs(self.runtime._s1_private_pool, pool)
        self.assertFalse(pool.locked_id)

    def test_notice_enters_real_nonmodal_info_qml_consumer(self):
        self.lock()
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('clientCoordinator', self.coordinator)
        engine.load(QUrl.fromLocalFile(str(Path(candidate_qml.HERE) / 'ClientToast.qml')))
        self.assertTrue(engine.rootObjects())
        toast = engine.rootObjects()[0]
        try:
            self.release.set(); self.settle()
            self.assertTrue(toast.isVisible())
            self.assertTrue(toast.flags() & Qt.WindowDoesNotAcceptFocus)
            self.assertTrue(toast.flags() & Qt.WindowTransparentForInput)
            self.assertEqual(toast.property('notificationTitle'), '结构矛盾，恢复识别')
            self.assertEqual(toast.property('notificationKind'), 'info')
            self.assertIn('下次开图继续核对', toast.property('notificationMessage'))
            self.assertEqual(toast.property('eventRevision'), 1)
            captured = toast.grabWindow()
            self.assertFalse(captured.isNull())
            screenshot = os.environ.get('S1_CONFLICT_TOAST_SCREENSHOT')
            if screenshot: self.assertTrue(captured.save(screenshot))
        finally:
            toast.close(); engine.deleteLater(); qml.APP.processEvents()

    def test_delivery_rechecks_geometry_epoch_and_resource_digest_after_queueing(self):
        for boundary in ('geometry', 'capture-rejected', 'epoch', 'resource-digest', 'end-round'):
            with self.subTest(boundary=boundary):
                fx = StructuralConflictNotice(); fx.setUp()
                try:
                    fx.lock(); fx.release.set()
                    worker = fx.runtime._s1_private_jobs._thread
                    if worker: worker.join(3)
                    event = next(e for e in fx.events if e['event'] == 'private-experimental-unlocked')
                    if boundary == 'geometry':
                        fx.producer.capture.frame = np.zeros((640, 960, 3), np.uint8)
                    elif boundary == 'capture-rejected':
                        fx.producer.capture.is_current = lambda *a, **k: False
                    elif boundary == 'epoch':
                        fx.runtime._epoch += 1
                    elif boundary == 'resource-digest':
                        # Suppress the genuine queued packet before testing an exact
                        # invalid resource packet with otherwise current lifetime.
                        fx.coordinator._pending_context = ('pending',)
                        qml.APP.processEvents(); fx.coordinator._pending_context = None
                        fx.coordinator.runtimeEvent.emit(dict(event, privateResourceDigest='old-resource'))
                    else:
                        fx.runtime.dispatch('end_round')
                    qml.APP.processEvents()
                    self.assertEqual(fx.conflict_notices(), [])
                finally: fx.doCleanups()

    def test_successive_real_lock_conflict_transitions_each_notify_once(self):
        pool = self.lock(); self.release.set(); self.settle()
        self.assertEqual(len(self.conflict_notices()), 1)
        self.producer.registry.engine.qt_s1_private_coordinate_prepare = (
            lambda frame, ids, floor, **k: evidence(ids, floor, ['b'] if 'b' in ids else ['a']))
        self.runtime.dispatch('close_map'); self.runtime.dispatch('capture_now'); self.settle()
        self.assertEqual(pool.remaining, ('a',)); self.assertFalse(pool.locked_id)
        self.runtime.dispatch('close_map'); self.runtime.dispatch('capture_now'); self.settle()
        self.assertFalse(pool.locked_id)
        self.assertEqual(pool.remaining, ('a', 'b'))
        self.assertEqual(len(self.conflict_notices()), 2)

    def test_standard_verified_contradiction_uses_same_info_transition(self):
        self.lock()
        self.result = lambda ids, floor: evidence(ids, floor, list(ids))
        self.release.set(); self.settle()
        self.assertEqual(len(self.conflict_notices()), 1)
        self.assertEqual(self.runtime._s1_private_pool.remaining, ('a', 'b'))

    def test_partial_unscanned_and_reason_only_are_unknown_without_notices(self):
        for report in (dict(reason='coordinate-or-catalogue-conflict'),
                dict(evidence(['a'], bad=['a']), evaluatedMapIds=[]),
                dict(evidence(['a']), coordinateVerified=False, reason='coordinate-work-obsolete')):
            with self.subTest(report=report):
                fx = StructuralConflictNotice(); fx.setUp()
                try:
                    pool = fx.lock(); revision = pool.revision
                    fx.result = lambda ids, floor: dict(report, mode='nightmare', resourceKey='fixture', floor=floor)
                    fx.release.set(); fx.settle()
                    self.assertEqual((pool.locked_id, pool.revision), ('a', revision))
                    self.assertEqual(fx.notices, [])
                finally: fx.doCleanups()

    def test_active_difficult_conflict_and_retired_mode_contract(self):
        for mode in ('difficult', 'difficulty2'):
            with self.subTest(mode=mode):
                fx = StructuralConflictNotice(); fx.setUp()
                try:
                    fx.producer.registry.engine.pack.mode = mode
                    fx.producer.registry.catalog = replace(fx.producer.registry.catalog, mode=mode)
                    fx.coordinator._applied_context = (mode, '', 's1')
                    original = fx.producer.registry.engine.qt_s1_private_coordinate_prepare
                    fx.producer.registry.engine.qt_s1_private_coordinate_prepare = (
                        lambda *args, **kw: dict(original(*args, **kw), mode=mode))
                    with patch('s1_structure_selection.StructureCatalog.from_engine',
                            return_value=fx.producer.registry.catalog):
                        self.assertTrue(fx.runtime.dispatch('set_context', dict(season='s1', mode=mode))['success'])
                        if mode == 'difficulty2':
                            # The retired identity remains selectable for old
                            # settings/resources, but may not start recognition.
                            result = fx.runtime.dispatch('start_round')
                            self.assertFalse(result['success'])
                            self.assertEqual(result['reason'], 'retired-mode')
                            self.assertEqual(fx.reports, 0)
                            self.assertIsNone(fx.producer.current_payload())
                            continue
                        pool = fx.lock(); fx.release.set(); fx.settle()
                        self.assertEqual(len(fx.conflict_notices()), 1)
                        self.assertFalse(pool.locked_id)
                        self.assertIsNone(fx.producer.current_payload())
                        self.assertTrue(fx.runtime.dispatch('capture_now')['success']); fx.settle()
                        self.assertTrue(fx.runtime.snapshot()['structureEvidence']['currentScanComplete'])
                finally: fx.doCleanups()

    def test_render_revoke_reentry_new_round_never_receives_old_conflict(self):
        old = self.lock(); new = []
        def reenter(event):
            if event['event'] == 'render-revoked' and event['reason'] == 'private-contradiction-recovery':
                self.runtime.dispatch('end_round')
                self.runtime.dispatch('start_round')
                new.append(self.runtime.dispatch('capture_now'))
                new.append(self.producer.current_payload()['token'])
        self.hook = reenter
        self.release.set(); self.settle()
        self.assertTrue(new[0]['success'], new)
        self.assertIsNot(self.runtime._s1_private_pool, old)
        self.assertEqual(self.producer.current_payload()['token'], new[1])
        self.assertEqual(self.conflict_notices(), [])

    def test_queued_historical_prune_cannot_shrink_next_recovery_scan(self):
        pool = self.lock()
        original = self.producer.registry.engine.qt_s1_private_coordinate_prepare
        def prepare(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.reports == 3:
                return evidence(args[1], args[2], ['b'])
            return result
        self.producer.registry.engine.qt_s1_private_coordinate_prepare = prepare
        # A successor capture finishes while the conflicting historical job is
        # running. Its evidence is queued, then prunes after full restoration.
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        self.release.set(); self.settle()
        self.assertEqual(pool.remaining, ('a',))
        self.assertTrue(pool.recovery_pending)
        self.assertFalse(pool.locked_id)
        self.plans.clear()
        self.assertTrue(self.runtime.dispatch('capture_now')['success']); self.settle()
        self.assertEqual(self.plans[0]['planned_map_ids'], ('a', 'b'))
        self.assertTrue(self.runtime.snapshot()['structureEvidence']['currentScanComplete'])


if __name__ == '__main__': unittest.main()
