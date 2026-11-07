import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from s1_pose_prior import CoordinatePriorIndex, AcknowledgedPose, isotropic_matrix
import fast_structure_alignment as fast

ROOT=Path(__file__).resolve().parents[1]
INDEX=ROOT/'s1-private-assets/s1_entrance_coordinate_index.json'
DIGEST='6fee640f5bc87d465459840626cfd5f88b52cdb9b888b58c1b03b46f1370cc19'
assert hashlib.sha256(INDEX.read_bytes()).hexdigest()==DIGEST, 'changed pose-prior fixture'
RAW=json.loads(INDEX.read_text())

def provider(mid,floor):
 e=RAW['maps']['nightmare'][mid][floor]
 return SimpleNamespace(clean_mask=np.zeros(e['shapeHW'],np.uint8),metadata={
  'sourceVersion':RAW['sourceVersion'],'binding':{'mapId':mid,'floor':floor,'profile':'shared-structure'},
  'sourceGeometry':{'sourceCatalogSha256':e['sourceCatalogSha256'],'rendererMetadataSha256':e['sourceRendererMetadataSha256']}})

class HintContracts(unittest.TestCase):
 def setUp(self):
  self.registry=CoordinatePriorIndex(INDEX,expected_sha256=DIGEST,mode='nightmare',resource_key='resource-1',provider=provider)
  self.source=AcknowledgedPose('nightmare','resource-1','107015','-2',('round-1','window-1'),'acked-frame-2',((.5,0,200),(0,.5,300),(0,0,1)),True,True)
 def test_real_cross_floor_composition(self):
  h=self.registry.make_hint(self.source,target_floor='-3',current_owner_scope=self.source.owner_scope)
  np.testing.assert_allclose(h.matrix,[[.5,0,230],[0,.5,90],[0,0,1]])
  self.assertFalse(h.identity_authorized);self.assertFalse(h.display_authorized)
 def test_same_floor_is_identity_coordinate_change(self):
  h=self.registry.make_hint(self.source,target_floor='-2',current_owner_scope=self.source.owner_scope)
  np.testing.assert_array_equal(h.matrix,self.source.matrix)
 def test_all_real_floors_roundtrip(self):
  for floor in ['-1','-2','-3','-4']:
   h=self.registry.make_hint(self.source,target_floor=floor,current_owner_scope=self.source.owner_scope)
   back=self.registry.make_hint(dataclasses.replace(self.source,floor=floor,matrix=h.matrix),target_floor='-2',current_owner_scope=self.source.owner_scope)
   np.testing.assert_allclose(back.matrix,self.source.matrix)
 def test_missing_ack_or_frame_proof_rejected(self):
  for change in [{'material_acknowledged':False},{'current_frame_geometry_verified':False},{'frame_id':''}]:
   self.assertIsNone(self.registry.make_hint(dataclasses.replace(self.source,**change),target_floor='-3',current_owner_scope=self.source.owner_scope))
 def test_ui_dictionary_rejected(self):
  self.assertIsNone(self.registry.make_hint(dataclasses.asdict(self.source),target_floor='-3',current_owner_scope=self.source.owner_scope))
 def test_owner_scope_rejected_at_creation_and_consumption(self):
  self.assertIsNone(self.registry.make_hint(self.source,target_floor='-3',current_owner_scope=('round-2','window-1')))
  h=self.registry.make_hint(self.source,target_floor='-3',current_owner_scope=self.source.owner_scope)
  good=dict(mode='nightmare',resource_key='resource-1',map_id='107015',floor='-3',owner_scope=self.source.owner_scope)
  self.assertIsNotNone(h.matrix_for(**good))
  for k,v in [('mode','difficult'),('resource_key','new'),('map_id','107014'),('floor','-1'),('owner_scope',('round-2','window-1'))]:
   self.assertIsNone(h.matrix_for(**dict(good,**{k:v})))
 def test_hash_and_typed_binding_rejected(self):
  with self.assertRaises(ValueError):CoordinatePriorIndex(INDEX,expected_sha256='bad',mode='nightmare',resource_key='r',provider=provider)
  def wrong(mid,floor):
   r=provider(mid,floor);r.metadata['binding']['mapId']='107014';return r
  self.registry.provider=wrong
  self.assertIsNone(self.registry.make_hint(self.source,target_floor='-3',current_owner_scope=self.source.owner_scope))
 def test_matrix_contract(self):
  for value in [np.eye(2),[[1,0,0],[0,2,0],[0,0,1]],[[0,-1,0],[1,0,0],[0,0,1]],[[1,0,float('nan')],[0,1,0],[0,0,1]],[[1,0,0],[0,1,0],[0,0,0]],[[0,0,0],[0,0,0],[0,0,1]]]:
   self.assertIsNone(isotropic_matrix(value))

