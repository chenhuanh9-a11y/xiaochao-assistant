"""Thin resource composition from synthetic completed survey records."""
import importlib.util
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

SPEC=importlib.util.spec_from_file_location('condition_builder',
    Path(__file__).resolve().parents[1]/'tools/maps/build_similarity_conditions.py')
builder=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(builder)


class ConditionBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        self.survey=self.root/'survey';self.survey.mkdir();self.output=self.root/'candidate'
        self.source_ids=['synthetic/a/1','synthetic/b/1']
        entries=[dict(mapId=map_id,floor='1',center=[60,0],outline=[[0,0],[120,0]],
            chains=[dict(points=[[0,1],point,[1,0]]) for point in [[0,0],[120,0]]]) for map_id in ['a','b']]
        self.write(self.source/'features.json',dict(entries=entries))
        inventory=[]
        for i,map_id in enumerate(['a','b']):
            path=self.source/f'{map_id}.npz';np.savez_compressed(path,shared_mask=np.ones((121,121),np.uint8))
            inventory.append(dict(status='AVAILABLE',mode='synthetic',mapId=map_id,floor='1',
                resourcePath=path.name,resourceSha256=builder.sha(path),binding=dict(profile='fixture',mapId=map_id,floor='1',variant='v'),
                structureVersionKey=f'fixture-{map_id}/1',sourceVersion='fixture',baseSnapshot='fixture',shapeHW=[121,121],
                coordinateDomain='fixture-reference',features=dict(front=dict(path='features.json',sha256=builder.sha(self.source/'features.json'),rowIndex=i,unit=60))))
        self.write(self.survey/'inventory.json',dict(structures=inventory))
        self.write(self.survey/'summary.json',dict(pilot=False,conditions=dict(pixelsPerUnit=60),limitations=['synthetic']))
        pose=dict(source=self.source_ids[0],target=self.source_ids[1],kind='front',sourceAnchorIndex=-1,
            sourceSeedIndex=0,targetSeedIndex=0,targetReferenceToCondition=np.eye(3).tolist(),exactEquivalentSeeds=[[0,0]])
        window=lambda r:dict(radiusUnits=r,sourceFeatureIds=[0,1],targetFeatureIds=[0,1])
        self.write_rows('poses.jsonl',[dict(pose,poseId=0,windows=[window(2)]),dict(pose,poseId=1,windows=[window(2),window(4)])])
        self.write_rows('conditions.jsonl',[dict(conditionId=i,source=self.source_ids[0],kind='front',sourceAnchorIndex=-1,
            radiusUnits=r,compatiblePoses=[dict(poseId=p,target=self.source_ids[1]) for p in ids],
            completeForeignMapCompetition=True,sameMapAlternativePoses='NOT_SCANNED') for i,r,ids in [(0,2,[0,1]),(1,4,[1])]])
        self.differences=[dict(conditionId=condition,poseA=None,poseB=pose,
            differenceScope='post-entry-positive-structure',observationBounds=[-60,0,60,120],
            distanceEvaluationSpace='original-source-and-target-positive-pixel-centers',persistedSampleDistanceChecked=True,
            minimumPositiveDistancePixels=3,differenceSemantics='synthetic exact positive distance',
            aOnly=dict(pointsXY=[[2,3],[2,4]],positivePixels=2),bOnly=dict(pointsXY=[],positivePixels=0))
            for condition,pose in [(0,0),(0,1),(1,1)]]
        self.write_rows('difference-samples.jsonl',self.differences)

    @staticmethod
    def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')

    def write_rows(self,name,values):
        (self.survey/name).write_text(''.join(json.dumps(x)+'\n' for x in values),encoding='utf-8')

    def test_exact_pose_union_and_shared_samples_keep_all_conditions(self):
        payload,result=builder.build(self.source,self.survey,self.output)
        self.assertEqual(result['poses'],2) # one canonical foreign pose + own
        self.assertEqual(result['comparisons'],2)
        self.assertEqual(result['sampleSets'],2) # common empty + exact point sequence
        pose=next(p for p in payload['poses'] if not p.get('ownSourcePose'))
        self.assertEqual(pose['scanPoseIds'],[0,1])
        self.assertEqual(sorted(w['radiusUnits'] for w in pose['windows']),[2,4])
        self.assertEqual(len(payload['conditions']),2)
        self.assertTrue(all(len(c['poseIds'])==2 for c in payload['conditions']))
        self.assertEqual(payload['domains'][0]['anchorPointIds'],[0,1])
        self.assertEqual(sorted(p.relative_to(self.output).as_posix() for p in self.output.rglob('*') if p.is_file()),
            sorted([builder.INDEX,builder.SAMPLES]))
        with np.load(self.output/builder.SAMPLES,allow_pickle=False) as archive:
            self.assertEqual(archive['points'].dtype,np.int32)
            self.assertEqual(archive['offsets'].dtype,np.int64)
            np.testing.assert_array_equal(archive['offsets'],[0,0,2])
            np.testing.assert_array_equal(archive['points'],[[2,3],[2,4]])

    def test_gzip_preserves_full_payload_and_is_deterministic(self):
        payload,result=builder.build(self.source,self.survey,self.output)
        self.assertEqual(builder.INDEX,'references/native-similarity-groups.json.gz')
        compressed=(self.output/builder.INDEX).read_bytes()
        expected=(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
        self.assertEqual(gzip.decompress(compressed),expected)
        self.assertEqual(builder.read(self.output/builder.INDEX),payload)
        other=self.root/'second-candidate'
        builder.build(self.source,self.survey,other)
        self.assertEqual((other/builder.INDEX).read_bytes(),compressed)
        self.assertEqual(result['newFiles'],[builder.INDEX,builder.SAMPLES])
        self.assertEqual(result['uncompressedIndexBytes'],len(expected))

    def test_read_rejects_corrupt_gzip_despite_plain_companion(self):
        path=self.root/'index.json.gz'
        path.write_bytes(b'not a gzip stream')
        self.write(self.root/'index.json',dict(valid=True))
        with self.assertRaises(gzip.BadGzipFile):builder.read(path)

    def test_default_output_keeps_all_front_conditions_and_excludes_side(self):
        inventory=builder.read(self.survey/'inventory.json')
        for reference in inventory['structures']:
            reference['features']['side']=dict(reference['features']['front'])
        self.write(self.survey/'inventory.json',inventory)
        conditions=list(builder.rows(self.survey/'conditions.jsonl'))
        poses=list(builder.rows(self.survey/'poses.jsonl'))
        conditions.append(dict(conditions[0],conditionId=99,kind='side',sourceAnchorIndex=0,
            compatiblePoses=[dict(poseId=99,target=self.source_ids[1])]))
        poses.append(dict(poses[0],poseId=99,kind='side',sourceAnchorIndex=0))
        self.write_rows('conditions.jsonl',conditions);self.write_rows('poses.jsonl',poses)
        self.write_rows('difference-samples.jsonl',self.differences+[dict(self.differences[0],conditionId=99,poseB=99)])
        payload,result=builder.build(self.source,self.survey,self.output)
        self.assertEqual([c['conditionId'] for c in payload['conditions']],[0,1])
        self.assertTrue(all(c['kind']=='front' for c in payload['conditions']))
        self.assertTrue(all(d['kind']=='front' for d in payload['domains']))
        self.assertEqual(result['scope'],'front-only')
        self.assertEqual(result['comparisons'],2)

    def test_missing_pose_pair_difference_is_rejected_before_output(self):
        self.write_rows('difference-samples.jsonl',self.differences[:2])
        with self.assertRaisesRegex(ValueError,'difference coverage incomplete'):
            builder.build(self.source,self.survey,self.output)
        self.assertFalse(self.output.exists())

    def test_entry_window_differences_cannot_masquerade_as_post_entry_evidence(self):
        for row in self.differences:row.pop('differenceScope')
        self.write_rows('difference-samples.jsonl',self.differences)
        with self.assertRaisesRegex(ValueError,'full post-entry observation scope'):
            builder.build(self.source,self.survey,self.output)
        self.assertFalse(self.output.exists())

    def test_post_entry_differences_require_original_source_geometry(self):
        for row in self.differences:row.pop('distanceEvaluationSpace')
        self.write_rows('difference-samples.jsonl',self.differences)
        with self.assertRaisesRegex(ValueError,'original source geometry'):
            builder.build(self.source,self.survey,self.output)
        self.assertFalse(self.output.exists())

    def test_changed_source_feature_file_is_rejected(self):
        self.write(self.source/'features.json',dict(entries=[]))
        with self.assertRaisesRegex(ValueError,'source feature file changed'):
            builder.build(self.source,self.survey,self.output)
        self.assertFalse(self.output.exists())

    def test_duplicate_exact_pose_pair_cannot_hide_inconsistent_samples(self):
        self.differences[1]['aOnly']=dict(pointsXY=[[9,9]],positivePixels=1)
        self.write_rows('difference-samples.jsonl',self.differences)
        with self.assertRaisesRegex(ValueError,'inconsistent difference evidence'):
            builder.build(self.source,self.survey,self.output)
        self.assertFalse(self.output.exists())


if __name__=='__main__':unittest.main()
