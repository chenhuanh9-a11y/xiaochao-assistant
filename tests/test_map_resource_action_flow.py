"""Actual off-screen Tk action placement and resource-page lifecycle.

Uses the existing page fixture's setup/cleanup/wait helpers, without inheriting
its tests. Controller snapshots, file picking and mutations remain substitutes:
this does not verify ZIP I/O, receipt transactions, an installed PE, or readable
on-screen pixels. Geometry assertions establish discoverability/clipping only.
"""
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

import test_map_resources_page as fixtures


class MapResourceActionFlowTests(unittest.TestCase):
    setUp = fixtures.MapResourcesPageTests.setUp
    tearDown = fixtures.MapResourcesPageTests.tearDown
    settle = fixtures.MapResourcesPageTests.settle

    def test_unchanged_page_poll_does_not_reconfigure_selected_actions(self):
        self.page.refresh()
        self.settle()
        self.page._render_library()
        with patch.object(self.page, '_show_selected') as detail, \
             patch.object(self.page.primary_button, 'configure') as primary, \
             patch.object(self.page.actual_selection, 'configure') as selection, \
             patch.object(self.page.catalog_refresh_button, 'configure') as refresh:
            for _ in range(10):
                self.page._render_library()
                self.page.sync_online_packages()
            detail.assert_not_called()
            primary.assert_not_called()
            selection.assert_not_called()
            refresh.assert_not_called()

    def test_current_tab_click_keeps_existing_list_and_context(self):
        self.page._detail_open = False
        context = self.page._browse_context
        signature = self.page._library_signature
        with patch.object(self.page, '_render_library') as render:
            self.page.tab_buttons[self.page._tab].invoke()
            render.assert_not_called()
        self.assertIs(self.page._browse_context, context)
        self.assertEqual(self.page._library_signature, signature)

    def test_unchanged_management_ready_does_not_repack_confirmation(self):
        self.show_managed()
        with patch.object(self.page.managed_message, 'configure') as message, \
             patch.object(self.page.managed_confirm, 'pack') as confirm, \
             patch.object(self.page.managed_surface, 'pack') as surface:
            for _ in range(10): self.page.sync_managed_packages()
            message.assert_not_called()
            confirm.assert_not_called()
            surface.assert_not_called()

    def test_navigation_only_unmaps_previous_page_and_current_click_is_noop(self):
        from desktop_main_ui import DesktopMainUI
        frames = {key: Mock() for key in ('home', 'maps', 'help')}
        for frame in frames.values(): frame.winfo_manager.return_value = 'pack'
        ui = SimpleNamespace(PAGES=tuple(frames), _page_frames=frames,
            _active_page_frame=frames['home'], _page='home',
            _nav_buttons={key: Mock() for key in frames}, header=Mock(),
            header_title=Mock(), _place_nav_mark=Mock(), _rise_page=Mock(),
            _apply_icons=Mock(), _refresh_maps=Mock())
        DesktopMainUI.show_page(ui, 'maps')
        frames['home'].pack_forget.assert_called_once()
        frames['help'].pack_forget.assert_not_called()
        frames['maps'].pack.assert_called_once()
        DesktopMainUI.show_page(ui, 'maps')
        frames['maps'].pack.assert_called_once()
        ui._refresh_maps.assert_called_once()

    def compact(self):
        # 560 px of page content approximates a 650 px window with its sidebar.
        self.host.geometry('560x500+24000+24000')
        self.root.update()
        self.page._layout_controls()
        self.root.update_idletasks()
        self.assertEqual((self.page.winfo_width(), self.page.winfo_height()), (560, 500))

    def rect(self, widget):
        left = widget.winfo_rootx() - self.page.winfo_rootx()
        top = widget.winfo_rooty() - self.page.winfo_rooty()
        return left, top, left + widget.winfo_width(), top + widget.winfo_height()

    def assert_fully_visible(self, widget):
        self.assertTrue(widget.winfo_viewable(), str(widget))
        self.assertGreater(widget.winfo_width(), 1, str(widget))
        self.assertGreater(widget.winfo_height(), 1, str(widget))
        left, top, right, bottom = self.rect(widget)
        parent = widget.master
        while parent is not None:
            pl, pt, pr, pb = self.rect(parent)
            self.assertGreaterEqual(left, pl - 1, (str(widget), 'left', str(parent)))
            self.assertGreaterEqual(top, pt - 1, (str(widget), 'top', str(parent)))
            self.assertLessEqual(right, pr + 1, (str(widget), 'right', str(parent)))
            self.assertLessEqual(bottom, pb + 1, (str(widget), 'bottom', str(parent)))
            if parent is self.page:
                break
            parent = parent.master

    def choose_zip(self, name='route.zip'):
        path = self.data.root / name
        with patch('map_resource_browser.filedialog.askopenfilename', return_value=str(path)):
            self.page.import_button.invoke()
        self.root.update_idletasks()
        self.assertIn(('inspect', str(path)), self.actions.calls)
        return path

    def show_managed(self):
        self.page._select_package('nightmare-nightingale')
        self.page.manage_button.invoke()
        self.root.update_idletasks()
        # The shared Actions fixture must use production's stage spelling.
        self.assertEqual(self.actions.managed.get('stage'), 'ready')
        self.assertTrue(self.page._managed_ready)

    def widgets(self, parent):
        for child in parent.winfo_children():
            yield child
            yield from self.widgets(child)

    def test_scrolled_detail_zip_confirmation_stays_in_fixed_top_host(self):
        self.compact()
        self.page._select_package('nightmare-nightingale')
        self.root.update_idletasks()
        self.page.detail_canvas.yview_moveto(1)
        self.root.update_idletasks()
        self.assertGreater(self.page.detail_canvas.yview()[0], 0,
                           'fixture must actually scroll before choosing a ZIP')
        self.choose_zip()
        self.assertEqual(self.page._operation_kind, 'import')
        self.assertIs(self.page.import_surface.master, self.page.operation_host)
        for widget in (self.page.import_title, self.page.import_confirm_button,
                       self.page.import_cancel_button):
            self.assert_fully_visible(widget)
            self.assertLessEqual(self.rect(widget)[3], self.rect(self.page.content)[1] + 1)
        before = (self.rect(self.page.import_confirm_button), self.rect(self.page.import_cancel_button))
        self.page.detail_canvas.yview_moveto(0)
        self.root.update_idletasks()
        self.assertEqual(before, (self.rect(self.page.import_confirm_button),
                                  self.rect(self.page.import_cancel_button)))

    def test_management_confirmation_and_cancel_stay_near_fixed_detail_footer(self):
        self.compact()
        self.show_managed()
        self.assertIs(self.page.managed_surface.master, self.page.detail_actions)
        for widget in (self.page.manage_button, self.page.managed_confirm, self.page.managed_cancel_button):
            self.assert_fully_visible(widget)
        confirm, cancel = self.rect(self.page.managed_confirm), self.rect(self.page.managed_cancel_button)
        self.assertLessEqual(abs(confirm[1] - cancel[1]), 2)
        self.assertGreaterEqual(cancel[0] - confirm[2], 0)
        self.assertLessEqual(cancel[0] - confirm[2], 20)
        self.assertLessEqual(confirm[1] - self.rect(self.page.manage_button)[3], 140)
        self.page.detail_canvas.yview_moveto(1)
        self.root.update_idletasks()
        self.assertEqual(confirm, self.rect(self.page.managed_confirm))
        self.assertEqual(cancel, self.rect(self.page.managed_cancel_button))

    def test_completed_import_hides_filename_and_actions_without_sync_resurrection(self):
        self.compact()
        self.choose_zip('completed-author.zip')
        self.page.import_confirm_button.invoke()
        self.assertEqual(self.actions.import_state.get('stage'), 'complete')
        for _ in range(8):
            self.page.sync_map_package_import()
            self.page.sync_online_packages()
        self.root.update_idletasks()
        self.assertIsNone(self.page._operation_kind)
        for widget in (self.page.operation_host, self.page.import_title,
                       self.page.import_confirm_button, self.page.import_cancel_button):
            self.assertFalse(widget.winfo_viewable(), str(widget))
        self.page.import_confirm_button.invoke()
        self.assertEqual(len([call for call in self.actions.calls if call[0] == 'import']), 1)

    def test_import_and_management_switches_revoke_the_previous_ready_action(self):
        self.compact()
        self.choose_zip('first.zip')
        self.show_managed()
        self.assertIn(('cancel-import', 'user-cancelled'), self.actions.calls)
        self.assertFalse(self.page.import_confirm_button.winfo_viewable())
        self.choose_zip('second.zip')
        self.assertEqual(self.page._operation_kind, 'import')
        self.assertFalse(self.page._managed_open)
        self.assertIsNone(self.page._managed_ready)
        self.assertFalse(self.page.managed_confirm.winfo_viewable())
        self.page.managed_confirm.invoke()
        self.assertFalse(any(call[0] == 'remove' for call in self.actions.calls))
        self.assert_fully_visible(self.page.import_confirm_button)

    def test_cancelled_or_wrong_mode_management_ready_cannot_reappear_or_remove(self):
        self.compact()
        self.show_managed()
        ready = dict(self.actions.managed['ready'])
        self.page.managed_cancel_button.invoke()
        # Simulate a late controller snapshot after cancelling the visible flow.
        self.actions.managed.update(stage='ready', busy=False, ready=ready)
        for _ in range(3):
            self.page.sync_managed_packages()
        self.assertIsNone(self.page._managed_ready)
        self.assertFalse(self.page.managed_confirm.winfo_viewable())
        self.show_managed()
        self.page.resource_mode_buttons['difficult'].invoke()
        self.settle()
        self.actions.managed.update(stage='ready', busy=False, ready=ready)
        self.page.sync_managed_packages()
        self.page.managed_confirm.invoke()
        self.assertIsNone(self.page._managed_ready)
        self.assertFalse(self.page.managed_confirm.winfo_viewable())
        self.assertFalse(any(call[0] == 'remove' for call in self.actions.calls))
        self.assertEqual(self.actions.game_mode, 'nightmare')

    def test_offline_discovery_has_one_installed_tab_and_no_duplicate_advanced_entry(self):
        self.compact()
        self.actions.online.update(stage='idle', busy=False, canCheck=False, canDownload=False,
                                   canCancel=False, packages=[], message='尚未配置地图下载地址')
        self.page.tab_buttons['discover'].invoke()
        self.page.sync_online_packages()
        self.root.update_idletasks()
        buttons = [widget for widget in self.widgets(self.page)
                   if isinstance(widget, (tk.Button, ttk.Button)) and widget.winfo_viewable()]
        texts = [str(widget.cget('text')) for widget in buttons]
        self.assertEqual(texts.count('已安装'), 1)
        for text in texts:
            self.assertNotIn('查看已安装', text)
            self.assertNotIn('高级', text)
            self.assertNotIn('来源设置', text)
        self.assertEqual(str(self.page.import_button.cget('state')), 'normal')
        self.assert_fully_visible(self.page.import_button)
        self.choose_zip('offline.zip')
        self.assert_fully_visible(self.page.import_confirm_button)
        self.assertFalse(any(call[0] in {'check', 'download'} for call in self.actions.calls))

    def test_published_context_invalidates_once_and_busy_file_operation_blocks_metadata(self):
        self.compact()
        epoch = self.page._browse_epoch
        self.actions.pack = SimpleNamespace(resource_root=self.data.root)
        self.actions.import_state.update(stage='installing', busy=True)
        with patch.object(self.page._metadata_executor, 'submit',
                          side_effect=AssertionError('metadata must not overlap file operation')) as submit:
            self.page.invalidate_resource_context()
            self.assertEqual(self.page._browse_epoch, epoch + 1)
            self.page.refresh()
            self.page._poll_context()
            self.root.update_idletasks()
            self.assertEqual(self.page._browse_epoch, epoch + 1)
            self.assertIsNone(self.page._browse_work)
            submit.assert_not_called()
        self.actions.import_state.update(stage='idle', busy=False)
        self.page.refresh()
        self.settle()
        self.assertEqual(self.page._browse_epoch, epoch + 1)
        self.assertIsNotNone(self.page._browse_context)

    def test_resource_publication_clears_old_image_but_reuses_existing_card_frame(self):
        self.compact()
        key = 'nightmare-nightingale'
        record = self.page._card_records[key]
        frame, image = record['frame'], record['image']
        old_photo = tk.PhotoImage(master=self.page, width=2, height=2)
        image.configure(image=old_photo)
        image._map_photo = old_photo
        self.actions.import_state.update(stage='installing', busy=True)
        self.actions.pack = SimpleNamespace(resource_root=self.data.root)
        self.page.invalidate_resource_context()
        self.assertEqual(str(image.cget('image')), '')
        self.assertIsNone(image._map_photo)
        self.page.refresh()
        self.assertIs(self.page._card_records[key]['frame'], frame)
        self.assertEqual(str(image.cget('image')), '')
        self.actions.import_state.update(stage='idle', busy=False)
        self.page.refresh()
        self.settle()
        self.assertIs(self.page._card_records[key]['frame'], frame)
        self.assertIsNotNone(self.page._browse_context)


if __name__ == '__main__':
    unittest.main()
