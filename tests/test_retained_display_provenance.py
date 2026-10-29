"""Production retained consumer + drag methods; OS sinks simulated, no HWND/input."""
import ast
import ctypes
import gc
import unittest
import weakref
import os
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'app'))
from base_display_integration import BaseDisplayIntegrationMixin, display_source_guard
import idvb0059_runtime as runtime
import identityv_live_mvp as native

# Compile exact production bodies, retaining the source-provenance guard.
# Outer telemetry/presentation-transaction decorators are outside this probe.
tree = ast.parse((SOURCE / 'app/identityv_daily_assistant.py').read_text(encoding='utf-8'))
wanted = {'_build_display_overlay', '_build_display_overlay_unchecked', '_alignment_v2_show_overlay'}
methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in wanted]
for method in methods:
    method.decorator_list = ([ast.Name(id='display_source_guard', ctx=ast.Load())]
                             if method.name == '_alignment_v2_show_overlay' else [])
namespace = dict(np=np, cv2=cv2, os=os, time=time, Path=Path,
                 display_source_guard=display_source_guard,
                 display_environment_signature=lambda environment: ('controlled',))
for method in methods:
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 str(SOURCE / 'app/identityv_daily_assistant.py'), 'exec'), namespace)


class Subject(BaseDisplayIntegrationMixin):
    pass
for name in wanted:
    setattr(Subject, name, namespace[name])


