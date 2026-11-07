"""Deliberately synthetic S1 preparation adversaries, never source-image proof."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import s1_floor_observation as detector
from map_interface_observation import BACKDROP, MAP_REGIONS, detect_map_interface
from client_space_geometry import normalized_rect_to_client
from test_s1_floor_observation import mathematical_frame, draw_column


def read(frame, **kw):
    return detector.detect_s1_floor(frame, season=kw.pop('season', 's1'),
                                    mode=kw.pop('mode', 'nightmare'), **kw)


def bright_frame(selected=(-1,)):
    frame = mathematical_frame(selected)
    # Independent, explicitly constructed map UI shape fixture, NOT a game
    # asset and NOT generated from the production detector's template cache.
    frame[90:190,1790:1840] = 40
    frame[700:800,1810:1850] = 40
    cv2.line(frame,(1794,130),(1834,170),(180,)*3,10)
    cv2.line(frame,(1834,130),(1794,170),(180,)*3,10)
    for cy in (452,930):
        cv2.circle(frame,(1818,cy),27,(180,)*3,-1)
        cv2.line(frame,(1800,cy),(1836,cy),(30,)*3,7)
    cv2.line(frame,(1818,434),(1818,470),(30,)*3,7)
    frame[500:883,1811:1825] = 120
    # An invented large revealed structure, not a reconstructed playtest frame.
    frame[260:700, 500:1400] = 180
    return frame


def remove_region(frame, name):
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = normalized_rect_to_client((w, h), MAP_REGIONS[name][:4],
                                              policy='safe-viewport')
    frame[y0:y1, x0:x1] = 40


class S1PreparationRobustness(unittest.TestCase):
    def assert_rejected(self, frame):
        result = read(frame)
        self.assertIsNone(result['selectedFloor'], result)
        self.assertFalse(result['mapOpen'], result)
        self.assertFalse(result['automaticFloorEligible'], result)
        self.assertFalse(result['observedFloorValidated'], result)
        return result

    def test_large_bright_structure_requires_fallback_for_all_floors_and_modes(self):
        for floor in detector.ORDER:
            for mode in ('nightmare', 'difficulty2', 'difficult'):
                with self.subTest(floor=floor, mode=mode):
                    frame = bright_frame((floor,))
                    baseline = detect_map_interface(frame, floor_read={})
                    self.assertFalse(baseline['detected'])
                    self.assertEqual(baseline['reason'], 'map-canvas-texture-mismatch')
                    result = read(frame, mode=mode)
                    self.assertEqual(result['selectedFloor'], floor, result)
                    self.assertTrue(result['mapOpen'])
                    self.assertEqual(result['mapObservation']['detectionMode'], 's1-signed-controls-ui-geometry')
                    self.assertEqual(result['observedFloorValidated'], floor in detector.SOURCE_REPLAY_FLOORS)
                    self.assertEqual(result['validationScope'], 'source-image-replay' if floor in detector.SOURCE_REPLAY_FLOORS else 'synthetic-only')
                    # No global threshold changes or hidden S0 floor reads.
                    self.assertEqual(detect_map_interface(frame, floor_read={}), baseline)

    def test_floor_search_pixels_cannot_supply_independent_anchors(self):
        frame = mathematical_frame(anchors=False)
        frame[260:700,500:1400] = 180
        # Fake close/zoom-colored strips inside the *floor* search area,
        # plus one genuine independent bottom anchor. Legacy broad rectangles
        # count the strips; the S1 fallback must mask their entire source area.
        frame[180:320,1740:1765] = 150
        frame[400:650,1750:1765] = 150
        frame[1000:1050,1600:1700] = 150
        observation = detect_map_interface(frame, floor_read={})
        self.assertFalse(observation['detected'])
        for anchor in ('closeButton','zoomControl','bottomControls'):
            self.assertGreaterEqual(observation['ratios'][anchor], MAP_REGIONS[anchor][4])
        self.assertEqual(detector._observe_signed_column(frame,1920,1200).get('selectedFloor'), -1)
        self.assert_rejected(frame)

    def test_each_independent_anchor_is_required(self):
        for name in ('closeButton', 'zoomControl', 'bottomControls'):
            with self.subTest(anchor=name):
                frame = bright_frame()
                remove_region(frame, name)
                raw = detector._observe_signed_column(frame, frame.shape[1], frame.shape[0])
                self.assertEqual(raw.get('selectedFloor'), -1)
                self.assert_rejected(frame)

    def test_broad_bright_background_cannot_be_rescued_by_controls(self):
        frame = bright_frame()
        frame[168:960, 480:1536] = 180
        raw = detector._observe_signed_column(frame, frame.shape[1], frame.shape[0])
        self.assertEqual(raw.get('selectedFloor'), -1)
        self.assertLess(detect_map_interface(frame, floor_read={})['backdropRatio'], .45)
        self.assert_rejected(frame)

    def test_fallback_does_not_reuse_previous_frame_floor(self):
        self.assertEqual(read(bright_frame((-4,)))['selectedFloor'], -4)
        frame = bright_frame((-4,))
        frame[200:600, 1630:1720] = 40
        self.assert_rejected(frame)

    def test_missing_sign_or_row_never_promoted(self):
        for floor in detector.ORDER:
            frame = mathematical_frame(missing_sign=floor)
            frame[260:700, 500:1400] = 180
            self.assert_rejected(frame)
        frame = bright_frame()
        frame[500:560, 1640:1730] = 40
        self.assert_rejected(frame)

    def test_no_or_duplicate_highlights_rejected(self):
        for selected in ((), (-1,-2), (-2,-4), tuple(detector.ORDER)):
            self.assert_rejected(bright_frame(selected))

    def test_duplicate_column_or_labels_rejected(self):
        frame = bright_frame()
        draw_column(frame, offset=-90)
        self.assert_rejected(frame)
        original = detector._labels
        with patch.object(detector, '_labels', side_effect=lambda *args: original(*args) * 2):
            self.assert_rejected(bright_frame())

    def test_independent_evidence_never_consults_s0_selected_floor(self):
        with patch('map_interface_observation.detect_selected_floor', side_effect=AssertionError('default S0 floor')):
            self.assertEqual(read(bright_frame())['selectedFloor'], -1)

    def test_known_stale_and_invalid_context_stop_before_pixel_probes(self):
        frame = bright_frame()
        with patch.object(detector, '_observe_signed_column', side_effect=AssertionError('stale pixels')):
            for kw in ({'season':'s0'}, {'mode':'unknown'}, {'frame_current':False},
                       {'frame_id':1,'expected_frame_id':2}, {'frame_id':True,'expected_frame_id':1}):
                result = read(frame, **kw)
                self.assertIsNone(result['selectedFloor'])
                self.assertFalse(result['automaticFloorEligible'])

    def test_shape_rejection_is_not_overridden(self):
        self.assert_rejected(cv2.resize(bright_frame(), (1000, 1200)))

    def test_tinted_bands_do_not_replace_neutral_independent_anchors(self):
        frame = mathematical_frame()
        # Preserve signed glyphs while making only independent anchors low
        # luminance. This tests the existing tint band, not real damage replay.
        for bounds in ((90,190,1790,1840), (700,800,1810,1850), (1000,1050,1600,1700)):
            y0,y1,x0,x1=bounds;frame[y0:y1,x0:x1]=(75,70,65)
        baseline = detect_map_interface(frame, floor_read={})
        self.assertFalse(baseline['detected'])
        self.assertGreaterEqual(baseline['backdropRatio'], .8)
        # Broad tinted color bands are not independent map-UI shape proof.
        # Even strong backdrop plus full signed controls must fail closed.
        self.assert_rejected(frame)
        for name in ('closeButton','zoomControl','bottomControls'):
            altered = frame.copy();remove_region(altered,name);self.assert_rejected(altered)
        frame[260:700,500:1400] = 180
        self.assertLess(detect_map_interface(frame, floor_read={})['backdropRatio'], .8)
        self.assert_rejected(frame)

    def test_anchor_dictionary_alone_cannot_invent_a_floor(self):
        frame = bright_frame(())
        self.assertIsNotNone(detector._s1_independent_map_evidence(frame, detect_map_interface(frame,floor_read={})))
        self.assert_rejected(frame)

    def test_false_map_mock_without_observed_metrics_remains_closed(self):
        with patch.object(detector,'detect_map_interface', return_value={'detected':False}):
            self.assert_rejected(bright_frame())


class S1PreparationRuntimeConsumer(unittest.TestCase):
    def test_candidate_consumer_retains_source_validation_scope(self):
        from test_s1_candidate_runtime import CandidateRuntimeContracts
        from client_live_runtime import LiveRuntime
        fixture = CandidateRuntimeContracts()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for floor in detector.ORDER:
            with self.subTest(floor=floor):
                observed, selected, raw = LiveRuntime._observe(
                    fixture.runtime, fixture.registry.engine, bright_frame((floor,)))
                self.assertTrue(observed['mapOpen'])
                self.assertEqual(observed['detectionMode'], 's1-signed-controls-ui-geometry')
                if floor in detector.SOURCE_REPLAY_FLOORS:
                    self.assertEqual(selected, str(floor))
                else:
                    self.assertIsNone(selected)
                    self.assertIsNone(raw['selectedFloor'])
                    self.assertEqual(raw['reason'], 's1-floor-current-evidence-unvalidated')
        observed, selected, raw = LiveRuntime._observe(
            fixture.runtime, fixture.registry.engine, bright_frame(()))
        self.assertFalse(observed['mapOpen'])
        self.assertIsNone(selected)
        self.assertFalse(raw['automaticFloorEligible'])

    def test_manual_consumer_uses_fresh_fallback_without_new_authority(self):
        from test_s1_manual_floor_runtime import ManualS1FloorRuntime
        fixture = ManualS1FloorRuntime()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        fixture.fit_result['observationComponent'].update(bounds=(0,0,1920,1200),
            center=(960,600), width=1920, height=1200, area=2304000)
        with patch('s1_floor_observation.detect_s1_floor', side_effect=fixture.original_detector):
            for floor in detector.ORDER:
                fixture.capture.frame = bright_frame((floor,))
                if floor == -1:
                    fixture.pick()
                else:
                    self.assertTrue(fixture.runtime.dispatch('observe_manual_floor')['success'])
                state = fixture.runtime.snapshot()
                self.assertEqual((state['floor'],state['observedFloor']),(str(floor),str(floor)))
                raw = fixture.runtime._last_observation['floor_read']
                self.assertEqual(raw['mapObservation']['detectionMode'], 's1-signed-controls-ui-geometry')
                self.assertEqual(raw['observedFloorValidated'], floor in detector.SOURCE_REPLAY_FLOORS)
                fixture.assert_no_live_or_rank()


if __name__ == '__main__': unittest.main()
