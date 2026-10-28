"""Idle importer/controller and resource-browser lifetime tests; no Tk creation."""
import ast
import copy
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

APP = Path(__file__).parents[1] / 'app'
sys.path.insert(0, str(APP))


def subject(file, names, ns):
    tree = ast.parse((APP / file).read_text('utf-8-sig'))
    methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == len(names)
    wrapper = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), file, 'exec'), ns)
    return ns['Subject']


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root = root
        names = {'_ensure_map_package_import_state', '_author_import_idle',
                 'map_package_import_snapshot', '_start_live_author_import',
                 '_consume_live_author_import', 'cancel_map_package_import',
                 '_refresh_imported_author_resources', '_invalidate_author_browser',
                 'inspect_map_package', 'select_route_profile', 'set_game_mode',
                 '_begin_capture', '_queue_map_hotkey_edge', '_handle_map_hotkey_event',
                 'start_round', 'confirm_route', 'close'}
        cls = subject('identityv_daily_assistant.py', names,
                      dict(Path=Path, ROOT=root, RESOURCE_ROOT=root/'_internal', copy=copy,
                           threading=threading, sys=sys, tk=SimpleNamespace(Misc=object),
                           tk_widget_exists=lambda x:x is not None))
        s = self.s = cls(); s.closed=False; s.events=queue.Queue(); s.pack=object()
        s.game_mode='nightmare'; s.gate=SimpleNamespace(confirmed_id=None)
        s._map_package_helper_path=lambda:root/'absent-helper.exe'
        s._ensure_map_package_import_state()
        self.checked=dict(generation=0, installRoot=str(root.resolve()),
            resourceRoot=str((root/'_internal').resolve()),packagePath='checked.zip',
            result=dict(packageId='nightmare-suju',replaceFiles=[],missingFiles=[],archiveSha256='a'*64))
        s._map_package_import_result=self.checked
        s._map_package_import_state.update(stage='ready',ready=dict(title='author',summary='ok'))
        s._invalidate_capture_work=Mock(); s._invalidate_author_browser=Mock()
        s._refresh_imported_author_resources=Mock()

    def completion(self, **extra):
        return dict(request=self.s._map_package_live_request, **extra)

    def start(self, impl):
        owner_thread=threading.get_ident()
        def install(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(),owner_thread)
            return impl(*args, **kwargs)
        with patch('star_map_import.install_author_package_live',side_effect=install):
            self.s._start_live_author_import(self.checked)
            event=self.s.events.get(timeout=5)
        return event

    def test_ready_addition_requires_idle_but_not_helper(self):
        state=self.s.map_package_import_snapshot()
        self.assertTrue(state['canImport']); self.assertTrue(state['liveAvailable'])
        self.assertFalse(state['restartRequired']);self.assertFalse(state['helperAvailable'])
        self.s.round_work_active=True
        self.assertFalse(self.s.map_package_import_snapshot()['canImport'])
        self.assertIn('结束本局',self.s.map_package_import_snapshot()['message'])

    def test_core_upgrade_retains_closed_helper_requirement(self):
        self.checked['result']['missingFiles']=[dict(path='_internal/references/core.json')]
        state=self.s.map_package_import_snapshot()
        self.assertFalse(state['liveAvailable']);self.assertTrue(state['restartRequired'])
        self.assertFalse(state['canImport'])

    def test_idle_checks_round_locks_prepared_capture_and_preload(self):
        for name,value in [('round_work_active',True),('map_is_open',True),
                           ('round_start_intent',{'prepared':True}),('active_confirmation',{'id':1}),
                           ('capture_in_progress',True),('resource_preload_pending',True),
                           ('_alignment_v2_worker_running',True)]:
            setattr(self.s,name,value);self.assertFalse(self.s._author_import_idle(),name)
            setattr(self.s,name,None)
        self.s.gate.confirmed_id='map';self.assertFalse(self.s._author_import_idle())
        self.s.gate.confirmed_id=None
        self.s._capture_request_lock=threading.Lock();self.s._capture_request_lock.acquire()
        self.assertFalse(self.s._author_import_idle());self.s._capture_request_lock.release()
        self.assertTrue(self.s._author_import_idle())

    def test_worker_only_posts_then_ui_refreshes_and_unlocks(self):
        event=self.start(lambda *a,**k:dict(authorFilesAdded=72))
        self.assertEqual(self.s.map_package_import_snapshot()['stage'],'installing')
        self.s._refresh_imported_author_resources.assert_not_called()
        self.s._consume_live_author_import(event)
        self.s._refresh_imported_author_resources.assert_called_once()
        self.assertIsNone(self.s._map_package_live_request)
        self.assertEqual(self.s._map_package_import_state['stage'],'complete')
        self.s._consume_live_author_import(event)
        self.s._refresh_imported_author_resources.assert_called_once()

    def test_cancel_keeps_lease_until_worker_rolls_back(self):
        entered=threading.Event(); release=threading.Event()
        def install(*a,**kwargs):
            entered.set();self.assertTrue(release.wait(5))
            if kwargs['cancelled']():raise ValueError('import-cancelled')
            return dict(authorFilesAdded=1)
        with patch('star_map_import.install_author_package_live',side_effect=install):
            self.s._start_live_author_import(self.checked);self.assertTrue(entered.wait(5))
            generation=self.s._map_package_import_generation
            self.s.cancel_map_package_import()
            self.assertEqual(self.s._map_package_import_generation,generation)
            self.assertIsNotNone(self.s._map_package_live_request)
            self.assertEqual(self.s.map_package_import_snapshot()['stage'],'cancelling')
            release.set();event=self.s.events.get(timeout=5)
        self.s._consume_live_author_import(event)
        self.assertIsNone(self.s._map_package_live_request)
        self.s._refresh_imported_author_resources.assert_not_called()
        self.assertEqual(self.s._map_package_import_state['stage'],'idle')

    def test_failure_releases_lease_without_refreshing(self):
        event=self.start(Mock(side_effect=OSError('write-failed')))
        self.s._consume_live_author_import(event)
        self.assertEqual(self.s._map_package_import_state['stage'],'error')
        self.assertIsNone(self.s._map_package_live_request)
        self.s._refresh_imported_author_resources.assert_not_called()

    def test_old_completion_cannot_release_new_lease_or_repaint(self):
        event=self.start(lambda *a,**k:dict(authorFilesAdded=1))
        current=self.s._map_package_live_request
        self.s._consume_live_author_import(dict(event,request=dict(current)))
        self.assertIs(self.s._map_package_live_request,current)
        self.s._map_package_import_generation+=1
        self.s._consume_live_author_import(event)
        self.s._refresh_imported_author_resources.assert_not_called()
        self.assertEqual(self.s._map_package_import_state['stage'],'error')

    def test_refresh_failure_explains_files_committed_and_allows_retry(self):
        event=self.start(lambda *a,**k:dict(authorFilesAdded=72))
        self.s._log=Mock();self.s._refresh_imported_author_resources.side_effect=ValueError('bad')
        self.s._consume_live_author_import(event)
        self.assertIsNone(self.s._map_package_live_request)
        self.assertIn('文件已安装',self.s._map_package_import_state['message'])
        self.assertIn('重试刷新',self.s._map_package_import_state['message'])

    def test_commit_point_success_wins_over_late_cancel(self):
        event=self.start(lambda *a,**k:dict(authorFilesAdded=0))
        self.s.cancel_map_package_import()
        self.s._consume_live_author_import(event)
        self.s._refresh_imported_author_resources.assert_called_once()
        self.assertEqual(self.s._map_package_import_state['stage'],'complete')

    def test_lease_blocks_new_work_and_close_waits_for_rollback(self):
        self.s._map_package_live_request={'cancelled':threading.Event()}
        self.s._set_status=Mock()
        self.assertFalse(self.s.start_round()['success'])
        self.assertFalse(self.s.set_game_mode('difficult')['success'])
        self.assertFalse(self.s.select_route_profile('suju')['success'])
        self.assertFalse(self.s.inspect_map_package('other.zip')['success'])
        self.assertFalse(self.s._begin_capture(False))
        self.s._queue_map_hotkey_edge();self.s._handle_map_hotkey_event();self.s.confirm_route()
        self.s.close();self.assertFalse(self.s.closed)
        self.assertTrue(self.s._map_package_live_request['cancelled'].is_set())

    def test_owner_refresh_rebuilds_options_but_keeps_custom_gallery_and_preference(self):
        self.s.__dict__.pop('_refresh_imported_author_resources')
        gallery=object();self.s.custom_maps=gallery
        self.s.preferred_route_profile_id='custom:one';self.s.mode_route_profiles={'nightmare':'custom:one'}
        self.s._install_map_mode=Mock();self.s._shown_route_profile=lambda:'custom:one'
        self.s._route_profile_label=lambda x:x;self.s.route_profile_var=Mock()
        self.s._mode_route_options=lambda:(('custom:one','自定义'),('suju','滋酥居'))
        self.s.route_profile_box=Mock();self.s._refresh_candidates=Mock();self.s._sync_route_settings_ui=Mock()
        self.s._refresh_imported_author_resources()
        self.assertIs(self.s.custom_maps,gallery)
        self.assertEqual(self.s.preferred_route_profile_id,'custom:one')
        self.assertEqual(self.s.mode_route_profiles,{'nightmare':'custom:one'})
        self.s.route_profile_box.configure.assert_called_once_with(values=['自定义','滋酥居'])


