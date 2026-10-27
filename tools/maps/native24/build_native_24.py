"""Independent 24-map/72-layer native data candidate; no application writes."""
import hashlib
import argparse
import json
import shutil
import sys
from pathlib import Path
sys.dont_write_bytecode = True
import cv2
import numpy as np
from PIL import Image

BASE = OUT = ROOT = RUNTIME = BUNDLE = None
PROFILE = 'zhan10-speed'  # Existing lookup key only, never an author pixel source.
I = np.eye(3).tolist()
TARGET_OPENCV = '4.12.0'

def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    global BASE, OUT, ROOT, RUNTIME, BUNDLE
    parser = argparse.ArgumentParser()
    parser.add_argument('--target-opencv', default=TARGET_OPENCV, choices=[TARGET_OPENCV])
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--baseline-bundle', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    BASE=args.workspace.resolve();OUT=BASE/'batch-native-24-72';ROOT=OUT/'runtime-root'
    RUNTIME=args.runtime_root.resolve();BUNDLE=args.baseline_bundle.resolve()
    if cv2.__version__ != args.target_opencv:
        raise RuntimeError(f'Contour extraction requires target OpenCV {args.target_opencv}; '
                           f'actual {cv2.__version__} at {sys.executable}. Run with the target runtime interpreter.')
    if OUT.exists():
        raise FileExistsError(f'Preserve prior output: {OUT}')
    plan = read(BASE / 'target-24-native-plan.json')
    assert (plan['targetMaps'], plan['targetLayers'], len(plan['entries'])) == (24,72,72)
    assert not plan['gaps']
    refs = ROOT / 'references'
    data = refs / 'nightmare-entries'
    data.mkdir(parents=True)
    sys.path.insert(0, str(RUNTIME / 'app'))
    from single_map_structure import contour_graph
    old_pack = read(BUNDLE / '_internal/references/nightmare-map-pack.json')
    old_maps = {r['id']:r for r in old_pack['maps']}
    shared_entries, contours, gaps, inventory, copies = [], [], [], [], []
    maps, catalog_maps, canonical_maps = {}, {}, {}
    reference_maps, display_maps = {}, {}
    bindings = []
    version_rows = []
    catalog_cache = {}
    source_version = None
    for n, row in enumerate(plan['entries'], 1):
        mid, floor, layout = row['runtimeMapId'], row['floor'], row['sourceLayout']
        png, source_path = Path(row['nativeSourcePNG']), Path(row['nativeSourceMetadata'])
        source = read(source_path)
        source_catalog_path = source['sourceCatalog']['sourceFile']
        if source_catalog_path not in catalog_cache:
            catalog_bytes = Path(source_catalog_path).read_bytes()
            assert hashlib.sha256(catalog_bytes).hexdigest() == source['sourceCatalog']['sourceFileSha256']
            catalog_cache[source_catalog_path] = json.loads(catalog_bytes.decode('utf-8-sig'))
        source_catalog = catalog_cache[source_catalog_path]
        if source_version is None: source_version = source_catalog['gameConfigVersion']
        assert source_catalog['gameConfigVersion'] == source_version
        raw_map = source_catalog['maps'][layout]
        raw_record = dict(room_data=raw_map['roomDataRaw'],door={
            str(d['doorIndex']):[d['roomIndex'],d['doorSlot']] for d in raw_map['doors']})
        raw_sha = hashlib.sha256(json.dumps(raw_record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        structure_version_key = f'{layout}@{raw_sha}/{floor}'
        version_rows.append(dict(mapId=mid,resourceKey=dict(layoutId=layout,
            structureRawRecordSha256=raw_sha,floor=floor),
            authorDisplay=dict(originalBaseSnapshot=row['displaySnapshot'])))
        canonical_meta_path = Path(row['canonicalSource']).with_name('source.json')
        canonical = read(canonical_meta_path)['render']
        assert str(source['levelId']) == layout and source['floor'] == row['sourceWorldFloor']
        assert source['targetPixelsPerVoxel'] == 60
        assert source['bounds'] == canonical['bounds'] and source['placements'] == canonical['placements']
        image = cv2.imdecode(np.fromfile(png, np.uint8), cv2.IMREAD_UNCHANGED)
        w, h = row['nativeCanvasWH']
        assert image.shape == (h,w,4)
        mask = (image[:,:,3] > 0).astype(np.uint8)
        assert mask.any() and set(np.unique(mask)) <= {0,1}
        binding = dict(profile=PROFILE, mapId=mid, floor=floor, variant='variant-01')
        bindings.append(binding)
        canvas = dict(coordinateSpace=f'game-native-pixels:{layout}:{floor}',
                      layout=layout, floor=floor, worldFloor=source['floor'],
                      width=w, height=h, shapeHW=[h,w], axes='x-right,y-down')
        geometry = dict(rendererImage=str(png), rendererMetadata=str(source_path),
            layout=layout, worldFloor=source['floor'], targetPixelsPerVoxel=60,
            bounds=source['bounds'], layoutSource=source.get('candidateProvenance',{}).get('layoutSource'),
            imageSource=source.get('candidateProvenance',{}).get('imageSource'),
            imageBytesReextracted=False, alphaRule='alpha>0', nativeShapeHW=[h,w],
            rendererVerified=source.get('verified'), rendererModules=source.get('rendererModules'),
            rendererUnverified=source.get('diagnostics',{}).get('unverified',[]),
            skippedRoomFloors=row['sourceMissingRoomFloors'],
            production='direct renderer alpha>0, without projection, resize or author pixels',
            matrixRendererPixelToProduct=I)
        meta = dict(sourceKind='game-rendered-shared-structure', binding=binding, canvas=canvas,
            sourceVersion=source_version,structureVersionKey=structure_version_key,baseSnapshot=row['displaySnapshot'],
            roles=dict(clean='shared_mask', validation='shared_mask'),
            roleSemantics=dict(shared_mask='room/corridor texture footprint, not semantic walls or walkable grid',
                validation='same shared field, not independent validation evidence'), sourceGeometry=geometry,
            missingChannels=['semantic-known-boundary','semantic-unknown-mask','native-wall-segments','walkable-grid'],
            missingChannelPolicy='Unavailable channels remain absent; no fake all-known or all-clear arrays.',
            authorPixelsUsedForStructure=False, authorValidationUsed=False,
            sourceImagePixelsStored=False, authorFeatureCacheStored=False)
        archive = data / f'{mid}-{floor}-shared.npz'
        np.savez_compressed(archive, shared_mask=mask, metadata=np.asarray(json.dumps(meta,ensure_ascii=False)))
        rel_archive = archive.relative_to(ROOT).as_posix()
        shared_entries.append(dict(binding,resourcePath=rel_archive))
        nodes = contour_graph(mask)
        ids = [i for i,node in enumerate(nodes) if node['valid']]
        lookup = {old:new for new,old in enumerate(ids)}
        turns = [dict(point=nodes[i]['point'].tolist(), rays=nodes[i]['rays'].tolist(),
                      next=lookup.get(nodes[i]['next'],-1), sourceNode=i) for i in ids]
        assert turns
        for turn in turns:
            p, rays = np.asarray(turn['point']), np.asarray(turn['rays'])
            assert p.shape == (2,) and rays.shape == (2,2)
            assert np.isfinite(p).all() and np.isfinite(rays).all()
            assert 0 <= p[0] < w and 0 <= p[1] < h
            assert np.allclose(np.linalg.norm(rays,axis=1),1)
        contours.append(dict(mapId=mid,floor=floor,region='game-native-canvas',profileId=PROFILE,
            variantId='variant-01',unit=60.0,unitBasis='renderer targetPixelsPerVoxel=60; voxel step128',
            turns=turns,gates=[],sourceKind=meta['sourceKind'],sourceProduct=archive.name,
            sourceArray='shared_mask',sourceIndex='../nightmare-shared-structure-index.json',
            structureResource=archive.name,canvas=canvas,
            featureRole='local-contour-proposals-not-known-walls-or-gates',
            contourGraphSource=str(RUNTIME/'app/single_map_structure.py'),
            topologyRule='original winding; valid nodes only; original immediate next or -1, never bridge removed nodes',
            extractorEnvironment=dict(opencv=cv2.__version__,numpy=np.__version__,
                python=sys.version,executable=sys.executable,source='single_map_structure.contour_graph'),
            extractorTargetRuntime=f'OpenCV {TARGET_OPENCV}',
            knownWallSupportUnavailable=True,authorFeaturesUsed=False,sourceGeometry=geometry))
        gap = dict(mapId=mid,floor=floor,region='game-native-canvas',
            missing=['semantic-gate-endpoints','front-entry-chains','entry-passports','known-wall-classification'],
            reason='No accepted semantic exporter was run; empty semantic collections do not mean no real world gates.')
        gaps.append(gap)
        asset = ROOT / 'assets/suju' / mid / f'floor-{floor}.png'
        asset.parent.mkdir(parents=True,exist_ok=True)
        display = Path(row['displaySource'])
        shutil.copy2(display,asset)
        assert display.read_bytes() == asset.read_bytes()
        with Image.open(asset) as shown:
            assert shown.size == (w,h)
        rel_display = asset.relative_to(ROOT).as_posix()
        copies.append(dict(source=str(display),target=rel_display,bytes=asset.stat().st_size,
                           bytesEqual=True,role='Suju-display-only; existing bake, no new resampling'))
        if mid not in maps:
            old = old_maps.get(mid)
            classification = ({k:old[k] for k in ['id','name','direction','doorCount','shape','frontDoorLayout']}
                if old else dict(id=mid,name=row['targetTitle'],direction='未知',doorCount=0,
                                 shape='未知',frontDoorLayout='unknown'))
            maps[mid] = dict(classification,resourceBase='resource-root',layers={},
                identityProfileId=PROFILE,routeProfileId='suju',sourceLayout=layout,targetGuid=row['targetGuid'],
                classificationMetadataSource='fixed F3 manifest' if old else 'unavailable; placeholders required by existing reader',
                classificationKnown=bool(old),doorCountMeaning='legacy map classification' if old else '0 is unknown sentinel, not no doors',
                oneFlowRole='B1-only real display preview; not an all-floor route',
                sideDoorCropRole='whole real 2F display preview; not a semantic gate location')
            catalog_maps[mid] = [dict(id='variant-01',displayName='作者路线',resourceBase='resource-root',
                identityStatus='existing-source-bound-ID; runtime recognition not accepted by this data batch',
                displayPolicy='requires-current-shared-structure-fit',layers={},layerMeta={})]
            canonical_maps[mid] = dict(floors={})
            reference_maps[mid] = dict(floors={})
            display_maps[mid] = dict(floors={})
        maps[mid]['layers'][floor] = rel_display
        if floor == 'B1': maps[mid]['oneFlow'] = rel_display
        if floor == '2': maps[mid]['sideDoorCrop'] = [0,0,w,h]
        variant = catalog_maps[mid][0]
        variant['layers'][floor] = rel_display
        variant['layerMeta'][floor] = dict(width=w,height=h,
            sha256=hashlib.sha256(asset.read_bytes()).hexdigest(),coordinateSpace=canvas['coordinateSpace'],
            sourceLayout=layout,sourceWorldFloor=source['floor'],alreadyBaked=True,role='display-only')
        canonical_maps[mid]['floors'][floor] = dict(coordinateWidth=w,coordinateHeight=h,
            coordinateSpace=canvas['coordinateSpace'],sourceLayout=layout,worldFloor=source['floor'],
            referenceResource=rel_archive,referenceLayerRole='typed-game-structure-only')
        reference_maps[mid]['floors'][floor] = dict(matrixProfileToCanonical=I,
            usableForCurrentFrameRendering=True,crossFloorProjectionAllowed=False,
            evidence=dict(method='same-typed-shared-native-reference-canvas',algebraicallyExact=True))
        pixel_to_world = [[128/60,0,source['bounds']['minX']],
                          [0,128/60,source['bounds']['minY']],[0,0,1]]
        display_maps[mid]['floors'][floor] = dict(matrixProfileToCanonical=I,
            usableForCurrentFrameRendering=True,crossFloorProjectionAllowed=False,role='display-only',
            referenceProfile='shared-structure',referenceCoordinateSpace=canvas['coordinateSpace'],
            evidence=dict(method='baked-display-canvas-to-shared-reference',algebraicallyExact=True,
                exactMeaning='Coordinate identity from same source geometry, not zero author wall error.',
                currentFrameGeometryFitRequired=True,authorizesMapIdentity=False,authorizesStructureValidation=False,
                bakedProvenance=dict(plan=plan['authoritativePlan'],registration=row['displayRegistration'],
                    bakedDisplay=str(display),copy=rel_display,sourceToReferenceAlreadyAppliedOnce=True,
                    additionalApplicationRequired=False,replayPixelsCheckedInThisBatch=False),
                domainEvidence=dict(layout=layout,floor=floor,worldFloor=source['floor'],bounds=source['bounds'],
                    cellPixels=60,placementsExactlyEqual=True,matrixNativePixelToWorld=pixel_to_world,
                    matrixSujuDisplayPixelToWorld=pixel_to_world,sourceGeometryMetadata=str(source_path),
                    canonicalGeometryMetadata=str(canonical_meta_path))))
        inventory.append(dict(row,archive=rel_archive,display=rel_display,maskPixels=int(mask.sum()),
            rawContourNodes=len(nodes),validContourTurns=len(turns),
            directNextPairs=sum(t['next']>=0 for t in turns),canvas=canvas,
            sourceMetadataVerifiedAgain=True,currentBatchReplay=False))
        if n % 12 == 0: print(f'generated {n}/72 layers',flush=True)
    assert len(maps) == 24 and len(shared_entries) == len(contours) == 72
    common = dict(schemaVersion=1,mode='nightmare',profileId=PROFILE,
        coordinateSpace='per-entry native canvas, never a shared cross-floor pixel domain',
        sourceKind='game-rendered-shared-structure',semanticGaps=gaps)
    write(data/'side-entries.json',dict(common,entries=contours))
    write(data/'entries.json',dict(common,entries=[]))
    write(data/'entry-passports.json',dict(common,entries=[],coverage=[]))
    write(refs/'nightmare-shared-structure-index.json',dict(schemaVersion=1,resourceKind='shared-structure',entries=shared_entries))
    write(refs/'nightmare-map-pack.json',dict(schemaVersion=old_pack['schemaVersion'],
        packId='native-nightmare-24-72-candidate-20261026',displayName='24图72层原生结构独立候选',
        sourceRoot=str(ROOT/'assets/suju'),sourcePolicy='display-only-assets',
        defaultEntryFloor=old_pack['defaultEntryFloor'],maps=list(maps.values()),
        coverageScope=dict(registeredMaps=24,displayLayers=72,sharedStructureLayers=72,localContourLayers=72,
            frontEntries=0,entryPassports=0,isRecognitionAccepted=False,
            legacyDefaultEntryFloorPreserved=True,missingSharedMustNotFallBackToAuthor=True)))
    write(refs/'nightmare-author-route-catalog.json',dict(schemaVersion=1,
        status='24-map-72-layer-display-only-candidate',defaultProfileId='suju',profiles=[dict(
            id='suju',displayName='滋酥居',authorName='滋酥居',routeFamilyName='作者路线',runtimeEnabled=True,
            settingsSelectable=True,alignedOverlayEligible=True,role='display-only',
            authorizesMapIdentity=False,authorizesStructureValidation=False,maps=catalog_maps)]))
    write(refs/'nightmare-canonical-coordinate-pack.json',dict(
        schema='identityv-nightmare-canonical-coordinate-pack-v1',canonicalPackId='native-nightmare-24-72-20261026',
        displayName='逐图逐层原生坐标',coordinateReferenceProfileId='shared-structure',referenceProfile='shared-structure',
        referenceCoordinateSpace='per-map-floor native canvas; no shared cross-floor transform',
        referenceProvider=dict(kind='typed-shared-structure',indexPath='references/nightmare-shared-structure-index.json',
            resourceRoot='.',lookupBindings=bindings,
            bindingProfileMeaning='legacy lookup key only, never author source or legacy C geometry',
            routingNote='Full table for application owner; data generation does not establish helper consumption.'),
        policy=dict(requiresCurrentFrameGeometryFit=True,authorizesMapIdentity=False,
                    authorizesCrossFloorProjection=False,authorPixelsForRecognitionOrValidation=False),
        maps=canonical_maps,profiles={
            'shared-structure':dict(role='shared-reference',maps=reference_maps),
            'suju':dict(role='display-only',authorizesMapIdentity=False,authorizesStructureValidation=False,maps=display_maps)},
        canonicalCrossFloorSearchCandidates={}))
    write(refs/'nightmare-source-candidate-index.json',dict(schemaVersion=1,entries=[],featureStore=None,
        scope='All24 runtime IDs explicitly bound in shared/contour pool; no unbound source-image candidate path.'))
    # Existing JSON declarations share the same per-layer content key. The later
    # version audit supplies the complete dependency sidecar without re-rendering.
    from declare_structure_versions import FILES,apply_declarations
    declared=apply_declarations({name:read(refs/name) for name in FILES},version_rows,source_version)
    for name,document in declared.items():write(refs/name,document)
    write(OUT/'inventory.json',dict(plan=str(BASE/'target-24-native-plan.json'),runtimeRoot=str(ROOT),
        maps=24,layers=72,entries=inventory,displayCopies=copies,
        recognitionInputs=[dict(png=r['nativeSourcePNG'],metadata=r['nativeSourceMetadata']) for r in inventory],
        authorPixelsForRecognition=False,oldFeatureInputs=[],noSolver=True,noGUI=True,noWorker=True,
        sourceLimitations='Current config plus legacy texture bytes; renderer verified=false; GPU/dynamic/final game equivalence unknown.',
        executionEnvironment=dict(python=sys.version,opencv=cv2.__version__,numpy=np.__version__),
        codeInputs=[str(RUNTIME/'app/single_map_structure.py')]))
    print(json.dumps(dict(runtimeRoot=str(ROOT),maps=24,sharedLayers=72,contourRows=72,
        displayLayers=72,frontEntries=0,passports=0,sourceGapLayers=0),indent=2))

if __name__ == '__main__': main()
