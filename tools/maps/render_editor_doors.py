"""Display-only source door frames. Recognition arrays and transforms stay unchanged."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import numpy as np
from PIL import Image
from build_native_source_connectors import cell_rotate

def read(p): return json.loads(Path(p).read_text('utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def contained(root,relative):
    value=Path(relative)
    if value.is_absolute() or value.drive or '..' in value.parts:raise ValueError('Unsafe package path')
    result=(root/value).resolve()
    if not result.is_relative_to(root.resolve()):raise ValueError('Package path escapes root')
    return result

def checked_geometry(meta,binding,map_id,floor):
    expected_world={'B1':1,'1':2,'2':3}.get(floor)
    geometry=meta['sourceGeometry']
    if expected_world is None or geometry.get('worldFloor') != expected_world:
        raise ValueError('Native source world/logical floor mismatch')
    for item in (binding,meta.get('binding',{})):
        if item.get('mapId')!=map_id or item.get('floor')!=floor:
            raise ValueError('Native map/floor identity mismatch')
    for key in ('structureVersionKey','sourceVersion','baseSnapshot'):
        if meta.get(key)!=binding.get(key):raise ValueError('Native binding version mismatch')
    if geometry.get('targetPixelsPerVoxel')!=60:raise ValueError('Unsupported native pixel scale')
    if not np.array_equal(np.asarray(geometry.get('matrixRendererPixelToProduct')),np.eye(3)):
        raise ValueError('Unsupported renderer-to-native transform')
    canvas=meta.get('canvas',{})
    if canvas.get('floor')!=floor or canvas.get('worldFloor')!=expected_world or str(canvas.get('layout'))!=str(geometry.get('layout')):
        raise ValueError('Native canvas binding mismatch')
    return geometry

def source_frames(layout, definitions):
    pending={}; frames=[]
    rooms=list(enumerate(layout['room_data']))
    rooms.sort(key=lambda row:definitions[str(row[1][0])].get('room_type') != 2)
    ports={}
    for ri,(cfg,pos,rotation) in rooms:
        definition=definitions[str(cfg)]
        for slot,pair in enumerate(definition['room_paths']):
            world=[]
            for x,y,z in pair:
                rx,rz=cell_rotate(x,z,rotation)
                world.append((pos[0]+rx,pos[1]+y,pos[2]+rz))
            key=tuple(world); reverse=tuple(reversed(world))
            if world[0][1]!=world[1][1]: raise ValueError('Cross-floor door pair')
            port=dict(roomIndex=ri,roomConfigId=cfg,slot=slot,worldVoxels=world,roomType=definition.get('room_type'))
            ports[ri,slot]=port
            if reverse in pending:
                first=pending.pop(reverse)
                frames.append(dict(first,kind='entry-room-connection' if 2 in (first['roomType'],port['roomType']) else 'internal-connection',peerRoomIndex=ri))
            else: pending[key]=port
    known={tuple(sorted(tuple(p) for p in f['worldVoxels'])) for f in frames}
    for identifier,(ri,slot) in layout.get('door',{}).items():
        port=ports.get((ri,slot))
        if port is None: raise ValueError('External door references missing source port')
        key=tuple(sorted(tuple(p) for p in port['worldVoxels']))
        if key not in known:
            frames.append(dict(port,kind='explicit-external-door',sourceDoorId=str(identifier)));known.add(key)
    return frames

def native_frame(frame,bounds):
    a,b=frame['worldVoxels']; dx,dz=b[0]-a[0],b[2]-a[2]
    if (dx,dz) not in {(0,1),(0,-1),(1,0),(-1,0)}: raise ValueError('Non-adjacent door port')
    return dict(frame,worldFloor=a[1],nativeCenter=[((a[0]+b[0])/2+.5)*60-bounds['minX']*60/128,
        -((a[2]+b[2])/2+.5)*60-bounds['minY']*60/128],rotationDegrees={(0,1):0,(1,0):90,(0,-1):180,(-1,0):270}[dx,dz])

def image_point(point,matrix):
    p=matrix@np.asarray([*point,1.]);return (p[:2]/p[2]).tolist()

def pixel_probe(image,frames,inverse):
    pixels=np.asarray(image.convert('RGBA'));hits=0
    for f in frames:
        x,y=map(round,image_point(f['nativeCenter'],inverse));radius=20
        patch=pixels[max(0,y-radius):min(image.height,y+radius+1),max(0,x-radius):min(image.width,x+radius+1)]
        if patch.size and int(((patch[:,:,:3].min(axis=2)>=140)&(np.ptp(patch[:,:,:3].astype(int),axis=2)<=35)&(patch[:,:,3]>160)).sum())>=4:hits+=1
    return dict(probed=len(frames),whiteLineNeighborhoodHits=hits,meaning='pixel evidence only; not proof of each door state or full geometric registration')

def composite_frames(image,frames,sprite,inverse):
    """Warp only the small sprite footprint with the existing native-to-image map."""
    result=image.convert('RGBA')
    for f in frames:
        x,y=f['nativeCenter']; angle=-f['rotationDegrees']
        patch=sprite.rotate(angle,expand=False,resample=Image.Resampling.NEAREST)
        local=np.array([[60/patch.width,0,x-30],[0,60/patch.height,y-30],[0,0,1.]])
        transform=inverse@local
        corners=[image_point(p,transform) for p in [(0,0),(patch.width,0),(patch.width,patch.height),(0,patch.height)]]
        left=max(0,math.floor(min(p[0] for p in corners)));top=max(0,math.floor(min(p[1] for p in corners)))
        right=min(image.width,math.ceil(max(p[0] for p in corners)));bottom=min(image.height,math.ceil(max(p[1] for p in corners)))
        if right<=left or bottom<=top: continue
        back=np.linalg.inv(transform)@np.array([[1,0,left],[0,1,top],[0,0,1.]])
        back=back/back[2,2]
        warped=patch.transform((right-left,bottom-top),Image.Transform.PERSPECTIVE,tuple(back.flatten()[:8]),Image.Resampling.BICUBIC)
        result.alpha_composite(warped,(left,top))
    return result

def main():
    parser=argparse.ArgumentParser()
    for name in ('package','runtime','literals','baseline-frames','sprite','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--render-maps',required=True,help='Explicit maps known to have no baked door frames; others preserve original bytes')
    args=parser.parse_args(); out=args.output.resolve()
    for value in (args.package,args.runtime,args.literals,args.baseline_frames,args.sprite):
        p=value.resolve()
        if out.is_relative_to(p) or p.is_relative_to(out):raise ValueError('Output overlaps input')
    if out.exists():raise FileExistsError(out)
    manifest=read(args.package/'manifest.json');literals=read(args.literals);baseline=read(args.baseline_frames)
    selected=set(args.render_maps.split(',')); known={m['mapId'] for m in manifest['maps']}
    if not selected or not selected<=known:raise ValueError('Unknown/empty render map selection')
    expected_sprite=baseline['sources']['textures']['empty']['candidates'][0]['sha256']
    if sha(args.sprite)!=expected_sprite:raise ValueError('Source empty-frame sprite hash mismatch')
    sprite=Image.open(args.sprite).convert('RGBA')
    index=read(args.runtime/'references/nightmare-shared-structure-index.json')
    bindings={(e['mapId'],e['floor']):e for e in index['entries']}
    old={str(r['levelId']):r for r in baseline['records']}; records=[]; cache={}; jobs=[]; catalogs={}
    for m in manifest['maps']:
        for floor,info in m['floors'].items():
            binding=bindings.get((m['mapId'],floor))
            if binding is None:raise ValueError('Display missing native binding')
            with np.load(contained(args.runtime,binding['resourcePath']),allow_pickle=False) as archive:meta=json.loads(archive['metadata'].item())
            geometry=checked_geometry(meta,binding,m['mapId'],floor)
            layout=str(geometry['layout']);world=geometry['worldFloor'];raw=literals['levelLayouts']['data'][layout]
            renderer=read(geometry['rendererMetadata']);catalog_ref=renderer['sourceCatalog']
            digest=catalog_ref['sourceFileSha256']
            if digest not in catalogs:
                if sha(catalog_ref['sourceFile'])!=digest:raise ValueError('Renderer source catalog hash mismatch')
                catalogs[digest]=read(catalog_ref['sourceFile'])
            catalog=catalogs[digest]
            for cfg,_,_ in raw['room_data']:
                current=literals['roomDefinitions']['data'][str(cfg)];registered=catalog['roomDefinitions'][str(cfg)]
                if any(current.get(k)!=registered.get(k) for k in ('room_paths','room_type','room_voxels')):
                    raise ValueError('Door geometry source differs from registered renderer room definitions')
            rawhash=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if binding['structureVersionKey'].split('@',1)[1].split('/')[0]!=rawhash:raise ValueError('Layout differs from the registered native structure')
            if layout not in cache:
                cache[layout]=source_frames(raw,literals['roomDefinitions']['data'])
                if layout in old:
                    expected={tuple(sorted(tuple(p) for p in f['worldVoxels'])) for f in old[layout]['frames']}
                    actual={tuple(sorted(tuple(p) for p in f['worldVoxels'])) for f in cache[layout] if f['kind']!='explicit-external-door'}
                    if expected!=actual:raise ValueError('Recomputed connections differ from source baseline')
            frames=[native_frame(f,geometry['bounds']) for f in cache[layout] if f['worldVoxels'][0][1]==world]
            image_path=contained(args.package,info['image']);contained(out,info['image'])
            if sha(image_path)!=info['imageSha256']:raise ValueError('Editor image hash mismatch')
            image=Image.open(image_path).convert('RGBA');matrix=np.asarray(info['matrixImageToNative'],float)
            if matrix.shape!=(3,3) or not np.isfinite(matrix).all():raise ValueError('Invalid display coordinate matrix')
            inverse=np.linalg.inv(matrix)
            row=dict(mapId=m['mapId'],floor=floor,layout=layout,image=info['image'],sourceImageSha256=sha(image_path),
                status='added-source-frames' if m['mapId'] in selected else 'existing-image-preserved',
                frames=frames,pixelProbe=pixel_probe(image,frames,inverse),matrixUnchanged=True,liveDoorStateKnown=False)
            records.append(row)
            if m['mapId'] in selected:jobs.append((info,image,frames,inverse,row))
    shutil.copytree(args.package,out)
    for info,image,frames,inverse,row in jobs:
        result=composite_frames(image,frames,sprite,inverse)
        path=out/info['image'];result.save(path,format='WEBP',lossless=True,method=6,exact=True)
        row['outputImageSha256']=info['imageSha256']=sha(path)
        row['changedPixels']=int(np.any(np.asarray(image)!=np.asarray(Image.open(path).convert('RGBA')),axis=2).sum())
        info['doorDisplay']={'profile':'source-empty-frame','frameCount':len(frames),'liveStateKnown':False,'sourceSpriteSha256':sha(args.sprite)}
    manifest['sources']['doorDisplay']={'schema':1,'sourceLiteralSha256':sha(args.literals),'baselineFramesSha256':sha(args.baseline_frames),
        'sourceSpriteSha256':sha(args.sprite),'oldImagePolicy':'preserve baked door pixels; no double draw','dynamicOpenStateKnown':False}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=1),encoding='utf8')
    report=dict(schema='editor-door-display/1',records=records,counts={'floors':len(records),'added':len(jobs),'existingPreserved':len(records)-len(jobs)},
        unknown=['Difficult mode has no registered normal editor images in this package; no door locations invented',
            'Preserved images have pixel-neighborhood evidence, not independently proven per-door completeness',
            'Dynamic open/closed states and current sprite bytes were not re-extracted'],recognitionModified=False,topologyModified=False)
    (out.parent/(out.name+'-doors.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report['counts']))

if __name__=='__main__':main()
