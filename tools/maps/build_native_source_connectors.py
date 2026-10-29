"""Export source-classified stair/entry ports; never infer interior navigation."""
import argparse
import hashlib
import json
from pathlib import Path

def read(path): return json.loads(Path(path).read_text('utf-8-sig'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def cell_rotate(x, z, rotation):
    return ((x,z),(z,-x-1),(-x-1,-z-1),(-z-1,x))[rotation % 4]

def build(literals, plan):
    layouts=literals['levelLayouts']['data']; definitions=literals['roomDefinitions']['data']
    rows=[]; by_layout={}
    for entry in plan['entries']:
        layout=entry['sourceLayout']; world=entry['sourceWorldFloor']
        metadata=read(entry['nativeSourceMetadata'])
        if str(metadata['levelId']) != layout or metadata['floor'] != world:
            raise ValueError('Renderer layout/floor mismatch')
        if entry['floor'] != {1:'B1',2:'1',3:'2'}[world] or metadata['targetPixelsPerVoxel'] != 60:
            raise ValueError('Logical floor/scale mismatch')
        by_layout.setdefault(layout,{})[world]=(entry,metadata)
    for layout,floors in by_layout.items():
        for index,(config_id,position,rotation) in enumerate(layouts[layout]['room_data']):
            definition=definitions[str(config_id)]; kind=definition.get('room_type')
            if kind not in (2,4): continue
            ports=[]
            for slot,pair in enumerate(definition['room_paths']):
                points=[]
                for x,y,z in pair:
                    rx,rz=cell_rotate(x,z,rotation)
                    points.append([position[0]+rx,position[1]+y,position[2]+rz])
                inside,outside=points
                if inside[1] != outside[1] or inside[1] not in floors:
                    raise ValueError('Source port has no single registered floor')
                entry,metadata=floors[inside[1]]; bounds=metadata['bounds']
                dx,dz=outside[0]-inside[0],outside[2]-inside[2]
                directions={(1,0):'E',(-1,0):'W',(0,1):'N',(0,-1):'S'}
                if (dx,dz) not in directions: raise ValueError('Nonadjacent source port')
                center=[((inside[0]+outside[0])/2+.5)*128, -((inside[2]+outside[2])/2+.5)*128]
                ports.append(dict(slot=slot,localPair=pair,worldInside=inside,worldOutside=outside,
                    worldFloor=inside[1],floor=entry['floor'],
                    nativeBoundaryCenter=[(center[0]-bounds['minX'])*60/128,(center[1]-bounds['minY'])*60/128],
                    direction=directions[(dx,dz)]))
            rows.append(dict(mapId=next(iter(floors.values()))[0]['runtimeMapId'],layoutId=layout,
                roomIndex=index,roomConfigId=config_id,roomType=kind,annotation=definition.get('annotation'),
                position=position,rotation=rotation,roomMapPath=definition.get('room_map_path'),ports=ports,
                semantics='source explicitly classifies stair-room' if kind==4 else 'source explicitly classifies main-entry-room',
                crossFloorRoomAssociation=kind==4,internalNavigationPolylineVerified=False,iconCenterVerified=False))
    return rows

def main():
    parser=argparse.ArgumentParser()
    for name in ('literals','plan','output'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--raw-literal',type=Path,required=True,help='Original literal-exports JSON for provenance only')
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    plan=read(args.plan); rows=build(read(args.literals),plan)
    result=dict(schema='native-source-connectors/1',version=plan['sourceVersion'],sourceLiteralSHA256=sha(args.raw_literal),
        normalizedLiteralSHA256=sha(args.literals),rows=rows,limitations=[
            'ports and same stair-room floor association are source evidence; no invented interior navigation polyline',
            'other multi-floor rooms may be drops or balconies, not treated as stairs'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(rooms=len(rows),ports=sum(len(r['ports']) for r in rows))))

if __name__=='__main__':main()
