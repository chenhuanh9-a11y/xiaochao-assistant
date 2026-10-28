"""Prepare F2 neighbourhood/point indices after build_side_door_anchors.py."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'app'))
from side_entry_geometry import load_side_doors
from side_entry_search import write_index


def build(root, output):
    root = Path(root)
    data = json.loads((root/'side-entries.json').read_text('utf8'))
    entries=[]
    for item in data['entries']:
        points=np.asarray([t['point'] for t in item['turns']],float).reshape(-1,2)
        rays=np.asarray([t['rays'] for t in item['turns']],float).reshape(-1,2,2)
        pairs=np.asarray([(i,t['next']) for i,t in enumerate(item['turns']) if 0<=t['next']<len(points)],int).reshape(-1,2)
        entries.append((item,points,rays,pairs))
    doors,error=load_side_doors(root,entries,data['mode'],data['profileId'])
    if error:
        raise ValueError(error)
    write_index(output,entries,doors)
    return len(doors)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    count=build(args.identity_root,args.output)
    print(json.dumps(dict(maps=count,output=str(args.output))))
