"""Retained pixel and real off-screen HWND regression checks (no game input)."""
import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from idvb0059_display import RetainedDisplayCache
from transparent_display_resources import unpremultiply_display_bgra


def source_pixels():
    pixels = np.zeros((40, 60, 4), np.uint8)
    pixels[3:36, 4:56] = [80, 160, 240, 160]
    pixels[9:24, 14:36] = [220, 35, 70, 255]
    return pixels


def transform(s=1.0, x=0.0, y=0.0):
    return np.array([[s, 0, x], [0, s, y], [0, 0, 1]], np.float64)


def full_warp(pixels, matrix, frame_shape):
    premultiplied = pixels.astype(np.float32)
    premultiplied[:, :, 3] /= 255.0
    premultiplied[:, :, :3] *= premultiplied[:, :, 3:4]
    height, width = frame_shape[:2]
    warped = cv2.warpPerspective(premultiplied, matrix, (width, height),
                                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return unpremultiply_display_bgra(warped)


class RetainedRasterTests(unittest.TestCase):
    def test_integer_translation_reuses_raster_and_matches_old_full_canvas(self):
        pixels, cache = source_pixels(), RetainedDisplayCache()
        output, first, _ = cache.build(pixels, transform(1.375, -18, -13), (90, 120), ('map', '1'))
        np.testing.assert_array_equal(output, full_warp(pixels, transform(1.375, -18, -13), (90, 120)))
        with patch('idvb0059_display.cv2.warpPerspective', side_effect=AssertionError('rewarp')):
            moved, second, diagnostics = cache.build(pixels, transform(1.375, 22, 26), (90, 120), ('map', '1'))
        self.assertIs(first.token, second.token)
        self.assertIs(first.pixels, second.pixels)
        self.assertTrue(diagnostics['renderedPixelsReused'])
        np.testing.assert_array_equal(moved, full_warp(pixels, transform(1.375, 22, 26), (90, 120)))

    def test_fractional_translation_rounding_is_bounded_and_does_not_change_solver_matrix(self):
        pixels, cache = source_pixels(), RetainedDisplayCache()
        matrix = transform(1.2, -12.49, 20.5)
        original = matrix.copy()
        output, packet, meta = cache.build(pixels, matrix, (100, 150), 'floor-1')
        rounded = matrix.copy()
        rounded[:2, 2] = np.rint(rounded[:2, 2])
        np.testing.assert_array_equal(matrix, original)
        np.testing.assert_array_equal(output, full_warp(pixels, rounded, (100, 150)))
        self.assertTrue(all(abs(v) <= .5 for v in meta['presentationRoundingError']))
        self.assertIsNotNone(packet)

    def test_rotation_retains_complete_canvas_at_all_four_edges(self):
        pixels, cache = source_pixels(), RetainedDisplayCache()
        angle = np.deg2rad(31)
        matrix = np.array([[np.cos(angle), -np.sin(angle), 4],
                           [np.sin(angle), np.cos(angle), -12], [0, 0, 1]], np.float64)
        tokens = []
        for x, y in [(-40, -30), (80, -20), (95, 80), (-25, 80), (30, 20)]:
            matrix[:2, 2] = x, y
            actual, packet, _ = cache.build(pixels, matrix, (100, 130), 'rotated')
            expected = full_warp(pixels, matrix, (100, 130))
            np.testing.assert_array_equal(actual, expected)
            tokens.append(packet.token)
        self.assertTrue(all(token is tokens[0] for token in tokens))

    def test_source_mutation_owner_scale_and_floor_invalidate_independently(self):
        pixels, cache = source_pixels(), RetainedDisplayCache()
        _, first, _ = cache.build(pixels, transform(), (100, 150), 'floor-1')
        _, other, _ = cache.build(pixels, transform(), (100, 150), 'floor-2')
        self.assertIsNot(first.token, other.token)
        pixels[12, 15, 0] ^= 255
        _, changed, _ = cache.build(pixels, transform(), (100, 150), 'floor-1')
        self.assertIsNot(first.token, changed.token)
        _, resized, _ = cache.build(pixels, transform(1.01), (100, 150), 'floor-1')
        self.assertIsNot(changed.token, resized.token)
        _, new_owner, _ = cache.build(pixels.copy(), transform(1.01), (100, 150), 'floor-1')
        self.assertIsNot(resized.token, new_owner.token)
        with self.assertRaises(ValueError):
            first.pixels.setflags(write=True)

    def test_lru_bounded_and_fallback_discards_old_identity(self):
        cache, pixels = RetainedDisplayCache(max_entries=2, max_bytes=100000), source_pixels()
        _, first, _ = cache.build(pixels, transform(), (100, 150), '1')
        for key in ('2', '3'):
            cache.build(pixels, transform(), (100, 150), key)
        self.assertLessEqual(len(cache._entries), 2)
        self.assertLessEqual(cache.retained_bytes, 100000)
        _, rebuilt, _ = cache.build(pixels, transform(), (100, 150), '1')
        self.assertIsNot(first.token, rebuilt.token)
        perspective = transform()
        perspective[2, 0] = .01
        self.assertIsNone(cache.build(pixels, perspective, (100, 150), '1')[1])
        self.assertNotIn('1', cache._entries)
        self.assertIsNone(cache.build(pixels, transform(100000), (100, 150), '3')[1])
        self.assertNotIn('3', cache._entries)
        cache.clear()
        self.assertEqual(cache.retained_bytes, 0)


@unittest.skipUnless(sys.platform == 'win32', 'requires real Windows HWND APIs')
class NativeRetainedTests(unittest.TestCase):
    def setUp(self):
        import identityv_live_mvp as native
        self.native = native
        self.overlay = native.LayeredOverlay()
        self.cache, self.pixels = RetainedDisplayCache(), source_pixels()
        # Far outside the entire virtual desktop; never covers or activates a
        # user's app, and no input or capture APIs are used by these tests.
        self.origin = (-25000, -25000)
        self.upload = patch.object(native.user32, 'UpdateLayeredWindow', wraps=native.user32.UpdateLayeredWindow)
        self.upload_spy = self.upload.start()
        self.addCleanup(self.upload.stop)
        self.addCleanup(self.overlay.close)

    def packet(self, x=10, y=10, scale=1.):
        return self.cache.build(self.pixels, transform(scale, x, y), (70, 90), 'floor')[1]

    def native_clip(self):
        native = self.native
        native.user32.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        native.user32.GetWindowRgn.restype = ctypes.c_int
        native.gdi32.GetRgnBox.argtypes = [ctypes.c_void_p, ctypes.POINTER(native.RECT)]
        native.gdi32.GetRgnBox.restype = ctypes.c_int
        region = native.gdi32.CreateRectRgn(0, 0, 0, 0)
        try:
            kind = native.user32.GetWindowRgn(self.overlay.hwnd, region)
            rect = native.RECT()
            native.gdi32.GetRgnBox(region, ctypes.byref(rect))
            return kind, (rect.left, rect.top, rect.right, rect.bottom)
        finally:
            native.gdi32.DeleteObject(region)

    def test_real_native_upload_move_hide_restore_and_clipping(self):
        packet = self.packet()
        self.assertTrue(self.overlay.show_retained(packet, self.origin))
        self.assertEqual(self.upload_spy.call_count, 1)
        for x, y in [(-20, -15), (70, -10), (70, 50), (-20, 50), (10, 10)]:
            packet = self.packet(x, y)
            self.assertTrue(self.overlay.show_retained(packet, self.origin))
            self.assertEqual(self.native_clip()[1], packet.clip_bounds)
            self.assertEqual(self.overlay.client_offset, packet.client_offset)
        self.assertEqual(self.upload_spy.call_count, 1)
        self.overlay.hide()
        self.assertFalse(self.overlay.visible)
        self.assertTrue(self.overlay.restore((-24990, -24980)))
        self.assertEqual(self.upload_spy.call_count, 1)
        self.assertTrue(self.overlay.move_by(-25, -20))
        self.assertEqual(self.native_clip()[1], self.overlay._retained_packet.clip_bounds)
        offset = self.overlay.client_offset
        self.overlay.hide()
        self.assertTrue(self.overlay.restore(self.origin))
        self.assertEqual(self.overlay.screen_position,
                         (self.origin[0] + offset[0], self.origin[1] + offset[1]))
        self.assertEqual(self.upload_spy.call_count, 1)

    def test_real_native_source_scale_opacity_and_legacy_upload(self):
        packet = self.packet()
        self.assertTrue(self.overlay.show_retained(packet, self.origin))
        self.pixels[12, 20, 1] ^= 255
        self.assertTrue(self.overlay.show_retained(self.packet(), self.origin))
        self.assertTrue(self.overlay.show_retained(self.packet(scale=1.2), self.origin))
        self.assertTrue(self.overlay.show_retained(self.packet(scale=1.2), self.origin, .5))
        self.assertEqual(self.upload_spy.call_count, 4)
        canvas = np.zeros((70, 90, 4), np.uint8)
        canvas[20:30, 30:40] = [20, 80, 150, 255]
        self.assertTrue(self.overlay.show_bgra(canvas, self.origin))
        self.assertIsNone(self.overlay._retained_token)
        self.assertEqual(self.native_clip()[0], 0)
        self.assertTrue(self.overlay.show_retained(packet, self.origin))
        self.assertEqual(self.upload_spy.call_count, 6)

    def test_failed_guard_and_close_edge_do_not_show_or_upload_stale_surface(self):
        packet = self.packet()
        self.overlay.visibility_guard = lambda: False
        self.assertFalse(self.overlay.show_retained(packet, self.origin))
        self.assertEqual(self.upload_spy.call_count, 0)
        self.overlay.visibility_guard = lambda: True
        self.assertTrue(self.overlay.show_retained(packet, self.origin))
        original = self.native.user32.SetWindowPos
        def close_during_move(*args):
            result = original(*args)
            self.overlay.visibility_guard = lambda: False
            return result
        with patch.object(self.native.user32, 'SetWindowPos', side_effect=close_during_move):
            self.assertFalse(self.overlay.show_retained(self.packet(-5, -5), self.origin))
        self.assertFalse(self.overlay.visible)
        self.assertEqual(self.upload_spy.call_count, 1)
        self.assertFalse(self.overlay.restore(self.origin))
        self.overlay.close()
        self.overlay.visibility_guard = lambda: True
        self.assertFalse(self.overlay.show_retained(packet, self.origin))

    def test_fully_outside_hides_without_losing_native_bitmap(self):
        self.assertTrue(self.overlay.show_retained(self.packet(), self.origin))
        self.assertFalse(self.overlay.show_retained(self.packet(-200, -200), self.origin))
        self.assertFalse(self.overlay.visible)
        self.assertEqual(self.native_clip()[1], (0, 0, 0, 0))
        self.assertTrue(self.overlay.move_by(210, 210))
        self.assertEqual(self.overlay.client_offset, self.packet().client_offset)
        self.assertEqual(self.upload_spy.call_count, 1)

    def test_late_close_after_upload_and_show_window_stays_hidden(self):
        packet = self.packet()
        native_upload = self.overlay._upload_retained_pixels
        def close_during_upload(*args):
            result = native_upload(*args)
            self.overlay.visibility_guard = lambda: False
            return result
        with patch.object(self.overlay, '_upload_retained_pixels', side_effect=close_during_upload):
            self.assertFalse(self.overlay.show_retained(packet, self.origin))
        self.assertFalse(self.overlay.visible)
        self.overlay.visibility_guard = lambda: True
        native_show = self.native.user32.ShowWindow
        def close_during_show(hwnd, command):
            result = native_show(hwnd, command)
            if command == self.overlay.SW_SHOWNOACTIVATE:
                self.overlay.visibility_guard = lambda: False
            return result
        with patch.object(self.native.user32, 'ShowWindow', side_effect=close_during_show):
            self.assertFalse(self.overlay.restore(self.origin))
        self.assertFalse(self.overlay.visible)
        self.assertEqual(self.upload_spy.call_count, 1)

    def test_explicit_hide_cancels_even_fully_clipped_drag(self):
        self.assertTrue(self.overlay.show_retained(self.packet(), self.origin))
        self.assertTrue(self.overlay.move_by(-210, -210))
        self.assertFalse(self.overlay.visible)
        self.overlay.hide()
        self.assertFalse(self.overlay.move_by(210, 210))
        self.assertFalse(self.overlay.visible)


if __name__ == '__main__':
    unittest.main()
