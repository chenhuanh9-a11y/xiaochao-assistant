"""Actual Qt Session/Runtime probe over explicit recorded pixels and resources.

Only OS capture/window facts are substituted. No annotation, recorded detector
result, identity or pose is supplied to the solver. Run in a fresh process with
PYTHONPATH selecting either the transplanted code or the exact donor checkout.
"""
from contextlib import ExitStack
from copy import deepcopy
import importlib.abc
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')

INTEGRATED_MODULES = {'recognition_race','structure_pose_solver','structure_pose_inputs',
    'client_structure_recognition','client_local_structure_recognition','client_s1_coordinate_recognition'}
FIELDS = ('season','mode','mapId','floor','preferredProfileId','effectiveProfileId','displayFallback',
    'fallbackReason','roundActive','mapOpen','liveAuthorization','evidenceKind','durableMapId',
    'displayReferenceMapId','identityState','identitySource','candidateRows','automaticCandidateRows',
    'selectedCandidateIndex','poseAttempt','poseResult','floorSource','observedFloor','floorRequired',
    'floorPolicyReason','displayAuthorized','render')
VARIABLES = {'token','frameId','roundId','cycleId','epoch','generation','requestId','attemptId',
    'renderToken','authorizationId','createdAt','startedAt','completedAt','deadlineAt','elapsedSeconds',
    'elapsedMs','identityElapsedMs','engineIdentity','requestGeneration','inputSequence'}