class BrowserTests(unittest.TestCase):
    def setUp(self):
        cls=subject('map_resource_browser.py',{'invalidate_resource_context','sync_map_package_import',
                                            '_confirm_map_package'},dict(Path=Path,SHADOW_DIP=1))
        self.s=cls();s=self.s;s._closed=False;s._import_signature=None
        for name in ('import_message','import_title','import_summary','import_feedback','import_surface',
                     'import_actions','import_button','import_confirm_button','import_cancel_button','filter_host','content'):
            setattr(s,name,Mock())
        self.state=dict(stage='ready',liveAvailable=True,helperAvailable=False,canImport=True,
                        ready=dict(title='author',summary='72'),message='无需重启')
        s._map_package_snapshot=lambda:self.state
        s.assistant=SimpleNamespace(map_package_import_snapshot=Mock(),inspect_map_package=Mock(),
                                    confirm_map_package_import=Mock(),cancel_map_package_import=Mock())

    def test_live_confirm_does_not_require_helper(self):
        self.s.sync_map_package_import()
        self.s.import_confirm_button.configure.assert_called_with(state='normal',text='导入并刷新')
        self.s._map_package_action=Mock();self.s.winfo_toplevel=lambda:'owner'
        self.s._confirm_map_package()
        self.s._map_package_action.assert_called_once_with('confirm_map_package_import',owner='owner')

    def test_installing_disables_new_selection_and_confirmation(self):
        self.state.update(stage='installing',busy=True)
        self.s.sync_map_package_import()
        self.s.import_confirm_button.configure.assert_called_with(state='disabled',text='导入并刷新')
        self.s.import_button.configure.assert_called_with(state='disabled')

    def test_context_invalidation_drops_old_photo_callbacks_and_caches(self):
        s=self.s;s._generation=4;s._pending={'old':[object()]};s._cache={'old':object()}
        s._close_preview=Mock();s._context=('old',);s._runtime_route_signature=('old',)
        s.invalidate_resource_context()
        self.assertEqual(s._generation,5);self.assertFalse(s._pending);self.assertFalse(s._cache)
        self.assertIsNone(s._context);self.assertIsNone(s._runtime_route_signature)
        s._close_preview.assert_called_once()


if __name__=='__main__':unittest.main()
