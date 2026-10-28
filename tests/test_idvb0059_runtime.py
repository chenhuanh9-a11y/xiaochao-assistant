"""Floor seed ownership; solver replacement is limited to this state boundary."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import idvb0059_runtime as runtime
from round_layer_group import RoundLayerGroup
from shared_structure_reference import SharedStructureReference


class ScaleOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.frame=np.zeros((240,320,3),np.uint8)
        self.reference=SharedStructureReference(np.ones((80,80),np.uint8),
            np.ones((80,80),np.uint8),{},Path('test-resource'))
        self.group=RoundLayerGroup('map','shared-structure','1',np.diag([.7,.7,1.]),
            relations={'2':np.eye(3)})
        self.group.canonical_geometry_verified_floors={'1','2'}
        self.assistant=SimpleNamespace(
            _shared_structure_mode_enabled=lambda:True,
            _active_round_layer_group=lambda mid:self.group,
            _shared_structure_reference=lambda *args:self.reference,
            author_coordinate_adapters=SimpleNamespace(matrix_profile_to_canonical=lambda *args:np.eye(3)),
            _open_cycle_transient_motion_active=lambda mid:False,
            current_frame_display_signature=(320,240,96),_log=lambda *args,**kw:None)

    def remember(self,floor,fit=None):
        with patch('idvb0059_steady.observe_coverage',
                   side_effect=lambda *a,coverage_state=None:coverage_state):
            runtime.remember_committed(self.assistant,'map',floor,self.frame,fit)

    def requested_scale(self,floor):
        with patch('idvb0059_steady.solve',return_value=dict(accepted=False,reason='fixture-rejected',diagnostics={})) as solver:
            self.assertIsNone(runtime.try_alignment(self.assistant,self.frame,'map',floor))
            return solver.call_args

    def test_floor_two_commit_does_not_rewrite_floor_one_scale(self):
        self.remember('1')
        self.group.adopt_floor_canonical_pose('2',np.diag([1.1,1.1,1.]),source='test',cycle_id=1)
        self.remember('2')
        self.assertEqual(self.requested_scale('1').args[2],.7)
        self.assertEqual(self.requested_scale('2').args[2],1.1)

    def test_unobserved_floor_never_inherits_shared_root_scale(self):
        self.remember('1')
        self.assertIsNone(self.requested_scale('2'))

    def test_round_replacement_rejects_even_the_same_map_and_dimensions(self):
        self.remember('1')
        self.group=RoundLayerGroup('map','shared-structure','1',np.diag([.9,.9,1.]))
        self.group.canonical_geometry_verified_floors={'1'}
        self.assertIsNone(self.requested_scale('1'))

    def test_reference_replacement_and_display_change_invalidate_scale(self):
        self.remember('1')
        self.reference=SharedStructureReference(self.reference.clean_mask,self.reference.validation_mask,{},Path('replacement'))
        self.assertIsNone(self.requested_scale('1'))
        self.remember('1')
        self.assistant.current_frame_display_signature=(320,240,120)
        self.assertIsNone(self.requested_scale('1'))

    def test_coverage_is_committed_with_its_floor_only(self):
        coverage={'milestone':3}
        self.remember('1',{'idvb0059Coverage':coverage})
        self.remember('2')
        self.assertIs(self.requested_scale('1').kwargs['coverage_state'],coverage)
        self.assertIsNone(self.requested_scale('2').kwargs['coverage_state'])

    def test_successful_fallback_preserves_pending_and_measures_accepted_frame(self):
        pending={'entries':{'320x240/80x80':{'reachedPercent':10,'refreshPending':True}}}
        self.remember('1',{'idvb0059Coverage':pending})
        measured={'entries':{'320x240/80x80':{'reachedPercent':15,'refreshPending':True}}}
        with patch('idvb0059_steady.observe_coverage',return_value=measured) as observer:
            runtime.remember_committed(self.assistant,'map','1',self.frame,{'reason':'original-recovery'})
        self.assertIs(observer.call_args.args[0],self.frame)
        self.assertIs(observer.call_args.args[1],self.reference)
        self.assertIs(observer.call_args.kwargs['coverage_state'],pending)
        np.testing.assert_array_equal(observer.call_args.args[2],self.group.root_pose)
        self.assertIs(self.requested_scale('1').kwargs['coverage_state'],measured)

    def test_first_commit_measures_coverage_without_inventing_prior(self):
        with patch('idvb0059_steady.observe_coverage',return_value={'entries':{}}) as observer:
            runtime.remember_committed(self.assistant,'map','1',self.frame)
        self.assertIsNone(observer.call_args.kwargs['coverage_state'])

    def test_failed_coverage_measurement_keeps_pending(self):
        pending={'entries':{'320x240/80x80':{'reachedPercent':10,'refreshPending':True}}}
        self.remember('1',{'idvb0059Coverage':pending})
        with patch('idvb0059_steady.observe_coverage',side_effect=ValueError('unavailable')):
            runtime.remember_committed(self.assistant,'map','1',self.frame)
        self.assertIs(self.requested_scale('1').kwargs['coverage_state'],pending)


if __name__=='__main__':unittest.main()
