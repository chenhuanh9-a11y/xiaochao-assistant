"""Conservative local graph preparation against the actual ordinary probe.

Synthetic graph measurements exercise numerical boundaries. The source loader,
identity adjudicator and runtime are not covered by these computation tests.
"""
import copy
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from local_structure_index import LocalStructureIndex, possible_turns
from side_entry_evidence import SideEntryEvidence
import side_entry_evidence as side_evidence
from side_entry_search import prepare_arrays
from difficult_side_entry_identity import MapLocalEntryIdentity


def graph(mid='native', floor='1', offset=(0., 0.), legacy=False):
    points = 3 * np.asarray([[0, 0], [60, 0], [60, 20], [100, 20],
        [100, 80], [40, 80], [40, 40], [0, 40]], float) + [30, 30] + offset
    rays = np.stack((np.roll(points, 1, axis=0) - points,
                     np.roll(points, -1, axis=0) - points), axis=1)
    rays /= np.linalg.norm(rays, axis=2)[:, :, None]
    pairs = np.column_stack((np.arange(len(points)), np.roll(np.arange(len(points)), -1)))
    item = dict(mapId=mid, floor=floor, region='actual-contour', unit=60.,
        canvas=dict(width=1100, height=1000), gates=[], sourceProduct=mid + '-source',
        sourceKind=('legacy-manually-reconstructed-clean-structure' if legacy
                    else 'game-rendered-shared-structure'))
    return item, points, rays, pairs


def probe(entries, q, qr, qpairs, *, index=None, floor='1', deadline=None, side_search=None):
    locator = SimpleNamespace(entries=entries, floors=('1', '2'),
        side_search=side_search or {}, profile_for_map=lambda mid: 'shared-structure',
        _side_door_contradicted=lambda row: False, side_door_error=None, side_search_error=None)
    frame = np.zeros((1000, 1600, 3), np.uint8)
    work = SideEntryEvidence(locator, frame, floor, prepared_index=index, deadline=deadline)
    work.q, work.qr, work.qpairs, work.scan_complete = q, qr, qpairs, not work.index_directory_gaps
    for entry in work.entries:
        work._probe(entry)
    work._prepare_competition()
    return work


def exact_turns(q, scales, translations, compatible, source):
    return np.asarray([np.any(compatible & (np.linalg.norm(q[:, None]
        - (source[None] * scale + shift), axis=2) <= 3), axis=1).sum()
        for scale, shift in zip(scales, translations)])


def original_oriented_hits(query, source, scale, translation, **_):
    """Independent original expression; never calls the axis-bound helper."""
    return np.linalg.norm(query - (source * scale + translation), axis=1) <= 3


