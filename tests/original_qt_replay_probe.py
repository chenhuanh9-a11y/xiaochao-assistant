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
    call_timings=[]
    original_rank,original_fit=MapIdentityEngine.rank,_AlignmentHost.fit
    def rank(*args,**kwargs):
        calls['rank']+=1
        began=time.perf_counter()
        try:
            return original_rank(*args,**kwargs)
        finally:
            call_timings.append(dict(stage='rank',elapsedMs=(time.perf_counter()-began)*1000))
    def fit(*args,**kwargs):
        calls['fit']+=1
        began=time.perf_counter()
        try:
            return original_fit(*args,**kwargs)
        finally:
            call_timings.append(dict(stage='fit',elapsedMs=(time.perf_counter()-began)*1000))
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
            stopped = (cancelled.is_set() if hasattr(cancelled, 'is_set') else cancelled()) if cancelled is not None else False
            if not self.enabled or stopped:
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
        paths=RuntimePaths(Path(config.get('resourceInstallationRoot',output)),False)
        resources=paths.imported_resource_root
        resolver=ResourceResolver(resources,supported_capabilities=SUPPORTED_CAPABILITIES)
        if not config.get('resourceInstallationRoot'):
            summary=save_archive(config['baseArchive'],resources)
            refs={ref.component_id:ref for ref in summary.component_refs}
            displays = ()
            if config.get('routeArchive'):
                displays = save_archive(config['routeArchive'],resources).component_refs
            activate_context(resolver,paths,refs['base'],refs['controls'],season=sample['season'],mode=sample['mode'],
                             display_refs=displays)
        factories=build_imported_engine_factories(resolver,paths)
    owner=(CSharpGridRuntime(Path(config['gridExecutable']),output/'grid-cache')
           if config.get('backend','csharp') == 'csharp' else None)
    read_root=paths.installation_root if config.get('provider','imported') == 'imported' else output
    runtime=LiveRuntime(engine_factories=factories,capture_provider=Capture(),allowed_read_root=read_root,
                        evidence_kind='recorded',grid_runtime=owner)
    render_events=[]
    def observe_event(event):
        if event.get('event') in ('render-ready','render-revoked'):
            render_events.append((time.perf_counter(),event['event']))
            del render_events[:-128]
    runtime.set_event_callback(observe_event)
    session=SessionService(runtime)
    action_timings=[]
    def wait():
        deadline=time.monotonic()+config.get('waitSeconds',90)
        while session.busy and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.001)
        assert not session.busy,'actual Qt worker did not finish'
        assert session._pool.waitForDone(1000)
        app.processEvents()
    def dispatch(action,payload=None):
        began=time.perf_counter()
        session.dispatchWithPayload(action,payload or {})
        wait()
        receipt=session.snapshot.get('lastReceipt',{})
        assert receipt.get('action') == action, (action,session.snapshot)
        action_timings.append(dict(action=action,elapsedMs=(time.perf_counter()-began)*1000,
                                   success=receipt.get('success'),reason=receipt.get('reason')))
        return receipt
    result=dict(sampleId=sample['id'],provider=config.get('provider','imported'),
        backend=config.get('backend','csharp'),environment=environment,
        opencv=cv2.__version__,numpy=np.__version__,captures=[],shortOpenCycles=[],
        resourceSource='existing-private-catalog' if config.get('resourceInstallationRoot') else 'fresh-import')
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(MapIdentityEngine,'rank',rank))
            stack.enter_context(patch.object(_AlignmentHost,'fit',fit))
            stack.enter_context(patch.object(s0_vertical_controls,'observe_s0_map',s0))
            stack.enter_context(patch.object(s1_floor_observation,'detect_s1_floor',s1))
            if owner is not None:
                stack.enter_context(patch.object(fast,'_grid',side_effect=AssertionError('unexpected-python-grid-fallback')))
            assert session.reconfigure(sample['mode'],config.get('profile','shared-structure'),sample['season'])
            wait()
            assert dispatch('inspect_engine_resources')['success'],session.snapshot
            assert dispatch('start_round')['success'],session.snapshot
            for repeat in range(config.get('repeats',3)):
                if repeat and config.get('closeBetweenCaptures', True):
                    assert dispatch('close_map')['success'], session.snapshot
                    assert runtime.render_payload((runtime.snapshot().get('render') or {}).get('token')) is None
                capture_action=config.get('captureAction','capture_now')
                stage_start=len(call_timings)
                receipt=dispatch(capture_action,dict(openIntent=True,inputSequence=repeat*2+1,
                    inputSource='recorded-replay',inputCreatedAt=time.monotonic()) if capture_action=='map' else None)
                state=runtime.snapshot()
                assert not state['liveAuthorization'],'recorded input gained live authority'
                render=runtime.render_payload((state.get('render') or {}).get('token'))
                if render is not None:
                    np.save(output/f'pixels-{repeat}.npy',render['pixels'])
                result['captures'].append(dict(receipt=semantics(receipt,resources),
                    state=semantics({key:state.get(key) for key in FIELDS},resources),
                    elapsedMs=action_timings[-1]['elapsedMs'],
                    callTimings=call_timings[stage_start:],
                    gridCalls=len(owner.calls) if owner is not None else None,
                    processId=owner.process_id if owner is not None else None,rendered=render is not None))
            for sequence, duration in enumerate(config.get('shortOpenSeconds', ())):
                assert dispatch('end_round')['success'],session.snapshot
                assert dispatch('start_round')['success'],session.snapshot
                began=time.perf_counter()
                session.dispatchWithPayload('map',dict(openIntent=True,inputSequence=1000+sequence*2,
                    inputSource='recorded-replay',inputCreatedAt=time.monotonic()))
                while time.perf_counter()-began < duration:
                    app.processEvents()
                    time.sleep(.001)
                was_busy=session.busy
                open_ms=(time.perf_counter()-began)*1000
                current_events=[event for at,event in render_events if at >= began]
                render_ready=bool(current_events and current_events[-1]=='render-ready')
                close=dispatch('close_map')
                after=runtime.snapshot()
                token=(after.get('render') or {}).get('token')
                assert close['success'] and not after['mapOpen'],session.snapshot
                assert runtime.render_payload(token) is None,'late worker restored closed map pixels'
                result['shortOpenCycles'].append(dict(requestedOpenMs=duration*1000,
                    actualOpenMs=open_ms,
                    busyAtClose=was_busy,renderReadyBeforeClose=render_ready,
                    closeMs=action_timings[-1]['elapsedMs'],
                    staleResults=session.stale_results,closed=True,renderedAfterClose=False))
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
        result['actionTimings']=action_timings
        result['callTimings']=call_timings
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
