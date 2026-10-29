"""Add only explicitly planned native floors to an isolated resource copy."""
import argparse, copy, hashlib, importlib.util, json, re, shutil, sys
from pathlib import Path
import cv2
import numpy as np

def read(p): return json.loads(Path(p).read_text('utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')

def validate_inputs(args):
    """Reject ambiguous bindings and overlapping output before creating any files."""
    root = args.output.resolve()
    if root.exists():
        raise FileExistsError(root)
    def separate(p):
        p = Path(p).resolve()
        if root == p or root in p.parents or p in root.parents:
            raise ValueError(f'Output overlaps input: {p}')
    for p in (args.base, args.source, args.plan, args.entry_inventory, args.literals, args.entry_builder):
        separate(p)
    plan, literals = read(args.plan), read(args.literals)
    if not isinstance(plan.get('sourceVersion'), str) or not plan['sourceVersion']:
        raise ValueError('Missing sourceVersion')
    entries = plan.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Plan must contain explicit floors')
    seen, layouts, catalogs = set(), {}, {}
    existing = {m['id'] for m in read(args.base/'references/nightmare-map-pack.json')['maps']}
    for target in entries:
        mid, floor, layout = target['runtimeMapId'], target['floor'], target['sourceLayout']
        if not isinstance(mid, str) or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', mid):
            raise ValueError('Unsafe runtimeMapId')
        if not isinstance(layout, str) or not re.fullmatch(r'[0-9]+', layout):
            raise ValueError('Invalid sourceLayout')
        if floor not in {'B1', '1', '2'} or type(target['sourceWorldFloor']) is not int or target['sourceWorldFloor'] != {'B1':1,'1':2,'2':3}[floor]:
            raise ValueError('Invalid logical/world floor binding')
        if (mid, floor) in seen or mid in existing:
            raise ValueError('Duplicate or existing map/floor')
        seen.add((mid, floor))
        if mid in layouts and layouts[mid] != layout:
            raise ValueError('One runtime map cannot bind multiple layouts')
        layouts[mid] = layout
        metadata_path, png_path = Path(target['nativeSourceMetadata']), Path(target['nativeSourcePNG'])
        separate(metadata_path); separate(png_path)
        metadata = read(metadata_path)
        if str(metadata.get('levelId')) != layout or metadata.get('floor') != target['sourceWorldFloor']:
            raise ValueError('Renderer metadata layout/floor mismatch')
        if metadata.get('targetPixelsPerVoxel') != 60 or Path(metadata['outTarget']).resolve() != png_path.resolve():
            raise ValueError('Renderer PNG path/scale mismatch')
        catalog_path = Path(metadata['sourceCatalog']['sourceFile']); separate(catalog_path)
        digest = sha(catalog_path)
        if digest != metadata['sourceCatalog']['sourceFileSha256']:
            raise ValueError('Renderer catalog hash mismatch')
        catalog = catalogs.setdefault(digest, read(catalog_path))
        if catalog.get('gameConfigVersion') != plan['sourceVersion']:
            raise ValueError('Renderer sourceVersion mismatch')
        raw = literals['levelLayouts']['data'].get(layout)
        if raw is None or catalog['maps'].get(layout, {}).get('roomDataRaw') != raw.get('room_data'):
            raise ValueError('Layout literal/catalog mismatch')
        expected_doors = sorted((int(k), v[0], v[1]) for k, v in raw.get('door', {}).items())
        actual_doors = sorted((d['doorIndex'], d['roomIndex'], d['doorSlot']) for d in catalog['maps'][layout]['normalizedDoors'])
        if expected_doors != actual_doors:
            raise ValueError('Door literal/catalog mismatch')
        image = cv2.imdecode(np.fromfile(png_path, np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None or image.ndim != 3 or image.shape[2] != 4 or not image[:, :, 3].any():
            raise ValueError('Expected nonempty RGBA source image')
        h, w = image.shape[:2]; bounds = metadata['bounds']
        expected_wh = [round((bounds['maxX']-bounds['minX'])*60/128), round((bounds['maxY']-bounds['minY'])*60/128)]
        if [w,h] != target['nativeCanvasWH'] or [w,h] != expected_wh:
            raise ValueError('Renderer bounds/image shape mismatch')
        if sha(png_path) != target.get('nativeSourcePNGSha256') or sha(metadata_path) != target.get('nativeSourceMetadataSha256'):
            raise ValueError('Plan PNG/metadata hash mismatch')
    if any({f for m,f in seen if m == mid} != {'B1','1','2'} for mid in layouts):
        raise ValueError('New maps must include all three explicitly bound floors')
    inventory = read(args.entry_inventory)
    if {x['mapId'] for x in inventory['entries']} != set(layouts) or len(inventory['entries']) != len(layouts):
        raise ValueError('Entry inventory must cover each new map exactly once')
    for row in inventory['entries']:
        if row['layout'] != layouts[row['mapId']] or row['floor'] != '1' or row['worldFloor'] != 2:
            raise ValueError('Front entry inventory binding mismatch')
    return plan, literals

def complete_version_registry(root, targets):
    refs=root/'references';registry=read(refs/'nightmare-structure-version-bindings.json');neutral=read(refs/'nightmare-structure-display.json')
    known={(x['mapId'],x['resourceKey']['floor']) for x in registry['entries']}
    for target in targets:
        mid,floor=target['runtimeMapId'],target['floor']
        if (mid,floor) in known:continue
        rel=f'references/nightmare-entries/{mid}-{floor}-shared.npz'
        with np.load(root/rel,allow_pickle=False) as z:meta=json.loads(z['metadata'].item())
        layout=target['sourceLayout'];rawhash=meta['structureVersionKey'].split('@',1)[1].rsplit('/',1)[0]
        key=dict(layoutId=layout,structureRawRecordSha256=rawhash,floor=floor)
        source=read(target['nativeSourceMetadata']);catalog=source['sourceCatalog']['sourceFile']
        registry['entries'].append(dict(resourceKey=key,mapId=mid,sourceVersion=meta['sourceVersion'],rendererCatalog=dict(path=catalog,sha256=sha(catalog)),rendererMetadata=dict(path=target['nativeSourceMetadata'],sha256=sha(target['nativeSourceMetadata'])),typedReference=rel,sharedLookup=dict(meta['binding'],resourcePath=rel),packReference='references/nightmare-map-pack.json',adapterReference='references/nightmare-canonical-coordinate-pack.json',neutralDisplay=dict(index='references/nightmare-structure-display.json',baseResourceKey=key),authorDisplay={},hashMeaning='layout raw placement and door records; dependency closure tracked separately'))
        neutral['entries'].append(dict(mapId=mid,floor=floor,binding=meta['binding'],referenceResource=rel,canvas=meta['canvas'],displayArray='shared_mask',sourceKind=meta['sourceKind'],displayRole='neutral-structure-footprint',authorResourceRequired=False,authorTransformRequired=False,sourceMissingRoomFloors=source.get('diagnostics',{}).get('skippedRoomFloors',[]),**{k:meta[k] for k in ['structureVersionKey','sourceVersion','baseSnapshot']}))
    registry['registeredMaps']=len({x['mapId'] for x in registry['entries']});registry['registeredFloors']=len(registry['entries'])
    neutral['counts']={'maps':len({x['mapId'] for x in neutral['entries']}),'floors':len(neutral['entries'])}
    write(refs/'nightmare-structure-version-bindings.json',registry);write(refs/'nightmare-structure-display.json',neutral)

def main():
    a=argparse.ArgumentParser();a.add_argument('--base',type=Path,required=True);a.add_argument('--source',type=Path,required=True);a.add_argument('--plan',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--entry-builder',type=Path,default=Path(__file__).with_name('native_increment_entry_rows.py'));a.add_argument('--entry-inventory',type=Path,required=True);a.add_argument('--literals',type=Path,required=True);args=a.parse_args()
    assert cv2.__version__=='4.12.0'
    root=args.output.resolve();base=args.base.resolve();work=args.plan.resolve().parent
    plan, lit = validate_inputs(args)
    sys.path.insert(0,str(args.source/'app'))
    from single_map_structure import contour_graph
    root.mkdir(parents=True)
    for folder in ('references','assets','recognition'):
        if (base/folder).is_dir():shutil.copytree(base/folder,root/folder)
    baseline={p.relative_to(base).as_posix():sha(p) for folder in ('references','assets','recognition') for p in (base/folder).rglob('*') if p.is_file()}
    refs=root/'references'; pack=read(refs/'nightmare-map-pack.json');index=read(refs/'nightmare-shared-structure-index.json');canonical=read(refs/'nightmare-canonical-coordinate-pack.json');side=read(refs/'nightmare-entries/side-entries.json');front=read(refs/'nightmare-entries/entries.json')
    version=plan['sourceVersion'];I=np.eye(3).tolist();newmaps={};rows=[];idx={}
    for target in plan['entries']:
        mid,floor,layout=target['runtimeMapId'],target['floor'],target['sourceLayout'];source=read(target['nativeSourceMetadata']);raw=lit['levelLayouts']['data'][layout]
        rawhash=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest();key=f'{layout}@{rawhash}/{floor}'
        image=cv2.imdecode(np.fromfile(target['nativeSourcePNG'],np.uint8),cv2.IMREAD_UNCHANGED);h,w=image.shape[:2];mask=(image[:,:,3]>0).astype(np.uint8)
        assert (w,h)==tuple(target['nativeCanvasWH']) and mask.any()
        binding=dict(profile='zhan10-speed',mapId=mid,floor=floor,variant='variant-01')
        canvas=dict(coordinateSpace=f'game-native-pixels:{layout}:{floor}',layout=layout,floor=floor,worldFloor=target['sourceWorldFloor'],width=w,height=h,shapeHW=[h,w],axes='x-right,y-down')
        geometry=dict(rendererImage=target['nativeSourcePNG'],rendererMetadata=target['nativeSourceMetadata'],layout=layout,worldFloor=target['sourceWorldFloor'],targetPixelsPerVoxel=60,bounds=source['bounds'],nativeShapeHW=[h,w],alphaRule='alpha>0',rendererModules=source['rendererModules'],imageSource='retained baseline texture bytes under unchanged mapImages configuration',imageBytesReextracted=False,matrixRendererPixelToProduct=I,production='direct renderer alpha>0 without author pixels')
        meta=dict(sourceKind='game-rendered-shared-structure',binding=binding,canvas=canvas,sourceVersion=version,structureVersionKey=key,baseSnapshot=version,roles=dict(clean='shared_mask',validation='shared_mask'),roleSemantics=dict(shared_mask='room/corridor texture footprint, not semantic walls',validation='same shared field; not independent evidence'),sourceGeometry=geometry,missingChannels=['semantic-known-boundary','semantic-unknown-mask','native-wall-segments','walkable-grid'],authorPixelsUsedForStructure=False,sourceImagePixelsStored=False)
        path=refs/'nightmare-entries'/f'{mid}-{floor}-shared.npz';np.savez_compressed(path,shared_mask=mask,metadata=np.asarray(json.dumps(meta,ensure_ascii=False)))
        rel=path.relative_to(root).as_posix();entry=dict(binding,resourcePath=rel,structureVersionKey=key,sourceVersion=version,baseSnapshot=version);index['entries'].append(entry);idx[mid,floor]=entry
        nodes=contour_graph(mask);valid=[i for i,n in enumerate(nodes) if n['valid']];lookup={v:i for i,v in enumerate(valid)}
        turns=[dict(point=nodes[i]['point'].tolist(),rays=nodes[i]['rays'].tolist(),next=lookup.get(nodes[i]['next'],-1),sourceNode=i) for i in valid]
        side['entries'].append(dict(mapId=mid,floor=floor,region='game-native-canvas',profileId='zhan10-speed',variantId='variant-01',unit=60.0,unitBasis='renderer pixels per voxel',turns=turns,gates=[],sourceKind=meta['sourceKind'],sourceProduct=path.name,sourceArray='shared_mask',sourceIndex='../nightmare-shared-structure-index.json',structureResource=path.name,canvas=canvas,featureRole='local-contour-proposals-not-known-walls-or-gates',knownWallSupportUnavailable=True,authorFeaturesUsed=False,sourceGeometry=geometry,structureVersionKey=key,sourceVersion=version,baseSnapshot=version))
        display=root/'assets/game-rendered'/mid/f'floor-{floor}.png';display.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target['nativeSourcePNG'],display);displayrel=display.relative_to(root).as_posix()
        if mid not in newmaps:
            assert not any(x['id']==mid for x in pack['maps'])
            newmaps[mid]=dict(id=mid,name='噩梦 '+layout,direction='unknown',doorCount=0,shape='unknown',frontDoorLayout='unknown',resourceBase='resource-root',layers={},identityProfileId='zhan10-speed',routeProfileId='shared-structure',sourceLayout=layout,classificationKnown=False,classificationMetadataSource='not inferred; source layout ID is authoritative',doorCountMeaning='0 unknown sentinel, not zero doors',sourceVersion=version,layoutRevisionSnapshot=version,structureContentSha256=rawhash)
            canonical['maps'][mid]=dict(floors={},baseSnapshot=version,structureContentSha256=rawhash)
            canonical['profiles']['shared-structure']['maps'][mid]=dict(floors={})
        newmaps[mid]['layers'][floor]=displayrel
        if floor=='B1':newmaps[mid]['oneFlow']=displayrel
        if floor=='2':newmaps[mid]['sideDoorCrop']=[0,0,w,h]
        canonical['referenceProvider']['lookupBindings'].append(binding)
        canonical['maps'][mid]['floors'][floor]=dict(coordinateWidth=w,coordinateHeight=h,coordinateSpace=canvas['coordinateSpace'],sourceLayout=layout,worldFloor=target['sourceWorldFloor'],referenceResource=rel,referenceLayerRole='typed-game-structure-only',structureVersionKey=key,sourceVersion=version)
        canonical['profiles']['shared-structure']['maps'][mid]['floors'][floor]=dict(matrixProfileToCanonical=I,usableForCurrentFrameRendering=True,crossFloorProjectionAllowed=False,evidence=dict(method='same-typed-shared-native-reference-canvas',algebraicallyExact=True),structureVersionKey=key)
        rows.append(dict(target,resourcePath=rel,sha256=sha(path),maskPixels=int(mask.sum()),contourTurns=len(turns),display=displayrel,displaySha256=sha(display),structureVersionKey=key))
    pack['maps'].extend(newmaps.values());pack['coverageScope'].update(registeredMaps=len(pack['maps']),displayLayers=len(index['entries']),sharedStructureLayers=len(index['entries']),localContourLayers=len(side['entries']))
    # Reuse the previously verified front-entry geometry extraction unchanged.
    spec=importlib.util.spec_from_file_location('entry_builder',args.entry_builder);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);mod.ORIGINAL=root;mod.RUNTIME=args.source
    newfront=[]
    for source in read(args.entry_inventory)['entries']:
        entry,stat=mod.build_row(source,idx[source['mapId'],source['floor']],contour_graph);front['entries'].append(entry);newfront.append(stat)
    pack['coverageScope']['frontEntries']=len(front['entries'])
    for name,value in [('nightmare-map-pack.json',pack),('nightmare-shared-structure-index.json',index),('nightmare-canonical-coordinate-pack.json',canonical),('nightmare-entries/side-entries.json',side),('nightmare-entries/entries.json',front)]:write(refs/name,value)
    complete_version_registry(root,plan['entries'])
    changed=[rel for rel,h in baseline.items() if sha(root/rel)!=h]
    assert len(index['entries'])==sum(len(m['layers']) for m in pack['maps']) and len(side['entries'])==len(index['entries']) and len(front['entries'])==len(pack['maps'])
    assert all(sha(root/rel)==h for rel,h in baseline.items() if rel.endswith('.npz'))
    write(root.parent/(root.name+'-generation.json'),dict(stage=str(root),source=str(args.source),version=version,maps=len(pack['maps']),shared=len(index['entries']),side=len(side['entries']),front=len(front['entries']),changedBaselineFiles=changed,newRows=rows,frontRows=newfront,baselineFiles=baseline,oldNPZBytePreserved=True,sourceImageBytesReextracted=False,noSolver=True,noGUI=True))
    print(json.dumps(dict(stage=str(root),maps=len(pack['maps']),shared=len(index['entries']),front=len(front['entries']),changedBaselineFiles=changed)))

if __name__=='__main__':main()
