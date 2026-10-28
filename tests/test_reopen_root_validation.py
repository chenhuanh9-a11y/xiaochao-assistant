"""Actual dispatch method; synthetic geometry boundaries, no GUI or solver latency claim."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys,time,unittest
from unittest.mock import Mock,patch
import numpy as np
import cv2
APP=Path(__file__).resolve().parents[1]/'app'

def subject():
    tree=ast.parse((APP/'identityv_daily_assistant.py').read_text(encoding='utf-8-sig'))
    fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_alignment_v2_structure_fit')
    fn.decorator_list=[]
    ns=dict(np=np,cv2=cv2,time=time,SHARED_STRUCTURE_PROFILE_ID='shared-structure',rendered_profile_residual_is_verified=lambda r:r.get('verified',False))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'production-dispatch','exec'),ns)
    return ns[fn.name]

class ReopenRootTests(unittest.TestCase):
    def setUp(self):
        self.call=subject()
        self.verify=Mock(return_value={'success':True})
        self.solve=Mock(return_value={'success':True,'reason':'local-solve'})
        self.rank=Mock(side_effect=AssertionError('reopen must not rank'))
        self.author=np.diag([3.,3.,1.]);self.shared=np.diag([2.,2.,1.])
        self.canonical=np.array([[1.,0.,11.],[0.,1.,19.],[0.,0.,1.]])
        self.matrix=self.canonical@self.author
        self.group=SimpleNamespace(verified_floors={'1'},relations={'1':np.eye(3)},route_profile_id='author',cycle_id=1,generation=2,
            root_pose=self.canonical,profile_pose_for_floor=lambda *a:self.matrix,canonical_pose_for_floor=lambda *a:self.canonical)
        self.s=SimpleNamespace(game_mode='nightmare',engine=SimpleNamespace(identity_profile_id='author',side_entry_identity=SimpleNamespace(rank_entry=self.rank,verify_existing_pose=self.verify)),
            gate=SimpleNamespace(confirmed_id='map'),current_floor='1',active_map_cycle_id=2,surface_visibility_generation=1,
            author_coordinate_adapters=SimpleNamespace(matrix_profile_to_canonical=lambda profile,*a:self.shared if profile=='shared-structure' else self.author),
            _shared_structure_mode_enabled=lambda:False,_alignment_v2_sparse_floor_fit=lambda *a,**kw:None,
            _active_round_layer_group=lambda *a:self.group,_open_cycle_transient_motion_active=lambda *a:False,
            _rendered_profile_current_frame_residual=Mock(return_value={'verified':True}))
    def run_fit(self,**kw):
        mods={'fast_structure_alignment':SimpleNamespace(try_alignment=lambda *a,**kw:None),'single_map_structure':SimpleNamespace(current_geometry=lambda f:{},solve=self.solve)}
        with patch.dict(sys.modules,mods):return self.call(self.s,np.zeros((8,8,3),np.uint8),np.ones((8,8),np.uint8),'map','1','author',**kw)
    def test_verified_reopen_uses_root_before_solver_both_modes(self):
        for mode in ('nightmare','difficult'):
            with self.subTest(mode=mode):
                self.s.game_mode=mode
                result=self.run_fit()
                self.assertTrue(result['success'])
                self.assertGreater(self.verify.call_count,0)
                self.solve.assert_not_called();self.rank.assert_not_called()
    def test_miss_uses_local_solve_without_rank(self):
        self.verify.return_value={'success':False}
        self.assertEqual(self.run_fit()['reason'],'local-solve')
        self.verify.assert_called_once();self.solve.assert_called_once();self.rank.assert_not_called()
    def test_author_residual_rejection_falls_back_to_solve(self):
        self.s._rendered_profile_current_frame_residual.return_value={"verified":False}
        self.assertEqual(self.run_fit()["reason"],"local-solve")
        self.verify.assert_called_once();self.solve.assert_called_once();self.rank.assert_not_called()
    def test_current_cycle_and_motion_skip_root(self):
        self.group.cycle_id=2
        self.run_fit();self.verify.assert_not_called()
        self.group.cycle_id=1
        self.s._open_cycle_transient_motion_active=lambda *a:True
        self.run_fit();self.verify.assert_not_called();self.assertEqual(self.solve.call_count,2)
    def test_cancel_after_verifier_has_no_geometry_verdict(self):
        cancelled=[False]
        def verify(*a, **kwargs):cancelled[0]=True;return {'success':True}
        self.verify.side_effect=verify
        result=self.run_fit(cancelled=lambda:cancelled[0])
        self.assertEqual(result['reason'],'structure-work-obsolete');self.solve.assert_not_called()
        self.s._rendered_profile_current_frame_residual.assert_not_called()
    def test_shared_and_rendered_checks_use_distinct_bases(self):
        self.s._shared_structure_mode_enabled=lambda:True
        result=self.run_fit()
        self.assertTrue(result['success'])
        self.verify.assert_called_once()
        self.assertIn('context', self.verify.call_args.kwargs)
        np.testing.assert_allclose(self.verify.call_args.args[3],self.canonical@self.shared)
        np.testing.assert_allclose(self.s._rendered_profile_current_frame_residual.call_args.args[2],self.matrix)

    def test_epoch_changes_during_validation_never_reach_solve(self):
        for mode in ('nightmare', 'difficult'):
            for changed in ('group', 'generation', 'surface'):
                for round_number in range(20):
                    with self.subTest(mode=mode, changed=changed, round=round_number):
                        self.s.game_mode = mode
                        self.s._active_round_layer_group = lambda *a: self.group
                        def verify(*args, **kwargs):
                            if changed == 'group':
                                self.s._active_round_layer_group = lambda *a: None
                            elif changed == 'generation':
                                self.group.generation += 1
                            else:
                                self.s.surface_visibility_generation += 1
                            return {'success': True}
                        self.verify.side_effect = verify
                        result = self.run_fit()
                        self.assertEqual(result['reason'], 'structure-work-obsolete')
                        self.solve.assert_not_called()
                        self.rank.assert_not_called()

if __name__=='__main__':unittest.main()
