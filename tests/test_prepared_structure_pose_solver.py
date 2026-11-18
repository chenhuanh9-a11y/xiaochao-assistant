"""Actual prepared resources through the unchanged numerical orchestration.

No identity/display authority is exercised. The black frame is a real negative
geometry input, not a mocked solver verdict; resource IO is prohibited in fit.
"""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

import fast_structure_alignment as fast
import resource_snapshot_io
from structure_pose_inputs import PoseInputsUnavailable, prepare_structure_pose_inputs
from structure_pose_solver import PreparedStructurePoseSolver
import test_structure_pose_resources as resource_fixture


class PreparedStructurePoseSolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resource_fixture.StructurePoseResourceTests.setUpClass()
        cls.addClassCleanup(resource_fixture.StructurePoseResourceTests.doClassCleanups)

    def setUp(self):
        self.fixture = resource_fixture.StructurePoseResourceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine
        self.engine.qt_native_base = self.fixture.bundle
        self.inputs = prepare_structure_pose_inputs(self.engine, self.fixture.map_id, '1',
            resource_revision=1, recovery_floors=('2',))

    @contextmanager
    def no_resource_io(self):
        def denied(*args, **kwargs):
            raise AssertionError('geometry-touched-resource-io')
        with ExitStack() as stack:
            for owner, name in ((self.engine, 'qt_assert_resource_current'),
                                (self.fixture.provider, '_assert_current'),
                                (self.engine.qt_fast_structure_source, 'read'),
                                (resource_snapshot_io, 'read_stable_file'),
                                (Path, 'stat'), (Path, 'resolve'), (Path, 'read_bytes'),
                                (Path, 'open')):
                stack.enter_context(patch.object(owner, name, side_effect=denied))
            yield

    def test_prepared_solver_reuses_original_math_with_no_resource_io(self):
        expected = self.fixture.host.fit(self.fixture.frame, self.fixture.map_id, '1', lambda: False)
        with self.no_resource_io():
            # Negative control: the old complete host is a resource operation.
            with self.assertRaisesRegex(AssertionError, 'geometry-touched-resource-io'):
                self.fixture.host.fit(self.fixture.frame, self.fixture.map_id, '1', lambda: False)
            host = PreparedStructurePoseSolver(self.inputs)
            with patch.object(fast, 'propose', wraps=fast.propose) as propose:
                actual = host.fit(self.fixture.frame, self.fixture.map_id, '1', lambda: False)
        self.assertFalse(actual['success'])
        self.assertEqual((actual['success'], actual['reason']), (expected['success'], expected['reason']))
        self.assertTrue(propose.called)
        self.assertIs(propose.call_args.args[1], self.inputs.reference('shared-structure', self.fixture.map_id, '1'))
        self.assertIsNone(host._fit_resources)

    def test_prepared_ram_survives_disk_damage_and_new_preparation_rejects_it(self):
        host = PreparedStructurePoseSolver(self.inputs)
        reference = self.inputs.reference('shared-structure', self.fixture.map_id, '1')
        original = reference.clean_mask.copy()
        before = host.fit(self.fixture.frame, self.fixture.map_id, '1', lambda: False)
        reference.path.write_bytes(b'changed-after-resource-preparation')
        with self.no_resource_io():
            after = host.fit(self.fixture.frame, self.fixture.map_id, '1', lambda: False)
        self.assertTrue(np.array_equal(reference.clean_mask, original))
        self.assertEqual((after['success'], after['reason']), (before['success'], before['reason']))
        with self.assertRaisesRegex(PoseInputsUnavailable, 'reference-decode-unavailable'):
            prepare_structure_pose_inputs(self.engine, self.fixture.map_id, '1', resource_revision=1)

    def test_scope_and_cancellation_are_memory_only(self):
        host = PreparedStructurePoseSolver(self.inputs)
        with self.no_resource_io():
            with self.assertRaisesRegex(ValueError, 'layer-not-prepared'):
                host.fit(self.fixture.frame, 'other-map', '1', lambda: False)
            cancelled = host.fit(self.fixture.frame, self.fixture.map_id, '1', lambda: True)
        self.assertFalse(cancelled['success'])
        self.assertEqual(cancelled['reason'], 'structure-work-obsolete')

    def test_numeric_constructor_rejects_live_steady_lifecycle(self):
        from client_idvb0059_adapter import Idvb0059Adapter
        current_calls = []
        with self.assertRaisesRegex(TypeError, 'steady'):
            PreparedStructurePoseSolver(self.inputs, steady=(Idvb0059Adapter(),
                ('runtime-context',), lambda context: current_calls.append(context) or False,
                ('display-signature',)))
        self.assertEqual(current_calls, [])


if __name__ == '__main__':
    unittest.main()
