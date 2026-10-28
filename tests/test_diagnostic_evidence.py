"""Real failure writer -> bounded ZIP -> decoded pixels; no GUI or game capture."""
import ast
import copy
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import zipfile

import cv2
import numpy as np

APP = Path(__file__).parents[1] / 'app'
sys.path.insert(0, str(APP))
from diagnostic_evidence import export_failure_evidence
from map_identity_engine import entry_rank_diagnostics
from nightmare_candidate import NightmareMemoryStore


def production_functions():
    tree = ast.parse((APP / 'identityv_daily_assistant.py').read_text('utf-8-sig'))
    names = {'export_safe_diagnostic_bundle', 'safe_diagnostic_log_files', '_bounded_log_tail',
             '_redact_local_paths', '_event_log_session_id', '_record_unresolved_identity_rejection',
             'export_diagnostic_logs', 'diagnostic_runtime_context'}
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == len(names)
    ns = dict(Path=Path, datetime=datetime, json=json, os=os, re=re, sys=sys,
              tempfile=tempfile, zipfile=zipfile, hashlib=hashlib, copy=copy, time=time, threading=threading,
              np=np, NightmareMemoryStore=NightmareMemoryStore,
              DIAGNOSTIC_LOG_LIMIT=8, DIAGNOSTIC_FILE_BYTE_LIMIT=8*1024*1024,
              DIAGNOSTIC_TOTAL_BYTE_LIMIT=32*1024*1024,
              APP_VERSION='test', BUILD_FINGERPRINT='TEST1234',
              process_integrity_level=lambda:dict(level='test'),
              collect_identityv_window_environment=lambda:dict(candidateCount=0),
              ranked_event_is_current=lambda token,current: token == current)
    exec(compile(ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
                 str(APP / 'identityv_daily_assistant.py'), 'exec'), ns)
    return ns


class DiagnosticEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production = production_functions()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.live = self.root / 'live'; self.live.mkdir()
        self.log = 'events-20261029-002000.jsonl'
        self.window = (datetime.fromisoformat('2026-10-29T00:20:00+08:00').timestamp(),
                       datetime.fromisoformat('2026-10-29T00:26:00+08:00').timestamp())
        self.pixels = np.arange(24*32*3, dtype=np.uint8).reshape(24,32,3)
        self.store = NightmareMemoryStore(SimpleNamespace(mode='nightmare',by_id={}), self.root/'memory')

    def save(self, floor='1', cycle=9):
        row = self.store.record_alignment_failure(None, floor, 'identity-unresolved', {}, self.pixels,
            cycle_id=cycle, lossless=True, diagnostic=dict(stage='identity-rejection', mode='nightmare',
                cycleId=cycle, eventPath=str(self.live/self.log), buildFingerprint='TEST1234',
                frameCapturedAtUtc='2026-10-28T16:21:00+00:00',
                difficultRecognition={'side':{'queryTurns':2}}))
        return row

    def export(self, **kwargs):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            report=export_failure_evidence(archive,[self.store.root],{self.log:self.window},
                self.production['_redact_local_paths'],build_fingerprint='TEST1234',**kwargs)
        return report,zipfile.ZipFile(output)

    def test_actual_writer_b1_frame_and_metadata_roundtrip(self):
        self.save('B1')
        report,archive=self.export()
        with archive:
            self.assertEqual(report['imageCount'],1)
            case=report['cases'][0]
            self.assertEqual((case['logFile'],case['cycleId'],case['floor']),(self.log,9,'B1'))
            decoded=cv2.imdecode(np.frombuffer(archive.read(case['files'][0]['path']),np.uint8),cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(decoded,self.pixels)
            metadata=json.loads(archive.read(case['metadataPath']))
            self.assertEqual(metadata['diagnostic']['difficultRecognition']['side']['queryTurns'],2)
            self.assertNotIn(str(self.root),archive.read(case['metadataPath']).decode())

    def test_actual_export_entry_includes_evidence_without_settings(self):
        self.save()
        (self.live/self.log).write_text('\n'.join(json.dumps({'time':t,'event':'ranked'}) for t in
            ['2026-10-29T00:20:00+08:00','2026-10-29T00:26:00+08:00']),encoding='utf-8')
        (self.store.root/'ui-settings.json').write_text('private',encoding='utf-8')
        for include in (False,True):
            result=self.production['export_safe_diagnostic_bundle'](self.root,self.live,
                package_metadata={'status':'test'},failure_roots=(self.store.root,) if include else ())
            with zipfile.ZipFile(result['path']) as archive:
                self.assertIsNone(archive.testzip())
                manifest=json.loads(archive.read('diagnostic-manifest.json'))
                self.assertEqual(manifest['failureEvidence']['imageCount'],int(include))
                self.assertEqual(manifest['textOnly'],not include)
                self.assertFalse(any('settings' in name for name in archive.namelist()))

    def test_absent_frame_reports_missing_instead_of_silent_success(self):
        row=self.save(); (self.store.root/row['samplePath']).unlink()
        report,archive=self.export()
        with archive:
            self.assertEqual(report['imageCount'],0)
            self.assertEqual(report['cases'][0]['files'][0]['status'],'sample-file-missing')
            self.assertIn(report['cases'][0]['metadataPath'],archive.namelist())

    def test_export_button_controller_reaches_real_archive_on_background_thread(self):
        self.save()
        (self.live/self.log).write_text('\n'.join(json.dumps({'time':t,'event':'ranked'}) for t in
            ['2026-10-29T00:20:00+08:00','2026-10-29T00:26:00+08:00']),encoding='utf-8')
        self.production.update(ROOT=self.root,LIVE_ROOT=self.live)
        subject=SimpleNamespace(diagnostic_export_in_progress=False,
            _set_diagnostic_export_buttons=lambda *args:None,_log=lambda *args,**kwargs:None,
            game_hwnd=None,game_foreground=True,map_is_open=True,package_metadata={'status':'test'},
            memory_store=self.store,game_mode='nightmare',events=queue.Queue())
        self.production['export_diagnostic_logs'](subject)
        result=subject.events.get(timeout=3)
        self.assertEqual(result['kind'],'diagnostic-exported',result)
        self.assertEqual(result['includedFailureImageCount'],1)
        with zipfile.ZipFile(result['path']) as archive:
            manifest=json.loads(archive.read('diagnostic-manifest.json'))
            self.assertIn('failureSampling',manifest['runtimeContext'])
            self.assertEqual(manifest['failureEvidence']['cases'][0]['cycleId'],9)

    def test_writer_budget_failure_is_exposed_without_frame_or_journal(self):
        self.store._failure_sampling.max_disk_bytes=0
        accepted=self.store.enqueue_alignment_failure(None,'1','identity-unresolved',{},self.pixels,
            cycle_id=9,diagnostic=dict(stage='identity-rejection',mode='nightmare',cycleId=9),
            sampling_scope='nightmare-identity-unresolved')
        self.assertTrue(accepted)
        deadline=time.monotonic()+3
        while self.store._failure_queue.unfinished_tasks and time.monotonic()<deadline:time.sleep(.01)
        status=self.store.alignment_failure_sampling_status()
        self.assertEqual(status['state'],'write-error-backoff')
        self.assertIn('alignment-failure-budget-exhausted',status['writeError'])
        self.assertEqual(status['savedCount'],0)

    def test_corrupt_frame_keeps_measured_failure_and_reports_hash_mismatch(self):
        row=self.save();path=self.store.root/row['samplePath'];path.write_bytes(path.read_bytes()+b'changed')
        report,archive=self.export()
        with archive:self.assertEqual(report['cases'][0]['files'][0]['status'],'sample-sha256-mismatch')

    def test_budget_and_count_omissions_are_explicit(self):
        self.save(cycle=9);self.save(cycle=10)
        report,archive=self.export(max_samples=1,max_bytes=0)
        with archive:
            self.assertEqual(report['omittedBySampleLimit'],1)
            self.assertEqual(report['cases'][0]['cycleId'],10)
            self.assertEqual(report['cases'][0]['files'][0]['status'],'export-image-budget-exhausted')

    def test_unrelated_session_excluded_and_partial_journal_line_counted(self):
        row=self.save();row['diagnostic']['eventPath']='events-other.jsonl';row['diagnostic']['buildFingerprint']='OTHER'
        self.store.alignment_failure_path.write_text(json.dumps(row)+'\n{partial',encoding='utf-8')
        report,archive=self.export()
        with archive:
            self.assertEqual(report['matchingCases'],0)
            self.assertEqual(report['journals'][0]['invalidLines'],1)

    def test_path_escape_is_never_exported(self):
        row=self.save();row['samplePath']='failures/../../secret.png'
        self.store.alignment_failure_path.write_text(json.dumps(row)+'\n',encoding='utf-8')
        report,archive=self.export()
        with archive:self.assertEqual(report['cases'][0]['files'][0]['status'],'invalid-sample-path')

    def test_no_samples_is_explicit(self):
        report,archive=self.export()
        with archive:
            self.assertEqual(report['status'],'no-matching-saved-frame')
            self.assertEqual(report['journals'][0]['status'],'journal-missing')

    def test_diagnostic_summary_keeps_counts_transform_and_cache_cause(self):
        matrix=[[1,0,23],[0,1,45],[0,0,1]]
        evidence=dict(tilemapEntry={'queryChains':2,'minimumQueryChains':3},
            tilemapSide={'queryTurns':2,'minimumQueryTurns':4,'queryPairs':0},
            tilemapSideCandidates=[dict(mapId='map',floor='1',score=4,matrixSourceToClient=matrix,
                verification=dict(success=False,reason='side-current-frame-evidence',
                    regions={'regionalEdgeVerified':False,'testedPixels':42},entryWalls={'maxPixels':180},
                    matrixSourceToClient=matrix))],
            sourceStructureCandidates={'featureStatus':{'ready':False,'reason':'extractor-mismatch'}})
        before=copy.deepcopy(evidence); summary=entry_rank_diagnostics(evidence)
        self.assertEqual(evidence,before)
        self.assertEqual(summary['main']['queryChains'],2)
        self.assertEqual(summary['side']['minimumQueryTurns'],4)
        self.assertEqual(summary['side']['bestCandidates'][0]['verificationMatrixSourceToClient'],matrix)
        self.assertEqual(summary['sourceStructures']['featureStatus']['reason'],'extractor-mismatch')
        json.dumps(summary)

    def test_real_controller_writer_accepts_b1_and_uses_same_frame_snapshot(self):
        now=time.perf_counter()
        for mode in ('nightmare','difficult'):
            with self.subTest(mode=mode):
                messages=[]
                store=NightmareMemoryStore(SimpleNamespace(mode=mode,by_id={}),self.root/mode)
                subject=SimpleNamespace(game_mode=mode,gate=SimpleNamespace(confirmed_id=None),
                    map_is_open=True,active_map_cycle_id=9,surface_visibility_generation=4,
                    current_frame_generation=3,capture_generation=2,memory_store=store,
                    route_profile_id='author',event_path=self.live/self.log,package_metadata={},
                    engine=SimpleNamespace(last_difficult_evidence={'sourceStructureCandidates':'WRONG FRAME'}),
                    _log=lambda event,**fields:messages.append((event,fields)))
                event=dict(cycleId=9,rankStartedMonotonicAt=now-.02,rankCompletedMonotonicAt=now-.01,
                    rankSourceSurfaceGeneration=4,rankSourceFrameGeneration=3,token=2,mapOpen=True,
                    floor='B1',floorSource='automatic',frame=self.pixels)
                top={'identityHardGatePassed':False,'difficultRecognition':{'sourceStructures':{'reason':'same-frame'}}}
                self.production['_record_unresolved_identity_rejection'](subject,event,top)
                deadline=time.monotonic()+3
                while store._failure_queue.unfinished_tasks and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(store.failure_saved_count,1)
                row=store.alignment_failures()[0]
                self.assertEqual(row['diagnostic']['sourceStructureCandidates'],{'reason':'same-frame'})
                self.production['_record_unresolved_identity_rejection'](subject,event,top)
                self.assertEqual(store.failure_saved_count,1)  # Existing per-profile cooldown.
                self.assertEqual(messages[-1][1]['sampling']['state'],'sample-cooldown')
                self.production['_record_unresolved_identity_rejection'](subject,dict(event,rankSourceFrameGeneration=0),top)
                self.assertEqual(messages[-1][1]['reason'],'frame-generation-mismatch')
                subject.map_is_open=False
                self.production['_record_unresolved_identity_rejection'](subject,event,top)
                self.assertEqual(messages[-1][1]['reason'],'map-closed')
                self.assertEqual(store.failure_saved_count,1)


if __name__=='__main__':unittest.main()
