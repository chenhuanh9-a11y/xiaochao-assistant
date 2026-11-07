"""Shared retained pixel ownership/provenance; no legacy UI fixture or HWND."""
import gc
import unittest
import weakref
from pathlib import Path
import queue
import sys
from types import SimpleNamespace

import numpy as np

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'app'))
from base_display_integration import BaseDisplayIntegrationMixin
import idvb0059_runtime as runtime

# Exercise the shared renderer and provenance owner directly. Native presentation
# is covered by test_idvb0059_retained_display; Qt by test_client_pr76_display_boundaries.
class Subject(BaseDisplayIntegrationMixin):
    pass


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
    return s


class RetainedProvenanceTests(unittest.TestCase):

    def build_raw(self, s):
        result, metadata = runtime.build_display(s, pixels, matrix, frame_shape,
            ('nightmare', 'fixture', '1', 'shared-structure', 'fixture-v1'),
            minimum_coverage=0, minimum_visible_pixels=1)
        metadata.update(s._display_descriptor(s._shared_display_layer(), 'fixture', '1'))
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


if __name__ == '__main__':
    unittest.main()

