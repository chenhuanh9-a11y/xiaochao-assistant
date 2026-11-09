"""Actual pure Python state machines, synthetic state sequences, no runtime flag injection."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import sys
import numpy as np

parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
sys.path.insert(0,str(args.source/'app'))
from alignment_core_v2 import AlignmentCoreV2,AlignmentCandidate,RoundPose
from round_layer_group import RoundLayerGroup
from alignment_observation import canonical_residual_evidence,rendered_profile_residual_is_verified

I=np.eye(3).tolist()
P=[[.4,0.,30.],[0.,.5,-20.],[.001,-.002,1.]]  # full, finite, deliberately non-affine
R=[[1.25,.1,7.],[-.05,.8,-9.],[.002,0.,1.]]
def plain(v):
    if isinstance(v,np.ndarray):return v.tolist()
    if isinstance(v,np.generic):return v.item()
    if isinstance(v,dict):return {str(k):plain(x) for k,x in v.items()}
    if isinstance(v,(tuple,list)):return [plain(x) for x in v]
    if isinstance(v,set):return sorted(plain(x) for x in v)
    return v
def pose(p):
    return RoundPose(p.get('mapId','m'),p.get('routeProfileId','shared-structure'),p.get('anchorFloor','1'),np.array(p.get('matrix',P)),
        {k:np.array(v) for k,v in p.get('relations',{}).items()},generation=p.get('generation',0),reliable=p.get('reliable',True),
        source=p.get('source','seed'),floors=tuple(p.get('floors',['B1','1','2'])))
def candidate(c):
    return AlignmentCandidate(c.get('mapId','m'),c.get('floor','1'),np.array(c.get('matrix',P)),c.get('source','initial-structure-v2'),c.get('frame',1),
        confidence=c.get('confidence',0),anchor_count=c.get('anchorCount',0),anchor_kind=c.get('anchorKind','global'),
        current_frame_verified=c.get('current',False),residual_verified=c.get('residual',False),global_residual_verified=c.get('global',False),
        translation_only=c.get('translationOnly',False),cross_floor=c.get('crossFloor',False),deterministic_relation=c.get('deterministic',False),
        target_local_correction_verified=c.get('localCorrection',False),identity_rerank=c.get('identityRerank',False),
        explicit_discontinuity=c.get('explicitDiscontinuity',False),metadata=copy.deepcopy(c.get('metadata',{})))
def token(t):
    return None if t is None else dict(authorizationId=t.authorization_id,observationId=t.observation_id,cycleId=t.cycle_id,mapId=t.map_id,
        floor=t.floor,frameGeneration=t.frame_generation,poseGeneration=t.pose_generation,source=t.source,issuedAt=t.issued_at,
        active=t.active,continuityUntil=t.continuity_until)
def decision(d):
    return dict(accepted=d.accepted,reason=d.reason,observationId=d.observation_id,pose=d.pose.snapshot() if d.pose else None,terminal=d.terminal)
def observation(o):
    return dict(observationId=o.observation_id,cycleId=o.cycle_id,mapId=o.map_id,floor=o.floor,frameGeneration=o.frame_generation,
        openedAt=o.opened_at,frameSignature=list(o.frame_signature),stages=o.stages,terminalVerdict=o.terminal_verdict,terminalReason=o.terminal_reason,closed=o.closed)
def core_state(core):
    return dict(snapshot=core.snapshot(),authorization=token(core.display_authorization),
        anchors=[dict(key=list(k),value=v) for k,v in sorted(core.anchor_pools.items())],
        cache=[dict(key=list(k),frames=[c.frame_generation for c in v],sources=[c.source for c in v]) for k,v in sorted(core.candidate_cache.items())],
        pending={str(k):[c.source for c in v] for k,v in sorted(core.pending_candidates.items())},events=core.events)
def execute_core(case):
    time=[10.];sink=[]
    core=AlignmentCoreV2(clock=lambda:time[0],event_sink=lambda event,payload:sink.append(copy.deepcopy(payload)),floors=tuple(case.get('floors',['B1','1','2'])))
    decisions={};tokens={};steps=[]
    for op in case['operations']:
        try:
            name=op['op'];result=None
            if name=='begin':result=observation(core.begin_observation(cycle_id=op.get('cycle',1),map_id=op.get('mapId','m'),floor=op.get('floor','1'),
                frame_generation=op.get('frame',1),frame_signature=op.get('signature',[]),reason=op.get('reason','open'),now=op.get('now')))
            elif name=='update':result=core.update_frame(frame_generation=op['frame'],floor=op.get('floor'),frame_signature=op.get('signature',[]))
            elif name=='stage':result=core.record_stage(op['name'],op['elapsed'])
            elif name=='seed':result=core.set_initial_pose(pose(op.get('pose',{})),reason=op.get('reason','initial'))
            elif name=='adjudicate':
                d=core.adjudicate(candidate(op.get('candidate',{})));decisions[op.get('save','d')]=d;result=decision(d)
            elif name=='authorize':
                t=core.authorize(decisions[op.get('decision','d')],source=op.get('source'),now=op.get('now'));tokens[op.get('save','t')]=t;result=token(t)
            elif name=='commit':result=core.commit(tokens[op.get('token','t')],persist_pose=op.get('persist',True))
            elif name=='can':result=core.can_display(authorization=tokens.get(op['token']) if 'token' in op else None,cycle_id=op.get('cycle',1),
                map_id=op.get('mapId','m'),floor=op.get('floor','1'),frame_generation=op.get('frame',1))
            elif name=='continuity':result=core.continuity_current(authorization=tokens.get(op['token']) if 'token' in op else None,cycle_id=op.get('cycle',1),
                map_id=op.get('mapId','m'),floor=op.get('floor','1'),frame_generation=op.get('frame',1))
            elif name=='revoke':core.revoke_display(reason=op.get('reason','hidden'))
            elif name=='close':result=core.close(cycle_id=op.get('cycle'),reason=op.get('reason','map-closed'))
            elif name=='reset':core.reset_round(reason=op.get('reason','round-reset'))
            elif name=='clock':time[0]=op['value'];result=time[0]
            elif name=='anchors':result=core.update_anchor_pool(map_id=op.get('mapId','m'),floor=op.get('floor','1'),anchors=copy.deepcopy(op.get('evidence',{})),frame_generation=op.get('frame',1),success=op.get('success',True))
            elif name=='cache':result=core.cache_candidate(candidate(op.get('candidate',{})),reason=op.get('reason','late-background-result'))
            elif name in ('shared','provisional'):
                t=core.authorize_shared_projection() if name=='shared' else core.authorize_provisional_target();tokens[op.get('save','t')]=t;result=token(t)
            elif name=='record':
                t=core.record_verified_display(map_id=op.get('mapId','m'),floor=op.get('floor','1'),matrix_source_to_client=np.array(op.get('matrix',P)),
                    source=op.get('source','verified-compositor'),frame_generation=op.get('frame',1),pose_verified=op.get('verified',True));tokens[op.get('save','t')]=t;result=token(t)
            elif name=='preserve':result=core.preserve_equivalent_display()
            else:raise ValueError(name)
        except (ValueError,TypeError,np.linalg.LinAlgError):result=dict(argumentError=True)
        state=core_state(core);state.update(eventSinkCount=len(sink),eventSinkLast=sink[-1] if sink else None)
        steps.append(dict(operation=op,result=plain(copy.deepcopy(result)),state=plain(copy.deepcopy(state))))
    return dict(name=case['name'],floors=case.get('floors',['B1','1','2']),steps=steps)

good={'current':True,'residual':True,'global':True,'metadata':{'routeProfileId':'shared-structure'}}
def adj(**kw):return dict(op='adjudicate',candidate={**good,**kw})
base=[dict(op='begin'),adj(),dict(op='authorize'),dict(op='commit'),dict(op='can')]
cases=[]
cases.append(dict(name='initial-stale-terminal-close-reset',operations=[dict(op='adjudicate'),dict(op='begin'),
    adj(mapId='other'),adj(floor='2'),adj(frame=0),adj(identityRerank=True),adj(current=False),adj(residual=False),dict(op='stage',name='extract',elapsed=-4),
    {**adj(),'save':'winner'},dict(op='stage',name='after-terminal',elapsed=5),adj(),dict(op='authorize',decision='winner'),dict(op='commit'),dict(op='commit'),dict(op='can'),
    dict(op='close',cycle='1'),dict(op='can'),dict(op='close',cycle=1),dict(op='commit'),dict(op='authorize',decision='winner',save='late'),dict(op='reset'),dict(op='begin',cycle='round-2',floor='2',frame=5)]))
cases.append(dict(name='continuity-expiry-and-preserve',operations=base+[dict(op='begin',frame=2),dict(op='can',frame=2),dict(op='preserve'),dict(op='can',frame=2),
    dict(op='begin',frame=3),dict(op='clock',value=12.5),dict(op='can',frame=3),dict(op='clock',value=12.5001),dict(op='can',frame=3),dict(op='preserve'),dict(op='begin',cycle=2,frame=4)]))
cases.append(dict(name='late-after-revoke-and-frame-update-source-semantics',operations=[dict(op='begin'),adj(),dict(op='authorize',save='first'),
    dict(op='revoke'),dict(op='commit',token='first'),dict(op='authorize',save='reissued'),dict(op='commit',token='reissued'),
    dict(op='update',frame=2),dict(op='authorize',save='old-adjudication-new-frame'),dict(op='commit',token='old-adjudication-new-frame'),dict(op='can',frame=2),
    dict(op='begin',cycle=2,frame=3),dict(op='authorize',save='foreign-observation'),dict(op='commit',token='first')]))
cases.append(dict(name='superseded-wrong-token-and-map-cycle-boundary',operations=[dict(op='begin'),adj(),dict(op='authorize',save='old'),
    dict(op='authorize',save='new'),dict(op='commit',token='old'),dict(op='can',token='old'),dict(op='commit',token='new'),
    dict(op='can',token='new',mapId='other'),dict(op='can',token='new',cycle='1'),dict(op='can',token='new'),
    dict(op='begin',mapId='other',frame=2),dict(op='commit',token='new'),dict(op='authorize',save='late-old-map')]))
cases.append(dict(name='anchors-floor-scope-cache-window',operations=[dict(op='anchors',success=False),dict(op='anchors',evidence={'anchors':[[1,2],[3,4]],'nested':{'mask':[0,1]}}),
    dict(op='anchors',frame=2,success=False),dict(op='anchors',floor='2',evidence={'anchors':[[10,20]]}),
    *[dict(op='cache',candidate={**good,'source':f'late-{i}','frame':i}) for i in range(11)],dict(op='begin'),dict(op='close'),dict(op='reset')]))
cases.append(dict(name='same-floor-translation-only-and-linear-tolerance',operations=[dict(op='seed',pose={'matrix':P,'relations':{'2':R}}),dict(op='begin'),
    adj(source='same-floor-reopen-v2',translationOnly=False),adj(source='same-floor-reopen-v2',translationOnly=True,matrix=[[.41,0,30],[0,.5,-20],[.001,-.002,1]]),
    adj(source='same-floor-reopen-v2',translationOnly=True,matrix=[[.400005,0,31],[0,.5,-19],[.001,-.002,1]]),dict(op='authorize'),dict(op='commit')]))
for kind,count,confidence in [('door',1,.8),('dynamic-anchor',2,.95),('dynamic-anchor',3,.8),('dynamic-anchor',3,.7999),('global',10,1)]:
    cases.append(dict(name=f'anchor-{kind}-{count}-{confidence}',operations=[dict(op='begin'),adj(anchorKind=kind,anchorCount=count,confidence=confidence,residual=False,**{'global':False}),dict(op='authorize')]))
cases.append(dict(name='fixed-reviewed-crossfloor-and-independent-floor',operations=[dict(op='seed',pose={'relations':{'2':R}}),dict(op='begin',floor='2'),
    adj(floor='2',crossFloor=True,deterministic=False,localCorrection=True),adj(floor='2',crossFloor=True,deterministic=True,localCorrection=False),
    adj(floor='2',crossFloor=True,deterministic=True,localCorrection=True,matrix=(np.array(P)@np.array(R)).tolist()),dict(op='authorize'),dict(op='commit'),
    dict(op='begin',floor='B1',frame=2),adj(floor='B1',frame=2),adj(floor='B1',frame=2,metadata={'independentFloorAnchor':True}),dict(op='authorize'),dict(op='commit')]))
for reliable in [False,True]:
    cases.append(dict(name=f'projection-reliable-{reliable}',operations=[dict(op='seed',pose={'reliable':reliable}),dict(op='begin',floor='2'),dict(op='shared'),dict(op='provisional'),
        dict(op='can',floor='2'),dict(op='begin',floor='B1',frame=2),dict(op='shared'),dict(op='provisional')]))
cases.append(dict(name='prepared-projection-no-persist-record-refresh',operations=[dict(op='seed',pose={'relations':{'2':R}}),dict(op='begin',floor='2'),dict(op='shared'),
    dict(op='can',floor='2'),dict(op='record',floor='2',frame=0),dict(op='record',floor='2',verified=False),dict(op='record',floor='2',matrix=(np.array(P)@np.array(R)).tolist()),
    dict(op='update',frame=2,floor='1'),dict(op='record',frame=2),dict(op='update',frame=3,floor='unsupported'),dict(op='close')]))
cases.append(dict(name='custom-s1-floors-and-no-projection-policy-expansion',floors=['-1','-2','-3','-4'],operations=[dict(op='begin',floor='-1'),adj(floor='-1'),
    dict(op='authorize'),dict(op='commit'),dict(op='begin',floor='-2',frame=2),dict(op='shared'),dict(op='provisional'),adj(floor='-2',frame=2,metadata={'independentFloorAnchor':True})]))
cases.append(dict(name='event-tail-256',operations=[*[dict(op='cache',candidate={**good,'source':f'cache-{i}'}) for i in range(260)],dict(op='reset')]))

class Adapters:
    def __init__(self,data):self.data=data
    def compose_profile_to_client_from_canonical(self,profile,mid,floor,canonical):
        matrix=self.data.get('profile',{}).get(floor);return None if matrix is None else canonical@np.array(matrix)
    def recognition_from_canonical_to_client(self,mid,floor,canonical):
        matrix=self.data.get('recognition',{}).get(floor);return None if matrix is None else canonical@np.array(matrix)
def execute_group(case):
    p=case['initial'];g=RoundLayerGroup('m',p.get('profile','source-structure'),'1',np.array(p.get('matrix',P)),
        relations={k:np.array(v) for k,v in p.get('relations',{}).items()},pose_client_size=p.get('clientSize'),pose_display_signature=tuple(p.get('signature',[])))
    steps=[];adapters=Adapters(case.get('adapters',{}))
    for op in case['operations']:
        name=op['op'];result=None
        try:
            if name=='relation':result=g.set_relation(op['floor'],np.array(op['matrix']),metadata=op.get('metadata'))
            elif name=='canonical':result=g.canonical_pose_for_floor(op['floor'])
            elif name=='profile':result=g.profile_pose_for_floor(None if op.get('noAdapters') else adapters,op['floor'])
            elif name=='recognition':result=g.recognition_pose_for_floor(None if op.get('noAdapters') else adapters,op['floor'])
            elif name=='translate':result=g.apply_translation(op['dx'],op['dy'],source=op.get('source','drag'),cycle_id=op.get('cycle',1))
            elif name=='reproject':result=g.reproject_client_pose(op.get('size'),source=op.get('source','resize'),cycle_id=op.get('cycle',1))
            elif name=='adopt':result=g.adopt_floor_canonical_pose(op['floor'],np.array(op['matrix']),source=op.get('source','fit'),cycle_id=op.get('cycle',1),maximum_linear_delta_ratio=op.get('maximum',.65))
            elif name=='tracking':g.set_tracking(op['floor'],copy.deepcopy(op['evidence']),cycle_id=op.get('cycle',1))
            elif name=='trackget':result=g.tracking_for_floor(op['floor'])
            elif name=='display':g.mark_displayed(op['floor'],np.array(op.get('pixels',[[0,1],[2,3]])),provisional=op.get('provisional',False),
                render_profile_verified=op.get('render',False),canonical_geometry_verified=op.get('canonical',False),source_structure_verified=op.get('source',False))
            else:raise ValueError(name)
        except (ValueError,TypeError,np.linalg.LinAlgError):result=dict(argumentError=True)
        steps.append(dict(operation=op,result=plain(copy.deepcopy(result)),state=plain(dict(snapshot=g.snapshot(),relations=g.relations,
            relationMetadata=g.relation_metadata,tracking=g.tracking,trackingByFloor=g.tracking_by_floor,overlay=g.displayed_overlay))))
    return dict(name=case['name'],initial=case['initial'],adapters=case.get('adapters',{}),steps=steps)
tracking={'translationMask':[[1,0],[0,1]],'translationMaskMatrix':P,'layoutAnchorMask':[[1]],'layoutAnchorMatrix':P,
    'translationAnchorHistory':[{'point':[4,5]}],'bounds':[1,2,3,4],'poseVerifiedAgainstRenderedProfile':True,
    'poseVerifiedAgainstCanonicalGeometry':True,'poseVerifiedAgainstLiveGeometry':True,'renderedProfileResidual':{'verified':True},'retained':'same'}
groups=[dict(name='full-matrix-relations-tracking-resize',initial={'matrix':P,'relations':{'2':R},'clientSize':[1920,1080],'signature':['display',1]},operations=[
    dict(op='canonical',floor='B1'),dict(op='relation',floor='B1',matrix=I,metadata={'reviewed':True,'nested':{'id':'fixed'}}),dict(op='relation',floor='unknown',matrix=I),
    dict(op='canonical',floor='2'),dict(op='profile',floor='2',noAdapters=True),dict(op='recognition',floor='2',noAdapters=True),
    dict(op='tracking',floor='1',evidence=tracking),dict(op='tracking',floor='2',evidence={**tracking,'floorNote':'two'}),dict(op='trackget',floor='B1'),
    dict(op='display',floor='2',provisional=True,render=True,canonical=True),dict(op='translate',dx=1e-8,dy=0),dict(op='translate',dx=8,dy=-3),
    dict(op='reproject',size=[1280,1024]),dict(op='trackget',floor='1'),dict(op='trackget',floor='2'),dict(op='display',floor='2',source=True),dict(op='canonical',floor='2')]),
    dict(name='adopt-no-relation-singular-and-linear-limits',initial={'matrix':I},operations=[dict(op='adopt',floor='2',matrix=I),dict(op='relation',floor='2',matrix=[[0,0,0],[0,0,0],[0,0,0]]),
        dict(op='adopt',floor='2',matrix=I),dict(op='relation',floor='2',matrix=R),dict(op='adopt',floor='2',matrix=(np.array(I)@np.array(R)).tolist()),
        dict(op='adopt',floor='1',matrix=[[3,0,0],[0,3,0],[0,0,1]]),dict(op='adopt',floor='1',matrix=[[1,0,.1],[0,1,.1],[0,0,1]]),
        dict(op='adopt',floor='1',matrix=[[1,0,4],[0,1,-7],[.01,0,1]])]),
    dict(name='resize-missing-source-and-invalid-target',initial={'matrix':P},operations=[dict(op='reproject',size=[0,10]),dict(op='reproject',size=[1280,720]),dict(op='reproject',size=[2560,1440])]),
    dict(name='profile-and-recognition-static-adapters',initial={'matrix':P,'profile':'author','relations':{'2':R}},adapters={'profile':{'2':R},'recognition':{'2':I}},
        operations=[dict(op='profile',floor='2'),dict(op='recognition',floor='2'),dict(op='profile',floor='1'),dict(op='recognition',floor='1'),dict(op='profile',floor='2',noAdapters=True)])]

evidence=[]
source_good=dict(verified=True,poseVerifiedAgainstSourceStructure=True,validationBasis='source-structure-heldout-current-frame',
    sourceCoordinatesVerified=True,sourceFloorIsProducerLabelOnly=True,sourceCoordinateSpace='producer-source-png-pixels',sourcePngIsDisplayImage=True,
    sourceGroup='explicit-source',gameFloor='1',heldOutInliers=5,heldOutCellCount=4,proposedTransformMedianError=4.,proposedTransformP90Error=8.)
for label,residual,basis,pack,bridge in [('canonical-valid',{'verified':True},'game-rendered-shared-structure','pack',True),
    ('canonical-no-pack',{'verified':True},'game-rendered-shared-structure','',True),('canonical-no-bridge',{'verified':True},'game-rendered-shared-structure','pack',False),
    ('canonical-nonbool-verified',{'verified':1},'game-rendered-shared-structure','pack',True),('canonical-not-verified',{'verified':False},'other','pack',True)]:
    wrapped=canonical_residual_evidence(copy.deepcopy(residual),basis=basis,coordinate_pack_id=pack,profile_bridge_verified=bridge)
    evidence.append(dict(name=label,residual=residual,basis=basis,pack=pack,bridge=bridge,wrapped=plain(wrapped),verified=rendered_profile_residual_is_verified(wrapped)))
for label,changes in [('source-valid',{}),('source-low-inliers',{'heldOutInliers':4}),('source-low-cells',{'heldOutCellCount':3}),
    ('source-median-too-large',{'proposedTransformMedianError':4.01}),('source-wrong-floor',{'gameFloor':'-1'}),('source-float-floor',{'gameFloor':1.0}),
    ('source-no-coordinates',{'sourceCoordinatesVerified':False}),('source-string-counts',{'heldOutInliers':'5','heldOutCellCount':'4'}),
    ('source-bad-counts',{'heldOutInliers':'5.0'}),('source-null-error',{'proposedTransformMedianError':None})]:
    residual={**source_good,**changes};evidence.append(dict(name=label,residual=residual,verified=rendered_profile_residual_is_verified(residual)))
for label,residual in [('legacy-valid',dict(verified=True,poseVerifiedAgainstRenderedProfile=True,validationBasis='actual-rendered-profile-structural-mask')),
    ('legacy-wrong-era',dict(verified=True,poseVerifiedAgainstRenderedProfile=True,validationBasis='shared-canonical-geometry-current-frame')),
    ('null',None)]:evidence.append(dict(name=label,residual=residual,verified=rendered_profile_residual_is_verified(residual)))

output=dict(scope='pure production state/authorization and round group; explicit synthetic verification inputs, no game acceptance',
    sourceCommit='6a541090b693ab3de47006200b85b14fa955aced',coreCases=[execute_core(c) for c in cases],groupCases=[execute_group(c) for c in groups],evidenceCases=evidence)
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps(dict(output=str(args.output),coreCases=len(cases),coreSteps=sum(len(c['steps']) for c in output['coreCases']),
    groupCases=len(groups),groupSteps=sum(len(c['steps']) for c in output['groupCases']),evidenceCases=len(evidence))))
