"""Geometric fixtures test retrieval mathematics/contracts, not gmap accuracy.

No pixels, historical pose answers, held-out B, local solver or game resources
enter retrieval. Expected maps/poses are compared only after retrieval returns.
"""
from dataclasses import replace
import copy
from fractions import Fraction
import math
import unittest
import warnings

import numpy as np

from structure_identity_index import (ConstellationA, DirectedAnchor, DirectoryEntry,
    FingerprintResult, MapFloorKey, NumericPoseUnresolved, StructureIdentityIndex,
    anchors_from_arrays, anchors_from_observed)


def polygon_anchors(offset=(0, 0), prefix="", scale=1):
    points = np.asarray(((0, 0), (100, 0), (100, 40),
                         (40, 40), (40, 90), (0, 90)), dtype=float)
    points = points * scale + offset
    chains = np.asarray([[points[(i - 1) % 6], points[i], points[(i + 1) % 6]]
                         for i in range(6)])
    delta = chains[:, [0, 2]] - chains[:, 1, None]
    rays = delta / np.hypot(delta[..., 0], delta[..., 1])[:, :, None]
    return anchors_from_arrays(chains, rays,
        feature_ids=[f"{prefix}a{i}" for i in range(6)],
        center_ids=[f"{prefix}c{i}" for i in range(6)])


def independent_corners(centres, prefix=""):
    chains = np.asarray([[np.asarray(point) + (-2, 0), point,
                          np.asarray(point) + (0, 3)] for point in centres], dtype=float)
    rays = np.tile(np.asarray(((-1, 0), (0, 1))), (len(chains), 1, 1))
    return anchors_from_arrays(chains, rays,
        feature_ids=[f"{prefix}a{i}" for i in range(len(chains))],
        center_ids=[f"{prefix}c{i}" for i in range(len(chains))])


def transform(anchors, scale=1, offset=(0, 0)):
    return tuple(DirectedAnchor(anchor.feature_id, anchor.center_id,
        np.asarray(anchor.points) * scale + offset, anchor.rays) for anchor in anchors)


_DEFAULT_ANCHORS = object()


def entry(name, anchors=_DEFAULT_ANCHORS, floor="1", mode="fixture", **changes):
    anchors = polygon_anchors() if anchors is _DEFAULT_ANCHORS else anchors
    result = DirectoryEntry(MapFloorKey(mode, name, floor),
        f"fixture-resource-instance/{mode}/{name}/{floor}", anchors,
        frozenset(anchor.feature_id for anchor in anchors) if anchors is not None else None, True, True)
    return replace(result, **changes)


def make_index(entries, domain=None, complete=True):
    return StructureIdentityIndex([row.key for row in entries] if domain is None else domain,
                                  entries, directory_complete=complete)


def query(anchors=None):
    return ConstellationA(polygon_anchors() if anchors is None else anchors, True)


