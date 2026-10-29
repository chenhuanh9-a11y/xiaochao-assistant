"""Controller lease/publication tests. Module IO is fake; no installation touched."""
import copy
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from test_reopen_lifecycle import method


class Host:
    pass


for name in ('managed_author_packages_snapshot', 'inspect_author_package_removal',
             'confirm_author_package_removal', '_author_management_context',
             '_begin_author_package_management', '_consume_author_package_management'):
    fn = method(name)
    fn.__globals__.update(copy=copy, Path=Path, ROOT='test-install', RESOURCE_ROOT='test-resources', threading=threading)
    setattr(Host, name, fn)


class ManagementController(unittest.TestCase):
    def setUp(self):
        self.host = h = Host()
        h.closed = False; h.pack = object(); h.game_mode = 'nightmare'
        h.events = queue.Queue(); h._map_package_import_generation = 0
        h._map_package_import_state = dict(stage='idle', ready=None)
        h._map_package_import_cancel = threading.Event()
        h._online_map_package_generation = 0; h._online_map_package_cancel = threading.Event()
        h._map_package_helper_request = None; h._map_package_import_worker = None
        h._ensure_map_package_import_state = Mock(); h._ensure_online_map_package_state = Mock()
        h._author_import_idle = Mock(return_value=True)
        h._invalidate_capture_work = Mock(); h._invalidate_author_browser = Mock()
        h._invalidate_native_resource_context = Mock(); h._invalidate_base_display = Mock()
        h._refresh_imported_author_resources = Mock()
        self.ui_thread = threading.get_ident()
        self.io_threads = []
        def io(value):
            def run(*args, **kwargs):
                self.io_threads.append(threading.get_ident())
                return value
            return run
        self.module = SimpleNamespace(
            managed_author_packages_snapshot=Mock(side_effect=io([{'packageId':'p'}])),
            inspect_author_package_removal=Mock(side_effect=io({'packageId':'p','receiptSha256':'abc','planSha256':'plan'})),
            remove_author_package_live=Mock(side_effect=io({'status':'author-removed-live'})))
        self.patch = patch.dict(sys.modules, author_package_management=self.module)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def consume(self):
        event = self.host.events.get(timeout=2)
        self.host._consume_author_package_management(event)
        return event

    def test_list_and_inspection_run_off_ui_and_publish_only_on_consume(self):
        h = self.host
        h.managed_author_packages_snapshot(refresh=True)
        self.assertEqual(h.managed_author_packages_snapshot()['packages'], [])
        self.consume()
        self.assertEqual(h.managed_author_packages_snapshot()['packages'], [{'packageId':'p'}])
        self.assertTrue(h.inspect_author_package_removal('p')['success'])
        self.assertIsNone(h.managed_author_packages_snapshot()['ready'])
        self.consume()
        self.assertEqual(h.managed_author_packages_snapshot()['ready']['receiptSha256'], 'abc')
        self.assertTrue(all(t != self.ui_thread for t in self.io_threads))

    def test_remove_requires_reviewed_receipt_and_idle_lease(self):
        h = self.host
        self.assertFalse(h.confirm_author_package_removal('p', expected_receipt_sha256='abc', expected_plan_sha256='plan')['success'])
        h.inspect_author_package_removal('p'); self.consume()
        self.assertFalse(h.confirm_author_package_removal('p', expected_receipt_sha256='changed', expected_plan_sha256='plan')['success'])
        self.assertFalse(h.confirm_author_package_removal('p', expected_receipt_sha256='abc', expected_plan_sha256='changed')['success'])
        h._author_import_idle.return_value = False
        self.assertFalse(h.confirm_author_package_removal('p', expected_receipt_sha256='abc', expected_plan_sha256='plan')['success'])
        self.module.remove_author_package_live.assert_not_called()

    def test_remove_completion_refreshes_on_ui_then_rechecks_packages(self):
        h = self.host
        h.inspect_author_package_removal('p'); self.consume()
        self.assertTrue(h.confirm_author_package_removal('p', expected_receipt_sha256='abc', expected_plan_sha256='plan')['success'])
        h._refresh_imported_author_resources.assert_not_called()
        event = self.consume()
        self.assertEqual(event['kind'], 'author-package-removed')
        self.assertEqual(self.module.remove_author_package_live.call_args.kwargs['expected_plan_sha256'], 'plan')
        h._refresh_imported_author_resources.assert_called_once()
        self.assertEqual(h._online_installed_packages['p']['status'], 'not-installed')
        self.consume()  # asynchronous list refresh
        self.assertIsNone(h._map_package_live_request)

    def test_stale_completion_cannot_release_new_lease(self):
        h = self.host
        h.inspect_author_package_removal('p')
        event = h.events.get(timeout=2)
        newer = {'cancelled': threading.Event()}
        h._map_package_live_request = newer
        h._consume_author_package_management(event)
        self.assertIs(h._map_package_live_request, newer)
        h._refresh_imported_author_resources.assert_not_called()

    def test_cancel_keeps_lease_until_writer_completion(self):
        h = self.host
        h.inspect_author_package_removal('p')
        request = h._map_package_live_request
        request['cancelled'].set()
        h._map_package_import_state['stage'] = 'cancelling'
        self.assertFalse(h.inspect_author_package_removal('p')['success'])
        event = h.events.get(timeout=2); event['cancelled'] = True
        h._consume_author_package_management(event)
        self.assertIsNone(h._map_package_live_request)
        self.assertEqual(h._map_package_import_state['stage'], 'idle')
        self.assertIsNone(h.managed_author_packages_snapshot()['ready'])

    def test_context_change_rejects_old_inspection(self):
        h = self.host
        h.inspect_author_package_removal('p')
        h.pack = object()
        self.consume()
        self.assertIsNone(h.managed_author_packages_snapshot()['ready'])
        h._refresh_imported_author_resources.assert_not_called()

    def test_readonly_list_preserves_reviewed_import(self):
        h = self.host
        reviewed = {'result': {'packageId': 'p'}}
        h._map_package_import_result = reviewed
        h._map_package_import_state.update(stage='ready', ready={'title': 'p'})
        h.managed_author_packages_snapshot(refresh=True); self.consume()
        self.assertIs(h._map_package_import_result, reviewed)
        self.assertEqual(h._map_package_import_state['stage'], 'ready')

    def test_real_temporary_install_remove_reimport_through_controller(self):
        # Real package, receipt, OS writer lock and backup, only UI factory IO
        # remains replaced. Never points at the user's installation.
        self.patch.stop()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
        from test_live_author_import import LiveAuthorImportTests, SUJU
        fixture = LiveAuthorImportTests('runTest')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        context_globals = Host._author_management_context.__globals__
        with patch.dict(context_globals, ROOT=str(fixture.root), RESOURCE_ROOT=str(fixture.root/'_internal')):
            archive = fixture.package('nightmare-suju', {SUJU: b'owned-author-pixels'})
            for _ in range(2):
                fixture.live(archive)
                self.host.inspect_author_package_removal('nightmare-suju')
                self.consume()
                ready = self.host.managed_author_packages_snapshot()['ready']
                self.assertTrue(self.host.confirm_author_package_removal('nightmare-suju',
                    expected_receipt_sha256=ready['receiptSha256'], expected_plan_sha256=ready['planSha256'])['success'])
                event = self.consume(); self.consume()
                backup = Path(event['result']['backupPath'])
                self.assertEqual((backup/SUJU).read_bytes(), b'owned-author-pixels')
                self.assertFalse((fixture.root/SUJU).exists())
                self.assertTrue((backup/'installed-receipt.json').is_file())
                fixture.assert_base_preserved()


if __name__ == '__main__': unittest.main()