class SearchContracts(unittest.TestCase):
 def setUp(self):
  self.host=SimpleNamespace(_log=lambda *a,**k:None)
  self.frame=np.zeros((20,20,3),np.uint8);self.prior=np.array([[.5,0,10],[0,.5,20],[0,0,1]])
 def test_default_calls_original_without_force(self):
  result=object()
  with patch.object(fast,'_try_alignment',return_value=result) as call:
   self.assertIs(fast.try_alignment(self.host,self.frame,'id','-2'),result)
   self.assertEqual(call.call_count,1);self.assertFalse(call.call_args.kwargs['_force_unseeded']);self.assertNotIn('_external_prior',call.call_args.kwargs)
 def test_local_miss_forces_original_fresh_scale(self):
  result={'success':True}
  with patch.object(fast,'_try_alignment',side_effect=[None,result]) as call:
   self.assertIs(fast.try_alignment(self.host,self.frame,'id','-2',pose_prior=self.prior),result)
   self.assertEqual(call.call_count,2);np.testing.assert_array_equal(call.call_args_list[0].kwargs['_external_prior'],self.prior)
   self.assertTrue(call.call_args_list[1].kwargs['_force_unseeded']);self.assertNotIn('_external_prior',call.call_args_list[1].kwargs)
 def test_local_acceptance_does_not_global_search(self):
  with patch.object(fast,'_try_alignment',return_value={'success':True}) as call:
   self.assertTrue(fast.try_alignment(self.host,self.frame,'id','-2',pose_prior=self.prior)['success']);self.assertEqual(call.call_count,1)
 def test_obsolete_does_not_fallback_or_display(self):
  with patch.object(fast,'_try_alignment',return_value={'success':False,'reason':'structure-work-obsolete'}) as call:
   self.assertFalse(fast.try_alignment(self.host,self.frame,'id','-2',pose_prior=self.prior)['success']);self.assertEqual(call.call_count,1)
 def test_invalid_hint_keeps_original_default(self):
  with patch.object(fast,'_try_alignment',return_value=None) as call:
   fast.try_alignment(self.host,self.frame,'id','-2',pose_prior=np.zeros((3,3)))
   self.assertEqual(call.call_count,1);self.assertFalse(call.call_args.kwargs['_force_unseeded'])
 def test_local_proposal_never_runs_global_grid(self):
  context={'observed':np.ones((20,20),np.uint8)}
  with patch('single_map_structure.current_part',return_value=context['observed']),patch.object(fast,'sample_edges',return_value=np.zeros((50,2))),patch.object(fast,'_grid',return_value=[]) as grid:
   fast.propose(self.frame,None,None,prior=self.prior,context=context,local_only=True)
   self.assertEqual(grid.call_count,1);self.assertIs(grid.call_args.args[3],self.prior)

if __name__=='__main__':unittest.main()

class ExistingValidatorContracts(unittest.TestCase):
 def setUp(self):
  self.prior=np.array([[.5,0,10],[0,.5,20],[0,0,1.]])
  self.frame=np.zeros((20,20,3),np.uint8);self.context={'query':np.ones((20,20),np.uint8),'visible':None}
  self.reference=SimpleNamespace(known_boundary=None,clean_mask=np.ones((20,20),np.uint8))
  self.host=SimpleNamespace(_log=lambda *a,**k:None,_shared_structure_mode_enabled=lambda:True,
   _shared_structure_reference=lambda *a:self.reference,_active_round_layer_group=lambda *a:None,
   _rendered_profile_current_frame_residual=lambda *a,**kw:{'verified':False})
 def test_original_residual_rejection_cannot_be_bypassed(self):
  geometry={'structureModel':'m','m':{'success':True,'matrixSourceToClient':self.prior}}
  with patch.object(fast,'load_prepared',return_value=object()),patch.object(fast,'propose',return_value=([(1.,.5,np.array([10.,20.]))],self.context,{})),patch.object(fast,'native_point_geometry',return_value=geometry),patch('single_map_structure.projected_geometry',return_value={'mask':self.context['query']}),patch('single_map_structure.regional_validation',return_value={'regionalEdgeVerified':True}),patch('single_map_structure.partial_wall_geometry',return_value={'success':False}),patch('alignment_observation.rendered_profile_residual_is_verified',side_effect=lambda r:r['verified']) as verify:
   self.assertIsNone(fast.try_alignment(self.host,self.frame,'id','-2',pose_prior=self.prior,context=self.context));self.assertEqual(verify.call_count,2)
 def test_scale_change_rejected_locally_then_reestimated_even_with_old_group(self):
  changed=self.prior.copy();changed[0,0]=changed[1,1]=.55
  self.host._active_round_layer_group=lambda *a:SimpleNamespace(verified_floors={'-2'})
  self.host._round_group_floor_view=lambda *a,**k:(_ for _ in ()).throw(AssertionError('old group scale must not constrain explicit-hint fallback'))
  self.host._rendered_profile_current_frame_residual=lambda *a,**k:{'verified':True}
  geometry={'structureModel':'m','m':{'success':True,'matrixSourceToClient':changed}}
  priors=[]
  def proposal(*a,**kw):
   priors.append(kw['prior']);return [(1.,.5 if kw['prior'] is not None else .55,np.array([10.,20.]))],self.context,{}
  with patch.object(fast,'load_prepared',return_value=object()),patch.object(fast,'propose',side_effect=proposal),patch.object(fast,'native_point_geometry',return_value=geometry),patch('single_map_structure.projected_geometry',return_value={'mask':self.context['query']}),patch('single_map_structure.regional_validation',return_value={'regionalEdgeVerified':True}),patch('single_map_structure.partial_wall_geometry',return_value={'success':False}),patch('alignment_observation.rendered_profile_residual_is_verified',side_effect=lambda r:r['verified']):
   result=fast.try_alignment(self.host,self.frame,'id','-2',pose_prior=self.prior,context=self.context)
   self.assertTrue(result['success']);self.assertEqual(len(priors),2);self.assertIsNone(priors[1]);self.assertEqual(result['matrixSourceToClient'][0,0],.55)
