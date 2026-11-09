"""Record the original production fast attempt with unchanged original consumers."""
import argparse
import copy
import functools
import inspect
import json
import math
from pathlib import Path
import sys

p=argparse.ArgumentParser()
for key in ('source','resources','index','output'):
    p.add_argument('--'+key,required=True,type=Path)
a=p.parse_args()
a.output.mkdir(exist_ok=False)
sys.path[:0]=[str(a.source/'experiments/qt_quick'),str(a.source/'app')]
import numpy as np
import cv2
import fast_structure_alignment as fast
import single_map_structure as single
from client_providers import build_engine_factories
from client_live_runtime import _AlignmentHost

if np.__version__!='2.2.6': raise RuntimeError('Pinned NumPy required')
factories=build_engine_factories(a.resources)
engines={}
caps={}

def record(module,name,key):
    original=getattr(module,name)
    @functools.wraps(original)
    def wrapped(*args,**kwargs):
        value=original(*args,**kwargs)
        if inspect.currentframe().f_back.f_code.co_name=='_try_alignment':
            caps[key]=copy.deepcopy(value)
        return value
    setattr(module,name,wrapped)

record(fast,'native_point_geometry','Point')
record(single,'graph_point_geometry','Point')
record(single,'regional_validation','Regions')
record(single,'partial_wall_geometry','Wall')
provenance=('validationBasis','validationSensorBasis','coordinatePackId',
    'poseVerifiedAgainstCanonicalGeometry','profileRenderDerivedFromCanonical',
    'poseVerifiedAgainstSharedStructure','poseVerifiedAgainstRenderedProfile','routeProfileIndependent')

def native(value):
    if isinstance(value,np.ndarray): return native(value.tolist())
    if isinstance(value,np.generic): return native(value.item())
    if isinstance(value,float) and not math.isfinite(value):
        return 'NaN' if math.isnan(value) else 'Infinity' if value>0 else '-Infinity'
    if isinstance(value,dict): return {k:native(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [native(v) for v in value]
    return value

cases=[]
for row in json.loads(a.index.read_text('utf8')):
    name=row['case']; directory=a.index.parent/name
    data=json.loads((directory/'input.json').read_text('utf8'))
    dest=a.output/name; dest.mkdir()
    if data.get('kind')=='empty-observed-negative':
        (dest/'expected-fit.json').write_text(json.dumps({'skipped':'This fixture replaces only proposal evidence, not the complete fit.'}),'utf8')
        continue
    prepared=json.loads((directory/data['preparedPath']).read_text('utf8'))
    filename=Path(prepared['resourcePath']).name
    archive=next(a.resources.rglob(filename))
    with np.load(archive,allow_pickle=False) as product:
        metadata=json.loads(str(product['sourceMetadata'].item()))
    binding=metadata['binding']; mid,floor=binding['mapId'],str(binding['floor'])
    mode='nightmare'; season='s0'
    if (season,mode) not in engines: engines[season,mode]=factories.get_engine(mode,season)
    host=_AlignmentHost(engines[season,mode],collect_fit_diagnostics=True)
    host.map_id,host.floor=mid,floor
    original_residual=host._rendered_profile_current_frame_residual
    def residual(*args,**kwargs):
        value=original_residual(*args,**kwargs)
        caps['Residual']=copy.deepcopy(value)
        return value
    host._rendered_profile_current_frame_residual=residual
    frame_path=Path(data['sourcePath'])
    if not frame_path.is_absolute(): frame_path=directory/frame_path
    frame=cv2.imdecode(np.fromfile(frame_path,np.uint8),cv2.IMREAD_COLOR)
    if frame is None: raise RuntimeError('Frame decode failed')
    caps={}
    kwargs=dict(cancelled=lambda:bool(data.get('cancelled')),context=single.current_geometry(frame))
    if data.get('prior') is not None:
        # The C# API evaluates one local attempt; fallback orchestration is a separate owner.
        result=fast._try_alignment(host,frame,mid,floor,_external_prior=np.asarray(data['prior'],np.float64),**kwargs)
    else:
        result=fast.try_alignment(host,frame,mid,floor,**kwargs)
    event=next((x for x in reversed(host.events) if x.get('event')=='experimental-fast-alignment'),{})
    if 'Residual' in caps:
        for key in provenance: caps['Residual'].pop(key,None)
    expected=dict(success=bool(result and result.get('success')),matrix=result.get('matrixSourceToClient') if result else None,
        reason=(result or event).get('reason'),**{key:caps.get(key) for key in ('Point','Regions','Wall','Residual')})
    (dest/'expected-fit.json').write_text(json.dumps(native(expected),ensure_ascii=False,indent=2,allow_nan=False),'utf8')
    (dest/'original-result.json').write_text(json.dumps(native(dict(result=result,events=host.events)),ensure_ascii=False,indent=2,allow_nan=False),'utf8')
    cases.append(dict(case=name,success=expected['success'],reason=expected['reason'],recorded=list(caps)))
(a.output/'environment.json').write_text(json.dumps(native(dict(numpy=np.__version__,opencv=cv2.__version__,
    originalFast=fast.__file__,originalRuntime=sys.modules[_AlignmentHost.__module__].__file__,
    providerInventory={str(k):v.qt_resource_inventory for k,v in engines.items()},
    instrumentation='Read-only wrappers record direct original fast-attempt consumers; numerical outputs returned unchanged.',
    excludedProvenance=list(provenance),timingFields=['preprocessAndFitMs'],
    scope='Single fast geometric attempt on a supplied exact reference. Does not test identity, canonical trust, fallback orchestration or display.',cases=cases)),indent=2,ensure_ascii=False),'utf8')
print(json.dumps(dict(cases=len(cases),successes=sum(x['success'] for x in cases))))