class StructureIdentityIndexTests(unittest.TestCase):
    def assert_never_locks(self, result):
        self.assertIs(result.authorizesIdentity, False)
        self.assertIs(result.lock, False)

    def test_unique_positive_fingerprint_is_only_a_proposal(self):
        result = make_index([entry("one")]).retrieve(query(), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.search_exhausted)
        self.assertTrue(result.fingerprint_unique)
        self.assertEqual([key.map_id for key in result.compatible_map_floors], ["one"])
        self.assert_never_locks(result)
        for proposal in result.proposals:
            self.assertEqual(len(proposal.correspondences), 6)
            self.assertEqual(len({pair.source_center_id for pair in proposal.correspondences}), 6)
            matrix = np.asarray(proposal.matrix_source_to_query)
            for pair in proposal.correspondences:
                predicted = np.asarray(pair.source_points) * matrix[0, 0] + matrix[:2, 2]
                self.assertLessEqual(np.abs(predicted - pair.query_points).max(), .1 + 1e-12)

    def test_repeated_visible_substructure_across_maps_remains_ambiguous(self):
        # An extra source island must not erase the common positive structure.
        repeated = polygon_anchors(prefix="other-") + polygon_anchors((500, 400), "extra-")
        result = make_index([entry("one"), entry("two", repeated)]).retrieve(query(), epsilon=.05)
        self.assertTrue(result.retrieval_complete)
        self.assertEqual({key.map_id for key in result.compatible_map_floors}, {"one", "two"})
        self.assertFalse(result.fingerprint_unique)
        self.assert_never_locks(result)

    def test_same_map_repeated_positions_and_floors_keep_all_pose_regions(self):
        anchors = polygon_anchors(prefix="left-") + polygon_anchors((300, 250), "right-")
        result = make_index([entry("one", anchors), entry("one", floor="2")]).retrieve(query(), epsilon=0)
        self.assertTrue(result.retrieval_complete)
        self.assertEqual({key.floor for key in result.compatible_map_floors}, {"1", "2"})
        seeds = [(row.key.floor, tuple(np.asarray(row.matrix_source_to_query)[:2, 2]))
                 for row in result.proposals]
        self.assertIn(("1", (0.0, 0.0)), seeds)
        self.assertIn(("1", (-300.0, -250.0)), seeds)
        self.assertIn(("2", (0.0, 0.0)), seeds)
        # This flag describes the map fingerprint; floors and poses stay plural.
        self.assertTrue(result.fingerprint_unique)
        self.assert_never_locks(result)

    def test_too_few_physical_centres_and_collinear_constellations_are_unresolved(self):
        index = make_index([entry("one")])
        for anchors, reason in [(polygon_anchors()[:5], "fewer-than-six"),
                (independent_corners([(i * 20, 0) for i in range(6)]), "two-dimensions"),
                (independent_corners([(i * 20, (i % 2) * .01) for i in range(6)]), "two-dimensions")]:
            with self.subTest(reason=reason):
                result = index.retrieve(query(anchors), epsilon=.1)
                self.assertFalse(result.retrieval_complete)
                self.assertFalse(result.fingerprint_unique)
                self.assertTrue(any(reason in text for text in result.unresolved_reasons))

    def test_missing_domain_entry_is_not_deleted_from_competition(self):
        one = entry("one")
        missing = MapFloorKey("fixture", "missing", "B3")
        result = make_index([one], domain=[one.key, missing]).retrieve(query(), epsilon=.1)
        self.assertTrue(result.proposals)
        self.assertTrue(result.search_exhausted)
        self.assertFalse(result.retrieval_complete)
        self.assertFalse(result.fingerprint_unique)
        self.assertIn(missing, {gap.key for gap in result.directory_gaps})

    def test_every_permission_binding_and_known_support_gap_blocks_uniqueness(self):
        changes = [dict(search_allowed=False), dict(search_allowed=None),
            dict(resource_binding=None), dict(eligibility_complete=False),
            dict(eligibility_complete=None), dict(eligible_feature_ids=None),
            dict(anchors=None), dict(unavailable_reason="resource-read-denied"),
            dict(eligible_feature_ids=frozenset(["missing-feature"]))]
        for change in changes:
            with self.subTest(change=change):
                result = make_index([entry("one"), entry("unknown", **change)]).retrieve(query(), epsilon=.1)
                self.assertTrue(result.directory_gaps)
                self.assertFalse(result.retrieval_complete)
                self.assertFalse(result.fingerprint_unique)
                self.assert_never_locks(result)

    def test_unknown_domain_completeness_and_undeclared_entry_are_gaps(self):
        for index in [make_index([entry("one")], complete=None),
                      make_index([entry("one")], domain=[])]:
            result = index.retrieve(query(), epsilon=.1)
            self.assertTrue(result.directory_gaps)
            self.assertFalse(result.fingerprint_unique)

    def test_entries_outside_declared_domain_cannot_propose_or_expand_competition(self):
        inside, outside = entry("inside"), entry("outside")
        result = make_index([inside, outside], domain=[inside.key]).retrieve(query(), epsilon=.1)
        self.assertEqual(result.compatible_map_floors, (inside.key,))
        self.assertTrue(any(gap.key == outside.key and gap.reason == "entry-outside-declared-domain"
                            for gap in result.directory_gaps))
        self.assertTrue(result.search_exhausted)
        self.assertFalse(result.retrieval_complete)
        self.assertFalse(result.fingerprint_unique)

    def test_result_authority_and_lock_cannot_be_supplied_by_constructor(self):
        arguments = dict(proposals=(), retrieval_complete=True, search_exhausted=True,
                         directory_gaps=(), unresolved_reasons=(), fingerprint_unique=False,
                         work_done=0)
        self.assert_never_locks(FingerprintResult(**arguments))
        for name in ("authorizesIdentity", "lock"):
            with self.subTest(field=name), self.assertRaises(TypeError):
                FingerprintResult(**arguments, **{name: True})

    def test_explicit_eligible_set_is_used_without_promoting_other_anchors(self):
        row = entry("one", eligible_feature_ids=frozenset(anchor.feature_id for anchor in polygon_anchors()[:5]))
        result = make_index([row]).retrieve(query(), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertEqual(result.proposals, ())
        self.assertFalse(result.fingerprint_unique)

    def test_scale_translation_and_closed_tolerance_boundary_keep_competitors(self):
        centres = [(0, 0), (80, 0), (0, 80), (80, 80), (30, 20), (50, 60)]
        anchors = independent_corners(centres)
        moved = list(anchors)
        moved[-1] = transform(moved[-1:], offset=(2, 0))[0]
        index = make_index([entry("exact", anchors), entry("boundary", tuple(moved))])
        for scale, offset in [(1, (0, 0)), (1.37, (19.2, -6.8)), (.41, (-100, 40))]:
            result = index.retrieve(query(transform(anchors, scale, offset)), epsilon=scale)
            self.assertTrue(result.retrieval_complete)
            self.assertEqual({key.map_id for key in result.compatible_map_floors}, {"exact", "boundary"})
            self.assertFalse(result.fingerprint_unique)
            boundary = next(row for row in result.proposals if row.key.map_id == "boundary")
            self.assertLessEqual(boundary.pose_region.scale_interval[0], scale + 1e-12)
            self.assertGreaterEqual(boundary.pose_region.scale_interval[1], scale - 1e-12)

    def test_continuous_scale_region_is_returned_instead_of_a_quantized_winner(self):
        anchors = polygon_anchors()
        actual_scale, actual_offset = 1.234567891, (103.37, -37.92)
        result = make_index([entry("one", anchors)]).retrieve(
            query(transform(anchors, actual_scale, actual_offset)), epsilon=.02)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)
        region = result.proposals[0].pose_region
        self.assertLess(region.scale_interval[0], actual_scale)
        self.assertGreater(region.scale_interval[1], actual_scale)
        for (lower, upper), target in zip(region.translation_bounds(actual_scale), actual_offset):
            self.assertLessEqual(lower, target + 1e-12)
            self.assertGreaterEqual(upper, target - 1e-12)

    def test_ratio_interval_includes_critical_endpoint_error(self):
        # q ratio exceeds the exact source ratio. A sorted exact-bin lookup
        # would lose this competitor, though all original coordinates fit.
        anchors = independent_corners([(0, 0), (100, 0), (0, 100), (100, 100), (30, 20), (60, 70)])
        observed = []
        for anchor in anchors:
            points = np.asarray(anchor.points).copy()
            points[0, 0] -= .5
            points[2, 1] -= .5
            delta = points[[0, 2]] - points[1]
            rays = delta / np.hypot(delta[:, 0], delta[:, 1])[:, None]
            observed.append(DirectedAnchor(anchor.feature_id, anchor.center_id, points, rays))
        result = make_index([entry("one", anchors)]).retrieve(query(observed), epsilon=.5)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)

    def test_ratio_range_covers_two_point_diagonal_screen_error(self):
        centres = np.asarray([(0, 0), (100, 0), (0, 100),
                              (100, 100), (30, 20), (60, 70)])
        chains = np.asarray([[point + (-1, -1), point, point + (1, -1)]
                             for point in centres], dtype=float)
        delta = chains[:, [0, 2]] - chains[:, 1, None]
        rays = delta / np.hypot(delta[..., 0], delta[..., 1])[:, :, None]
        source = anchors_from_arrays(chains, rays, feature_ids=range(6), center_ids=range(6))
        observed_chains = chains + (.5, .5)
        observed_chains[:, 0] = chains[:, 0] - (.5, .5)
        observed = anchors_from_arrays(observed_chains, rays, feature_ids=range(6), center_ids=range(6))
        # The previous arm grows by 2*sqrt(2)*epsilon. Even the query centre
        # participates in the error; a single-point epsilon ratio window loses
        # source ratio 1 although every measured point is within .5 pixels.
        qdelta = observed_chains[0, [0, 2]] - observed_chains[0, 1]
        qlengths = np.hypot(qdelta[:, 0], qdelta[:, 1])
        self.assertGreater((qlengths[0] - .5) / (qlengths[1] + .5), 1)
        result = make_index([entry("one", source)]).retrieve(query(observed), epsilon=.5)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)

    def test_positive_query_connections_require_actual_source_connections(self):
        source = polygon_anchors()
        broken = list(source)
        points = np.asarray(broken[0].points).copy()
        points[2] += (1, 0)
        broken[0] = replace(broken[0], points=points)
        # All coordinates of this disconnected near-neighbour fit epsilon=1.
        # The query's measured shared endpoint is stronger positive evidence.
        result = make_index([entry("connected", source), entry("disconnected", tuple(broken))]).retrieve(
            query(), epsilon=1)
        self.assertTrue(result.retrieval_complete)
        self.assertEqual({row.key.map_id for row in result.proposals}, {"connected"})

    def test_short_arm_direction_includes_endpoint_and_center_uncertainty(self):
        source = independent_corners([(0, 0), (100, 0), (0, 100),
                                      (100, 100), (30, 20), (60, 70)])
        observed = []
        for anchor in source:
            points = np.asarray(anchor.points) + (0, -.5)
            points[0] = np.asarray(anchor.points[0]) + (0, .5)
            delta = points[[0, 2]] - points[1]
            rays = delta / np.hypot(delta[:, 0], delta[:, 1])[:, None]
            self.assertLess(np.dot(rays[0], anchor.rays[0]), .95)
            observed.append(replace(anchor, points=points, rays=rays))
        result = make_index([entry("one", source)]).retrieve(query(observed), epsilon=.5)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)

    def test_accepted_input_ray_rounding_cannot_drop_a_geometric_competitor(self):
        centres = np.asarray([(0, 0), (100, 0), (0, 100),
                              (100, 100), (30, 20), (60, 70)])
        chains = np.asarray([[point + (-2, 2), point, point + (0, 3)]
                             for point in centres], dtype=float)
        delta = chains[:, [0, 2]] - chains[:, 1, None]
        rays = delta / np.hypot(delta[..., 0], delta[..., 1])[:, :, None]
        source = anchors_from_arrays(chains, rays, feature_ids=range(6), center_ids=range(6))
        observed_points = chains + (-.5, -.5)
        observed_points[:, 0] = chains[:, 0] + (.5, .5)
        observed_delta = observed_points[:, [0, 2]] - observed_points[:, 1, None]
        observed_rays = observed_delta / np.hypot(observed_delta[..., 0], observed_delta[..., 1])[:, :, None]
        observed = anchors_from_arrays(observed_points, observed_rays,
                                       feature_ids=range(6), center_ids=range(6))

        def rotate_previous(anchors, angle):
            rotation = np.asarray(((np.cos(angle), -np.sin(angle)),
                                   (np.sin(angle), np.cos(angle))))
            rounded = []
            for anchor in anchors:
                directions = np.asarray(anchor.rays).copy()
                directions[0] = rotation @ directions[0]
                # This producer discrepancy is accepted by DirectedAnchor's
                # raw-ray consistency check, so it cannot narrow the exact
                # point-derived angular window and erase a competitor.
                self.assertGreaterEqual(np.dot(directions[0], anchor.rays[0]), 1 - 1e-6)
                rounded.append(replace(anchor, rays=directions))
            return tuple(rounded)

        rounded_source = rotate_previous(source, .001)
        exact, rounded = entry("exact", source), entry("rounded", rounded_source)
        combined = make_index([exact, rounded]).retrieve(query(observed), epsilon=.5)
        self.assertTrue(combined.retrieval_complete)
        self.assertEqual({key.map_id for key in combined.compatible_map_floors}, {"exact", "rounded"})
        self.assertFalse(combined.fingerprint_unique)
        rounded_query = rotate_previous(observed, -.001)
        query_result = make_index([exact]).retrieve(query(rounded_query), epsilon=.5)
        self.assertTrue(query_result.retrieval_complete)
        self.assertTrue(query_result.proposals)

    def test_unit_direction_roundoff_keeps_zero_error_scale_equivalent_maps(self):
        centres = np.asarray([(0, 0), (100, 0), (0, 100),
                              (100, 100), (30, 20), (60, 70)])
        chains = np.asarray([[point + (-2, 2), point, point + (0, 3)]
                             for point in centres], dtype=float)
        delta = chains[:, [0, 2]] - chains[:, 1, None]
        rays = delta / np.hypot(delta[..., 0], delta[..., 1])[:, :, None]
        anchors = anchors_from_arrays(chains, rays, feature_ids=range(6), center_ids=range(6))
        same = entry("same", anchors)
        scaled = entry("scaled", transform(anchors, 3))
        for entries in ([same], [same, scaled]):
            with self.subTest(maps=[row.key.map_id for row in entries]):
                result = make_index(entries).retrieve(query(anchors), epsilon=0,
                                                       minimum_ray_dot=1, max_work=None)
                self.assertTrue(result.retrieval_complete)
                self.assertEqual({key.map_id for key in result.compatible_map_floors},
                                 {row.key.map_id for row in entries})
                self.assertEqual(result.fingerprint_unique, len(entries) == 1)

    def test_power_of_two_source_scales_keep_exact_geometric_competitors(self):
        base = polygon_anchors()
        ordinary = entry("ordinary", base)
        for exponent in (-540, 540):
            with self.subTest(exponent=exponent):
                factor = math.ldexp(1.0, exponent)
                inverse = math.ldexp(1.0, -exponent)
                scaled_anchors = transform(base, factor)
                # Exact rational post-oracle establishes zero geometric error;
                # a norm's squared under/overflow cannot erase this competitor.
                for scaled_anchor, original in zip(scaled_anchors, base):
                    for scaled_point, original_point in zip(scaled_anchor.points, original.points):
                        for value, expected in zip(scaled_point, original_point):
                            self.assertEqual(Fraction(value) * Fraction(inverse), Fraction(expected))
                scaled = entry("scaled", scaled_anchors)
                for rows in ([scaled], [ordinary, scaled]):
                    result = make_index(rows).retrieve(query(base), epsilon=0,
                                                       minimum_ray_dot=1, max_work=None)
                    self.assertTrue(result.retrieval_complete)
                    self.assertEqual(set(result.compatible_map_floors), {row.key for row in rows})
                    self.assertEqual(result.fingerprint_unique, len(rows) == 1)

    def test_large_power_of_two_query_uses_finite_two_dimensional_lengths(self):
        base = polygon_anchors()
        factor = math.ldexp(1.0, 540)
        result = make_index([entry("ordinary", base)]).retrieve(
            query(transform(base, factor)), epsilon=0, minimum_ray_dot=1, max_work=None)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.fingerprint_unique)
        self.assertTrue(result.proposals)

    def test_ray_input_normalization_handles_power_of_two_magnitudes(self):
        base = polygon_anchors()
        for exponent in (-540, 540):
            with self.subTest(exponent=exponent):
                factor = math.ldexp(1.0, exponent)
                anchors = tuple(replace(anchor, rays=np.asarray(anchor.rays) * factor) for anchor in base)
                result = make_index([entry("ordinary", anchors)]).retrieve(query(base),
                    epsilon=0, minimum_ray_dot=1, max_work=None)
                self.assertTrue(result.retrieval_complete)
                self.assertTrue(result.fingerprint_unique)

    def test_finite_points_with_overflowing_cross_center_differences_are_unresolved(self):
        centres = np.asarray([(-1, -1), (-1, 1), (1, -1), (1, 1), (0, 0), (.5, .25)])
        chains = np.asarray([[point + (-.125, 0), point, point + (0, .125)] for point in centres])
        rays = np.tile(np.asarray(((-1, 0), (0, 1))), (6, 1, 1))
        anchors = anchors_from_arrays(chains, rays, feature_ids=range(6), center_ids=range(6))
        factor, inverse = math.ldexp(1.0, 1023), math.ldexp(1.0, -1023)
        big_anchors = transform(anchors, factor)
        for big, ordinary in zip(big_anchors, anchors):
            for point, expected in zip(big.points, ordinary.points):
                for value, target in zip(point, expected):
                    self.assertTrue(math.isfinite(value))
                    self.assertEqual(Fraction(value) * Fraction(inverse), Fraction(target))
        ordinary, big = entry("ordinary", anchors), entry("big", big_anchors)
        index = make_index([ordinary, big])
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", RuntimeWarning)
            result = index.retrieve(query(anchors), epsilon=0, max_work=None)
        self.assertFalse(result.retrieval_complete)
        self.assertFalse(result.fingerprint_unique)
        self.assertTrue(any("numeric" in reason for reason in result.unresolved_reasons))
        self.assertEqual(set(index.domain), {ordinary.key, big.key})
        # A partial correspondence is not a completed compatible pose family.
        self.assertFalse(any(row.key == big.key for row in result.unresolved_proposals))
        self.assertFalse(recorded)

    def test_unrepresentable_source_length_ratio_is_a_directory_gap(self):
        extreme = DirectedAnchor("extreme", "extreme", ((-1e308, 0), (1000, 0), (1000, 1e-308)),
                                 ((-1, 0), (0, 1)))
        source = entry("one", polygon_anchors() + (extreme,))
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", RuntimeWarning)
            result = make_index([source]).retrieve(query(), epsilon=.1)
        self.assertFalse(result.retrieval_complete)
        self.assertFalse(result.fingerprint_unique)
        self.assertTrue(any("numeric" in gap.reason for gap in result.directory_gaps))
        self.assertTrue(result.proposals)
        self.assertFalse(recorded)

    def test_large_source_origins_never_emit_an_uncertified_global_seed(self):
        ordinary = entry("ordinary", polygon_anchors())
        for origin, scale in [((1e6, 1e6), .001), ((1e15, 1e15), 1.37)]:
            with self.subTest(origin=origin, scale=scale):
                large = entry("large-origin", polygon_anchors(origin))
                result = make_index([ordinary, large]).retrieve(
                    query(transform(polygon_anchors(), scale)), epsilon=0, max_work=None)
                for proposal in result.proposals:
                    matrix = np.asarray(proposal.matrix_source_to_query)
                    bounds = proposal.pose_region.translation_bounds(matrix[0, 0])
                    for (lower, upper), value in zip(bounds, matrix[:2, 2]):
                        self.assertLessEqual(lower, upper)
                        self.assertLessEqual(lower, value)
                        self.assertLessEqual(value, upper)
                    predicted = (np.asarray(proposal.pose_region.source_points) * matrix[0, 0]
                                 + matrix[:2, 2])
                    error = np.abs(predicted - proposal.pose_region.query_points).max()
                    declared_error = proposal.pose_region.epsilon + proposal.pose_region.roundoff_slack
                    self.assertLessEqual(error, declared_error)

    def test_numeric_unresolved_candidate_stays_in_competition_and_blocks_uniqueness(self):
        ordinary = entry("ordinary", polygon_anchors())
        large = entry("large-origin", polygon_anchors((1e15, 1e15)))
        result = make_index([ordinary, large]).retrieve(
            query(transform(polygon_anchors(), 1.37)), epsilon=0, max_work=None)
        self.assertFalse(result.retrieval_complete)
        self.assertFalse(result.fingerprint_unique)
        self.assertTrue(result.search_exhausted)
        self.assertEqual(set(result.compatible_map_floors), {ordinary.key, large.key})
        self.assertTrue(any("numeric" in reason for reason in result.unresolved_reasons))
        unresolved = [row for row in result.unresolved_proposals if row.key == large.key]
        self.assertTrue(unresolved)
        self.assertEqual(len(unresolved[0].correspondences), 6)
        self.assertTrue(any(row.key == ordinary.key for row in result.proposals))
        for row in unresolved:
            region = row.pose_region
            scale = sum(value / 2 for value in region.scale_interval)
            for lower, upper in (*region.centered_translation_bounds(scale),
                                 *region.translation_bounds(scale)):
                self.assertLessEqual(lower, upper)
            with self.assertRaises(NumericPoseUnresolved):
                region.seed()

    def test_published_global_matrix_arithmetic_bound_covers_exact_affine_evaluation(self):
        source = entry("large-origin", polygon_anchors((1e6, 1e6)))
        result = make_index([source]).retrieve(query(transform(polygon_anchors(), .001)),
                                              epsilon=1e-5, max_work=None)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)
        for proposal in result.proposals:
            matrix = np.asarray(proposal.matrix_source_to_query)
            arithmetic = proposal.global_matrix_arithmetic_error
            points = np.asarray(proposal.pose_region.source_points)
            predicted = points * matrix[0, 0] + matrix[:2, 2]
            for point, evaluated in zip(points, predicted):
                for axis in range(2):
                    exact = (Fraction(float(matrix[0, 0])) * Fraction(float(point[axis]))
                             + Fraction(float(matrix[axis, 2])))
                    actual_error = abs(Fraction(float(evaluated[axis])) - exact)
                    self.assertLessEqual(actual_error, Fraction(arithmetic[axis]))
            self.assertLessEqual(np.abs(predicted - proposal.pose_region.query_points).max(),
                                 proposal.pose_region.epsilon + proposal.pose_region.roundoff_slack)

    def test_backtracking_does_not_discard_a_non_greedy_correspondence(self):
        centres = [(0, 0), (1.5, 0), (0, 100), (100, 100), (100, 0), (50, 50)]
        anchors = independent_corners(centres)
        observed_centres = [centres[1], centres[0], *centres[2:]]
        observed = independent_corners(observed_centres, "q-")
        result = make_index([entry("one", anchors)]).retrieve(query(observed), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)
        self.assertTrue(any(row.correspondences[0].source_feature_id == "a1"
                            and row.correspondences[1].source_feature_id == "a0" for row in result.proposals))

    def test_distinct_query_nodes_cannot_reuse_a_source_physical_centre(self):
        centres = [(0, 0), (1, 0), (100, 0), (0, 100), (100, 100), (50, 50)]
        observed = independent_corners(centres)
        source = list(observed)
        source[1] = replace(source[0], feature_id="duplicate-description")
        result = make_index([entry("one", tuple(source))]).retrieve(query(observed), epsilon=.75)
        self.assertTrue(result.retrieval_complete)
        self.assertEqual(result.proposals, ())

    def test_multiple_descriptions_of_one_query_centre_do_not_create_new_centres(self):
        anchors = polygon_anchors()
        duplicate = replace(anchors[0], feature_id="another-description")
        observed = anchors + (duplicate,)
        result = make_index([entry("one", observed)]).retrieve(query(observed), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)
        self.assertEqual(len({pair.source_center_id for pair in result.proposals[0].correspondences}), 6)
        only_five = anchors[:5] + (duplicate,)
        result = make_index([entry("one")]).retrieve(query(only_five), epsilon=.1)
        self.assertFalse(result.retrieval_complete)

    def test_reversed_contour_traversal_keeps_real_arm_correspondences(self):
        # Reversing contour winding changes arm numbering, not the geometry.
        reversed_anchors = tuple(replace(anchor, points=(anchor.points[2], anchor.points[1], anchor.points[0]),
                                        rays=(anchor.rays[1], anchor.rays[0])) for anchor in polygon_anchors())
        result = make_index([entry("reversed", reversed_anchors)]).retrieve(query(), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)
        self.assertTrue(all(pair.source_arm_order == (2, 1, 0)
                            for row in result.proposals for pair in row.correspondences))

    def test_arm_order_is_checked_independently_for_each_anchor(self):
        mixed = tuple(replace(anchor, points=(anchor.points[2], anchor.points[1], anchor.points[0]),
                              rays=(anchor.rays[1], anchor.rays[0])) if i % 2 else anchor
                      for i, anchor in enumerate(polygon_anchors()))
        result = make_index([entry("mixed", mixed)]).retrieve(query(), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)
        orders = [pair.source_arm_order for pair in result.proposals[0].correspondences]
        self.assertEqual(orders, [(0, 1, 2), (2, 1, 0)] * 3)

    def test_arm_reordering_does_not_admit_reflection_or_undirected_rays(self):
        reflected = tuple(replace(anchor, points=np.asarray(anchor.points) * (-1, 1),
                                  rays=np.asarray(anchor.rays) * (-1, 1)) for anchor in polygon_anchors())
        result = make_index([entry("reflected", reflected)]).retrieve(query(), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertEqual(result.proposals, ())

    def test_cancel_and_budget_never_promote_a_partial_search(self):
        index = make_index([entry("one")])
        for options, reason in [(dict(cancelled=lambda: True), "cancelled"),
                                (dict(max_work=0), "work-budget-exhausted"),
                                (dict(time_budget_ms=0), "time-budget-exhausted")]:
            with self.subTest(reason=reason):
                result = index.retrieve(query(), epsilon=.1, **options)
                self.assertFalse(result.retrieval_complete)
                self.assertFalse(result.fingerprint_unique)
                self.assertIn(reason, result.unresolved_reasons)
                self.assert_never_locks(result)
        full = index.retrieve(query(), epsilon=.1)
        calls = 0

        def cancel_at_final_check():
            nonlocal calls
            calls += 1
            return calls >= full.work_done + 2

        result = index.retrieve(query(), epsilon=.1, cancelled=cancel_at_final_check)
        self.assertTrue(result.proposals)
        self.assertFalse(result.retrieval_complete)
        self.assertFalse(result.fingerprint_unique)
        two = make_index([entry("one"), entry("two")])
        full_two = two.retrieve(query(), epsilon=.1)
        partial = two.retrieve(query(), epsilon=.1, max_work=full_two.work_done - 1)
        self.assertTrue(partial.proposals)
        self.assertFalse(partial.retrieval_complete)
        self.assertFalse(partial.fingerprint_unique)
        self.assertIn("work-budget-exhausted", partial.unresolved_reasons)

    def test_input_arrays_records_and_snapshots_are_not_modified(self):
        original = polygon_anchors()
        chains = np.asarray([anchor.points for anchor in original])
        rays = np.asarray([anchor.rays for anchor in original])
        before = chains.copy(), rays.copy()
        anchors = anchors_from_arrays(chains, rays, feature_ids=range(6), center_ids=range(6))
        records = [dict(index=i, points=chains[i], rays=rays[i]) for i in range(6)]
        records_before = copy.deepcopy(records)
        index = make_index([entry("one", anchors)])
        observed = anchors_from_observed(records)
        first = index.retrieve(query(observed), epsilon=.1)
        np.testing.assert_array_equal(chains, before[0])
        np.testing.assert_array_equal(rays, before[1])
        for row, saved in zip(records, records_before):
            np.testing.assert_array_equal(row["points"], saved["points"])
            np.testing.assert_array_equal(row["rays"], saved["rays"])
        chains[:] = -999
        rays[:] = 0
        second = index.retrieve(query(observed), epsilon=.1)
        self.assertEqual(first, second)

    def test_a_is_caller_selected_and_b_or_pose_answers_are_not_accepted(self):
        class ForbiddenEvidence:
            def __array__(self, *args, **kwargs):
                raise AssertionError("held-out evidence was read")

        records = [dict(index=i, points=anchor.points, rays=anchor.rays,
                        B=ForbiddenEvidence(), pose=ForbiddenEvidence())
                   for i, anchor in enumerate(polygon_anchors())]
        selected = anchors_from_observed(records)
        index = make_index([entry("one")])
        result = index.retrieve(query(selected), epsilon=.1)
        self.assertTrue(result.fingerprint_unique)
        rejected = index.retrieve(ConstellationA(selected, False), epsilon=.1)
        self.assertFalse(rejected.fingerprint_unique)
        self.assertFalse(rejected.retrieval_complete)
        with self.assertRaises(TypeError):
            index.retrieve(query(selected), epsilon=.1, B=ForbiddenEvidence())
        with self.assertRaises(TypeError):
            index.retrieve(query(selected), epsilon=.1, pose=ForbiddenEvidence())

    def test_resource_binding_and_true_correspondences_survive_result(self):
        row = entry("one", polygon_anchors((500, 300), "resource-"))
        result = make_index([row]).retrieve(query(), epsilon=.1)
        proposal = result.proposals[0]
        self.assertEqual(proposal.resource_binding, row.resource_binding)
        by_id = {anchor.feature_id: anchor for anchor in row.anchors}
        for pair in proposal.correspondences:
            self.assertEqual(pair.source_points, tuple(by_id[pair.source_feature_id].points[i]
                                                       for i in pair.source_arm_order))
            self.assertEqual(pair.source_center_id, by_id[pair.source_feature_id].center_id)

    def test_perturbed_points_and_mixed_arm_orders_keep_the_true_continuous_region(self):
        # The generating pose is used only as a post-retrieval mathematical
        # oracle. Retrieval receives just A geometry and declared epsilon.
        rng = np.random.default_rng(86721)
        source = independent_corners([(0, 0), (100, 0), (0, 100),
                                      (100, 100), (30, 20), (60, 70)])
        index = make_index([entry("one", source)])
        for sample in range(40):
            scale = 10 ** rng.uniform(-1.3, 1.1)
            epsilon = scale * .45
            offset = rng.uniform(-300, 300, 2)
            observed = []
            for anchor in source:
                points = (np.asarray(anchor.points) * scale + offset
                          + rng.uniform(-epsilon, epsilon, (3, 2)))
                delta = points[[0, 2]] - points[1]
                rays = delta / np.hypot(delta[:, 0], delta[:, 1])[:, None]
                if rng.random() < .5:
                    points, rays = points[[2, 1, 0]], rays[[1, 0]]
                observed.append(replace(anchor, points=points, rays=rays))
            with self.subTest(sample=sample):
                result = index.retrieve(query(observed), epsilon=epsilon, max_work=None)
                self.assertTrue(result.retrieval_complete)
                correct = [row for row in result.proposals if all(
                    pair.query_center_id == pair.source_center_id for pair in row.correspondences)]
                self.assertTrue(correct)
                self.assertTrue(any(
                    row.pose_region.scale_interval[0] <= scale <= row.pose_region.scale_interval[1]
                    and all(lower <= target <= upper for (lower, upper), target in
                            zip(row.pose_region.translation_bounds(scale), offset)) for row in correct))


if __name__ == "__main__":
    unittest.main()
