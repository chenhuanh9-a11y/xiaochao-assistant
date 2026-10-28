"""Build front-entry condition resources from an offline survey, retaining references.

This writes only the condition index and shared sample arrays. It never copies or
replaces the complete base's map/reference indices. Pilot and unknown coverage
remain incomplete; this producer does not authorize runtime identity or pose.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np


INDEX='references/native-similarity-groups.json.gz'
SAMPLES='references/native-similarity/condition-samples.npz'


def read(path):
    path=Path(path)
    data=path.read_bytes()
    return json.loads((gzip.decompress(data) if path.suffix=='.gz' else data).decode('utf-8'))
def rows(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:yield json.loads(line)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def domain_id(source,kind,anchor):return f'{source}|{kind}|{anchor}'


def build(source_root, survey_root, output_root, *, summary_name='summary.json', kind='front'):
    if kind!='front':raise ValueError('production condition scope is front-only')
    selected_kind=kind
    source_root,survey_root,output_root=map(lambda p:Path(p).resolve(),(source_root,survey_root,output_root))
    if output_root==source_root or output_root.is_relative_to(source_root):raise ValueError('use independent output root')
    if output_root.exists() and any(output_root.iterdir()):raise ValueError('preserve existing output')
    inventory=read(survey_root/'inventory.json');summary=read(survey_root/summary_name)
    references=[];by_ref={};feature_cache={};raw_features={}
    for item in inventory['structures']:
        if item['status']!='AVAILABLE':continue
        reference_id=f'{item["mode"]}/{item["mapId"]}/{item["floor"]}'
        reference={key:item[key] for key in ('mode','mapId','floor','resourcePath','resourceSha256',
            'binding','structureVersionKey','sourceVersion','baseSnapshot','shapeHW','coordinateDomain','features')}
        reference['referenceId']=reference_id
        if sha(source_root/reference['resourcePath'])!=reference['resourceSha256']:
            raise ValueError('source reference changed after survey')
        for feature_kind,feature in reference['features'].items():
            path=feature['path']
            if path not in feature_cache:
                if sha(source_root/path)!=feature['sha256']:raise ValueError('source feature file changed after survey')
                feature_cache[path]=read(source_root/path)
            row=feature_cache[path]['entries'][feature['rowIndex']]
            if row['mapId']!=item['mapId'] or str(row['floor'])!=item['floor']:
                raise ValueError('source feature row binding mismatch')
            raw_features[(reference_id,feature_kind)]=row
        references.append(reference);by_ref[reference_id]=reference
    all_conditions=list(rows(survey_root/'conditions.jsonl'))
    scan_conditions=[row for row in all_conditions if row['kind']==selected_kind]
    excluded_condition_ids={row['conditionId'] for row in all_conditions if row['kind']!=selected_kind}
    domains={}
    for condition in scan_conditions:
        source,kind,anchor=condition['source'],condition['kind'],condition['sourceAnchorIndex']
        identifier=domain_id(source,kind,anchor)
        if identifier in domains:continue
        feature=by_ref[source]['features'][kind];row=raw_features[(source,kind)]
        if kind=='side':
            edges=[(i,t['next']) for i,t in enumerate(row['turns']) if 0<=t.get('next',-1)<len(row['turns'])]
            anchor_ids=list(edges[anchor]);center=np.asarray(row['turns'][anchor_ids[0]]['point'],float)
        else:
            points=np.asarray([c['points'][1] for c in row['chains']],float)
            outline=np.asarray(row['outline'],float)
            anchor_ids=np.flatnonzero(np.linalg.norm(points[:,None]-outline[None],axis=2).min(axis=1)<=3).tolist()
            center=np.asarray(row['center'],float)
        unit=float(feature['unit']);factor=60/unit
        domains[identifier]=dict(domainId=identifier,source=source,referenceId=source,kind=kind,
            sourceAnchorIndex=anchor,centerReference=center.tolist(),unit=unit,
            feature=feature,anchorPointIds=anchor_ids,
            sourceReferenceToCondition=[[factor,0,float(-center[0]*factor)],
                [0,factor,float(-center[1]*factor)],[0,0,1]],
            coordinateSpace='fixed-60-pixels-per-unit-local-condition',
            semanticScope='front-entry-window' if kind=='front' else 'local-structure-window')
    poses={};canonical={};scan_pose_ids={}
    for record in rows(survey_root/'poses.jsonl'):
        if record['kind']!=selected_kind:continue
        identifier=domain_id(record['source'],record['kind'],record['sourceAnchorIndex'])
        key=(identifier,record['target'],tuple(np.asarray(record['targetReferenceToCondition'],float).ravel()))
        pose_id=canonical.setdefault(key,str(record['poseId']))
        scan_pose_ids[record['poseId']]=pose_id
        windows=record.get('windows')
        if windows is None:
            # Older pilots retain largest-window matches. Filter those measured
            # correspondences by the declared smaller window, never add points.
            raw=raw_features[(record['source'],record['kind'])]
            points=np.asarray([c['points'][1] for c in raw['chains']] if record['kind']=='front'
                              else [t['point'] for t in raw['turns']],float)
            domain=domains[identifier];center=np.asarray(domain['centerReference']);factor=60/domain['unit']
            ids=np.asarray(record['sourceFeatureIds'],int);target_ids=np.asarray(record['targetFeatureIds'],int)
            windows=[]
            for radius in record['radiiUnits']:
                inside=np.max(np.abs((points[ids]-center)*factor),axis=1)<=radius*60+1e-8
                windows.append(dict(radiusUnits=radius,sourceFeatureIds=ids[inside].tolist(),
                    targetFeatureIds=target_ids[inside].tolist(),
                    supportCountsStatus='PILOT_LARGEST_WINDOW_SUBSET; runtime gate not evaluated'))
        if pose_id not in poses:
            poses[pose_id]=dict(poseId=pose_id,domainId=identifier,target=record['target'],
                targetReferenceToCondition=record['targetReferenceToCondition'],windows=[],
                exactEquivalentSeeds=[],scanPoseIds=[],proposalOnly=True)
        pose=poses[pose_id];pose['scanPoseIds'].append(record['poseId'])
        for seed in record.get('exactEquivalentSeeds',[[record['sourceSeedIndex'],record['targetSeedIndex']]]):
            if seed not in pose['exactEquivalentSeeds']:pose['exactEquivalentSeeds'].append(seed)
        for window in windows:
            old=next((w for w in pose['windows'] if w['radiusUnits']==window['radiusUnits']),None)
            if old is not None:
                if old['sourceFeatureIds']!=window['sourceFeatureIds'] or old['targetFeatureIds']!=window['targetFeatureIds']:
                    raise ValueError('same exact pose has inconsistent window correspondences')
            else:pose['windows'].append(window)
    for domain in domains.values():
        own='own:'+domain['domainId']
        poses[own]=dict(poseId=own,domainId=domain['domainId'],target=domain['source'],
            targetReferenceToCondition=domain['sourceReferenceToCondition'],windows=[],
            exactEquivalentSeeds=[],scanPoseIds=[],ownSourcePose=True,proposalOnly=True)
    conditions=[];condition_domains={}
    for row in scan_conditions:
        identifier=domain_id(row['source'],row['kind'],row['sourceAnchorIndex']);own='own:'+identifier
        pose_ids=list(dict.fromkeys([own]+[scan_pose_ids[p['poseId']] for p in row['compatiblePoses']]))
        condition_domains[row['conditionId']]=identifier
        conditions.append(dict(conditionId=row['conditionId'],domainId=identifier,source=row['source'],
            mode=by_ref[row['source']]['mode'],floor=by_ref[row['source']]['floor'],kind=row['kind'],
            radiusUnits=row['radiusUnits'],poseIds=pose_ids,members=sorted({poses[p]['target'] for p in pose_ids}),
            completeForeignMapCompetition=bool(row['completeForeignMapCompetition'] and not summary['pilot']),
            unresolvedTargets=row.get('unresolvedTargets',[]),sameMapAlternativePoses=row['sameMapAlternativePoses'],
            semanticSideEntryCoverage=row.get('semanticSideEntryCoverage','UNKNOWN'),runtimeVerified=False))
    sample_ids={b'':0};sample_arrays=[np.empty((0,2),np.int32)];comparisons=[];seen=set();comparison_content={}
    def sample_id(sample):
        array=np.asarray(sample['pointsXY'],np.int32).reshape(-1,2);key=array.tobytes()
        if key not in sample_ids:sample_ids[key]=len(sample_arrays);sample_arrays.append(array)
        result=dict(sampleSetId=sample_ids[key],positivePixels=sample['positivePixels'])
        if 'rawExclusivePositivePixels' in sample:result['rawExclusivePositivePixels']=sample['rawExclusivePositivePixels']
        return result
    for row in rows(survey_root/'difference-samples.jsonl'):
        if row['conditionId'] in excluded_condition_ids:continue
        if row.get('differenceScope')!='post-entry-positive-structure':
            raise ValueError('front differences require the full post-entry observation scope')
        if (row.get('distanceEvaluationSpace')!='original-source-and-target-positive-pixel-centers'
                or row.get('persistedSampleDistanceChecked') is not True):
            raise ValueError('front differences require original source geometry and checked persisted samples')
        comparison_scope={key:row[key] for key in ('differenceScope','observationBounds',
            'minimumPositiveDistancePixels','differenceSemantics','distanceEvaluationSpace','persistedSampleDistanceChecked')}
        identifier=condition_domains[row['conditionId']]
        a='own:'+identifier if row['poseA'] is None else scan_pose_ids[row['poseA']]
        b='own:'+identifier if row['poseB'] is None else scan_pose_ids[row['poseB']]
        sa,sb=row['aOnly'],row['bOnly']
        if a>b:a,b,sa,sb=b,a,sb,sa
        key=(row['conditionId'],a,b)
        a_sample,b_sample=sample_id(sa),sample_id(sb)
        content=(a_sample,b_sample,comparison_scope)
        if key in seen:
            if comparison_content[key]!=content:raise ValueError('same exact pose pair has inconsistent difference evidence')
            continue
        seen.add(key)
        comparison_content[key]=content
        comparisons.append(dict(domainId=identifier,conditionId=row['conditionId'],poseA=a,poseB=b,
            aOnly=a_sample,bOnly=b_sample,**comparison_scope,positiveEvidenceOnly=True))
    # Every cross-member pose pair must have a comparison, including empty sets.
    expected=set()
    for condition in conditions:
        ids=condition['poseIds']
        for i,a in enumerate(ids):
            for b in ids[i+1:]:
                if poses[a]['target']!=poses[b]['target']:
                    expected.add((condition['conditionId'],*sorted([a,b])))
    if seen!=expected:raise ValueError('difference coverage incomplete or unexpected')
    sample_path=output_root/SAMPLES;sample_path.parent.mkdir(parents=True,exist_ok=True)
    offsets=np.r_[0,np.cumsum([len(a) for a in sample_arrays])].astype(np.int64)
    points=np.concatenate(sample_arrays) if sample_arrays else np.empty((0,2),np.int32)
    np.savez_compressed(sample_path,points=points,offsets=offsets)
    payload=dict(schemaVersion=2,resourceKind='native-similarity-conditions',
        scanConditions=summary['conditions'],survey=dict(pilot=summary['pilot'],
            scope='front-only',
            limitations=summary['limitations'],sourceRoot=str(source_root),
            summaryFile=summary_name,summarySha256=sha(survey_root/summary_name)),
        references=references,domains=list(domains.values()),poses=list(poses.values()),
        conditions=conditions,comparisons=comparisons,
        samples=dict(resourcePath=SAMPLES,sha256=sha(sample_path),pointsArray='points',offsetsArray='offsets'))
    index_path=output_root/INDEX
    index_bytes=(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
    index_path.write_bytes(gzip.compress(index_bytes,compresslevel=6,mtime=0))
    if gzip.decompress(index_path.read_bytes())!=index_bytes:raise ValueError('gzip index byte readback mismatch')
    if read(index_path)!=payload:raise ValueError('condition index readback mismatch')
    with np.load(sample_path,allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive['points'],points);np.testing.assert_array_equal(archive['offsets'],offsets)
    result=dict(index=str(index_path),newFiles=[INDEX,SAMPLES],scope='front-only',baseReferenceFilesCopied=False,
        preserveBaseSharedIndices=True,references=len(references),domains=len(domains),poses=len(poses),
        scanPoseIds=len(scan_pose_ids),conditions=len(conditions),comparisons=len(comparisons),
        sampleSets=len(sample_arrays),samplePoints=len(points),indexBytes=index_path.stat().st_size,
        uncompressedIndexBytes=len(index_bytes),indexCompression='gzip-level-6-mtime-0',
        sampleBytes=sample_path.stat().st_size,pilot=summary['pilot'])
    print(json.dumps(result),flush=True);return payload,result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_root',type=Path);parser.add_argument('survey_root',type=Path)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--summary-name',default='summary.json')
    parser.add_argument('--kind',choices=['front'],default='front',help='production scope (default: front)')
    args=parser.parse_args();build(args.source_root,args.survey_root,args.output_root,summary_name=args.summary_name,kind=args.kind)


if __name__=='__main__':main()
