"""Offline preparation through the same installed shared-reference providers."""
from pathlib import Path
from dataclasses import replace
import argparse
import hashlib
import json
import sys
import threading

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'app'))
import identityv_daily_assistant as app
from fast_structure_alignment import prepare,load_prepared,prepared_path_for


def references(root, modes, memory_root):
    """Construct resources only; no Tk, listener, capture, rank or pose solve."""
    app.RESOURCE_ROOT = root
    for mode in modes:
        context = app.DailyAssistant.__new__(app.DailyAssistant)
        context.aligned_author_route_lock = threading.RLock()
        context.preferred_route_profile_id = 'shared-structure'
        context._log = lambda *args,**kwargs:None
        context._install_map_mode(mode,resource_root=root if mode=='nightmare' else None,
                                  memory_root=memory_root/mode)
        if not context._shared_structure_mode_enabled():
            raise ValueError(f'{mode} has no declared shared reference provider')
        for item in context.pack.maps:
            for floor in item.layers:
                yield mode,item.id,str(floor),context


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('resource_root',type=Path)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--mode',choices=('nightmare','difficult'),action='append')
    parser.add_argument('--select',action='append',default=[],metavar='MAP/FLOOR')
    args=parser.parse_args()
    root=args.resource_root.resolve();output_root=args.output_root.resolve()
    if root==output_root:
        raise ValueError('Use an independent output root; source packages remain read-only')
    selected={tuple(value.rsplit('/',1)) for value in args.select}
    if any(len(key)!=2 for key in selected):
        raise ValueError('Selection must be MAP/FLOOR')
    seen=set();rows=[]
    for mode,map_id,floor,context in references(root,args.mode or ('nightmare','difficult'),output_root/'work/generator-memory'):
        if selected and (map_id,floor) not in selected:
            continue
        reference=context._shared_structure_reference('shared-structure',map_id,floor)
        if reference is None:
            raise ValueError(f'Shared reference unavailable: {mode}/{map_id}/{floor}')
        target_reference=replace(reference,resource_root=output_root)
        target=prepared_path_for(target_reference)
        status='existing-matching'
        if target.exists():
            if load_prepared(target_reference) is None:
                raise ValueError(f'Existing prepared file has another binding: {target}')
        else:
            result=prepare(reference.clean_mask)
            target.parent.mkdir(parents=True,exist_ok=True)
            np.savez_compressed(target,distance=result.distance,chains=result.chains,rays=result.rays,
                pitch=result.pitch,shape=result.shape,sourceMetadata=json.dumps(reference.metadata,sort_keys=True))
            status='generated'
        loaded=load_prepared(target_reference)
        if loaded is None:
            raise ValueError(f'Prepared readback rejected: {target}')
        seen.add((map_id,floor))
        rows.append(dict(mode=mode,mapId=map_id,floor=floor,status=status,
            sourceReference=str(reference.path),sourceKind=reference.metadata.get('sourceKind'),
            sourceClass=reference.metadata.get('sourceClass'),shape=list(reference.shape),
            knownBoundary=reference.known_boundary is not None,unknown=reference.unknown_mask is not None,
            nativeWalls=reference.native_wall_segments is not None,
            prepared=str(target),bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            distanceDtype=str(loaded.distance.dtype),sourceMetadata=reference.metadata))
        print(json.dumps({k:rows[-1][k] for k in ('mapId','floor','status','bytes','prepared')},ensure_ascii=False),flush=True)
    if selected-seen:
        raise ValueError(f'Selected bindings not declared by providers: {sorted(selected-seen)}')
    report=dict(resourceRoot=str(root),outputRoot=str(output_root),modes=args.mode or ['nightmare','difficult'],
        scope='Offline reference preparation/readback only; no rank/solver/GUI. Known/unknown/wall channels remain in runtime providers.',rows=rows)
    output_root.mkdir(parents=True,exist_ok=True)
    receipt=output_root/'generation-result.json'
    receipt.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert json.loads(receipt.read_text(encoding='utf-8'))==report
    print(json.dumps(dict(success=True,generated=sum(row['status']=='generated' for row in rows),receipt=str(receipt))))


if __name__=='__main__':
    main()
