"""Apply reviewed same-room exceptions and explicit source stair-room associations.

No links are inferred from disconnected components. Source occupancy alone never
establishes a stair. This does not export an internal stair navigation polyline.
"""
import argparse, collections, hashlib, json, math, shutil
from pathlib import Path
import numpy as np
from build_custom_map_package import apply_road_patches, sha256
from extend_custom_map_package import read, verify_package, check_native_binding


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()


def world(local, room):
    x,y,z=local; a,b=[(x,z),(z,-x-1),(-x-1,-z-1),(-z-1,x)][room["rotation"]]
    return [a+room["position"][0],y+room["position"][1],b+room["position"][2]]


def local(voxel, room):
    x,y,z=[a-b for a,b in zip(voxel,room["position"])];a,b=[(x,z),(-z-1,x),(-x-1,-z-1),(z,-x-1)][room["rotation"]]
    return [a,y,b]


def pixel(voxel,bounds):
    return [(voxel[0]+.5)*60-bounds["minX"]*60/128,(-voxel[2]-.5)*60-bounds["minY"]*60/128]


def checked_definitions(old, new):
    old_def=old["roomDefinitions"]["data"]["10609"];new_def=new["roomDefinitions"]["data"]["10609"]
    if canonical(old_def)!=canonical(new_def):raise ValueError("reviewed room definition changed")
    return hashlib.sha256(canonical(new_def)).hexdigest()


def derive_exception(old,new,catalog,index,resources,patches):
    digest=checked_definitions(old,new);pairs=collections.Counter();instances=set()
    for link in patches["links"]:
        row=index[link["mapId"],str(link["floor"])];layout=row["structureVersionKey"].split("@",1)[0]
        room_index,world_floor=map(int,link["room"].split(":"));room=catalog["maps"][layout]["rooms"][room_index]
        if room["roomConfigId"]!=10609:raise ValueError("reviewed exception has another room type")
        if old['levelLayouts']['data'][layout]!=new['levelLayouts']['data'][layout]:raise ValueError("reviewed source layout changed")
        with np.load(resources/row['resourcePath'],allow_pickle=False) as z:meta=json.loads(str(z['metadata'].item()))
        bounds=meta['sourceGeometry']['bounds'];ends=[]
        for end in (link['a'],link['b']):
            v=[end['x']/60+bounds['minX']/128-.5,world_floor,-end['y']/60-bounds['minY']/128-.5]
            if any(abs(x-round(x))>1e-5 for x in v):raise ValueError('noninteger exception cell')
            ends.append(tuple(local([round(x) for x in v],room)))
        pairs[tuple(sorted(ends))]+=1;instances.add((layout,world_floor,room_index))
    if len(pairs)!=2 or set(pairs.values())!={len(instances)}:raise ValueError('no unanimous local exception template')
    return sorted(pairs),dict(roomConfigId=10609,definitionSha256=digest,reviewedInstances=len(instances),
                              reviewedEdges=sum(pairs.values()),localEdges=sorted(pairs))


def find_point(topo,point,rid):
    found=[i for i,p in enumerate(topo['points']) if topo['pointRooms'][i]==rid and
           abs(p[0]-point[0])<.05 and abs(p[1]-point[1])<.05]
    if len(found)!=1:raise ValueError('source port or exception does not bind one topology point')
    return found[0]


def check_connector(connector, new, catalog):
    room=catalog['maps'][connector['layoutId']]['rooms'][connector['roomIndex']]
    definition=new['roomDefinitions']['data'][str(room['roomConfigId'])]
    if (connector['roomConfigId']!=room['roomConfigId'] or
            connector['roomType']!=definition.get('room_type') or
            connector['roomType'] not in (2,4)):
        raise ValueError('connector is not the declared source entry or stair room')
    slots=set()
    for port in connector['ports']:
        slot=port['slot']
        if slot in slots or not 0<=slot<len(definition['room_paths']):
            raise ValueError('invalid source connector slot')
        slots.add(slot);pair=definition['room_paths'][slot]
        a,b=[world(v,room) for v in pair]
        if (port['localPair']!=pair or port['worldInside']!=a or port['worldOutside']!=b or
                port['worldFloor']!=a[1] or a[1]!=b[1] or
                port['floor']!={1:'B1',2:'1',3:'2'}.get(a[1])):
            raise ValueError('source connector coordinate mismatch')
    if slots!=set(range(len(definition['room_paths']))):
        raise ValueError('incomplete source connector ports')


