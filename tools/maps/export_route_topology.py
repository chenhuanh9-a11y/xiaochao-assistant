"""Bounded pilot exporter. Inputs are authorized local snapshots, never bundled raw.

Usage: --base layout.json --structures released-structure-root --output pilot.json
Only selected 104001 upper/ground floors are exported. No images, game scene
objects, private paths, arbitrary cross-floor edges or author art are copied.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

def digest(raw): return hashlib.sha256(raw).hexdigest()

def export(base_path, structures, output, python_module=None):
    raw=base_path.read_bytes();base=json.loads(raw);assert base['id']=='104001'
    floors=[]
    for world,floor in [(2,'1'),(3,'2')]:
        file=structures/f'structures/nightmare/nightmare-09-{floor}.npz'
        with np.load(file,allow_pickle=False) as a:
            mask=a['clean_mask']>0;meta=json.loads(str(a['metadata'].item()))
        geo=meta['sourceGeometry'];canvas=meta['canvas'];bounds=geo['bounds']
        assert canvas['layout']=='104001' and canvas['worldFloor']==world
        assert geo['production']=='direct renderer alpha>0, without projection, resize or author pixels'
        assert geo['matrixRendererPixelToProduct']==[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]
        assert geo['targetPixelsPerVoxel']==60 and bounds==base['floorImages'][str(world)]['bounds']
        # Producer contract: renderer has 128px source cells, emits 60px tiles,
        # crops at explicit minX/minY, and publishes identity pixel registration.
        # This is not an image-bound/bounding-box fit.
        pitch=60;ox=-bounds['minX']*pitch/128;oy=-bounds['minY']*pitch/128
        project=lambda p:[round((p[0]+.5)*pitch+ox,6),round((-p[2]-.5)*pitch+oy,6)]
        points=[];rooms=[];links=[];lookup={};room_rows=[]
        def add(room,v):
            key=(room,tuple(v));p=project(v)
            if not(0<=p[0]<mask.shape[1] and 0<=p[1]<mask.shape[0] and mask[round(p[1]),round(p[0])]):return None
            if key not in lookup:lookup[key]=len(points);points.append(p);rooms.append(room)
            return lookup[key]
        placements={p['roomIndex'] for p in base['floorImages'][str(world)]['placements']}
        for node in base['nodes']:
            if node['floor']!=world or not node['visible'] or node['roomIndex'] not in placements:continue
            room=node['id'];nav=node.get('navigation')
            if nav:
                ids=[add(room,p) for p in nav['points']]
                for a,b in nav['links']:
                    if ids[a] is not None and ids[b] is not None:links.append([ids[a],ids[b],'source-diagonal'])
            else:
                cells=node.get('planningMask',{}).get('cells',node['voxels']);allowed=node.get('planningMask',{}).get('links');cell_map={tuple(v):add(room,v) for v in cells}
                for v,a in cell_map.items():
                    for delta in [(1,0,0),(0,0,1)]:
                        q=tuple(x+y for x,y in zip(v,delta));b=cell_map.get(q)
                        # Preserve explicit local cell-link restrictions when present.
                        key='|'.join(sorted([','.join(map(str,v)),','.join(map(str,q))]))
                        if a is not None and b is not None and (allowed is None or key in allowed):links.append([a,b,'room-grid'])
            room_rows.append({'id':room,'roomIndex':node['roomIndex'],'roomConfigId':node['roomConfigId']})
        def doorway(room,v):
            exact=lookup.get((room,tuple(v)))
            if exact is not None:return exact
            # Diagonal source centreline endpoint must agree with the source door
            # voxel within its half-tile cell, never an arbitrary nearest node.
            p=project(v);candidates=[i for i,r in enumerate(rooms) if r==room and abs(points[i][0]-p[0])<=30 and abs(points[i][1]-p[1])<=30]
            return candidates[0] if len(candidates)==1 else None
        doors=[]
        for edge in base['edges']:
            if edge['kind']!='door' or edge['floor']!=world:continue
            a=doorway(edge['a'],edge['voxels'][0]);b=doorway(edge['b'],edge['voxels'][1])
            if a is not None and b is not None:links.append([a,b,'source-door']);doors.append({'id':edge['id'],'a':a,'b':b,'liveState':'unknown'})
        assert len(points)>100 and len(doors)>25
        floors.append({'mapId':'nightmare-09','sourceLayout':'104001','floor':floor,'worldFloor':world,'shape':list(mask.shape),'maskSha256':digest(np.ascontiguousarray(mask,dtype=np.uint8).tobytes()),'resourceSha256':digest(file.read_bytes()),'sourceBaseSha256':digest(raw),'sourceTopologyVersion':base['layoutRevision'],'sharedSourceVersion':meta['sourceVersion'],'registration':{'basis':'explicit native renderer producer contract, identity product registration, source layout and world floor binding','pixelsPerTile':pitch,'matrixCellCentreToNative':[[pitch,0,ox+pitch/2],[0,-pitch,oy-pitch/2],[0,0,1]],'matrixRendererPixelToProduct':geo['matrixRendererPixelToProduct'],'bounds':bounds,'pixelProof':'Every admitted graph point and traversed edge rechecked against exact released clean_mask; resource/mask SHA bound at load.'},'points':points,'pointRooms':rooms,'links':links,'rooms':room_rows,'doors':doors,'entrances':[{'id':e['id'],'kind':e['kind'],'room':e['room'],'pixel':project(e['voxel']),'markerPixel':[e['point'][0]*pitch+ox,e['point'][1]*pitch+oy]} for e in base['exits'] if e['floor']==world],'transfers':[],'limitations':['No complete interior collision or native-wall channel','Static paired doors use planning-open policy; live states unknown','No cross-floor routing enabled; unknown transfers excluded','Older topology snapshot; binding to this released footprint does not assert current runtime door state']})
    value={'schema':'custom-route-topology-v1','scope':'one-layout-two-floor pilot','displayNames':{'nightmare-09':{'name':'一楼正门 · 二楼侧门 01','sourceLayout':'104001','basis':'source entrance markers; pilot group ordinal, internal IDs unchanged'}},'floors':floors}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    if python_module:
        python_module.parent.mkdir(parents=True,exist_ok=True)
        python_module.write_text('# Generated bounded, path-free topology pilot; see docs/route-topology.md.\nPILOT = '+repr(value)+'\n',encoding='utf-8')
    return value

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--structures',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--python-module',type=Path);a=p.parse_args();r=export(a.base,a.structures,a.output,a.python_module);print([(f['floor'],len(f['points']),len(f['doors'])) for f in r['floors']])
