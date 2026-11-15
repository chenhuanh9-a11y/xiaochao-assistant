"""Synthetic geometry and real observation/residual consumers, without game IO."""
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import season_optional_room as optional
from client_live_runtime import _AlignmentHost
from nightmare_candidate import selected_route_current_frame_residual, visible_map_component
from single_map_structure import current_geometry


def room(kind='round'):
    """Hand-rasterized terminal room attached to an ordinary rectangular body."""
    body = np.zeros((450, 960), np.uint8)
    body[100:321, 90:421] = 1
    if kind == 'round':
        body[200:224, 420:451] = 1
        points = [(450,194), (458,180), (475,162), (498,157), (522,161),
                  (540,172), (550,187), (548,200), (544,212), (548,224),
                  (550,237), (540,252), (522,262), (498,267), (475,262),
                  (458,244), (450,230)]
    else:
        body[200:224, 420:451] = 1
        points = [(450,198), (471,181), (496,176), (514,194), (540,170),
                  (572,172), (610,190), (610,230), (586,247), (554,250),
                  (533,244), (514,227), (482,239), (450,223)]
    cv2.fillPoly(body, [np.asarray(points, np.int32)], 1)
    return body


def frame(mask):
    result = np.full((*mask.shape, 3), 25, np.uint8)
    result[mask != 0] = (95, 105, 112)
    return result


