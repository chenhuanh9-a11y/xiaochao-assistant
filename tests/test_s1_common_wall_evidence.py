"""Actual RGB/mask/negative comparison; static pool and coordinate domain are fixtures.

These tests isolate observation qualification, not coordinate discovery or live
identity. The separate saved-client replay exercises the production composition.
"""
import unittest

import cv2
import numpy as np

from nightmare_candidate import map_color_mask
from s1_common_coordinate_filter import CommonCoordinateFilter
from single_map_structure import current_part
from structure_observation import observed_wall_fragment_edges


def room(points=None):
    mask = np.zeros((220, 300), np.uint8)
    points = points or [(40, 40), (260, 40), (260, 180), (40, 180)]
    cv2.fillPoly(mask, [np.array(points, np.int32)], 1)
    return mask


def image(mask):
    frame = np.full((*mask.shape, 3), 25, np.uint8)
    frame[mask > 0] = [80, 95, 112]
    return frame


def compare(frame, masks):
    """Unchanged comparator with an explicitly supplied one-to-one test canvas."""
    observer = CommonCoordinateFilter.__new__(CommonCoordinateFilter)
    observer.mode, observer.resource_key = 'nightmare', 'fixture'
    observer.entries = {mid: {} for mid in masks}
    fields = []
    for mask in masks.values():
        edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        fields.append(cv2.distanceTransform((~edge).astype(np.uint8), cv2.DIST_L2, 3))
    data = dict(ids=tuple(masks), prepared=object(), shape=frame.shape[:2],
                origin=np.zeros(2), fields=fields)
    observer._pool = lambda *args: data
    observer._domains_from_common = lambda *args: [(np.array([[1., 0., 0.]]), np.eye(3), {})]
    context = dict(query=map_color_mask(frame, live=True),
                   observationBounds=(0, 0, frame.shape[1], frame.shape[0]))
    return observer.evaluate(frame, [context], tuple(masks), '-1'), context


class CommonWallEvidenceTests(unittest.TestCase):
    def test_small_icon_strokes_do_not_exclude_every_candidate(self):
        mask = room()
        frame = image(mask)
        # Four separated light strokes form a small neutral icon. Its total
        # extent passes the old pixel-count/spread test, but not a wall run.
        for x, y in ((145, 105), (164, 105), (145, 124), (164, 124)):
            frame[y:y+7, x:x+5] = 230
        report, _ = compare(frame, {'a': mask, 'b': mask.copy()})
        self.assertTrue(report['coordinateVerified'], report)
        self.assertEqual(report['contradictedMapIds'], [])
        self.assertEqual(report['unknownMapIds'], ['a', 'b'])
        self.assertTrue(all(not row['explicitContradiction'] for row in report['candidateChecks']))
        self.assertFalse(report['identityAuthorized'])

    def test_actual_missing_walls_still_exclude_the_wrong_reference(self):
        live = room([(40, 40), (200, 40), (200, 90), (260, 90), (260, 180), (40, 180)])
        report, _ = compare(image(live), {'wrong': room(), 'matching': live})
        self.assertTrue(report['coordinateVerified'], report)
        self.assertEqual(report['contradictedMapIds'], ['wrong'])
        self.assertEqual(report['unknownMapIds'], ['matching'])
        self.assertFalse(report['identityAuthorized'])

    def test_real_wall_conflict_still_recovers_a_fully_incompatible_pool(self):
        live = room([(40, 40), (200, 40), (200, 90), (260, 90), (260, 180), (40, 180)])
        report, _ = compare(image(live), {'a': room(), 'b': room()})
        self.assertEqual(report['reason'], 'coordinate-or-catalogue-conflict')
        self.assertFalse(report['coordinateVerified'])
        self.assertTrue(all(row['explicitContradiction'] for row in report['candidateChecks']))

    def test_diagonal_wall_still_excludes_a_reference_missing_it(self):
        live = room([(40, 40), (100, 40), (260, 180), (40, 180)])
        report, context = compare(image(live), {'wrong': room(), 'matching': live})
        qualified = observed_wall_fragment_edges(image(live), context)
        self.assertGreater(np.count_nonzero(qualified[100:121, 165:196]), 10)
        self.assertEqual(report['contradictedMapIds'], ['wrong'])
        self.assertEqual(report['unknownMapIds'], ['matching'])

    def test_faint_wall_keeps_relative_contrast_measurement(self):
        mask = room()
        frame = np.full((*mask.shape, 3), 80, np.uint8)
        frame[mask > 0] = 84
        # The helper consumes an already extracted query. Only four gray
        # levels separate the actual current-pixel wall in this unit fixture.
        context = dict(query=mask, observationBounds=(0, 0, 300, 220))
        raw = current_part(frame, context, 'observed').copy()
        qualified = observed_wall_fragment_edges(frame, context)
        self.assertGreater(qualified.sum(), 500)
        self.assertTrue(np.all(~qualified | raw))
        np.testing.assert_array_equal(context['observed'], raw)

    def test_crop_boundary_is_not_a_wall(self):
        mask = room()
        context = dict(query=mask, observationBounds=(40, 40, 261, 181))
        self.assertFalse(observed_wall_fragment_edges(image(mask), context).any())

    def test_short_or_broken_runs_stay_unknown_without_changing_raw_pixels(self):
        mask = room()
        for samples in (11, 12):
            with self.subTest(samples=samples):
                observed = np.zeros_like(mask, dtype=bool)
                observed[40, 85:85+samples] = True
                context = dict(query=mask, observed=observed)
                before = observed.copy()
                qualified = observed_wall_fragment_edges(image(mask), context)
                self.assertEqual(bool(qualified.any()), samples == 12)
                np.testing.assert_array_equal(observed, before)

    def test_diagonal_length_boundary_counts_arc_samples_not_unique_pixels(self):
        for delta in (8, 9):
            with self.subTest(delta=delta):
                query = np.zeros((40, 40), np.uint8)
                polygon = [(10, 10), (10+delta, 10+delta), (10+delta, 35), (1, 35), (1, 10)]
                cv2.fillPoly(query, [np.array(polygon, np.int32)], 1)
                observed = np.zeros_like(query, dtype=bool)
                coords = np.arange(10, 11+delta)
                observed[coords, coords] = True
                context = dict(query=query, observed=observed)
                qualified = observed_wall_fragment_edges(image(query), context)
                self.assertLess(observed.sum(), 12)
                self.assertEqual(bool(qualified.any()), delta == 9)


if __name__ == '__main__':
    unittest.main()
