"""S1 candidate probe display lifecycle: real Qt/session/runtime, synthetic IO."""
import threading
import unittest
from unittest.mock import patch
from client_coordinator import LiveImages
import test_s1_native_candidate_qml as candidate_qml
import test_client_recognition as qml

class Retention(unittest.TestCase):
    base_setUp=candidate_qml.CandidateQmlContracts.setUp
    def settle(self):
        # Let the worker reacquire the GIL after NumPy/hashlib releases it.
        # QTest.qWait in this binding starves it in a tight Python polling loop.
        import time
        until=time.monotonic()+5
        while self.session.busy and time.monotonic()<until:
            qml.APP.processEvents();time.sleep(.002)
        qml.APP.processEvents()
        self.assertFalse(self.session.busy)
    def setUp(self):
        self.base_setUp()
        self.producer.registry.capability['observedFloors']=(-1,-2,-3,-4)
        self.coordinator.images=LiveImages()
        self.coordinator._convert_client_geometry=lambda row:row.update(deviceScale=1.)
        self.coordinator._sync_reference_payload=lambda row:None
        self.coordinator._pending_context=None
        self.first=self.producer.show();qml.APP.processEvents()
        self.session.refreshRuntime();qml.APP.processEvents()
        self.assertTrue(self.coordinator._aligned)
        self.fixture.windows.hide.reset_mock();self.fixture.windows.show.reset_mock()
    def blocked_probe(self, error=False):
        entered,release=threading.Event(),threading.Event();original=self.producer.capture.capture
        def capture(*a,**kw):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture-timeout')
            if error:raise OSError('fixture-capture-failure')
            return original(*a,**kw)
        guard=patch.object(self.producer.capture,'capture',side_effect=capture)
        guard.start();self.addCleanup(guard.stop);self.addCleanup(release.set)
        self.session.dispatch('observe_candidate_floor')
        self.assertTrue(entered.wait(3))
        return release
    def assert_retained(self):
        self.assertEqual(self.coordinator._last_render_token,self.first['token'])
        self.assertTrue(self.coordinator._aligned)
        self.assertIsNotNone(self.runtime.render_payload(self.first['token']))
        self.fixture.windows.hide.assert_not_called()
    def test_unchanged_probe_never_hides_current_valid_material(self):
        release=self.blocked_probe()
        during=(self.coordinator._last_render_token, bool(self.coordinator._aligned),
                self.runtime.render_payload(self.first['token']) is not None,
                list(self.fixture.windows.hide.call_args_list))
        release.set();self.settle();qml.APP.processEvents()
        self.assertEqual(during, (self.first['token'], True, True, []))
        self.assert_retained()
        self.assertEqual(self.session.snapshot['lastReceipt']['reason'],'candidate-floor-current')
    def test_explicit_close_during_probe_still_hides_immediately(self):
        release=self.blocked_probe();self.assert_retained()
        self.coordinator._close_map_from_input(dict(inputSequence=2,inputSource='map-key',inputCreatedAt=1.))
        self.assertFalse(self.coordinator._aligned)
        self.assertIsNone(self.runtime.render_payload(self.first['token']))
        release.set();self.settle();qml.APP.processEvents()
        self.assertFalse(self.coordinator._aligned)
    def test_end_during_probe_still_hides_and_never_redisplays(self):
        release=self.blocked_probe();self.assert_retained()
        self.session.dispatch('end_round');self.assertFalse(self.coordinator._aligned)
        release.set();self.settle();qml.APP.processEvents()
        self.assertFalse(self.coordinator._aligned);self.assertFalse(self.runtime.snapshot()['roundActive'])
    def test_capture_failure_withdraws_after_failed_probe(self):
        release=self.blocked_probe(error=True);self.assert_retained();release.set();self.settle();qml.APP.processEvents()
        self.assertFalse(self.coordinator._aligned);self.assertIsNone(self.runtime.render_payload(self.first['token']))
    def test_unknown_floor_withdraws_after_probe(self):
        self.producer.floor=None
        self.session.dispatch('observe_candidate_floor');self.settle();qml.APP.processEvents()
        self.assertFalse(self.coordinator._aligned);self.assertIsNone(self.runtime.render_payload(self.first['token']))
    def test_actual_floor_change_commits_new_floor_material(self):
        self.producer.floor='-2'
        self.session.dispatch('observe_candidate_floor');self.settle();qml.APP.processEvents()
        self.assertEqual(self.coordinator._aligned['floor'],'-2')
        self.assertNotEqual(self.coordinator._last_render_token,self.first['token'])

    def test_periodic_ten_same_floor_probes_never_hide_or_replace_token(self):
        for _ in range(10):
            self.session.dispatch('observe_candidate_floor');self.settle();qml.APP.processEvents()
            self.assert_retained()
    def test_observed_close_is_quiet_but_withdraws(self):
        problems=[];notices=[]
        self.coordinator.errorReported.connect(problems.append)
        self.coordinator.notificationRequested.connect(lambda *x:notices.append(x))
        self.producer.opened=False
        self.session.dispatch('observe_candidate_floor');self.settle();qml.APP.processEvents()
        self.assertEqual(self.session.snapshot['lastReceipt']['reason'],'candidate-map-closed')
        self.assertFalse(self.coordinator._aligned);self.assertIsNone(self.runtime.render_payload(self.first['token']))
        self.assertEqual(problems,[])
        self.assertFalse(any(n[0]=='操作未完成' for n in notices),notices)
    def test_real_probe_capture_error_is_not_suppressed(self):
        problems=[];self.coordinator.errorReported.connect(problems.append)
        release=self.blocked_probe(error=True);release.set();self.settle();qml.APP.processEvents()
        self.assertTrue(problems);self.assertEqual(problems[-1]['kind'],'error')
    def test_no_supported_candidate_has_specific_failure_class(self):
        problems=[];self.coordinator.errorReported.connect(problems.append)
        self.producer.fit={'success':False,'reason':'fixture-no-match'}
        self.session.dispatch('capture_now');self.settle();qml.APP.processEvents()
        self.assertFalse(self.coordinator._aligned)
        self.assertEqual(self.session.snapshot['lastReceipt']['reason'],'no-current-supported-candidates')
        self.assertEqual(problems[-1]['reason'],'no-current-supported-candidates')
        self.assertEqual(problems[-1]['title'],'当前结构暂未匹配')
    def test_registered_codes_preserve_fixed_terms_not_free_text(self):
        from diagnostic_schema import CODES
        from client_problem_messages import problem_reason
        for term in ('observe_candidate_floor','candidate-map-closed','candidate-floor-unavailable',
                     'candidate-floor-current','no-current-supported-candidates'):
            self.assertIn(term,CODES);self.assertEqual(problem_reason(term),term)
        self.assertEqual(problem_reason('OSError: private path or arbitrary text'),'unknown-operation-failure')

if __name__ == '__main__':
    unittest.main()
