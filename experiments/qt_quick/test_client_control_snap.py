"""Synthetic monitor geometry and own offscreen Qt window, no native input."""
import os
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from client_control_snap import ControlSnap, choose_area, clamp_position, release_position


class ControlSnapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def window(self, saved=None):
        window = QQuickWindow()
        window.setObjectName("clientControlsWindow")
        window.resize(352, 243)
        snap = ControlSnap(areas=lambda: [QRectF(0, 0, 1920, 1040)])
        snap.bindWindow(window, saved)
        self.addCleanup(snap.shutdown)
        self.addCleanup(window.deleteLater)
        return window, snap

    def test_fresh_defaults_to_available_top_right_with_margin(self):
        window, snap = self.window()
        self.assertEqual((window.x(), window.y()), (1552, 16))
        self.assertLessEqual(window.y() + window.height(), 1040)

    def test_valid_saved_position_is_preserved_including_screen_edge(self):
        for saved in ([24, 24], [0, 0], [640, 560]):
            window, snap = self.window(saved)
            self.assertEqual((window.x(), window.y()), tuple(saved))

    def test_missing_monitor_and_taskbar_bounds_are_clamped(self):
        area = choose_area([QRectF(-1280, 0, 1280, 984), QRectF(0, 0, 1920, 1040)],
                           (-1000, 50), (352, 243))
        self.assertEqual(area.x(), -1280)
        self.assertEqual(clamp_position((2200, 1200), (352, 243), QRectF(0, 0, 1920, 1040)), (1568, 797))
        self.assertEqual(clamp_position((-2500, -50), (352, 243), area), (-1280, 0))

    def test_release_snaps_only_near_edge_and_allows_free_drag_away(self):
        area = QRectF(0, 0, 1920, 1040)
        self.assertEqual(release_position((1563, 400), (352, 243), area), ((1552, 400), ("right", None)))
        self.assertEqual(release_position((600, 400), (352, 243), area), ((600, 400), (None, None)))
        window, snap = self.window()
        snap.beginDrag(); window.setPosition(600, 400); snap.releaseDrag()
        window.resize(64, 64); self.app.processEvents()
        self.assertEqual((window.x(), window.y()), (600, 400))

    def test_collapsed_expanded_anchor_keeps_right_edge(self):
        window, snap = self.window()
        window.resize(64, 64); self.app.processEvents()
        self.assertEqual((window.x(), window.y()), (1840, 16))
        window.resize(352, 540); self.app.processEvents()
        self.assertEqual((window.x(), window.y()), (1552, 16))

    def test_refuses_to_move_main_or_sheet_window(self):
        window = QQuickWindow(); window.setObjectName("qtClientMain")
        snap = ControlSnap(areas=lambda: [QRectF(0, 0, 1920, 1040)])
        with self.assertRaisesRegex(ValueError, "another surface"):
            snap.bindWindow(window)
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
