"""Real Tk geometry regression; withdrawn windows, fixture metadata, no user input."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from map_resource_browser import MapResourceBrowser


class ManagedPackagesTkLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = dict(packages=[dict(packageId='nightmare-suju', title='Test author',
                                        status='installed', canRemove=True)])
        self.refreshes = []
        def snapshot(**kwargs):
            self.refreshes.append(kwargs)
            return self.state
        self.assistant = SimpleNamespace(
            pack=SimpleNamespace(mode='nightmare', maps=[], default_entry_floor='1',
                                 resource_root=Path(self.temp.name)),
            _mode_route_options=lambda: [('test-author', 'Test author')],
            managed_author_packages_snapshot=snapshot)
        self.window = None
        self.open_browser()

    def open_browser(self):
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.browser = MapResourceBrowser(self.window, self.assistant)
        self.browser.pack(fill='both', expand=True)
        self.browser.refresh()
        self.root.update_idletasks()

    def assert_panel_before(self, anchor):
        children = self.browser.pack_slaves()
        self.assertEqual(self.browser.managed_surface.winfo_manager(), 'pack')
        self.assertLess(children.index(self.browser.managed_surface), children.index(anchor))
        self.assertEqual(self.errors, [], str(self.errors))
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertEqual(self.window.state(), 'withdrawn')

    def background_refresh(self, message):
        self.state['message'] = message
        # Exercise the same scheduled Tk callback -> _drain -> sync path as runtime.
        self.browser._schedule(0, self.browser._drain)
        self.root.update()
        self.assertEqual(self.errors, [], str(self.errors))
        self.assertEqual(self.browser.managed_message.cget('text'), message)

    def test_overview_button_then_background_refresh_without_filter_anchor(self):
        self.assertEqual(self.browser.filter_host.winfo_manager(), '')
        self.browser.manage_button.invoke()
        self.assert_panel_before(self.browser.content)
        self.assertIn({'refresh': True}, self.refreshes)
        self.background_refresh('Local package status updated')
        self.assert_panel_before(self.browser.content)

    def test_gallery_overview_and_repeated_panel_toggles(self):
        self.browser._open_gallery()
        self.browser.manage_button.invoke()
        self.assert_panel_before(self.browser.filter_host)
        for turn in range(3):
            self.browser._show_overview()
            self.assertEqual(self.browser.filter_host.winfo_manager(), '')
            self.background_refresh(f'Overview status {turn}')
            self.assert_panel_before(self.browser.content)
            self.browser.manage_button.invoke()
            self.assertEqual(self.browser.managed_surface.winfo_manager(), '')
            self.browser.manage_button.invoke()
            self.assert_panel_before(self.browser.content)
            self.browser._open_gallery()
            self.background_refresh(f'Gallery status {turn}')
            self.assert_panel_before(self.browser.filter_host)

    def test_window_destroy_and_reopen_with_late_status(self):
        self.browser.manage_button.invoke()
        self.assert_panel_before(self.browser.content)
        old_browser = self.browser
        self.window.destroy()
        self.assertTrue(old_browser._closed)
        self.assertFalse(old_browser._jobs)
        self.state['message'] = 'Status arrived after close'
        old_browser.sync_managed_packages()
        self.root.update()
        self.open_browser()
        self.assertEqual(self.browser.managed_surface.winfo_manager(), '')
        self.browser.manage_button.invoke()
        self.assert_panel_before(self.browser.content)
        self.background_refresh('Reopened status')


if __name__ == '__main__':
    unittest.main()
