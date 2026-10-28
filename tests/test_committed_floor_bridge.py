"""Commit-to-switch regression: actual production commit/refresh, synthetic pixels."""
import ast
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock
import cv2
import numpy as np
from test_route_selection_regressions import RoundLayerGroup
APP=Path(__file__).parents[1]/'app'
SHARED='shared-structure'

def subject():
    path=APP/'identityv_daily_assistant.py'
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    names={'_commit_alignment_v2_round_pose','_refresh_shared_route_pixels','_shared_display_bridge'}
    methods=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
    wrapper=ast.ClassDef(name='Subject',bases=[],keywords=[],body=methods,decorator_list=[])
    ns=dict(np=np,cv2=cv2,time=time,SHARED_STRUCTURE_PROFILE_ID=SHARED,
        RoundLayerGroup=RoundLayerGroup,ROUND_LAYER_FLOORS=('1','2','B1'),
        normalize_route_profile_id=lambda x:x,
        rendered_profile_residual_is_verified=lambda x:bool(x and x.get('verified')),
        visible_map_component=lambda frame:(frame,(0,0,8,8),{}),
        map_reuse_evidence=lambda *a,**kw:{'translationMask':(np.ones((8,8),np.uint8),1.)},
        map_anchor_focus_point=lambda *a,**kw:(np.array([6.,4.]),True),
        live_stair_screen_candidates=lambda *a,**kw:[],display_environment_signature=lambda x:())
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[])),str(path),'exec'),ns)
    return ns['Subject']()

class CommittedFloorBridge(unittest.TestCase):
    def test_mixed_committed_floors_transport_using_each_actual_display_basis(self):
        s=subject(); s.pack=SimpleNamespace(by_id={'map':object()})
        s.gate=SimpleNamespace(confirmed_id='map'); s.current_floor='2'; s.active_map_cycle_id=7
        s.current_frame_generation=2; s._open_cycle_transient_motion_active=lambda _:False
        root=np.array([[1.,0.,10.],[0.,1.,20.],[0.,0.,1.]])
        group=s.round_layer_group=RoundLayerGroup('map','author-a','1',root.copy(),relations={'1':np.eye(3),'2':np.eye(3)},cycle_id=7)
        s._active_round_layer_group=lambda *args:group
        s._log=Mock()
        bases={SHARED:np.eye(3),'author-a':np.diag([2.,2.,1.]),'author-b':np.diag([3.,3.,1.])}
        s.author_coordinate_adapters=SimpleNamespace(matrix_profile_to_canonical=lambda profile,*args:bases[profile])
        s._shared_structure_mode_enabled=lambda:True
        s._shared_display_layer=lambda profile,*args:dict(matrixDisplayToReference=bases[profile],routeProfileId=profile)
        for floor,profile in [('1','author-a'),('2',SHARED)]:
            self.assertTrue(s._commit_alignment_v2_round_pose('map',floor,np.zeros((8,8,4),np.uint8),
                np.zeros((8,8,3),np.uint8),(0,0,8,8),render_matrix=root@bases[profile],
                canonical_matrix=root,route_profile_id=profile,source='synthetic',residual={'verified':True}))
        self.assertEqual(group.route_profile_id,'author-a')
        before={floor:np.asarray(t['translationMaskMatrix'])@np.r_[t['verifiedPlayerAuthorPoint'],1.]
                for floor,t in group.tracking_by_floor.items()}
        s.route_profile_id='author-b'; s._effective_route_profile=lambda *args:'author-b'
        s._refresh_shared_route_pixels('author-a')
        for floor in ('1','2'):
            with self.subTest(floor=floor):
                tracking=group.tracking_for_floor(floor)
                np.testing.assert_allclose(tracking['translationMaskMatrix'],root@bases['author-b'])
                np.testing.assert_allclose(tracking['layoutAnchorMatrix'],root@bases['author-b'])
                np.testing.assert_allclose(np.asarray(tracking['translationMaskMatrix'])@np.r_[tracking['verifiedPlayerAuthorPoint'],1.],before[floor])
        np.testing.assert_allclose(group.root_pose,root)

if __name__=='__main__':unittest.main()