class SideAxisBoundTests(unittest.TestCase):
    def actual_hits(self, query, source, scale=1., translation=None):
        translation = np.zeros(2) if translation is None else translation
        return side_evidence._oriented_seed_hits(query, source, scale, translation,
            axis_safe=side_evidence._axis_bound_inputs_safe(query, source))

    def test_actual_complete_work_uses_axis_helper_and_matches_original_kernel(self):
        entries = [graph(), graph('rival', offset=(400, 200)), graph('legacy', legacy=True)]
        _, points, rays, pairs = entries[0]
        query = points * .45 + [400., 270.]
        # Only extracted measurements and the final pixel validator are supplied.
        # Actual seed search, correspondence/refinement, jobs and finish execute.
        locator = SimpleNamespace(entries=entries, floors=('1',), side_search={},
            profile_for_map=lambda mid: 'shared-structure', side_door_error=None, side_search_error=None,
            _side_door_contradicted=lambda row: False,
            _observe=lambda frame: (None, [dict(point=p, rays=r) for p, r in zip(query, rays)], pairs),
            verify=lambda frame, row, **kw: dict(success=False, reason='fixture-pixel-validator-unavailable'))
        frame = np.zeros((1000, 1600, 3), np.uint8)

        def drain(index):
            work = MapLocalEntryIdentity.prepare_rank(locator, frame, '1',
                prepared_index=index, deadline=float('inf'))
            while (job := work.next_job()) is not None:
                job.run()
            result = work.finish()
            return work, result

        perturbation = np.asarray([[0., .2], [.3, -.1], [-.4, .2], [.1, .3],
                                   [-.2, -.4], [.3, 0.], [0., -.3], [.2, .4]])
        for noise in (0., .7, 2.9):
            query = points * .45 + [400., 270.] + perturbation * noise
            for index in (None, LocalStructureIndex(entries, max_lower_bytes=0)):
                with self.subTest(prepared=index is not None, noise=noise):
                    with patch.object(side_evidence, '_oriented_seed_hits',
                                      side_effect=original_oriented_hits, create=True) as oracle:
                        old, old_result = drain(index)
                    self.assertGreater(old.seed_count, 0)
                    self.assertEqual(oracle.call_count, old.seed_count,
                        'the actual per-seed production distance must call the replaceable numeric helper')
                    with patch.object(side_evidence, '_oriented_seed_hits',
                                      wraps=side_evidence._oriented_seed_hits) as actual, \
                         patch.object(side_evidence, '_axis_bound_inputs_safe',
                                      wraps=side_evidence._axis_bound_inputs_safe) as static:
                        new, new_result = drain(index)
                    self.assertEqual(actual.call_count, new.seed_count)
                    self.assertEqual(static.call_count, len(entries), 'static input checks run once per source probe')
                    self.assertEqual(old.seed_count, new.seed_count)
                    self.assertEqual(old.scan_complete, new.scan_complete)
                    self.assertTrue(new._complete)
                    self.assertEqual(old_result, new_result)

    def test_float64_ulp_boundary_tiny_dy_and_signed_zero_match_original(self):
        lower, upper = np.nextafter(3., 0.), np.nextafter(3., np.inf)
        xs = np.asarray([-upper, -3., -lower, -0., 0., lower, 3., upper])
        query = np.column_stack((np.tile(xs, 3), np.repeat([0., 2. ** -40, 3.], len(xs))))
        source = np.zeros_like(query)
        self.assertTrue(side_evidence._axis_bound_inputs_safe(query, source))
        expected = original_oriented_hits(query, source, 1., np.zeros(2))
        norm = np.linalg.norm
        with patch.object(side_evidence.np.linalg, 'norm', wraps=norm) as calls:
            actual = self.actual_hits(query, source)
        np.testing.assert_array_equal(actual, expected)
        self.assertTrue(actual[6])  # dx=3, dy=0 remains measured.
        self.assertTrue(actual[len(xs) + 6])  # Small nonzero dy can still round norm to 3.
        self.assertEqual(calls.call_count, 1)
        self.assertLess(len(calls.call_args.args[0]), len(query), 'outside-x pairs never enter the 2D norm')

    def test_all_retained_all_rejected_and_ordered_interleaving(self):
        source = np.zeros((6, 2))
        for query in (np.zeros((6, 2)), np.full((6, 2), 20.),
                      np.asarray([[4., 0.], [3., 0.], [-4., 0.], [0., 3.], [1., 4.], [0., 0.]])):
            with self.subTest(query=query.tolist()):
                expected = original_oriented_hits(query, source, 1., np.zeros(2))
                actual = self.actual_hits(query, source)
                np.testing.assert_array_equal(actual, expected)
                qids, sids = np.asarray([2, 0, 2, 7, 0, 1]), np.asarray([4, 2, 1, 5, 1, 0])
                np.testing.assert_array_equal(qids[actual], qids[expected])
                np.testing.assert_array_equal(sids[actual], sids[expected])
        np.testing.assert_array_equal(self.actual_hits(source[:0], source[:0]), np.empty(0, bool))

    def test_cancellation_arithmetic_and_readonly_noncontiguous_inputs(self):
        source_store = np.zeros((6, 4))
        source_store[:, ::2] = [[2. ** 50, 2. ** 49], [30., 80.], [90., 40.],
                                [0., -0.], [180., 270.], [60., 60.]]
        source = source_store[:, ::2]
        scale = np.float64(np.nextafter(.5, 1.))
        translation = np.asarray([-2. ** 49, -2. ** 48])
        query_store = np.zeros((6, 4))
        query_store[:, ::2] = source * scale + translation + [3., 2. ** -40]
        query = query_store[:, ::2]
        query.setflags(write=False)
        source.setflags(write=False)
        translation.setflags(write=False)
        saved = query.copy(), source.copy(), translation.copy()
        self.assertFalse(query.flags.c_contiguous)
        self.assertTrue(side_evidence._axis_bound_inputs_safe(query, source))
        np.testing.assert_array_equal(self.actual_hits(query, source, scale, translation),
            original_oriented_hits(query, source, scale, translation))
        for actual, expected in zip((query, source, translation), saved):
            np.testing.assert_array_equal(actual, expected)
            self.assertFalse(actual.flags.writeable)

    def test_unknown_input_kinds_and_dynamic_values_use_full_original_norm(self):
        class ArraySubclass(np.ndarray):
            pass

        ordinary = np.asarray([[10., 0.], [20., 0.], [0., 0.]])
        variants = [(ordinary.astype(np.float32), np.zeros_like(ordinary, dtype=np.float32), 1., np.zeros(2)),
                    (ordinary.view(ArraySubclass), np.zeros_like(ordinary), 1., np.zeros(2)),
                    (ordinary, np.zeros_like(ordinary), np.float32(1.), np.zeros(2)),
                    (ordinary, np.zeros_like(ordinary), 1., np.zeros(2, np.float32)),
                    (ordinary, np.zeros_like(ordinary), 1., [0., 0.]),
                    (ordinary, np.zeros((1, 2)), 1., np.zeros(2)),
                    (ordinary, np.zeros_like(ordinary), 2. ** 101, np.zeros(2)),
                    (ordinary, np.zeros_like(ordinary), 1., np.asarray([2. ** -101, 0.]))]
        norm = np.linalg.norm
        for query, source, scale, translation in variants:
            with self.subTest(dtype=query.dtype, kind=type(query), scale=scale, translation=translation):
                expected = original_oriented_hits(query, source, scale, translation)
                with patch.object(side_evidence.np.linalg, 'norm', wraps=norm) as calls:
                    actual = self.actual_hits(query, source, scale, translation)
                np.testing.assert_array_equal(actual, expected)
                self.assertEqual(calls.call_count, 1)
                self.assertEqual(calls.call_args.args[0].shape, query.shape)

    def test_fallback_preserves_nan_overflow_underflow_warnings_and_raises(self):
        zero = np.zeros((1, 2))
        signaling_nan = np.asarray([0x7ff0000000000001], dtype=np.uint64).view(np.float64)[0]
        cases = [
            (np.asarray([[10., np.nan]]), zero, 1., np.zeros(2)),
            (np.asarray([[10., signaling_nan]]), zero, 1., np.zeros(2)),
            (np.asarray([[10., np.inf]]), zero, 1., np.zeros(2)),
            (np.asarray([[10., 0.]]), zero, np.inf, np.zeros(2)),
            (np.asarray([[10., 0.]]), zero, 1., np.asarray([np.inf, np.nan])),
            (zero, np.asarray([[1e308, 1.]]), 2., np.zeros(2)),  # finite multiply overflow
            (zero, np.asarray([[1e308, 1.]]), 1., np.asarray([1e308, 0.])),  # finite add overflow
            (np.asarray([[1e200, 0.]]), zero, 1., np.zeros(2)),  # square overflow
            (np.asarray([[10., 0.]]), np.asarray([[1., 1e-200]]), 1e-200, np.zeros(2)),
            (np.asarray([[10., 1e-200]]), zero, 1., np.zeros(2)),  # norm square underflow
            (np.asarray([[10., np.nextafter(0., 1.)]]), zero, 1., np.zeros(2)),
            (np.asarray([[10., 0.]]), zero, 1., np.asarray([0., 1e-200])),
        ]

        def outcome(function, args, mode):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                with np.errstate(all=mode):
                    try:
                        result, error = function(*args), None
                    except FloatingPointError as exc:
                        result, error = None, (type(exc), str(exc))
            return result, error, [(warning.category, str(warning.message)) for warning in caught]

        for mode in ('warn', 'raise'):
            for args in cases:
                with self.subTest(mode=mode, query=args[0].tolist(), scale=args[2]):
                    expected, old_error, old_warnings = outcome(original_oriented_hits, args, mode)
                    actual, new_error, new_warnings = outcome(self.actual_hits, args, mode)
                    self.assertEqual(old_error, new_error)
                    self.assertEqual(old_warnings, new_warnings)
                    if expected is not None:
                        np.testing.assert_array_equal(actual, expected)

    def test_strict_raise_mode_always_uses_original_full_norm_without_changing_seterr(self):
        query, source = np.full((4, 2), 20.), np.zeros((4, 2))
        norm = np.linalg.norm
        eligible = side_evidence._axis_bound_inputs_safe(query, source)
        self.assertTrue(eligible)
        with np.errstate(all='raise'):
            settings = np.geterr()
            with patch.object(side_evidence.np.linalg, 'norm', wraps=norm) as calls:
                actual = side_evidence._oriented_seed_hits(query, source, 1., np.zeros(2), axis_safe=eligible)
            self.assertEqual(np.geterr(), settings)
        np.testing.assert_array_equal(actual, np.zeros(4, bool))
        self.assertEqual(calls.call_args.args[0].shape, query.shape)


