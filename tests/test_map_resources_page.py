"""Actual Tk page with isolated controller I/O; no user windows/network/game."""
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import tkinter as tk
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from map_resources_page import MapResourcesPage
import map_resources_page as page_module
import test_map_resource_context as context_fixture


class Actions:
    def __init__(self, root):
        self.pack = SimpleNamespace(resource_root=root)
        self.game_mode = 'nightmare'
        self.calls = []
        self.selected = 'wang-qingxin'
        self.import_state = dict(stage='idle', busy=False, liveAvailable=True, helperAvailable=False)
        self.online = dict(stage='ready', busy=False, canCheck=True, canDownload=True, canCancel=False,
            packages=[dict(packageId='nightmare-nightingale', title='爱吃醋', bytes=100, revision=2,
                           installation={'status': 'installed'}),
                      dict(packageId='nightmare-lianghapi', title='凉哈皮', bytes=120, revision=1,
                           installation={'status': 'not-installed'})])
        self.managed = dict(packages=[dict(packageId='nightmare-nightingale', title='爱吃醋',
                            status='installed', canRemove=True)], busy=False)
    def route_selection_snapshot(self):
        return dict(mode=self.game_mode, selected=self.selected, effective=self.selected,
                    options=(('nightingale', '爱吃醋'), ('wang-qingxin', '王清心')))
    def select_route_profile(self, value):
        self.calls.append(('use', value)); self.selected=value
        return dict(success=True)
    def set_game_mode(self, value):
        raise AssertionError('Browsing must never call the runtime mode writer')
    def map_package_import_snapshot(self): return dict(self.import_state)
    def online_map_packages_snapshot(self): return dict(self.online)
    def managed_author_packages_snapshot(self, refresh=False): return dict(self.managed)
    def check_online_map_packages(self): self.calls.append(('check',)); return dict(success=True)
    def download_online_map_package(self, package):
        self.calls.append(('download',package)); self.online.update(stage='downloading',busy=True,canCancel=True)
        return dict(success=True)
    def cancel_online_map_package(self, reason):
        self.calls.append(('cancel-download',reason)); self.online.update(stage='cancelled',busy=False,canCancel=False)
        return dict(success=True)
    def inspect_map_package(self, path, *, auto_import=False):
        self.calls.append(('inspect',path)); self.import_state.update(stage='ready', packagePath=str(path),
            ready=dict(title='fixture', summary='检查通过'), canImport=True)
        return dict(success=True)
    def confirm_map_package_import(self, owner):
        self.calls.append(('import',)); self.import_state.update(stage='complete',ready=None,canImport=False,restartRequired=False)
        return dict(success=True)
    def cancel_map_package_import(self, reason):
        self.calls.append(('cancel-import',reason)); self.import_state=dict(stage='idle',busy=False,liveAvailable=True)
        return dict(success=True)
    def inspect_author_package_removal(self, package):
        self.calls.append(('inspect-remove',package));self.managed.update(stage='ready');self.managed['ready']=dict(packageId=package,title='爱吃醋',
            fileCount=2,files=[{'path':'author/a'},{'path':'author/b'}], receiptSha256='receipt',planSha256='plan')
        return dict(success=True)
    def confirm_author_package_removal(self, package, **kwargs):
        self.calls.append(('remove',package,kwargs)); self.managed.pop('ready',None)
        self.managed.update(stage='complete',packages=[dict(packageId=package,status='not-installed')])
        return dict(success=True)


