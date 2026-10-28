"""Synthetic regression inputs for route selection; no GUI or private assets.

Controller, refresh, worker consumer/pose gates and AlignmentCoreV2 are real
production methods. Static adapters/layer availability, renderer/pixel sink,
settings IO and observation entry are controlled boundaries. This is not a
full-poll replay or an image-algorithm/real-game acceptance test.
"""
import ast
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np


APP = Path(__file__).parents[1] / 'app'
SHARED = 'shared-structure'


def load_core():
    name = '_route_selection_regression_core'
    spec = importlib.util.spec_from_file_location(name, APP / 'alignment_core_v2.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_core()


def load_round_group():
    name = '_route_selection_regression_group'
    spec = importlib.util.spec_from_file_location(name, APP / 'round_layer_group.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.RoundLayerGroup


RoundLayerGroup = load_round_group()


def subject_type():
    path = APP / 'identityv_daily_assistant.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    names = {
        'route_profile_id', '_shown_route_profile', 'route_selection_snapshot',
        'select_route_profile', '_apply_route_profile_setting',
        '_refresh_shared_route_pixels', '_shared_display_bridge',
        '_display_authorization_is_current',
        '_run_alignment_v2_for_current_frame_unlocked',
        '_alignment_v2_current_frame_candidate',
        '_alignment_v2_worker_pose_stale', '_alignment_v2_note_worker_failure',
        '_active_round_layer_group', '_round_group_floor_view', '_aligned_render_spec',
    }
    methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == len(names) + 1  # Property getter and setter.
    wrapper = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    namespace = dict(np=np, cv2=cv2, hashlib=hashlib, replace=replace,
        SHARED_STRUCTURE_PROFILE_ID=SHARED, SOURCE_STRUCTURE_PROFILE_ID='source-structure',
        DEFAULT_ROUTE_PROFILE_ID='author-a', ROUTE_PROFILE_IDS_BY_LABEL={},
        normalize_route_profile_id=lambda profile: profile,
        gallery_of=lambda profile: None, AlignmentCandidate=CORE.AlignmentCandidate,
        RoundLayerGroup=RoundLayerGroup, ROUND_LAYER_FLOORS=('B1', '1', '2'))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace['Subject']


def scale(value):
    return np.diag([float(value), float(value), 1.0])


class RouteSelectionRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Subject = subject_type()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='route-selection-regression-')
        self.addCleanup(self.temporary.cleanup)
        self.settings_path = Path(self.temporary.name) / 'settings.json'
        s = self.s = self.Subject()
        s.preferred_route_profile_id = 'author-a'
        s.game_mode = 'nightmare'
        s.mode_route_profiles = {'difficult': 'other-mode-author'}
        s._mode_route_options = lambda: tuple((p, p) for p in ('author-a', 'author-b', 'author-c'))
        s.author_route_catalog = None
        s._shared_structure_mode_enabled = lambda: True
        s.gate = SimpleNamespace(confirmed_id='nightmare-03')
        s.current_floor, s.active_map_cycle_id, s.current_frame_generation = '1', 5, 6
        s.surface_visibility_generation = s._alignment_v2_reset_generation = 1
        s.current_frame = np.zeros((8, 8, 3), np.uint8)
        s.current_bounds = (0, 0, 8, 8)
        s.current_alignment_candidate = {}
        s.map_is_open = True
        self.availability = {('author-b', '2'): False}

        def effective(map_id, floor):
            selected = s.preferred_route_profile_id
            return selected if self.availability.get((selected, str(floor)), True) else SHARED
        s._effective_route_profile = effective
        self.bases = {SHARED: np.eye(3), 'author-a': scale(2),
                      'author-b': scale(3), 'author-c': scale(4)}
        s.author_coordinate_adapters = SimpleNamespace(
            matrix_profile_to_canonical=lambda profile, *args: self.bases[profile].copy())
        s._shared_display_layer = lambda profile, map_id, floor: dict(
            matrixDisplayToReference=s._shared_display_bridge(profile, map_id, floor))
        self.root = np.array([[1., 0., 23.], [0., 1., -11.], [0., 0., 1.]])
        self.group = s.round_layer_group = SimpleNamespace(
            map_id='nightmare-03', route_profile_id='author-a', root_pose=self.root.copy(),
            relations={'1': np.eye(3), '2': np.eye(3)}, generation=7,
            tracking_by_floor={}, render_profile_verified_floors={'1', '2'}, displayed_overlay=None)
        s.alignment_core_v2 = CORE.AlignmentCoreV2(clock=lambda: 1.0)
        s.route_profile_var = Mock(get=lambda: 'author-a')
        s._route_profile_label = lambda profile: profile
        s._sync_route_settings_ui = Mock()
        s._refresh_visible_sheet_after_route_setting = Mock()
        s._hide_spatial_route = Mock()
        s._set_status = Mock()
        s._log = s._log_core_chain_event = Mock()
        # Settings IO is isolated, but the assertion reads actual saved JSON.
        def save():
            self.settings_path.write_text(json.dumps(dict(
                routeProfileId=s.preferred_route_profile_id,
                modeRouteProfiles=s.mode_route_profiles)), encoding='utf-8')
            return True
        s._save_settings = save
        # A selection must never identify, fit, clear the root or reset a round.
        for name in ('_invalidate_alignment_work', '_alignment_v2_reset_round',
                     '_clear_round_layer_group', '_start_alignment', '_start_capture',
                     '_alignment_v2_structure_fit'):
            setattr(s, name, Mock(side_effect=AssertionError('display-only selection invoked ' + name)))

    def assert_canonical_unchanged(self):
        np.testing.assert_array_equal(self.group.root_pose, self.root)
        self.assertEqual(self.group.generation, 7)
        for relation in self.group.relations.values():
            np.testing.assert_array_equal(relation, np.eye(3))
        self.assertEqual(self.s.gate.confirmed_id, 'nightmare-03')

    def test_bad_render_cache_cannot_turn_a_saved_selection_into_failure(self):
        bad_inputs = (
            {'translationMaskMatrix': 'malformed-cache'},
            {'translationMaskMatrix': scale(2).tolist(), 'verifiedPlayerAuthorPoint': [1., 2., 3.]},
        )
        for bad in bad_inputs:
            with self.subTest(cache=tuple(bad)):
                self.setUp()
                mask = np.ones((2, 2), np.uint8)
                tracking = dict(bad, translationMask=mask,
                                poseVerifiedAgainstCanonicalGeometry=True)
                self.group.tracking_by_floor['1'] = tracking
                receipt = self.s.select_route_profile('author-c')
                self.assertTrue(receipt['success'])
                self.assertEqual(self.s.preferred_route_profile_id, 'author-c')
                saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
                self.assertEqual(saved['routeProfileId'], 'author-c')
                self.assertEqual(saved['modeRouteProfiles']['nightmare'], 'author-c')
                self.assertNotIn('translationMaskMatrix', tracking)
                self.assertNotIn('verifiedPlayerAuthorPoint', tracking)
                self.assertTrue(tracking['poseVerifiedAgainstCanonicalGeometry'])
                self.assertIs(tracking['translationMask'], mask)
                self.assert_canonical_unchanged()

    def test_two_floor_fallback_then_another_author_preserves_each_native_basis(self):
        original_points = {}
        for floor in ('1', '2'):
            point = np.array([5., 7., 1.])
            matrix = self.root @ self.bases['author-a']
            original_points[floor] = matrix @ point
            self.group.tracking_by_floor[floor] = dict(
                translationMaskMatrix=matrix.tolist(), layoutAnchorMatrix=matrix.tolist(),
                verifiedPlayerAuthorPoint=point[:2].tolist(),
                poseVerifiedAgainstCanonicalGeometry=True)
        self.assertTrue(self.s.select_route_profile('author-b')['success'])
        for floor, profile in (('1', 'author-b'), ('2', SHARED)):
            np.testing.assert_allclose(self.group.tracking_by_floor[floor]['translationMaskMatrix'],
                                       self.root @ self.bases[profile])
        self.assertTrue(self.s.select_route_profile('author-c')['success'])
        for floor in ('1', '2'):
            tracking = self.group.tracking_by_floor[floor]
            matrix = np.asarray(tracking['translationMaskMatrix'])
            np.testing.assert_allclose(matrix, self.root @ self.bases['author-c'])
            np.testing.assert_allclose(tracking['layoutAnchorMatrix'], matrix)
            point = np.array([*tracking['verifiedPlayerAuthorPoint'], 1.])
            np.testing.assert_allclose(matrix @ point, original_points[floor])
        self.assert_canonical_unchanged()

    def prepare_authorized_display(self):
        s = self.s
        core = s.alignment_core_v2
        core.begin_observation(cycle_id=5, map_id='nightmare-03', floor='1', frame_generation=6)
        candidate = CORE.AlignmentCandidate('nightmare-03', '1', self.root, 'initial-structure-v2', 6,
            current_frame_verified=True, residual_verified=True, global_residual_verified=True,
            metadata={'routeProfileId': 'author-a'})
        grant = core.authorize(core.adjudicate(candidate))
        self.assertTrue(core.commit(grant))
        s.current_alignment_candidate = dict(accepted=True, mapId='nightmare-03', floor='1', cycleId=5,
            routeProfileId='author-a', displayAuthorizationId=grant.authorization_id,
            verifiedFrame=s.current_frame.copy())
        s.drag_transform = self.root @ self.bases['author-a']
        s.drag_route_profile_id = 'author-a'
        # Renderer/sink are explicit IO substitutes; the refresh and grant
        # checks that reach them remain production code.
        s._aligned_render_spec = lambda m, f, native, profile_id: dict(
            routeSource=np.ones((2, 2, 4), np.uint8),
            matrixSourceToClient=native @ self.bases[profile_id], routeProfileId=profile_id)
        s._build_display_overlay = lambda *args, **kwargs: (np.ones((8, 8, 4), np.uint8), {})
        s._alignment_v2_show_overlay = Mock(return_value=True)
        self.assertTrue(s.select_route_profile('author-b')['success'])
        s._alignment_v2_show_overlay.assert_called_once()
        self.assertEqual(s.current_alignment_candidate['routeProfileId'], 'author-b')
        self.assertIs(core.display_authorization, grant)
        core.begin_observation(cycle_id=5, map_id='nightmare-03', floor='1', frame_generation=7)
        s.current_frame_generation = 7
        # Actual observation creation is outside this consumer unit contract.
        s._alignment_v2_observation = Mock(return_value=core.active_observation)
        s._alignment_v2_seed_group_pose = Mock(return_value=True)
        s._alignment_v2_runtime_is_enabled = lambda: True
        s._current_frame_floor_conflicts = lambda floor: False
        s._active_round_layer_group = lambda map_id: self.group
        s._front_group_provisional_lock_context = lambda map_id: None
        s._consume_b1_floor_transition_evidence = lambda m, f, p, event: event
        s._alignment_v2_structure_enabled = lambda: True
        s._alignment_v2_retry_mode = lambda mode: True
        s._alignment_v2_front_pose_seed = lambda *args: None
        s._alignment_v2_note_worker_failure = Mock(wraps=s._alignment_v2_note_worker_failure)
        s._hide_spatial_route.reset_mock()
        return grant

    def check_old_author_bound_worker(self, recovery=False):
        grant = self.prepare_authorized_display()
        s = self.s
        core = s.alignment_core_v2
        observation = core.active_observation
        root_before = core.pose_by_map['nightmare-03'].root_matrix.copy()
        fit = dict(success=True, coordinateSpace='author-render-to-client', routeProfileId='author-a',
                   matrixSourceToClient=self.root @ self.bases['author-a'],
                   selectedRouteCurrentFrameResidual={'verified': True})
        event = dict(alignmentV2WorkerFit=fit, routeProfileId='author-a', sourceFrameGeneration=7,
            sensorMode='group-recovery', basePoseGeneration=0, baseGroupGeneration=7,
            workerToken=(5, 'nightmare-03', '1', 7, ('display-A',), 1),
            sourceSurfaceGeneration=1, resetGeneration=1,
            basePoseRootFingerprint=hashlib.sha256(np.ascontiguousarray(self.root).tobytes()).hexdigest()[:20])
        if recovery:
            fit.pop('coordinateSpace')
            fit.pop('routeProfileId')
            event.update(alignmentV2WorkerRecoveryFit=fit,
                alignmentV2WorkerBaseRender=self.root @ self.bases['author-a'],
                alignmentV2WorkerBaseCanonical=self.root.copy(),
                alignmentV2WorkerRenderMatrix=fit['matrixSourceToClient'],
                alignmentV2WorkerRoute=np.ones((2, 2, 4), np.uint8))
        current = dict(cycle_id=5, map_id='nightmare-03', floor='1', frame_generation=7)
        self.assertTrue(core.can_display(**current))
        self.assertFalse(s._alignment_v2_worker_pose_stale('nightmare-03', event))
        result = s._run_alignment_v2_for_current_frame_unlocked('nightmare-03', '1', '1', event)
        self.assertTrue(result['handled'])
        s._alignment_v2_observation.assert_not_called()
        s._alignment_v2_note_worker_failure.assert_not_called()
        s._hide_spatial_route.assert_not_called()
        self.assertIs(core.active_observation, observation)
        self.assertIs(core.display_authorization, grant)
        self.assertTrue(core.can_display(**current))
        np.testing.assert_array_equal(core.pose_by_map['nightmare-03'].root_matrix, root_before)
        self.assertEqual(core.pose_by_map['nightmare-03'].route_profile_id, 'author-b')
        self.assertEqual(s.current_alignment_candidate['routeProfileId'], 'author-b')
        self.assert_canonical_unchanged()

    def test_old_author_bound_worker_cannot_hide_the_new_author_display(self):
        self.check_old_author_bound_worker()

    def test_old_author_recovery_binding_cannot_hide_the_new_author_display(self):
        self.check_old_author_bound_worker(recovery=True)

    def test_shared_floor_view_and_solver_use_native_not_author_coordinates(self):
        """Real view/render algebra and solve; geometric validators are controlled."""
        s = self.s
        canonical = np.array([[.5, 0., 23.], [0., .5, -11.], [0., 0., 1.]])
        self.bases[SHARED] = scale(2)
        self.bases['author-b'] = np.array([[3., 0., 9.], [0., 4., -6.], [0., 0., 1.]])
        s.preferred_route_profile_id = 'author-b'
        group = s.round_layer_group = RoundLayerGroup(
            'nightmare-03', 'author-b', '1', canonical.copy(),
            relations={'1': np.eye(3), '2': np.eye(3)}, cycle_id=5)
        group.verified_floors.update(('1', '2'))
        group.canonical_geometry_verified_floors.update(('1', '2'))
        reference = SimpleNamespace(metadata={'canvas': {'coordinateSpace': 'synthetic-native'}},
                                    shape=(8, 8))
        s._shared_structure_reference = lambda *args: reference
        s.pack = SimpleNamespace(pack_id='synthetic-pack')
        pixels = np.ones((8, 8, 4), np.uint8)
        def layer(profile, map_id, floor):
            return dict(routeSource=pixels, routeProfileId=profile,
                matrixDisplayToReference=s._shared_display_bridge(profile, map_id, floor),
                displayKind='shared-structure' if profile == SHARED else 'author-route',
                displayLabel=profile)
        s._shared_display_layer = layer
        s._active_open_cycle_zoom_transform = lambda map_id: np.eye(3)
        s._active_open_cycle_drag_offset = lambda map_id: np.zeros(2)
        s._alignment_v2_runtime_is_enabled = lambda: True
        native = canonical @ self.bases[SHARED]
        view = s._round_group_floor_view('nightmare-03', '1', render_overlay=False)
        np.testing.assert_allclose(view['recognitionMatrixSourceToClient'], native)
        np.testing.assert_allclose(view['matrixSourceToClient'], canonical @ self.bases['author-b'])
        self.assertFalse(np.allclose(view['matrixSourceToClient'], native))
        # Global author-b metadata cannot strand its missing floor-2 layer.
        fallback = s._round_group_floor_view('nightmare-03', '2', render_overlay=False)
        self.assertEqual(fallback['routeProfileId'], SHARED)
        np.testing.assert_allclose(fallback['matrixSourceToClient'], native)
        self.assertIs(s._active_round_layer_group('nightmare-03', SHARED), group)

        route = np.ones((8, 8), np.uint8)
        product = SimpleNamespace(clean_mask=route, known_boundary=None)
        s._author_route_clean_product_for_route = lambda *args: product
        s._author_route_structure_reference_cached = lambda *args, **kwargs: (route, False, 0.)
        seen = []
        def graph(frame, clean, target, **kwargs):
            seen.append(np.asarray(target).copy())
            return dict(structureModel='rigid', rigid={'success': True, 'matrixSourceToClient': target},
                        graph={'regionalEdgeVerified': True}, acceptedPointCount=4)
        def partial(frame, clean, target, **kwargs):
            return dict(success=True, matrix=target, diagnostics={'planes': [1, 2, 3, 4]})
        def residual(frame, source, target, **kwargs):
            np.testing.assert_allclose(target, native)
            return {'verified': True}
        namespace = dict(np=np, cv2=cv2, time=time, graph_point_geometry=graph,
            partial_wall_geometry=partial, selected_route_current_frame_residual=residual,
            selected_scale_observation=lambda *args, **kwargs: None)
        path = APP / 'single_map_structure.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        solve = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'solve')
        exec(compile(ast.fix_missing_locations(ast.Module(body=[solve], type_ignores=[])), str(path), 'exec'), namespace)
        result = namespace['solve'](s, s.current_frame, 'nightmare-03', '1', 'author-b', route,
                                    _context={'query': np.ones((8, 8), np.uint8)})
        self.assertTrue(result['success'])
        self.assertEqual(len(seen), 1)
        np.testing.assert_allclose(seen[0], native)
        np.testing.assert_allclose(result['matrixSourceToClient'], native)
        self.assertEqual(result['coordinateSpace'], 'shared-structure-to-client')
        self.assertEqual(result['routeProfileId'], SHARED)
        self.assertFalse(result['selectedRouteCurrentFrameResidual']['poseVerifiedAgainstRenderedProfile'])
        for key in ('_v2WorkerBaseRender', '_v2WorkerRenderMatrix', '_v2WorkerRoute'):
            self.assertNotIn(key, result)
        np.testing.assert_array_equal(group.root_pose, canonical)
        # A first visit to floor 1 can recover via the entry locator rather
        # than the final accepted-candidate return. It consumes the same N
        # pixels, so neither success path may label them author validation.
        group.verified_floors = {'2'}
        group.canonical_geometry_verified_floors = {'2'}
        s._open_cycle_transient_motion_active = lambda map_id: False
        locator = SimpleNamespace(rank=Mock(return_value=(
            [dict(poseVerified=True, verification=dict(success=True,
                  matrixSourceToClient=native, residual={'verified': True}))], {})))
        s._alignment_entry_locator = lambda: locator
        namespace['graph_point_geometry'] = lambda *args, **kwargs: dict(
            structureModel=None, graph={'regionalEdgeVerified': False}, acceptedPointCount=0)
        namespace['partial_wall_geometry'] = lambda *args, **kwargs: dict(
            success=False, reason='controlled-local-miss', diagnostics={})
        entry_result = namespace['solve'](s, s.current_frame, 'nightmare-03', '1', 'author-b', route,
                                          _context={'query': np.ones((8, 8), np.uint8)})
        self.assertTrue(entry_result['success'])
        self.assertEqual(locator.rank.call_count, 1)
        np.testing.assert_allclose(entry_result['matrixSourceToClient'], native)
        self.assertEqual(entry_result['coordinateSpace'], 'shared-structure-to-client')
        self.assertEqual(entry_result['routeProfileId'], SHARED)
        entry_residual = entry_result['selectedRouteCurrentFrameResidual']
        self.assertFalse(entry_residual['poseVerifiedAgainstRenderedProfile'])
        self.assertTrue(entry_residual['poseVerifiedAgainstCanonicalGeometry'])
        np.testing.assert_array_equal(group.root_pose, canonical)


if __name__ == '__main__':
    unittest.main()
