"""Bind two existing normal renderer PNGs without shipping their image bytes.

Only the path-free generated manifest module is versioned. Companion images go
to an explicitly selected asset root (assets/ is gitignored in the source tree).
No binary recognition resources are regenerated or changed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from PIL import Image
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'app'))
from route_topology_pilot_data import PILOT

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--source-images',type=Path,required=True);p.add_argument('--asset-root',type=Path,required=True);p.add_argument('--module',type=Path,required=True);a=p.parse_args()
    raw=a.base.read_bytes();base=json.loads(raw);assert base['id']=='104001';entries=[]
    for t in PILOT['floors']:
        assert t['sourceBaseSha256']==hashlib.sha256(raw).hexdigest()
        floor=base['floorImages'][str(t['worldFloor'])];source=a.source_images/Path(floor['image']).name
        image=source.read_bytes();assert hashlib.sha256(image).hexdigest()==floor['sha256']
        assert Image.open(source).size==(t['shape'][1],t['shape'][0])
        assert floor['bounds']==t['registration']['bounds']
        relative=f'assets/route-render/104001-{t["worldFloor"]}.png';dest=a.asset_root/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        entries.append({'mapId':t['mapId'],'sourceLayout':'104001','floor':t['floor'],'worldFloor':t['worldFloor'],'shape':t['shape'],'maskSha256':t['maskSha256'],'imageSha256':floor['sha256'],'sourceKind':'source-rendered-no-author-overlays','sourceVersion':base['layoutRevision'],'sourceBaseSha256':t['sourceBaseSha256'],'imagePath':relative,'registration':{'matrixNormalImageToSharedPixels':[[1,0,0],[0,1,0],[0,0,1]],'basis':'same explicit source renderer framing and 60px native canvas, with source layout/floor, bounds, shape and image SHA verified','bounds':floor['bounds']},'limitations':['Normal RGB snapshot is older than current shared mask; no current GPU/runtime pixel equivalence claimed','Companion image bytes are local, not included in the public code commit']})
    payload={'schema':'custom-route-render-v1','entries':entries};a.module.parent.mkdir(parents=True,exist_ok=True);a.module.write_text('# Generated normal-render bindings; companion images supplied independently.\nRENDERS = '+repr(payload)+'\n',encoding='utf-8');print(json.dumps({'entries':len(entries),'files':[e['imagePath'] for e in entries],'imagesCommitted':False}))

if __name__=='__main__':main()
