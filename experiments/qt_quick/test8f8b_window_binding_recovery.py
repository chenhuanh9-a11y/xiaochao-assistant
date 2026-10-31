"""Production binding/input classes, fake metadata and hook; never OS input."""
import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock
import psutil

from client_window_binding import probe_game_binding
import test_client_input_recovery as input_fixtures


class BindingIdentityTests(unittest.TestCase):
    def setUp(self):
        self.identity = ('42', 700, 12.5, 'C:/Game/dwrg.exe')
        self.user = SimpleNamespace(IsWindow=Mock(return_value=True), IsIconic=Mock(return_value=True))
        self.native = SimpleNamespace(get_user32=lambda: self.user,
            window_process_id=Mock(return_value=700), window_text=Mock(return_value='game'),
            identity_is_allowed=Mock(return_value=True))
        self.process = SimpleNamespace(create_time=Mock(return_value=12.5),
                                       exe=Mock(return_value='c:\\game\\dwrg.exe'))
        self.factory = Mock(return_value=self.process)

    def probe(self):
        return probe_game_binding(self.identity, native=self.native, process_factory=self.factory)

    def test_minimized_identity_survives_without_geometry_or_iconic_dependency(self):
        self.assertEqual(self.probe()['status'], 'current')
        self.user.IsIconic.assert_not_called()

    def test_destroyed_hwnd_or_reused_pid_is_lost(self):
        self.user.IsWindow.return_value = False
        self.assertEqual(self.probe()['status'], 'lost')
        self.user.IsWindow.return_value = True
        self.native.window_process_id.return_value = 701
        self.assertEqual(self.probe()['status'], 'lost')

    def test_same_pid_new_process_birth_or_image_is_lost(self):
        self.process.create_time.return_value = 13
        self.assertEqual(self.probe()['status'], 'lost')
        self.process.create_time.return_value = 12.5
        self.process.exe.return_value = 'C:/Other/dwrg.exe'
        self.assertEqual(self.probe()['status'], 'lost')

    def test_metadata_denial_is_unknown_and_never_replacement_authority(self):
        self.factory.side_effect = psutil.AccessDenied(700)
        self.assertEqual(self.probe()['status'], 'unknown')
        self.factory.side_effect = None
        self.native.window_text.return_value = ''
        self.assertEqual(self.probe()['status'], 'unknown')

    def test_change_during_probe_is_rejected(self):
        self.native.window_process_id.side_effect = [700, 701]
        self.assertEqual(self.probe()['status'], 'lost')

    def test_strict_identity_failure_is_lost(self):
        self.native.identity_is_allowed.return_value = False
        self.assertEqual(self.probe()['status'], 'lost')


class InputBindingRecoveryTests(unittest.TestCase):
    setUp = input_fixtures.RecoveryTests.setUp
    arm = input_fixtures.RecoveryTests.arm
    def test_unbind_is_waiting_and_retains_management(self):
        self.arm()
        self.keys.bindWindow(0)
        self.assertEqual(self.keys.snapshot['status'], 'waiting')
        self.assertFalse(self.keys.enabled)
        self.assertFalse(self.keys.snapshot['gameRequested'])
        self.source.edge(0x78, foreground=77)
        self.assertEqual(self.events, ['toggle-panel'])

    def test_unknown_binding_suspends_game_but_preserves_intent(self):
        self.arm()
        self.keys.suspendGameBinding()
        self.assertFalse(self.keys.enabled)
        self.assertTrue(self.keys.snapshot['gameRequested'])
        self.assertEqual(self.source.target, 0)
        self.source.edge(ord('G'))
        self.source.edge(ord('G'), False)
        self.source.edge(0x78, foreground=77)
        self.assertEqual(self.events, ['toggle-panel'])
        self.source.crash(); self.keys.recoverListening(); self.source.finish_start()
        self.assertFalse(self.keys.enabled)
        self.assertEqual(self.source.target, 0)
        self.keys.resumeGameBinding()
        self.assertTrue(self.keys.enabled)
        self.source.edge(ord('G'))
        self.assertEqual(self.events, ['toggle-panel', 'map'])

    def test_explicit_stop_while_suspended_does_not_revive_game(self):
        self.arm()
        self.keys.suspendGameBinding()
        self.keys.disable()
        self.keys.resumeGameBinding()
        self.assertFalse(self.keys.enabled)
        self.assertFalse(self.keys.snapshot['gameRequested'])
        self.assertEqual(self.source.target, 0)

    def test_visual_reconcile_rejects_later_input_and_closed_backend(self):
        self.keys.setOrderedMapInputEnabled(True)
        self.keys._dispatch_action('map')
        self.assertTrue(self.keys.observeMapState(False, 1))
        self.keys._dispatch_action('map')
        self.assertTrue(self.keys._map_input.open_intent)
        self.assertFalse(self.keys.observeMapState(False, 1))
        self.assertTrue(self.keys._map_input.open_intent)
        self.keys.shutdown()
        self.assertFalse(self.keys.observeMapState(False, 2))

    def test_queued_game_edge_before_resume_is_rejected_but_held_and_management_survive(self):
        self.arm()
        self.keys.suspendGameBinding()
        for vk in (ord('G'), 0x78):
            self.source.eventReceived.emit(dict(vk=vk, down=True, foreground=42,
                held=frozenset([vk]), generation=self.source.generation, createdAt=time.monotonic()))
        self.keys.resumeGameBinding()
        input_fixtures.APP.processEvents()
        self.assertEqual(self.events, ['toggle-panel'])
        self.source.edge(ord('G'))  # Repeat still belongs to the blocked press.
        self.assertEqual(self.events, ['toggle-panel'])
        self.source.edge(ord('G'), False); self.source.edge(ord('G'))
        self.assertEqual(self.events, ['toggle-panel', 'map'])

    def test_old_started_snapshot_cannot_clear_new_configuration_held_keys(self):
        self.arm()
        old = self.source.generation
        self.source.started.emit(dict(held=frozenset(), generation=old))
        self.source.held = {ord('G')}
        self.keys.configure({'map':'G','toggle-panel':'F9'})
        self.keys.enable()
        input_fixtures.APP.processEvents()
        self.source.edge(ord('G'))
        self.assertEqual(self.events, [])
        self.source.edge(ord('G'), False); self.source.edge(ord('G'))
        self.assertEqual(self.events, ['map'])


if __name__ == '__main__': unittest.main()