class LocalStructureIndexTests(unittest.TestCase):
    def setUp(self):
        self.entry = graph()
        _, self.points, self.rays, self.pairs = self.entry
        self.query = self.points * .45 + [400., 270.]

    def assert_probe_equal(self, entries, q, qr, qpairs, **kwargs):
        index = LocalStructureIndex(entries, **kwargs)
        ordinary = probe(entries, q, qr, qpairs)
        indexed = probe(entries, q, qr, qpairs, index=index)
        self.assertEqual(ordinary.rows, indexed.rows)
        self.assertEqual(ordinary.competitors, indexed.competitors)
        self.assertEqual(ordinary.scan_complete, indexed.scan_complete)
        self.assertEqual(ordinary.seed_count, indexed.search_counts['preparedSeedsScreened'])
        self.assertEqual(ordinary.seed_count, indexed.seed_count + indexed.search_counts['preparedSeedsRejected'])
        return ordinary, indexed

    def test_actual_probe_preserves_all_rows_and_greedy_order(self):
        entries = [self.entry, graph('rival', offset=(500, 300)),
                   graph('map29', offset=(100, 450), legacy=True)]
        ordinary, indexed = self.assert_probe_equal(entries, self.query, self.rays, self.pairs)
        self.assertTrue(ordinary.rows)
        self.assertEqual({row['mapId'] for row in indexed.competitors}, {'native', 'rival', 'map29'})
        self.assertEqual(indexed.search_counts['preparedIndexMaps'], 3)
        self.assertTrue(all(not row['identityVerified'] for row in indexed.rows))

    def test_impossible_seeds_are_screened_before_the_actual_exact_probe(self):
        item, points, rays, pairs = graph('different-lengths')
        for axis, replacements in ((0, {30: 30, 150: 140, 210: 250, 330: 710}),
                                   (1, {30: 30, 90: 145, 150: 330, 270: 750})):
            points[:, axis] = [replacements[value] for value in points[:, axis]]
        old, new = self.assert_probe_equal([(item, points, rays, pairs)],
                                           self.query, self.rays, self.pairs)
        self.assertGreater(old.seed_count, 0)
        self.assertGreater(new.search_counts['preparedSeedsRejected'], 0)
        self.assertLess(new.seed_count, old.seed_count)

    def test_source_and_query_rounding_on_both_sides_of_half_cells(self):
        for phase in (np.nextafter(2.5, 0.), 2.5, np.nextafter(2.5, np.inf)):
            entry = graph(offset=(phase, phase))
            prepared = LocalStructureIndex([entry]).members[0]
            compatible = np.min(np.sum(self.rays[:, None] * prepared.rays[None], axis=3), axis=2) >= .95
            for scale in (8 / 60, .45, 1.2):
                shifts = np.asarray([[400., 270.]])
                scales = np.asarray([scale])
                q = prepared.points * scale + shifts[0]
                q[:, 0] += np.nextafter(3., 0.)
                upper = possible_turns(q, scales, shifts, compatible, prepared)
                self.assertTrue(np.all(upper >= exact_turns(q, scales, shifts, compatible, prepared.points)))

    def test_exact_probe_equivalence_on_partial_and_perturbed_measurements(self):
        rng = np.random.default_rng(361)
        for scale in (.14, .45, 1.2):
            for noise in (0., .7, 2.9):
                with self.subTest(scale=scale, noise=noise):
                    q = self.points * scale + [400., 270.]
                    q = q + rng.uniform(-noise, noise, q.shape)
                    self.assert_probe_equal([self.entry], q, self.rays, self.pairs)
        # Ordinary partial observations retain true next pairs only.
        self.assert_probe_equal([self.entry], self.query[:6], self.rays[:6], self.pairs[:5])

    def test_lower_bound_at_grid_and_three_pixel_boundaries(self):
        prepared = LocalStructureIndex([self.entry]).members[0]
        compatible = np.min(np.sum(self.rays[:, None] * self.rays[None], axis=3), axis=2) >= .95
        scales = np.asarray([.13333333333333333, .45, .5, 1.2])
        shifts = np.asarray([[400., 270.], [400., 270.], [400., 270.], [400., 270.]])
        for epsilon in (0., np.nextafter(3., 0.), 3., np.nextafter(3., np.inf)):
            for phase in (-.5, 0., .5):
                q = self.query + [epsilon, phase * prepared.cell * .45]
                upper = possible_turns(q, scales, shifts, compatible, prepared)
                exact = exact_turns(q, scales, shifts, compatible, prepared.points)
                self.assertTrue(np.all(upper >= exact), (epsilon, phase, upper, exact))

    def test_saved_fields_are_lower_than_independent_exact_point_distances(self):
        prepared = LocalStructureIndex([graph(offset=(2.5, 2.5))]).members[0]
        rng = np.random.default_rng(901)
        for group, field in enumerate(prepared.lower):
            xy = np.column_stack((rng.integers(0, field.shape[1], 500),
                                  rng.integers(0, field.shape[0], 500)))
            actual = np.linalg.norm(xy[:, None] * prepared.cell
                - prepared.points[prepared.groups == group][None], axis=2).min(axis=1)
            self.assertTrue(np.all(field[xy[:, 1], xy[:, 0]] <= actual))

    def test_random_bounds_and_large_translation_never_erase_exact_hits(self):
        rng = np.random.default_rng(47)
        prepared = LocalStructureIndex([self.entry]).members[0]
        compatible = np.min(np.sum(self.rays[:, None] * self.rays[None], axis=3), axis=2) >= .95
        scales = rng.uniform(.14, 2., 350)
        shifts = rng.uniform(-400, 700, (len(scales), 2))
        scales[-1], shifts[-1] = .45, [400., 270.]
        upper = possible_turns(self.query, scales, shifts, compatible, prepared)
        self.assertTrue(np.all(upper >= exact_turns(self.query, scales, shifts, compatible, prepared.points)))
        for shift in (1e12, 1e16, 1e100):
            q = prepared.points * .45 + shift
            scales, shifts = np.asarray([.45]), np.full((1, 2), shift)
            upper = possible_turns(q, scales, shifts, compatible, prepared)
            self.assertTrue(np.all(upper >= exact_turns(q, scales, shifts, compatible, prepared.points)))

    def test_outside_field_and_uncertain_arithmetic_remain_possible(self):
        prepared = LocalStructureIndex([self.entry]).members[0]
        compatible = np.ones((len(self.query), len(self.points)), bool)
        scales = np.asarray([1e-300, 1., float('inf'), float('nan')])
        shifts = np.asarray([[1e300, 1e300], [1e8, 1e8], [0, 0], [0, 0]])
        upper = possible_turns(self.query, scales, shifts, compatible, prepared)
        np.testing.assert_array_equal(upper, len(self.query))

    def test_memory_budget_coarsens_or_keeps_full_exact_fallback(self):
        for budget in (0, 64, 4096):
            with self.subTest(budget=budget):
                index = LocalStructureIndex([self.entry], max_lower_bytes=budget)
                self.assertLessEqual(index.lower_bytes, budget)
                ordinary, indexed = self.assert_probe_equal([self.entry], self.query, self.rays,
                                                           self.pairs, max_lower_bytes=budget)
                self.assertEqual(indexed.rows, ordinary.rows)

    def test_changed_points_rays_pairs_and_unit_use_ordinary_probe(self):
        index = LocalStructureIndex([self.entry])
        for part in ('points', 'rays', 'pairs', 'unit', 'sourceProduct'):
            with self.subTest(part=part):
                changed = copy.deepcopy(self.entry)
                if part == 'points':
                    changed[1][0, 0] += 1
                elif part == 'rays':
                    changed[2][0, 0, 0] += .001
                elif part == 'pairs':
                    changed[3][0, 1] = 2
                elif part == 'unit':
                    changed[0]['unit'] += .01
                else:
                    changed[0]['sourceProduct'] += '-replacement'
                self.assertIsNone(index.prepared_for(changed))
                old = probe([changed], self.query, self.rays, self.pairs)
                new = probe([changed], self.query, self.rays, self.pairs, index=index)
                self.assertEqual(old.rows, new.rows)
                self.assertEqual(old.seed_count, new.seed_count)
                self.assertEqual(new.search_counts['preparedIndexFallbacks'], 1)

    def test_preparation_owns_readonly_snapshots_and_floor_partition_shares_them(self):
        other = graph('native', floor='2')
        index = LocalStructureIndex([self.entry, other])
        member = index.members[0]
        self.points[0, 0] += 1
        self.assertNotEqual(self.points[0, 0], member.points[0, 0])
        for value in (member.points, member.rays, member.pairs, member.deltas,
                      member.denoms, member.groups, member.lower):
            self.assertFalse(value.flags.writeable)
            with self.assertRaises(ValueError):
                value.setflags(write=True)
        floor = index.for_floor('1')
        self.assertEqual(floor.domain, (('native', '1'),))
        self.assertIs(floor.members[0], member)

    def test_missing_directory_source_cannot_become_complete_competition(self):
        index = LocalStructureIndex([self.entry], competition_domain=(('native', '1'), ('missing', '1')))
        self.assertEqual(index.directory_gaps, (('missing', '1'),))
        work = probe([self.entry], self.query, self.rays, self.pairs, index=index)
        self.assertFalse(work.scan_complete)
        rows, diagnostics = work.finish()
        self.assertFalse(diagnostics['complete'])
        self.assertEqual(diagnostics['reason'], 'side-resource-directory-incomplete')
        self.assertTrue(all(not row['competitionComplete'] for row in rows))

    def test_runtime_source_removal_keeps_the_index_domain_unresolved(self):
        rival = graph('legacy-rival', legacy=True)
        index = LocalStructureIndex([self.entry, rival])
        self.assertFalse(index.directory_gaps)
        locator = SimpleNamespace(entries=[self.entry], floors=('1',), side_search={},
            profile_for_map=lambda mid: 'shared-structure', side_door_error=None, side_search_error=None,
            _side_door_contradicted=lambda row: False,
            _observe=lambda frame: (None, [dict(point=p, rays=r) for p, r in
                zip(self.query, self.rays)], self.pairs),
            verify=lambda frame, row, **kw: dict(success=True, residual=dict(verified=True)))
        frame = np.zeros((1000, 1600, 3), np.uint8)
        work = MapLocalEntryIdentity.prepare_rank(locator, frame, '1', prepared_index=index)
        probed = []
        while (job := work.next_job()) is not None:
            if job.phase == 'probe':
                probed.append(job.map_id)
            job.run()
        rows, diagnostics = work.finish()
        self.assertEqual(probed, ['native'])
        self.assertEqual(index.domain, (('native', '1'), ('legacy-rival', '1')))
        self.assertEqual(work.index_directory_gaps, (('legacy-rival', '1'),))
        self.assertFalse(diagnostics['complete'])
        self.assertFalse(diagnostics['verificationComplete'])
        self.assertTrue(rows)
        self.assertTrue(all(not row['competitionComplete'] for row in rows))

    def test_runtime_domain_coverage_uses_the_same_requested_scope(self):
        index = LocalStructureIndex([self.entry, graph('same-floor-rival'), graph('other-floor', floor='2')])
        locator = SimpleNamespace(entries=[self.entry], floors=('1', '2'), side_search={})
        frame = np.zeros((100, 100, 3), np.uint8)
        for scope in (dict(map_id='native'), dict(candidate_map_ids={'native'})):
            with self.subTest(scope=scope):
                work = MapLocalEntryIdentity.prepare_rank(locator, frame, '1', prepared_index=index, **scope)
                self.assertFalse(work.index_directory_gaps)
        full = MapLocalEntryIdentity.prepare_rank(locator, frame, '1', prepared_index=index)
        self.assertEqual(full.index_directory_gaps, (('same-floor-rival', '1'),))

    def test_tiny_finite_unit_omits_unreliable_atlas_without_overflow(self):
        for unit in (1e-309, 1e-300):
            with self.subTest(unit=unit):
                item, points, rays, pairs = copy.deepcopy(self.entry)
                item['unit'] = unit
                entry = item, points, rays, pairs
                index = LocalStructureIndex([entry])
                self.assertIsNone(index.members[0].lower)
                ordinary = probe([entry], self.query, self.rays, self.pairs)
                indexed = probe([entry], self.query, self.rays, self.pairs, index=index)
                self.assertEqual(ordinary.rows, indexed.rows)
                self.assertEqual(ordinary.scan_complete, indexed.scan_complete)

    def test_huge_integer_canvas_omits_atlas_and_keeps_normal_probe(self):
        item, points, rays, pairs = copy.deepcopy(self.entry)
        item['canvas']['width'] = 10 ** 400
        entry = item, points, rays, pairs
        ordinary = probe([entry], self.query, self.rays, self.pairs)
        self.assertEqual(len(ordinary.rows), 1)
        self.assertTrue(ordinary.scan_complete)
        index = LocalStructureIndex([entry])
        self.assertIsNone(index.members[0].lower)
        indexed = probe([entry], self.query, self.rays, self.pairs, index=index)
        self.assertEqual(ordinary.rows, indexed.rows)
        self.assertEqual(ordinary.scan_complete, indexed.scan_complete)

    def test_existing_door_search_keeps_its_semantics(self):
        entry = graph(floor='2')
        item, points, rays, pairs = entry
        door = dict(boundaryCenter=[30, 30])
        original = {('native', '2'): prepare_arrays(item, points, rays, pairs, door)}
        old = probe([entry], self.query, self.rays, self.pairs, floor='2', side_search=original)
        new = probe([entry], self.query, self.rays, self.pairs, floor='2', side_search=original,
                    index=LocalStructureIndex([entry]))
        self.assertEqual(old.rows, new.rows)
        for key in old.search_counts:
            self.assertEqual(old.search_counts[key], new.search_counts[key])
        self.assertEqual(new.search_counts['preparedIndexMaps'], 0)

    def test_expired_deadline_cannot_complete_an_indexed_probe(self):
        deadline = time.perf_counter() - 1
        old = probe([self.entry], self.query, self.rays, self.pairs, deadline=deadline)
        new = probe([self.entry], self.query, self.rays, self.pairs, deadline=deadline,
                    index=LocalStructureIndex([self.entry]))
        self.assertEqual(old.rows, new.rows)
        self.assertFalse(new.scan_complete)

    def test_cancelled_preparation_and_invalid_domain_are_explicit(self):
        with self.assertRaisesRegex(ValueError, 'preparation-cancelled'):
            LocalStructureIndex([self.entry], cancelled=lambda: True)
        for domain in ((('other', '1'),), (('native', '1'), ('native', '1'))):
            with self.assertRaises(ValueError):
                LocalStructureIndex([self.entry], competition_domain=domain)

    def test_locator_entry_transparently_accepts_the_explicit_index(self):
        locator = SimpleNamespace(entries=[self.entry], floors=('1',), side_search={})
        index = LocalStructureIndex(locator.entries)
        frame = np.zeros((100, 100, 3), np.uint8)
        ordinary = MapLocalEntryIdentity.prepare_rank(locator, frame, '1')
        indexed = MapLocalEntryIdentity.prepare_rank(locator, frame, '1', prepared_index=index)
        self.assertIsNone(ordinary.prepared_index)
        self.assertIs(indexed.prepared_index, index)
        self.assertEqual(ordinary.entries, indexed.entries)

    def test_closing_indexed_work_cannot_complete_unsearched_rivals(self):
        entries = [self.entry, graph('legacy-rival', legacy=True)]
        locator = SimpleNamespace(entries=entries, floors=('1',), side_search={},
            profile_for_map=lambda mid: 'shared-structure', side_door_error=None, side_search_error=None,
            _side_door_contradicted=lambda row: False,
            _observe=lambda frame: (None, [dict(point=p, rays=r) for p, r in
                zip(self.query, self.rays)], self.pairs))
        frame = np.zeros((1000, 1600, 3), np.uint8)
        work = MapLocalEntryIdentity.prepare_rank(locator, frame, '1',
            prepared_index=LocalStructureIndex(entries))
        work.next_job().run()  # Actual observation transfer, then first full probe.
        work.next_job().run()
        self.assertEqual(work.next_job().map_id, 'legacy-rival')
        work.close()
        rows, diagnostics = work.finish()
        self.assertFalse(diagnostics['complete'])
        self.assertFalse(diagnostics['verificationComplete'])
        self.assertTrue(all(not row['competitionComplete'] for row in rows))
        self.assertIsNone(work.next_job())


if __name__ == '__main__':
    unittest.main()