class MapResourcesPageTests(unittest.TestCase):
    def setUp(self):
        # This controller substitute keeps the old manual-ready scenario;
        # real automatic file transactions have their separate short-flow audit.
        prompt = patch('tkinter.messagebox.askyesno', return_value=True)
        prompt.start(); self.addCleanup(prompt.stop)
        self.data = context_fixture.BrowseContextTests()
        self.data.setUp(); self.addCleanup(self.data.doCleanups)
        self.data.nightmare()
        self.data.catalog('nightingale', {'map-0': {'1':'authors/0.png','2':'authors/1.png'}})
        self.data.png('authors/0.png'); self.data.png('authors/1.png')
        self.data.native(); self.data.png('authors/0.png')
        self.root = tk.Tk(); self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda kind, error, tb: self.callback_errors.append((kind.__name__,str(error)))
        self.host = tk.Toplevel(self.root)
        import ctypes
        from ctypes import wintypes
        import ui_packaged_audit as audit
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        audit.isolate_window(self.host, tk.Wm.attributes, user)
        audit.set_isolated_override(self.host, True, tk.Wm.overrideredirect, tk.Wm.attributes, user)
        self.host.geometry('960x680+24000+24000')
        self.host.deiconify()
        self.actions = Actions(self.data.root)
        self.page = MapResourcesPage(self.host, self.actions)
        self.page.pack(fill='both',expand=True)
        self.page.refresh()
        self.settle()
    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.callback_errors, [])
    def settle(self):
        until=time.monotonic()+3
        while time.monotonic()<until:
            self.root.update()
            if self.page._browse_work is None and self.page._browse_pending is None:
                self.root.update_idletasks();break
            time.sleep(.005)
    def test_actual_page_browses_both_modes_without_runtime_writer(self):
        self.assertEqual(self.page._browse_context.mode,'nightmare')
        self.page.resource_mode_buttons['difficult'].invoke();self.settle()
        self.assertEqual(self.page._browse_context.mode,'difficult')
        self.assertEqual(self.actions.game_mode,'nightmare')
        self.assertEqual(self.actions.calls,[])
    def test_preview_and_download_never_change_selected_author(self):
        self.page._select_package('nightmare-nightingale')
        self.page.map_box.current(0);self.page._map_changed()
        self.assertEqual(self.actions.selected,'wang-qingxin')
        self.page.primary_button.invoke()
        self.assertEqual(self.actions.selected,'nightingale')
        self.page._select_tab('discover');self.page._select_package('nightmare-lianghapi')
        self.page.primary_button.invoke();self.page.primary_button.invoke()
        self.assertEqual([call for call in self.actions.calls if call[0]=='download'], [('download','nightmare-lianghapi')])
        self.assertEqual(self.actions.selected,'nightingale')
        self.page.task_cancel.invoke()
        self.assertIn(('cancel-download','user-cancelled'),self.actions.calls)
    def test_offline_import_and_duplicate_confirm_use_existing_contract(self):
        self.actions.online.update(canCheck=False,canDownload=False,packages=[],message='尚未配置地图下载地址')
        self.page._select_tab('discover')
        with patch('map_resource_browser.filedialog.askopenfilename',return_value=str(self.data.root/'route.zip')):
            self.page.import_button.invoke()
        self.page.import_confirm_button.invoke();self.page.import_confirm_button.invoke()
        self.assertEqual(len([call for call in self.actions.calls if call[0]=='import']),1)
        self.assertEqual(self.actions.selected,'wang-qingxin')
    def test_managed_remove_keeps_receipt_and_plan_confirmation(self):
        self.page._select_package('nightmare-nightingale');self.page.manage_button.invoke()
        self.page.managed_confirm.invoke()
        self.assertIn(('remove','nightmare-nightingale',dict(expected_receipt_sha256='receipt',expected_plan_sha256='plan')),self.actions.calls)
    def test_small_layout_dpi_and_reentry_keep_recovery_accessible(self):
        for scale in (1,1.5,2,1):
            self.page.set_display_scale(scale)
            self.host.geometry(f'{round(500*scale)}x{round(450*scale)}+24000+24000')
            self.root.update();self.page._layout_controls()
            self.page._select_package('nightmare-nightingale')
            self.root.update_idletasks()
            self.assertEqual(self.page.back_button.winfo_manager(),'pack')
            self.page.back_button.invoke();self.root.update_idletasks()
            self.assertEqual(self.page.list_host.winfo_manager(),'grid')
        for _ in range(10):
            self.page.suspend();self.page.refresh();self.settle()
        self.assertLessEqual(len(self.page._preview_futures),2)
        self.assertLessEqual(len(self.page._preview_waiting),64)
        self.assertLessEqual(len(self.page._browse_cache),2)

    def test_desktop_page_entry_constructs_the_actual_new_page(self):
        from desktop_main_ui import DesktopMainUI
        self.page.destroy()
        entry = SimpleNamespace(body=self.host, assistant=self.actions, _page_frames={}, drag_handles=())
        DesktopMainUI._build_maps(entry)
        self.page = entry.map_browser
        self.page.pack(fill='both', expand=True);self.page.refresh();self.settle()
        self.assertIs(entry._page_frames['maps'], self.page)
        self.assertIsInstance(self.page, MapResourcesPage)
        self.assertTrue(entry.drag_handles)

    def test_packaged_diagnostic_uses_visible_resource_tab_actions(self):
        import ui_packaged_audit as audit
        self.page._open_gallery = self.page._show_overview = lambda: self.fail('obsolete gallery action')
        audit.activate_resource_tab(self.page, 'discover')
        self.assertEqual(self.page._tab,'discover')
        audit.activate_resource_tab(self.page, 'installed')
        self.assertEqual(self.page._tab,'installed')
        with self.assertRaises(ValueError): audit.activate_resource_tab(self.page,'unknown')
        self.assertEqual(self.actions.calls,[])

    def test_diagnostic_windows_are_inert_before_constructor_mapping(self):
        import ctypes
        from ctypes import wintypes
        import ui_packaged_audit as audit
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]
        user.GetAncestor.restype=wintypes.HWND
        user.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]
        user.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_long]
        window=tk.Toplevel(self.root);window.withdraw()
        try:
            hwnd=audit.isolate_window(window,tk.Wm.attributes,user)
            self.assertTrue(window.attributes('-disabled'))
            self.assertEqual(window.attributes('-alpha'),0.0)
            self.assertEqual(window.state(),'withdrawn')
            self.assertTrue(user.GetWindowLongW(hwnd,-20)&0x08000000)
            window.title('星图 1.0.5')
            audit.set_isolated_override(window, True, tk.Wm.overrideredirect, tk.Wm.attributes, user)
            window.update_idletasks()
            hwnd=user.GetAncestor(window.winfo_id(),2)
            self.assertTrue(window.attributes('-disabled'))
            self.assertTrue(user.GetWindowLongW(hwnd,-20)&0x08000000)
        finally:
            window.destroy()

    def test_slow_old_context_never_blocks_ui_or_replaces_latest_mode(self):
        started, release = threading.Event(), threading.Event()
        original = page_module.load_browse_context
        calls = []
        def slow(root, mode, **kwargs):
            calls.append(mode)
            if len(calls) == 1:
                started.set();release.wait(2)
            return original(root, mode)  # Simulates an uncancellable slow read.
        with patch.object(page_module, 'load_browse_context', side_effect=slow):
            self.page.invalidate_resource_context()
            self.assertTrue(started.wait(.5))
            begin = time.perf_counter()
            self.page.resource_mode_buttons['difficult'].invoke()
            for _ in range(15): self.root.update()
            self.assertLess(time.perf_counter()-begin, .5)
            self.assertIsNone(self.page._browse_context)
            release.set();self.settle()
        self.assertEqual(self.page._browse_context.mode,'difficult')
        self.assertEqual(self.actions.game_mode,'nightmare')
        self.assertEqual(calls,['nightmare','difficult'])

    def test_ui_preview_requests_do_not_stat_files(self):
        with patch('pathlib.Path.stat', side_effect=AssertionError('UI file stat forbidden')):
            self.page._show_selected()
            self.page._request_detail()
            self.page._sync_selected_actions()

    def test_periodic_render_uses_route_snapshot_from_explicit_sync(self):
        with patch.object(self.actions, 'route_selection_snapshot', side_effect=AssertionError('poll route I/O')):
            for _ in range(10):
                self.page._render_library()
        self.page.sync_route_selection()
        self.assertEqual(self.page._route_snapshot['selected'],self.actions.selected)

    def test_global_cancel_targets_live_import_before_online_busy_alias(self):
        self.actions.import_state.update(stage='installing',busy=True)
        self.actions.online.update(busy=True,canCancel=True)
        self.page._cancel_current_task()
        self.assertIn(('cancel-import','user-cancelled'),self.actions.calls)
        self.assertFalse(any(call[0]=='cancel-download' for call in self.actions.calls))

    def test_global_cancel_targets_shared_management_lease(self):
        self.actions.managed.update(stage='removing',busy=True,message='正在备份移出')
        self.actions.online.update(busy=True,canCancel=False)
        self.page.sync_online_packages()
        self.assertEqual(self.page.task_cancel.winfo_manager(),'pack')
        self.page.task_cancel.invoke()
        self.assertIn(('cancel-import','user-cancelled'),self.actions.calls)

    def test_unknown_helper_before_inspection_does_not_claim_import_unsupported(self):
        self.actions.import_state=dict(stage='idle',busy=False,helperAvailable=False,liveAvailable=False)
        self.page.sync_map_package_import()
        self.assertNotIn('不支持',self.page.import_message.cget('text'))
        self.assertEqual(str(self.page.import_button.cget('state')),'normal')

    def test_tab_reentry_reuses_cards_and_restores_empty_detail(self):
        self.page._select_package('nightmare-nightingale')
        frame = self.page._card_records['nightmare-nightingale']['frame']
        command_count = len(self.root.tk.call('info', 'commands'))
        self.actions.online.update(packages=[])
        for _ in range(15):
            self.page._select_tab('discover')
            self.assertEqual(self.page._map_items, ())
            self.assertIsNone(self.page.detail_image._image_request)
            self.page._select_tab('installed')
            self.assertTrue(self.page._map_items)
            self.assertIs(self.page._card_records['nightmare-nightingale']['frame'], frame)
        self.assertLessEqual(len(self.root.tk.call('info', 'commands')), command_count + 4)

    def test_cancel_management_rejects_late_confirmation(self):
        self.page._select_package('nightmare-nightingale')
        self.page.manage_button.invoke()
        self.assertTrue(self.page._managed_ready)
        self.page.managed_cancel_button.invoke()
        self.page._confirm_managed_removal()
        self.assertFalse(self.page._managed_open)
        self.assertIsNone(self.page._managed_ready)
        self.assertFalse(any(call[0] == 'remove' for call in self.actions.calls))

    def test_management_confirmation_is_scoped_to_page_mode_and_selection(self):
        for change in (
                lambda: self.page._select_tab('discover'),
                lambda: self.page._select_resource_mode('difficult'),
                lambda: (self.page.suspend(), self.page.refresh())):
            self.page._select_resource_mode('nightmare');self.settle()
            self.page._select_tab('installed')
            self.page._select_package('nightmare-nightingale')
            self.page.manage_button.invoke()
            self.assertTrue(self.page._managed_ready)
            change()
            self.page._confirm_managed_removal()
            self.assertFalse(any(call[0] == 'remove' for call in self.actions.calls))


if __name__=='__main__':unittest.main()