def complete(source,out,resources,plan_path,geometry_root,old_path,new_path,patch_path,connector_path):
    if out.exists():raise ValueError('output must not exist')
    manifest=read(source/'manifest.json');verify_package(source,manifest);plan=read(plan_path)['entries']
    old,new=read(old_path),read(new_path);catalog=read(geometry_root/'catalog.json')
    index={(r['mapId'],str(r['floor'])):r for r in read(resources/'references/nightmare-shared-structure-index.json')['entries'] if r['profile']=='zhan10-speed'}
    local_edges,proof=derive_exception(old,new,catalog,index,resources,read(patch_path))
    proof['patchFileSha256']=sha256(patch_path);proof['newLiteralSha256']=sha256(new_path)
    maps={m['mapId']:m for m in manifest['maps']};topos={};metadata={};added=[]
    for row in plan:
        mid,floor=row['runtimeMapId'],row['floor'];entry=maps[mid]['floors'][floor]
        meta=read(row['nativeSourceMetadata']);metadata[mid,floor]=meta
        with np.load(resources/index[mid,floor]['resourcePath'],allow_pickle=False) as z:native_meta=json.loads(str(z['metadata'].item()))
        check_native_binding(meta,row,index[mid,floor],native_meta)
        topo=read(source/entry['topology']);topos[mid,floor]=topo;geometry=read(geometry_root/(row['sourceLayout']+'.geometry.json'))
        for room in geometry['rooms']:
            if room['roomConfigId']!=10609:continue
            for edge in local_edges:
                points=[world(v,room) for v in edge]
                if points[0][1]!=row['sourceWorldFloor']:continue
                rid=f"{room['roomIndex']}:{row['sourceWorldFloor']}";ends=[]
                for voxel in points:
                    xy=pixel(voxel,meta['bounds']);idx=find_point(topo,xy,rid);ends.append(dict(index=idx,x=xy[0],y=xy[1]))
                link=dict(mapId=mid,floor=floor,room=rid,a=ends[0],b=ends[1]);count,covered=apply_road_patches(topo,[link],mid+'/'+floor)
                added.append(dict(**link,added=count,covered=covered,derivedFromRoomConfigId=10609))
    stairs=[];front=[]
    for connector in read(connector_path)['rows']:
        check_connector(connector,new,catalog)
        mid=connector['mapId'];ports=[]
        for port in connector['ports']:
            floor=port['floor'];topo=topos[mid,floor];rid=f"{connector['roomIndex']}:{port['worldFloor']}"
            xy=pixel(port['worldInside'],metadata[mid,floor]['bounds']);idx=find_point(topo,xy,rid)
            ports.append((floor,idx,xy,port['worldInside']))
        if connector['roomType']==2:
            # Planning anchor belongs to the explicit entry room, not a guessed spawn icon.
            floor,idx,xy,_=ports[0];maps[mid]['floors'][floor]['entrances']=[dict(label='正门房间',x=xy[0],y=xy[1])]
            front.append(dict(mapId=mid,floor=floor,node=idx,roomIndex=connector['roomIndex'],kind='source-entry-room-port-not-icon'))
        elif connector['roomType']==4:
            unique={ (f,i):(f,i,xy,v) for f,i,xy,v in ports };levels=sorted({f for f,i in unique})
            if len(levels)!=2:raise ValueError('stair room must bind exactly two admitted floors')
            for a in unique.values():
                for b in unique.values():
                    if a[0]!=levels[0] or b[0]!=levels[1]:continue
                    cost=60*sum(abs(x-y) for x,y in zip(a[3],b[3]))
                    stair=dict(id=f"source-stair:{connector['roomIndex']}:{a[0]}:{a[1]}:{b[0]}:{b[1]}",
                       **{'from':dict(floor=a[0],x=a[2][0],y=a[2][1]),'to':dict(floor=b[0],x=b[2][0],y=b[2][1])},
                       cost=max(60,cost),bidirectional=True,sourceRoomConfigId=connector['roomConfigId'],
                       semantics='explicit source stair-room association; internal polyline and gameplay not verified')
                    maps[mid]['stairs'].append(stair);stairs.append(dict(mapId=mid,**stair))
        else:raise ValueError('connector kind not admitted')
    shutil.copytree(source,out)
    for (mid,floor),topo in topos.items():
        entry=maps[mid]['floors'][floor];p=out/entry['topology'];p.write_text(json.dumps(topo,separators=(',',':')),'utf8');entry['topologySha256']=sha256(p)
        maps[mid]['crossFloorRoutingVerified']=False
        maps[mid]['crossFloorRoutingEvidence']='source stair-room association, offline graph checked; gameplay and internal polyline unverified'
    manifest['sources']['incrementalConnections']=dict(sourceConnectorsSha256=sha256(connector_path),derivedRoomException=proof,
        addedRoomLinks=sum(x['added'] for x in added),sourceStairLinks=len(stairs),sourceEntryRoomAnchors=len(front))
    manifest['sources']['incrementalNativeMaps']['crossFloorTransfers']=(
        'source stair-room associations added; internal navigation polyline and gameplay unverified; '
        'existing verified stairs retained')
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=1),'utf8');verify_package(out,manifest)
    return dict(derivedExceptions=added,exceptionProof=proof,stairLinks=stairs,entryRoomAnchors=front)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','out','resources','plan','geometry','old','new','patches','connectors'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();print(json.dumps(complete(a.source,a.out,a.resources,a.plan,a.geometry,a.old,a.new,a.patches,a.connectors),indent=2))
