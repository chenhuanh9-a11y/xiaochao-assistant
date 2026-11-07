import unittest
import test_s1_formal_round_lock as formal

class ReferenceCouplingAudit(unittest.TestCase):
    def setUp(self):
        self.fx=formal.FormalRoundLockContracts();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
    def test_locked_new_floor_failed_fit_should_have_new_floor_static(self):
        pool,row=self.fx.locked();self.fx.floor='-3';self.fx.unknown.add('a')
        self.assertFalse(self.fx.runtime.dispatch('capture_now')['success']);self.fx.idle()
        self.assertEqual(pool.locked_id,'a')
        self.assertFalse(self.fx.runtime.snapshot()['displayAuthorized'])
        ref=self.fx.runtime.reference_payload();self.assertIsNotNone(ref)
        self.assertEqual((ref['mapId'],ref['floor']),('a','-3'))
    def test_unknown_floor_should_keep_labeled_historical_static(self):
        pool,row=self.fx.locked();self.fx.floor=None
        self.assertFalse(self.fx.runtime.dispatch('capture_now')['success']);self.fx.idle()
        self.assertFalse(self.fx.runtime.snapshot()['displayAuthorized'])
        ref=self.fx.runtime.reference_payload();self.assertIsNotNone(ref)
        self.assertEqual(ref['floor'],'-1');self.assertFalse(ref['referenceFloorCurrent'])

    def test_unlocked_previous_candidate_new_floor_static_is_explicitly_provisional(self):
        self.fx.bad=()
        self.fx.show();self.fx.idle()
        self.assertFalse(self.fx.runtime._s1_private_pool.locked_id)
        self.fx.floor='-2';self.fx.unknown.update(('a','b'))
        self.assertFalse(self.fx.runtime.dispatch('capture_now')['success']);self.fx.idle()
        ref=self.fx.runtime.reference_payload();self.assertIsNotNone(ref)
        self.assertEqual((ref['mapId'],ref['floor']),('a','-2'))
        self.assertTrue(ref['provisional']);self.assertFalse(ref['identityLocked'])

    def test_missing_new_floor_static_resource_never_relabels_old_floor(self):
        from unittest.mock import patch
        self.fx.locked();self.fx.floor='-3';self.fx.unknown.add('a')
        with patch.object(self.fx.runtime,'_display_layer',side_effect=ValueError('missing target floor')):
            self.assertFalse(self.fx.runtime.dispatch('capture_now')['success'])
        self.fx.idle();self.assertIsNone(self.fx.runtime.reference_payload())

    def test_late_static_preparation_after_end_cannot_restore_reference(self):
        import threading
        from unittest.mock import patch
        self.fx.locked();self.fx.floor='-3';self.fx.unknown.add('a')
        entered,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        def delayed(*args,**kwargs):
            entered.set()
            if not release.wait(4): raise TimeoutError('fixture')
            return self.fx.display
        results=[]
        with patch.object(self.fx.runtime,'_display_layer',side_effect=delayed):
            thread=threading.Thread(target=lambda: results.append(self.fx.runtime.dispatch('capture_now')))
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.fx.runtime.dispatch('end_round')
            finally:
                release.set();thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.fx.runtime.reference_payload())
        self.assertFalse(self.fx.runtime.snapshot()['displayAuthorized'])

    def test_end_after_historical_floor_clears_reference(self):
        self.fx.locked();self.fx.floor=None
        self.fx.runtime.dispatch('capture_now');self.fx.idle()
        self.assertIsNotNone(self.fx.runtime.reference_payload())
        self.fx.runtime.dispatch('end_round')
        self.assertIsNone(self.fx.runtime.reference_payload())

import test_s1_map_open_reference_lifecycle as physical
import test_client_recognition as qml

class ReferenceIndependentConsumer(physical.MapOpenReferenceContracts, unittest.TestCase):
    def test_same_b2_geometry_failure_then_candidate_pose_unavailable_keeps_reference(self):
        from unittest.mock import patch, call
        from s1_structure_selection import StructureCandidateScanner
        # Match the logged lifecycle: locked identity, known B2, failed fit,
        # then same-floor passive probes see no pose. Map ID is a fixture.
        self.producer.floor='-2'
        self.session.dispatch('capture_now');self.settle();self.producer.idle()
        self.session.refreshRuntime();qml.APP.processEvents()
        self.assertEqual(self.coordinator.referenceData.get('floor'),'-2')
        previous=dict(self.coordinator.referenceData)
        events=[]
        self.runtime.set_event_callback(lambda e: (events.append(e),self.coordinator.runtimeEvent.emit(e)))
        reasons=[]
        def fail(*a,**kw):
            reasons.append('initial-structure-current-geometry-failed')
            return dict(success=False,reason=reasons[-1])
        def scan(frame,**kwargs):
            return StructureCandidateScanner(self.producer.registry.catalog,fail).scan(frame,**kwargs)
        with patch.object(self.producer.registry.engine,'qt_s1_structure_scan',side_effect=scan):
            self.session.dispatch('capture_now');self.settle();self.producer.idle()
            self.fixture.windows.hide.reset_mock()
            for _ in range(3):
                self.session.dispatch('observe_candidate_floor');self.settle();self.producer.idle()
                self.session.refreshRuntime();qml.APP.processEvents()
                self.assertEqual(self.coordinator.referenceData.get('imageUrl'),previous['imageUrl'])
                self.assertEqual(self.coordinator.referenceData.get('floor'),'-2')
                self.assertTrue(self.runtime.snapshot()['identityLocked'])
                self.assertFalse(self.coordinator.alignedData)
                self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
                self.assertNotIn(call('reference'),self.fixture.windows.hide.call_args_list)
        self.assertTrue(reasons)
        self.assertEqual(len([e for e in events if e['event']=='render-revoked'
            and e['reason']=='candidate-pose-unavailable']),3)

    def test_unknown_floor_title_is_historical_and_aligned_is_hidden(self):
        self.producer.floor=None
        self.session.dispatch('capture_now');self.settle();self.producer.idle()
        self.session.refreshRuntime();qml.APP.processEvents()
        self.assertTrue(self.coordinator.referenceData)
        self.assertIn('上次 -1层 · 当前楼层待核对',self.coordinator.referenceData['title'])
        self.assertFalse(self.coordinator.alignedData)

    def test_new_floor_static_publishes_before_blocked_failed_fit(self):
        import threading,time
        from unittest.mock import patch
        entered,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        original=self.producer.registry.engine.qt_s1_structure_scan
        def blocked(*a,**kw):
            entered.set()
            if not release.wait(4): raise TimeoutError('fixture')
            return original(*a,**kw)
        self.producer.floor='-3';self.producer.unknown.add('a')
        with patch.object(self.producer.registry.engine,'qt_s1_structure_scan',side_effect=blocked):
            self.session.dispatch('capture_now')
            self.assertTrue(entered.wait(3))
            try:
                for _ in range(15):qml.APP.processEvents();time.sleep(.002)
                self.assertEqual(self.coordinator.referenceData.get('floor'),'-3')
                self.assertFalse(self.coordinator.alignedData)
            finally:
                release.set();self.settle()
        self.assertEqual(self.coordinator.referenceData.get('floor'),'-3')
        self.assertFalse(self.coordinator.alignedData)
