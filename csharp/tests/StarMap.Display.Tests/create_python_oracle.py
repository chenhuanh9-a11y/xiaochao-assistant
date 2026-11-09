"""Production Python entry points, real resources and labelled synthetic poses.

No game screenshots are available for this migration check. This produces an oracle;
it never changes the resource tree. Synthetic native-corner images live beside output.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=Path)
parser.add_argument('--resources', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.source / 'app'))
import cv2
import numpy as np
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
from author_route_catalog import AuthorRouteCatalog
from author_display_policy import shared_route_validation_spec
from shared_structure_reference import SharedStructureResourceStore
from native_static_corner_registration import (NativeCornerRequest, reconstruct_native_corner_pairs,
    validate_native_static_corner_registration)
from nightmare_candidate import author_route_structure_mask

root = args.resources.resolve()
args.output.parent.mkdir(parents=True, exist_ok=True)
out = dict(scope='production resource/static display bridge; synthetic native poses; no live game acceptance',
           opencv=cv2.__version__, numpy=np.__version__, adapterCases=[], bridgeCases=[], nativeCornerCases=[], calibrationCases=[])

contexts = [('s0','nightmare',root, dict(coordinatePack='references/nightmare-canonical-coordinate-pack.json',
    typedIndex='references/nightmare-shared-structure-index.json',authorCatalog='references/nightmare-author-route-catalog.json'))]
difficult = json.loads((root/'difficult-native-manifest.json').read_text('utf-8'))
contexts.append(('s0','difficult',root,difficult['runtimeInterfaces']))
for manifest_path in sorted((root/'seasons').glob('*/*/native-map-manifest.json')):
    manifest=json.loads(manifest_path.read_text('utf-8'))
    contexts.append((manifest['season'],manifest['mode'],manifest_path.parent,manifest['runtimeInterfaces']))

# Exercise unchanged production admission gates on copies, away from the source tree.
negative_dir=args.output.parent/'adapter-negative-fixtures'
negative_dir.mkdir(exist_ok=True)
original=json.loads((root/'references/nightmare-canonical-coordinate-pack.json').read_text('utf-8'))
for label in ['wrong-floor','cross-floor','singular','unverified-native','insufficient-evidence']:
    document=copy.deepcopy(original)
    profile='suju';mid='nightmare-01';floor='1'
    row=document['profiles'][profile]['maps'][mid]['floors'][floor]
    if label=='wrong-floor': floor='other-floor'
    if label=='cross-floor': row['crossFloorProjectionAllowed']=True
    if label=='singular': row['matrixProfileToCanonical'][0][0]=0
    if label=='unverified-native': row['evidence']['type']='native-static-corner-registration'
    if label=='insufficient-evidence': row['evidence']={}
    path=negative_dir/(label+'.json')
    path.write_text(json.dumps(document),encoding='utf-8')
    adapter=AuthorCoordinateAdapters(path)
    matrix=adapter.matrix_profile_to_canonical(profile,mid,floor)
    out['adapterCases'].append(dict(document=document,profile=profile,mapId=mid,floor=floor,
        accepted=matrix is not None,matrix=matrix.tolist() if matrix is not None else None,label=label))

for season, mode, scope, api in contexts:
    adapter=AuthorCoordinateAdapters(scope/api['coordinatePack'])
    catalog=AuthorRouteCatalog(scope/api['authorCatalog'],asset_base=scope)
    store=SharedStructureResourceStore(scope/api['typedIndex'],scope)
    for profile, p in adapter.profiles.items():
        for mid, maprow in p.get('maps',{}).items():
            for floor in maprow.get('floors',{}):
                matrix=adapter.matrix_profile_to_canonical(profile,mid,floor)
                out['adapterCases'].append(dict(path=str((scope/api['coordinatePack']).relative_to(root)).replace('\\','/'),
                    profile=profile,mapId=mid,floor=floor,accepted=matrix is not None,
                    matrix=matrix.tolist() if matrix is not None else None))
    s1_context=None
    if season=='s1':
        from s1_native_resources import S1NativeContext
        s1_context=S1NativeContext(scope,mode)
    for (identity,mid,variant,floor), index in sorted(store.entries.items()):
        reference=store.load(identity,mid,floor,variant=variant)
        if reference is None:
            raise RuntimeError(f'production reference unavailable: {season}/{mode}/{mid}/{floor}')
        # Chosen independently of any historical answer or display matrix.
        pose=np.array([[.37,0.,213.],[0.,.42,-47.],[0.,0.,1.]])
        calibration=reference.reference_to_canvas_matrix
        reference_to_native=np.eye(3) if calibration is None else calibration
        native_to_canonical=adapter.matrix_profile_to_canonical('shared-structure',mid,floor)
        canonical_to_client=pose@np.linalg.inv(native_to_canonical@reference_to_native)
        display_profiles=['shared-structure']+[p for p in adapter.profiles if p!='shared-structure']
        for profile in display_profiles:
            display_to_canonical=adapter.matrix_profile_to_canonical(profile,mid,floor)
            display_to_reference=None
            display_to_client=None
            image=None
            accepted=display_to_canonical is not None
            if profile=='shared-structure' and s1_context is not None:
                resolved=s1_context._rendered_display.resolve(mid,floor,reference)
                accepted=resolved.layer is not None
                if accepted:
                    image=Path(resolved.layer['resourceIdentity']['path'])
            elif profile=='shared-structure':
                # These pixels are the actual current-reference clean array. Execute
                # the original neutral-display consumer instead of assuming native pixels.
                assistant=SimpleNamespace(author_coordinate_adapters=adapter,pack=SimpleNamespace(pack_id=adapter.canonical_pack_id),
                    _shared_structure_reference=lambda *a: reference)
                spec=shared_route_validation_spec(assistant,profile,mid,floor,pose,reference.clean_mask)
                accepted=spec is not None and bool(spec['profileBridgeVerified'])
                if accepted:
                    display_to_reference=spec['profileToValidation']
                    display_to_client=spec['matrixSourceToClient']@display_to_reference
                    display_to_canonical=native_to_canonical@reference_to_native@display_to_reference
            elif profile!='shared-structure':
                image=catalog.route_path(profile,mid,floor,include_inactive=True)
                accepted=accepted and image is not None
                if accepted:
                    # Execute the original selected-author validation contract too.
                    assistant=SimpleNamespace(author_coordinate_adapters=adapter,pack=SimpleNamespace(pack_id=adapter.canonical_pack_id),
                        _shared_structure_reference=lambda *a: reference)
                    render=adapter.compose_profile_to_client_from_canonical(profile,mid,floor,canonical_to_client)
                    spec=shared_route_validation_spec(assistant,profile,mid,floor,render,object())
                    accepted=spec is not None and bool(spec['profileBridgeVerified'])
            expected=dict(nativeToCanonical=native_to_canonical.tolist(),displayToCanonical=display_to_canonical.tolist(),
                displayToReference=(display_to_reference if display_to_reference is not None else
                    np.linalg.inv(native_to_canonical@reference_to_native)@display_to_canonical).tolist(),
                displayToClient=(display_to_client if display_to_client is not None else
                    adapter.compose_profile_to_client_from_canonical(profile,mid,floor,canonical_to_client)).tolist(),
                canonicalToClient=canonical_to_client.tolist()) if accepted else None
            out['bridgeCases'].append(dict(season=season,mode=mode,identityProfile=identity,profile=profile,
                mapId=mid,floor=floor,referenceToClient=pose.tolist(),accepted=bool(accepted),expected=expected,
                imagePath=str(image) if image else None))
        if season=='s0' and mode=='nightmare' and mid=='nightmare-01':
            from base_map_display import BaseMapDisplay
            from custom_maps import CustomMapLibrary
            library=CustomMapLibrary(root/'assets/custom-maps')
            resolved=BaseMapDisplay().resolve(library,mid,floor,reference)
            accepted=resolved.layer is not None
            expected=None;image=None
            if accepted:
                asset=library.floor(mid,floor)
                display_to_canonical=native_to_canonical@asset.to_native
                image=asset.image_path
                expected=dict(nativeToCanonical=native_to_canonical.tolist(),displayToCanonical=display_to_canonical.tolist(),
                    displayToReference=(np.linalg.inv(native_to_canonical@reference_to_native)@display_to_canonical).tolist(),
                    displayToClient=(pose@np.linalg.inv(reference_to_native)@asset.to_native).tolist(),canonicalToClient=canonical_to_client.tolist())
            out['bridgeCases'].append(dict(season=season,mode=mode,identityProfile=identity,profile='base-map',mapId=mid,
                floor=floor,referenceToClient=pose.tolist(),accepted=bool(accepted),expected=expected,imagePath=str(image) if image else None))

# Independently constructed calibrated NPZ: no real package is copied or modified.
# Both the reference->native calibration and native->canonical chart are nonidentity.
# The neutral consumer must still map the actual clean pixels directly by caller pose.
calibrated_root=args.output.parent/'calibrated-clean-resource'
calibrated_references=calibrated_root/'references'
calibrated_references.mkdir(parents=True,exist_ok=True)
mid='synthetic-calibrated-clean';floor='1';layout='synthetic-layout'
binding=dict(profile='shared-structure',mapId=mid,floor=floor,variant='variant-01')
version=dict(structureVersionKey='synthetic-calibrated-layout/1',sourceVersion='synthetic-source-v1',baseSnapshot='synthetic-calibration-fixture')
canvas=dict(coordinateSpace=f'game-native-pixels:{layout}:{floor}',layout=layout,floor=floor,worldFloor=2,
    width=48,height=32,shapeHW=[32,48],axes='x-right,y-down')
reference_to_native=np.array([[1.25,0.,10.],[0.,.75,-6.],[0.,0.,1.]])
native_to_canonical=np.array([[1.5,0.,7.],[0.,2.,-3.],[0.,0.,1.]])
pose=np.array([[.4,0.,30.],[0.,.5,-20.],[0.,0.,1.]])
clean=np.zeros((32,48),np.uint8)
clean[4:26,5:43]=1
clean[10:14,16:22]=0
relative='references/calibrated-shared.npz'
metadata=dict(sourceKind='game-rendered-shared-structure',binding=binding,canvas=canvas,
    roles=dict(clean='shared_mask',validation='shared_mask'),
    calibration=dict(matrixCurrentReferenceToNative=reference_to_native.tolist()),**version)
np.savez_compressed(calibrated_root/relative,metadata=np.asarray(json.dumps(metadata)),shared_mask=clean)
index=dict(schemaVersion=1,resourceKind='shared-structure',entries=[dict(**binding,resourcePath=relative,**version)])
coordinate=dict(coordinateWidth=48,coordinateHeight=32,coordinateSpace=canvas['coordinateSpace'],sourceLayout=layout,
    worldFloor=2,referenceResource=relative,referenceLayerRole='typed-game-structure-only',**version)
pack=dict(schema='identityv-nightmare-canonical-coordinate-pack-v1',canonicalPackId='synthetic-calibration-fixture',
    coordinateReferenceProfileId='shared-structure',policy=dict(requiresCurrentFrameGeometryFit=True,
    authorizesMapIdentity=False,authorizesCrossFloorProjection=False),maps={mid:dict(floors={floor:coordinate})},
    profiles={'shared-structure':dict(maps={mid:dict(floors={floor:dict(matrixProfileToCanonical=native_to_canonical.tolist(),
        usableForCurrentFrameRendering=True,crossFloorProjectionAllowed=False,evidence=dict(algebraicallyExact=True))})})})
documents={
    'nightmare-map-pack.json':dict(mode='nightmare',floors=[floor],maps=[dict(id=mid,name='Synthetic calibrated clean pixels',
        identityProfileId='shared-structure',sourceLayout=layout,sourceVersion=version['sourceVersion'],layers={floor:relative})]),
    'nightmare-shared-structure-index.json':index,
    'nightmare-canonical-coordinate-pack.json':pack,
    'nightmare-structure-display.json':dict(entries=[dict(mapId=mid,floor=floor,binding=binding,referenceResource=relative,canvas=canvas,**version)]),
    'nightmare-structure-version-bindings.json':dict(entries=[]),
    'nightmare-author-route-catalog.json':dict(schemaVersion=1,profiles=[]),
}
for name,document in documents.items():
    (calibrated_references/name).write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
calibrated_store=SharedStructureResourceStore(calibrated_references/'nightmare-shared-structure-index.json',calibrated_root)
calibrated_reference=calibrated_store.load('shared-structure',mid,floor)
if calibrated_reference is None or not np.array_equal(calibrated_reference.clean_mask,clean):
    raise RuntimeError('Synthetic calibrated NPZ did not roundtrip through production structure loader')
calibrated_adapter=AuthorCoordinateAdapters(calibrated_references/'nightmare-canonical-coordinate-pack.json')
assistant=SimpleNamespace(author_coordinate_adapters=calibrated_adapter,pack=SimpleNamespace(pack_id=calibrated_adapter.canonical_pack_id),
    _shared_structure_reference=lambda *a: calibrated_reference)
spec=shared_route_validation_spec(assistant,'shared-structure',mid,floor,pose,calibrated_reference.clean_mask)
if spec is None or spec['sharedReference'] is not calibrated_reference:
    raise RuntimeError('Production neutral display did not retain actual calibrated reference')
native_to_canonical=calibrated_adapter.matrix_profile_to_canonical('shared-structure',mid,floor)
reference_to_native=calibrated_reference.reference_to_canvas_matrix
reference_to_canonical=native_to_canonical@reference_to_native
canonical_to_client=pose@np.linalg.inv(reference_to_canonical)
point=np.array([12.,9.,1.])
canonical_point=reference_to_canonical@point
client_point=spec['matrixSourceToClient']@spec['profileToValidation']@point
# Independent scalar geometry, rather than a repeated copy of the bridge formula.
if not np.allclose(canonical_point,[44.5,-1.5,1.],rtol=0,atol=1e-12) or not np.allclose(client_point,[34.8,-15.5,1.],rtol=0,atol=1e-12):
    raise RuntimeError('Synthetic calibration scalar geometry disagrees with production neutral consumer')
out['calibrationCases'].append(dict(label='nonidentity-reference-calibration-clean-pixels',resourceRoot=str(calibrated_root.resolve()),
    season='s0',mode='nightmare',identityProfile='shared-structure',profile='shared-structure',mapId=mid,floor=floor,
    source='independent synthetic typed NPZ; original shared_route_validation_spec with actual clean pixels',
    referenceToClient=pose.tolist(),referenceToNative=reference_to_native.tolist(),accepted=True,
    expected=dict(nativeToCanonical=native_to_canonical.tolist(),displayToCanonical=reference_to_canonical.tolist(),
        displayToReference=spec['profileToValidation'].tolist(),displayToClient=spec['matrixSourceToClient'].tolist(),
        canonicalToClient=canonical_to_client.tolist()),
    probe=dict(referencePoint=point[:2].tolist(),canonicalPoint=canonical_point[:2].tolist(),clientPoint=client_point[:2].tolist())))

# Direct production validator with new synthetic native/author images. The expected
# correspondence set is reconstructed from pixels by production code, never injected.
fixtures=args.output.parent/'display-fixtures'
fixtures.mkdir(exist_ok=True)
mask=np.zeros((1000,1000),np.uint8)
for y in range(8):
    for x in range(8):
        cv2.rectangle(mask,(45+x*110,40+y*110),(85+x*110,95+y*110),255,-1)
author=np.zeros((1000,1000,4),np.uint8)
author[mask>0]=[120,120,120,255]
author_path=fixtures/'author.png';native_path=fixtures/'native.png'
cv2.imencode('.png',author)[1].tofile(str(author_path))
cv2.imencode('.png',mask)[1].tofile(str(native_path))
request=NativeCornerRequest(profile='zhan10-speed',map_id='synthetic-map',floor='synthetic-floor',
    variant='variant-01',native_layout='synthetic-layout',native_floor=7)
primary=np.array([[1.2,0.,21.],[0.,1.2,-13.],[0.,0.,1.]])
sources=dict(authorResource=dict(kind='author-route-catalog',profile=request.profile,mapId=request.map_id,
    floor=request.floor,variant=request.variant),nativeResource=dict(kind='author-structure-native-mask',profile='primary-chart',
    mapId=request.map_id,floor=request.floor,variant=request.variant,nativeLayout=request.native_layout,nativeFloor=request.native_floor,
    resourcePath='trusted/native.png'),canonicalGeometryReference=dict(profile='primary-chart',mapId=request.map_id,
    floor=request.floor,role='independent-fixed-coordinate-chart'))
context=dict(request=request,entry=dict(matrixAuthorProfileToGamePixel=np.eye(3).tolist()),source_path=author_path,
    native_path=native_path,source_mask=author_route_structure_mask(author,profile_id=request.profile),
    native_mask=(mask>0).astype(np.uint8),primary_profile_to_canonical=primary,expected_sources=sources)
reconstruction=reconstruct_native_corner_pairs(context)
evidence=dict(type='native-static-corner-registration',binding=dict(profile=request.profile,mapId=request.map_id,
    floor=request.floor,variant=request.variant,nativeLayout=request.native_layout,nativeFloor=request.native_floor),
    sources=sources,pairs=reconstruction['rows'])
for label in ['positive','forged-point','wrong-floor','bad-residual','overflow']:
    candidate=primary.copy();item=copy.deepcopy(evidence)
    if label=='forged-point': item['pairs'][0]['sourcePoint'][0]+=1
    if label=='wrong-floor': item['binding']['floor']='other-floor'
    if label=='bad-residual': candidate[0,2]+=100
    if label=='overflow': candidate[0,0]=1e308;candidate[1,1]=1e308
    verdict=validate_native_static_corner_registration(candidate,item,context)
    out['nativeCornerCases'].append(dict(label=label,accepted=bool(verdict['accepted']),reasons=verdict['reasons'],
        profile=request.profile,mapId=request.map_id,floor=request.floor,variant=request.variant,nativeLayout=request.native_layout,
        nativeFloor=request.native_floor,defaultProfile='wang-qingxin',canonicalProfile='primary-chart',authorPath=str(author_path),
        nativeMaskPath=str(native_path),nativeWidth=1000,nativeHeight=1000,nativeBytes=native_path.stat().st_size,
        nativeRelativePath='trusted/native.png',primaryProfileToCanonical=primary.tolist(),primaryProfileToNative=np.eye(3).tolist(),
        candidateProfileToCanonical=candidate.tolist(),evidence=item))
if not out['nativeCornerCases'][0]['accepted']:
    raise RuntimeError('Synthetic positive did not satisfy unchanged production validator')
args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps(dict(output=str(args.output),adapters=len(out['adapterCases']),bridges=len(out['bridgeCases']),
    nativeCorners=len(out['nativeCornerCases']),calibrations=len(out['calibrationCases']),
    accepted=sum(c['accepted'] for c in out['bridgeCases'])),ensure_ascii=False))