class ShapeTests(unittest.TestCase):
    def unchanged(self, mask):
        recovery = mask.copy()
        result = optional.core(mask, recovery)
        self.assertIs(result.query, mask)
        self.assertIs(result.recovery, recovery)
        self.assertEqual(result.removed_pixels, 0)

    def test_both_shapes_scales_and_four_directions_preserve_ordinary_pixels_and_recovery(self):
        for kind in ('double', 'round'):
            original = room(kind)
            for scale in (.75, 1., 1.25):
                scaled = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
                for turn in range(4):
                    with self.subTest(kind=kind, scale=scale, turn=turn):
                        query = np.rot90(scaled, turn).copy()
                        before = query.copy()
                        result = optional.core(query, query.copy())
                        self.assertGreater(result.removed_pixels, 0)
                        self.assertGreaterEqual(np.count_nonzero(result.query), np.count_nonzero(query) * (.75 if kind == 'round' else .8))
                        np.testing.assert_array_equal(query, before)
                        np.testing.assert_array_equal(result.query, result.recovery)
                        restored = np.rot90(result.query, -turn)
                        # Same row as the appendage, safely inside the parent.
                        np.testing.assert_array_equal(restored[int(110*scale):int(310*scale), int(100*scale):int(410*scale)],
                                                      scaled[int(110*scale):int(310*scale), int(100*scale):int(410*scale)])

    def test_ordinary_rectangle_octagon_and_round_head_without_cleft_are_intact(self):
        for kind in ('rectangle', 'octagon', 'oval'):
            query = room()
            query[150:275, 445:560] = 0
            if kind == 'rectangle':
                query[157:268, 450:551] = 1
            elif kind == 'octagon':
                cv2.fillPoly(query, [np.array([(450,185), (475,157), (525,157), (550,185),
                                              (550,240), (525,267), (475,267), (450,240)])], 1)
            else:
                cv2.ellipse(query, (500,212), (50,55), 0, 0, 360, 1, -1)
            with self.subTest(kind=kind):
                self.unchanged(query)

    def test_incomplete_clipped_disconnected_sparse_and_multiple_hypotheses_are_intact(self):
        for kind in ('round', 'double'):
            query = room(kind)
            incomplete = query.copy()
            incomplete[160:260, 505:630] = 0
            disconnected = query.copy()
            disconnected[:, 435:450] = 0
            sparse = query.copy()
            sparse[:, :420] = 0
            cases = (incomplete, query[:, :510].copy(), sparse,
                     np.concatenate((query, query), axis=1),
                     np.concatenate((query, room('double' if kind == 'round' else 'round')), axis=1))
            # Android's double-lobe core receives an already connected body;
            # its caller owns that guard. The round core also walks to a parent.
            if kind == 'round':
                cases += (disconnected,)
            for index, negative in enumerate(cases):
                with self.subTest(kind=kind, negative=index):
                    self.unchanged(negative)

    def test_invalid_empty_and_cancelled_inputs_stay_unchanged(self):
        for query in (np.zeros((0, 0), np.uint8), np.zeros((3,), np.uint8), room()):
            self.assertIs(optional.core(query, cancelled=lambda: True).query, query)
        query = room()
        self.assertIs(optional.core(query, np.zeros((2,2), np.uint8)).query, query)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.mask = room()
        self.frame = frame(self.mask)
        self.bounds = (0, 0, self.frame.shape[1], self.frame.shape[0])
        self.visible = visible_map_component(self.frame, observation_bounds=self.bounds)

    def test_disconnected_appendages_are_not_excluded_from_either_mode_observation(self):
        for kind in ('double', 'round'):
            mask = room(kind)
            mask[:, 425:440] = 0
            pixels = frame(mask)
            with optional.observation_scope(pixels, enabled=True):
                self.assertIs(optional.filter_query(pixels, mask, self.bounds), mask)

    def test_capture_scaling_and_rotation_reproject_only_the_appendage(self):
        for scale in (.75, 1.5, 2.):
            scaled = cv2.resize(self.mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            for turn in range(4):
                mask = np.rot90(scaled, turn).copy()
                pixels = frame(mask)
                bounds = (0, 0, mask.shape[1], mask.shape[0])
                with self.subTest(scale=scale, turn=turn), optional.observation_scope(pixels, enabled=True):
                    filtered = optional.filter_query(pixels, mask, bounds)
                    self.assertIsNot(filtered, mask)
                    unturned = np.rot90(filtered, -turn)
                    np.testing.assert_array_equal(unturned[int(110*scale):int(310*scale), int(100*scale):int(410*scale)],
                                                  scaled[int(110*scale):int(310*scale), int(100*scale):int(410*scale)])

    def test_geometry_and_original_final_residual_share_exclusion_but_wrong_pose_still_fails(self):
        reference = np.zeros_like(self.mask)
        reference[100:321, 90:421] = 1
        source = SimpleNamespace(validation_mask=reference)
        before = self.frame.copy()
        with optional.observation_scope(self.frame, enabled=True) as observation, \
                patch.object(optional, 'core', wraps=optional.core) as detector:
            geometry = current_geometry(self.frame, visible_override=self.visible)
            self.assertGreater(observation.removed_pixels, 0)
            residual = selected_route_current_frame_residual(self.frame, None, np.eye(3),
                visible_override=self.visible, shared_reference=source)
            self.assertTrue(residual['verified'], residual)
            self.assertEqual(residual['withinSixPixels'], 1.)
            wrong = np.eye(3)
            wrong[0,2] = 40
            rejected = selected_route_current_frame_residual(self.frame, None, wrong,
                visible_override=self.visible, shared_reference=source)
            self.assertFalse(rejected['verified'], rejected)
            self.assertEqual(detector.call_count, 1)
            self.assertFalse(geometry['query'][180:245, 470:540].any())
        np.testing.assert_array_equal(self.frame, before)
        raw = current_geometry(self.frame, visible_override=self.visible)
        self.assertTrue(raw['query'][180:245, 470:540].any())

    def test_scopes_bind_exact_frame_restore_on_error_and_disable_for_old_season(self):
        with self.assertRaisesRegex(RuntimeError, 'stop'):
            with optional.observation_scope(self.frame, enabled=True):
                self.assertIs(optional.filter_query(self.frame.copy(), self.mask, self.bounds), self.mask)
                with optional.observation_scope(self.frame, enabled=False):
                    self.assertIs(optional.filter_query(self.frame, self.mask, self.bounds), self.mask)
                self.assertIsNot(optional.filter_query(self.frame, self.mask, self.bounds), self.mask)
                raise RuntimeError('stop')
        self.assertIs(optional.filter_query(self.frame, self.mask, self.bounds), self.mask)
        with optional.observation_scope(self.frame, enabled=True, cancelled=lambda: True):
            self.assertIs(optional.filter_query(self.frame, self.mask, self.bounds), self.mask)

    def test_both_mode_hosts_apply_same_core_and_do_not_turn_failure_into_success(self):
        for mode in ('difficult', 'nightmare'):
            for season in ('s0', 's1'):
                host = _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode=mode),
                    qt_adapters=object(), qt_season=season))
                def fit(*args, **kwargs):
                    geometry = current_geometry(self.frame, visible_override=self.visible)
                    self.assertEqual(bool(geometry['query'][180:245, 470:540].any()), season == 's0')
                    return dict(success=False, reason='unchanged-strict-proof-rejection')
                with patch.object(host, '_fit_owned', side_effect=fit):
                    result = host.fit(self.frame, 'map', '-3', lambda: False)
                self.assertFalse(result['success'])
                self.assertEqual(result['reason'], 'unchanged-strict-proof-rejection')
                self.assertEqual(result.get('optionalRoomPixels', 0) > 0, season == 's1')

    def test_one_scan_reuses_exclusion_new_scan_and_reopen_recompute_it(self):
        host = _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode='nightmare'),
            qt_adapters=object(), qt_season='s1'))
        def fit(*args, **kwargs):
            self.assertFalse(kwargs['_context']['query'][180:245, 470:540].any())
            return dict(success=True)
        with patch.object(host, '_fit_observation', side_effect=fit), \
                patch.object(optional, 'core', wraps=optional.core) as detector:
            prepared = host.prepare_structure_scan(self.frame, self.bounds)
            for map_id in ('a', 'b'):
                prepared(self.frame, map_id, '-3', lambda: False, observation_bounds=self.bounds)
            self.assertEqual(detector.call_count, 1)
            fresh = host.prepare_structure_scan(self.frame, self.bounds)
            fresh(self.frame, 'a', '-3', lambda: False, observation_bounds=self.bounds)
            self.assertEqual(detector.call_count, 2)
            host.fit(self.frame, 'a', '-3', lambda: False, observation_bounds=self.bounds)
            self.assertEqual(detector.call_count, 3)
            with self.assertRaisesRegex(ValueError, 'context-mismatch'):
                prepared(self.frame.copy(), 'a', '-3', lambda: False, observation_bounds=self.bounds)

    def test_original_provider_background_and_fixed_coordinate_consumers_are_scoped(self):
        from client_original_providers import attach_original_s1_scanner
        from s1_structure_selection import StructureCatalog
        for mode in ('difficult', 'nightmare'):
            engine = SimpleNamespace(pack=SimpleNamespace(mode=mode), qt_adapters=object(), qt_season='s1')
            bundle = SimpleNamespace(reader=SimpleNamespace(root=Path('synthetic-original')))
            catalog = StructureCatalog('s1', mode, 'synthetic', ('map',), ('-1', '-2', '-3', '-4'))
            def prepare(pixels, *args, **kwargs):
                self.assertIs(pixels, self.frame)
                self.assertIsNot(optional.filter_query(pixels, self.mask, self.bounds), self.mask)
                return dict(coordinateVerified=True)
            def fixed(pixels, *args, **kwargs):
                self.assertIsNot(pixels, self.frame)  # Scanner owns fresh immutable bytes.
                self.assertIsNot(optional.filter_query(pixels, self.mask, self.bounds), self.mask)
                return dict(success=False, reason='unchanged-strict-proof-rejection')
            with patch.object(StructureCatalog, 'from_engine', return_value=catalog), \
                    patch('s1_private_experiment.PrivateCoordinateObserver') as observer:
                observer.return_value.prepare.side_effect = prepare
                observer.return_value.fixed_fit.side_effect = fixed
                attach_original_s1_scanner(engine, None, bundle)
                report = engine.qt_s1_private_coordinate_prepare(self.frame, ('map',), '-3', cancelled=lambda: False)
                result = engine.qt_s1_structure_scan(self.frame, floor='-3', private_coordinate_report=report)
                self.assertEqual(result['supportedMapIds'], [])
                self.assertEqual(result['outcomes'][0]['reason'], 'unchanged-strict-proof-rejection')
                observer.return_value.fixed_fit.assert_called_once()
                observer.return_value.prepare.side_effect = RuntimeError('stop')
                with self.assertRaisesRegex(RuntimeError, 'stop'):
                    engine.qt_s1_private_coordinate_prepare(self.frame, ('map',), '-3', cancelled=lambda: False)
            self.assertIs(optional.filter_query(self.frame, self.mask, self.bounds), self.mask)


if __name__ == '__main__':
    unittest.main()