class RejectIntegrated(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in INTEGRATED_MODULES:
            raise AssertionError('retired-integrated-module-imported:' + fullname)


def semantics(value, resource_root):
    if isinstance(value,dict):
        result={key:semantics(item,resource_root) for key,item in value.items() if key not in VARIABLES}
        if isinstance(result.get('path'),str):
            path=Path(result['path'])
            if path.is_absolute() and path.is_relative_to(resource_root):
                result['path']='<resources>/'+path.relative_to(resource_root).as_posix()
        return result
    if isinstance(value,(tuple,list)):
        return [semantics(item,resource_root) for item in value]
    return value


def run_case(config):
    forbidden=RejectIntegrated()
    sys.meta_path.insert(0,forbidden)
    try:
        return _run_case(config)
    finally:
        sys.meta_path.remove(forbidden)


def _run_case(config):
    import cv2
    import numpy as np
    import PySide6
    environment = dict(python='.'.join(map(str, sys.version_info[:3])),
        opencv=cv2.__version__, numpy=np.__version__, pyside=PySide6.__version__)
    expected = config.get('expectedEnvironment', dict(python='3.13.3', opencv='4.12.0',
        numpy='2.2.6', pyside='6.11.1'))
    assert environment == expected, ('recorded-probe-runtime-mismatch', environment, expected)
    from PySide6.QtCore import QCoreApplication
    from client_region_capture import ClientCaptureContext,RegionCapture
    from client_live_runtime import LiveRuntime,_AlignmentHost
    from client_session import SessionService
    from client_startup_cleanup import shutdown_client
    from csharp_grid_runtime import CSharpGridRuntime
    from map_identity_engine import MapIdentityEngine
    import fast_structure_alignment as fast
    import s0_vertical_controls,s1_floor_observation

    output=Path(config['output']).resolve()
    output.mkdir(parents=True,exist_ok=False)
    sample=config['sample']
    calls=dict(rank=0,fit=0,floor=0)
    original_rank,original_fit=MapIdentityEngine.rank,_AlignmentHost.fit
    def rank(*args,**kwargs):
        calls['rank']+=1
        return original_rank(*args,**kwargs)
    def fit(*args,**kwargs):
        calls['fit']+=1
        return original_fit(*args,**kwargs)
    original_s0,original_s1=s0_vertical_controls.observe_s0_map,s1_floor_observation.detect_s1_floor
    def s0(*args,**kwargs):
        calls['floor']+=1
        return original_s0(*args,**kwargs)
    def s1(*args,**kwargs):
        calls['floor']+=1
        return original_s1(*args,**kwargs)
    class Capture:
        evidence_kind='recorded'
        def __init__(self):
            self.frame=cv2.imdecode(np.fromfile(sample['image'],np.uint8),cv2.IMREAD_COLOR)
            self.frame.setflags(write=False)
            facts=sample['physicalClient']
            self.context=ClientCaptureContext((facts['Width'],facts['Height']),
                (facts['Left'],facts['Top'],facts['Left']+facts['Width'],facts['Top']+facts['Height']),facts['Dpi'])
            self.enabled=False
            self.generation=1
        @property
        def snapshot(self):
            return dict(enabled=self.enabled,generation=self.generation,evidenceKind='recorded')
        def enable(self):
            self.enabled=True
            return True
        def disable(self):
            self.enabled=False
        def shutdown(self):
            self.disable()
            self.generation+=1
        def is_current(self,context,*,generation=None):
            return context==self.context and (generation is None or generation==self.generation)
        def capture(self,cancelled=None):
            if not self.enabled or cancelled is not None and cancelled():
                raise InterruptedError('recorded-capture-unavailable')
            bounds=(0,0,*self.context.full_client_size)
            return RegionCapture(self.frame,bounds,bounds,self.context,'recorded-png')

    app=QCoreApplication.instance() or QCoreApplication([])
    if config.get('provider','imported') == 'donor':
        resources=output/'resources'
        shutil.copytree(config['resources'],resources)
        from client_providers import EngineFactories
        factories=EngineFactories(resources)
    else:
        from imported_resource_archive import save_archive
        from imported_resource_catalog import ResourceResolver
        from client_imported_providers import SUPPORTED_CAPABILITIES,build_imported_engine_factories
        from client_imported_activation import activate_context
        from client_paths import RuntimePaths
        paths=RuntimePaths(output,False)
        resources=paths.imported_resource_root
        summary=save_archive(config['baseArchive'],resources)
        resolver=ResourceResolver(resources,supported_capabilities=SUPPORTED_CAPABILITIES)
        refs={ref.component_id:ref for ref in summary.component_refs}
        activate_context(resolver,paths,refs['base'],refs['controls'],season=sample['season'],mode=sample['mode'])
        factories=build_imported_engine_factories(resolver,paths)
    owner=(CSharpGridRuntime(Path(config['gridExecutable']),output/'grid-cache')
           if config.get('backend','csharp') == 'csharp' else None)
    runtime=LiveRuntime(engine_factories=factories,capture_provider=Capture(),allowed_read_root=output,
                        evidence_kind='recorded',grid_runtime=owner)
    session=SessionService(runtime)
    def wait():
        deadline=time.monotonic()+90
        while session.busy and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.001)
        assert not session.busy,'actual Qt worker did not finish'
        assert session._pool.waitForDone(1000)
        app.processEvents()
    def dispatch(action):
        session.dispatch(action)
        wait()
        receipt=session.snapshot.get('lastReceipt',{})
        assert receipt.get('action') == action, (action,session.snapshot)
        return receipt
    result=dict(sampleId=sample['id'],provider=config.get('provider','imported'),
        backend=config.get('backend','csharp'),environment=environment,
        opencv=cv2.__version__,numpy=np.__version__,captures=[])
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(MapIdentityEngine,'rank',rank))
            stack.enter_context(patch.object(_AlignmentHost,'fit',fit))
            stack.enter_context(patch.object(s0_vertical_controls,'observe_s0_map',s0))
            stack.enter_context(patch.object(s1_floor_observation,'detect_s1_floor',s1))
            if owner is not None:
                stack.enter_context(patch.object(fast,'_grid',side_effect=AssertionError('unexpected-python-grid-fallback')))
            assert session.reconfigure(sample['mode'],'shared-structure',sample['season'])
            wait()
            assert dispatch('inspect_engine_resources')['success'],session.snapshot
            assert dispatch('start_round')['success'],session.snapshot
            for repeat in range(config.get('repeats',3)):
                if repeat and config.get('closeBetweenCaptures', True):
                    assert dispatch('close_map')['success'], session.snapshot
                    assert runtime.render_payload((runtime.snapshot().get('render') or {}).get('token')) is None
                receipt=dispatch('capture_now')
                state=runtime.snapshot()
                assert not state['liveAuthorization'],'recorded input gained live authority'
                render=runtime.render_payload((state.get('render') or {}).get('token'))
                if render is not None:
                    np.save(output/f'pixels-{repeat}.npy',render['pixels'])
                result['captures'].append(dict(receipt=semantics(receipt,resources),
                    state=semantics({key:state.get(key) for key in FIELDS},resources),
                    gridCalls=len(owner.calls) if owner is not None else None,
                    processId=owner.process_id if owner is not None else None,rendered=render is not None))
            assert dispatch('end_round')['success'],session.snapshot
            assert not runtime.snapshot()['roundActive']
            if config.get('requireGrid'):
                assert sum(row['gridCalls'] or 0 for row in result['captures']) > 0,'production path never reached C#'
            assert calls['floor'] > 0,'production path never observed current pixels'
            if config.get('requireFit'):
                assert calls['fit'] > 0,'production path never reached original Host'
            pids={row['processId'] for row in result['captures'] if row['gridCalls']}
            assert len(pids)<=1 and None not in pids,'C# process was not reused'
        result['calls']=dict(calls)
    finally:
        process=owner._client._process if owner is not None and owner._client is not None else None
        shutdown_client(SimpleNamespace(session=session,runtime=runtime,grid_runtime=owner))
        runtime.shutdown()
        factories.invalidate()
        if process is not None:
            assert process.poll()==0,'C# process did not exit normally'
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result


if __name__=='__main__':
    config=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    result=run_case(config)
    print(json.dumps(dict(sample=result['sampleId'],calls=result['calls'],
        gridCalls=[row['gridCalls'] for row in result['captures']],
        rendered=[row['rendered'] for row in result['captures']]),ensure_ascii=False))
