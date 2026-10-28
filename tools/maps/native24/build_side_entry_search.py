"""Prepare both F2 side-entry resources in an independent complete resource stage."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'app'))
from side_entry_geometry import load_side_doors
from side_entry_search import load_index, write_index


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
    if Path(output).resolve() == (root/'side-entry-search.npz').resolve():
        loaded,error=load_index(root,entries,doors)
        if error or len(loaded) != len(doors):
            raise ValueError(error or 'Side-entry index did not load completely')
    return len(doors)


def build_resources(root, exports):
    from build_side_door_anchors import build as build_anchors

    root=Path(root)
    identity_root=root/'references/nightmare-entries'
    anchors=identity_root/'side-door-anchors.json'
    search=identity_root/'side-entry-search.npz'
    if anchors.exists() or search.exists():
        raise FileExistsError('Preserve existing side-entry resources; use a new resource stage')
    inventory=build_anchors(root,exports)
    with anchors.open('x',encoding='utf8') as target:
        json.dump(inventory,target,ensure_ascii=False,indent=2)
        target.write('\n')
    count=build(identity_root,search)
    return dict(maps=count,files=[p.relative_to(root).as_posix() for p in (anchors,search)])


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources',type=Path,help='New complete resource stage; generate both resources.')
    parser.add_argument('--exports',type=Path,help='Matching levelLayouts and roomDefinitions literal exports.')
    parser.add_argument('--identity-root',type=Path,help='Existing entry directory with prepared door anchors.')
    parser.add_argument('--output',type=Path,help='Index-only output, used with --identity-root.')
    args=parser.parse_args()
    if args.resources is not None:
        if args.exports is None or args.identity_root is not None or args.output is not None:
            parser.error('--resources requires --exports and cannot be combined with index-only options')
        result=build_resources(args.resources,args.exports)
    else:
        if args.identity_root is None or args.output is None or args.exports is not None:
            parser.error('Use --resources/--exports or --identity-root/--output')
        count=build(args.identity_root,args.output)
        result=dict(maps=count,output=str(args.output))
    print(json.dumps(result))