class Sink:
    """In-memory DIB allocation; never calls any real OS function."""
    def __init__(self):
        self.buffers = []
        self.uploads = 0

    def CreateDIBSection(self, dc, info, usage, bits, section, offset):
        header = ctypes.cast(info, ctypes.POINTER(native.BITMAPINFO)).contents.bmiHeader
        buf = ctypes.create_string_buffer(header.biWidth * abs(header.biHeight) * 4)
        self.buffers.append(buf)
        ctypes.cast(bits, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(buf)
        return 1

    def UpdateLayeredWindow(self, *args):
        self.uploads += 1
        return 1

    def __getattr__(self, name):
        return lambda *args: 1


def surface():
    s = native.LayeredOverlay.__new__(native.LayeredOverlay)
    s.hwnd = 123  # Not a real handle; every native API is replaced below.
    s._visibility_lock = threading.RLock()
    s.visibility_guard = None
    for key in ('screen_position', 'window_size', 'client_offset', '_last_presented_bgra',
                '_last_presented_position', '_last_presented_opacity', '_retained_packet',
                '_retained_token', '_retained_client_origin', '_surface_clip'):
        setattr(s, key, None)
    s.visible = s._retained_clipped_out = False
    s.present_count = s.retained_move_count = 0
    s._record_native_call = lambda *args: None
    s._record_diagnostic = lambda *args, **kwargs: None
    return s


pixels = np.zeros((40, 60, 4), np.uint8)
pixels[:] = [80, 160, 240, 160]
pixels[9:24, 14:36] = [220, 35, 70, 255]
matrix = np.eye(3)
matrix[:2, 2] = -20, -10
frame_shape = (70, 90)
origin = (100, 200)


def subject():
    s = Subject()
    s.game_mode = 'nightmare'
    s.current_floor = '1'
    s.route_profile_id = s.preferred_route_profile_id = 'shared-structure'
    s.active_map_cycle_id = s.current_frame_generation = 1
    s.surface_visibility_generation = s._base_display_epoch = s._display_round_epoch = 1
    s.gate = SimpleNamespace(confirmed_id='fixture')
    s._shared_structure_mode_enabled = lambda: True
    s._shared_display_layer = lambda *args: dict(sourcePath=Path('fixture.png'), routeSource=pixels,
        routeProfileId='shared-structure', displayKind='structure', displayResourceKey='fixture-v1')
    s._route_profile_validation_spec = lambda *args: dict(profileBridgeVerified=True)
    s._log = lambda *args, **kwargs: None
    s.events = queue.Queue()
    s._native_similarity_resources_ready = lambda: True
    s._current_frame_matches_display_environment = lambda: True
    s._display_authorization_is_current = lambda value: value == 7
    s._surface_visibility_is_current = lambda value: value == s.surface_visibility_generation
    s.map_is_open = True
    s.SHEET_OPACITY = s.sheet_opacity = .75
    s._ui_action_first_content = s._queue_ui_action_settle = lambda *args, **kwargs: None
    s.aligned_overlay = surface()
    return s


def canvas(s):
    """Reconstruct client pixels from the exact submitted native surface buffer."""
    out = np.zeros((*frame_shape, 4), np.uint8)
    p = s._last_presented_bgra
    mask = np.ones(p.shape[:2], bool)
    if s._surface_clip is not None:
        mask[:] = False
        x0, y0, x1, y1 = s._surface_clip
        mask[y0:y1, x0:x1] = True
    x, y = s.screen_position[0]-origin[0], s.screen_position[1]-origin[1]
    for yy, xx in zip(*np.nonzero(mask)):
        if 0 <= y+yy < out.shape[0] and 0 <= x+xx < out.shape[1]:
            out[y+yy, x+xx] = p[yy, xx]
    return out


def run(use_wrapped):
    s = subject()
    captured = {}
    real_remember = s._remember_display_output
    def remember(image, metadata):
        captured['raw'] = image
        return real_remember(image, metadata)
    s._remember_display_output = remember
    builder = s._build_display_overlay if use_wrapped else s._build_display_overlay_unchecked
    output, metadata = builder(pixels, matrix, frame_shape,
        minimum_coverage=0, minimum_visible_pixels=1)
    assert metadata['success'] and metadata['idvb0059Display']['retained']
    image = output
    # Fixture bypasses only native surface selection/allocation, never packet lookup.
    with patch.object(runtime, 'select_surface', side_effect=lambda owner, packet: owner.aligned_overlay):
        assert s._alignment_v2_show_overlay(image, origin, authorization_id=7)
    before = canvas(s.aligned_overlay)
    retained = s.aligned_overlay._retained_packet is not None
    assert s.aligned_overlay.move_by(25, 15)
    after = canvas(s.aligned_overlay)
    # Stale resource-generation and authorization gates still reject presentation.
    s._base_display_epoch += 1
    rejected_resource = (not s._alignment_v2_show_overlay(output, origin, authorization_id=7)) if use_wrapped else None
    rejected_auth = not s._alignment_v2_show_overlay(image, origin, authorization_id=8)
    return dict(wrapper=use_wrapped, retained_lookup=runtime.packet_for(s, image) is not None,
        retained_consumer=retained, initial_alpha_pixels=int(np.count_nonzero(before[:,:,3])),
        dragged_alpha_pixels=int(np.count_nonzero(after[:,:,3])),
        native_buffer_shape=list(s.aligned_overlay._last_presented_bgra.shape),
        clip_bounds=s.aligned_overlay._surface_clip,
        resource_guard_rejected=rejected_resource, authorization_rejected=rejected_auth), before, after


class RetainedProvenanceTests(unittest.TestCase):
    def test_real_build_consumer_and_drag_match_full_retained_pixels(self):
        sink = Sink()
        with patch.object(native, 'user32', sink), patch.object(native, 'gdi32', sink):
            wrapped, initial, dragged = run(True)
            control, raw_initial, raw_dragged = run(False)
        self.assertTrue(wrapped['retained_lookup'])
        self.assertTrue(wrapped['retained_consumer'])
        self.assertTrue(control['retained_consumer'])
        np.testing.assert_array_equal(initial, raw_initial)
        np.testing.assert_array_equal(dragged, raw_dragged)
        self.assertEqual(wrapped['initial_alpha_pixels'], 1200)
        self.assertEqual(wrapped['dragged_alpha_pixels'], 2400)
        self.assertEqual(wrapped['clip_bounds'], (0, 0, 64, 44))
        self.assertTrue(wrapped['resource_guard_rejected'])
        self.assertTrue(wrapped['authorization_rejected'])
        self.assertEqual(sink.uploads, 2)  # One upload each, no upload on drag.

    def build_raw(self, s):
        result, metadata = s._build_display_overlay_unchecked(pixels, matrix, frame_shape,
            minimum_coverage=0, minimum_visible_pixels=1)
        self.assertTrue(metadata['success'])
        return result, metadata

    def test_only_exact_full_buffer_view_can_receive_packet(self):
        s = subject()
        raw, metadata = self.build_raw(s)
        packet = runtime.packet_for(s, raw)
        self.assertIsNotNone(packet)
        # Includes identical-shape copy, changed origin, shape, strides and dtype.
        for invalid in (raw.copy(), raw[:, 1:], raw.reshape(-1, 4),
                        raw[::-1], raw.transpose(1, 0, 2), raw.view(np.int8)):
            with self.subTest(shape=invalid.shape, strides=invalid.strides, dtype=invalid.dtype):
                self.assertFalse(runtime.transfer_display_packet(s, raw, invalid))
                self.assertIsNone(runtime.packet_for(s, invalid))
                self.assertIs(runtime.packet_for(s, raw), packet)
        wrapped = s._remember_display_output(raw, metadata)
        self.assertIs(runtime.packet_for(s, wrapped), packet)
        self.assertIsNone(runtime.packet_for(s, raw))
        self.assertTrue(np.shares_memory(raw, wrapped))
        self.assertEqual(raw.ctypes.data, wrapped.ctypes.data)

    def test_copies_and_slices_preserve_descriptor_but_never_packet(self):
        s = subject()
        raw, metadata = self.build_raw(s)
        wrapped = s._remember_display_output(raw, metadata)
        for derived in (wrapped.copy(), wrapped[:, 1:], wrapped.view()):
            self.assertEqual(s._display_output_metadata(derived), s._display_output_metadata(wrapped))
            self.assertIsNone(runtime.packet_for(s, derived))
        self.assertIsNotNone(runtime.packet_for(s, wrapped))

    def test_wrong_weak_owner_cannot_transfer_even_with_matching_registry_key(self):
        s = subject()
        raw, metadata = self.build_raw(s)
        packet = runtime.packet_for(s, raw)
        other = raw.copy()
        s._idvb0059_packets[id(raw)] = (weakref.ref(other), packet)
        self.assertFalse(runtime.transfer_display_packet(s, raw, raw.view()))
        self.assertIsNone(runtime.packet_for(s, raw))

    def test_ownership_remains_weak_and_registry_stays_bounded(self):
        s = subject()
        outputs = []
        for _ in range(12):
            raw, metadata = self.build_raw(s)
            outputs.append(s._remember_display_output(raw, metadata))
            self.assertLessEqual(len(s._idvb0059_packets), 8)
        newest = weakref.ref(outputs[-1])
        outputs.clear()
        gc.collect()
        self.assertIsNone(newest())
        self.build_raw(s)  # Existing producer reclaims dead weak owners.
        self.assertEqual(len(s._idvb0059_packets), 1)

    def test_expired_build_descriptor_is_rejected_before_wrapping(self):
        s = subject()
        layer = s._shared_display_layer()
        layer['displayContext'] = s._display_notice_context('fixture', '1')
        s._shared_display_layer = lambda *args: layer
        s._base_display_epoch += 1
        output, metadata = s._build_display_overlay(pixels, matrix, frame_shape,
            minimum_coverage=0, minimum_visible_pixels=1)
        self.assertIsNone(output)
        self.assertEqual(metadata['reason'], 'display-source-context-expired')


if __name__ == '__main__':
    unittest.main()

