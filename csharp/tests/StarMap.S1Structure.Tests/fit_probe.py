"""Passive intermediate-value probe of one unchanged original production candidate fit."""
import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np


def plain(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,dict):return {key:plain(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [plain(item) for item in value]
    return value


def main():
    p=argparse.ArgumentParser()
    for key in ('repo','resources','input','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    sys.path[:0]=[str(a.repo/'experiments/qt_quick'),str(a.repo/'app')]
    from client_providers import build_engine_factories
    from client_live_runtime import _AlignmentHost
    from single_map_structure import current_part,observed_corner_chains
    import fast_structure_alignment as fast
    description=json.loads(a.input.read_text(encoding='utf-8'))
    frame=cv2.imdecode(np.fromfile(description['image'],np.uint8),cv2.IMREAD_COLOR)
    engine=build_engine_factories(a.resources).get_engine(description['mode'],description['season'])
    proposals=[];points=[];contexts=[]
    propose,native=fast.propose,fast.native_point_geometry
    def observed_proposal(*args,**kwargs):
        result=propose(*args,**kwargs);proposals.append(dict(rows=plain(result[0]),diagnostics=plain(result[2])));contexts.append(result[1]);return result
    def observed_points(*args,**kwargs):
        result=native(*args,**kwargs);points.append(plain(result));return result
    fast.propose,fast.native_point_geometry=observed_proposal,observed_points
    try:
        host=_AlignmentHost(engine,collect_fit_diagnostics=True)
        bounds=(0,0,frame.shape[1],frame.shape[0])
        result=host.prepare_structure_scan(frame,bounds)(frame,description['mapId'],description['floor'],lambda:False,observation_bounds=bounds)
    finally:fast.propose,fast.native_point_geometry=propose,native
    context=contexts[0]
    observation=dict(bounds=context['observationBounds'],component=context['visible'][2],nodes=current_part(frame,context,'nodes'),chains=observed_corner_chains(frame,context))
    for name,value in (('fit.json',result),('proposals.json',proposals),('point-geometry.json',points),('events.json',host.events),('observation.json',observation)):
        (a.output/name).write_text(json.dumps(plain(value),ensure_ascii=False,indent=2),encoding='utf-8')
    cv2.imencode('.png',context['query'])[1].tofile(a.output/'query.png')
    cv2.imencode('.png',current_part(frame,context,'observed').astype(np.uint8))[1].tofile(a.output/'observed.png')
    print(json.dumps(dict(success=result.get('success'),nodes=len(observation['nodes']),chains=len(observation['chains']))))


if __name__=='__main__':main()
