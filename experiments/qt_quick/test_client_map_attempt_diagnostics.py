"""Attempt identity survives real Qt diagnostics writer/export with synthetic events.

Temporary files only; this is not native input, capture or game validation.
"""
import json
from types import SimpleNamespace
import unittest
import zipfile

from client_coordinator import ClientCoordinator
from client_input_diagnostics import InputDiagnostics
from client_preparation_diagnostics import PreparationDiagnostics
from client_problem_messages import operation_problem
from diagnostic_export import export_safe_diagnostic_bundle
import test_client_problems as fixtures


def attempt(**extra):
    return dict(attemptId='a'*32, openingId='b'*32, inputSequence=3,
                requestGeneration=4, recoveryId='c'*32, inputCreatedAt=1.25, **extra)


class MapAttemptSinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ProblemTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.ProblemTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service

    def target(self):
        target = SimpleNamespace(_closed=False, _closing=False, _diagnostics=None,
            _notified=set(), runtime=SimpleNamespace(snapshot=lambda:{}),
            traceReported=SimpleNamespace(emit=self.service.recordTrace))
        target._trace = lambda stage, **values:ClientCoordinator._trace(target,stage,**values)
        return target

    def rows(self):
        self.fixture.drain()
        return [json.loads(line) for line in self.service.log_path.read_bytes().splitlines()]

    def test_automatic_sample_event_keeps_attempt_identity_through_writer_and_export(self):
        ClientCoordinator._on_runtime_event(self.target(), dict(event='automatic-failure-sample',
            reason='metadata-only', season='s0',contextKey='s0:nightmare',mode='nightmare',
            mapAttempt=attempt(username='PRIVATE PLAYER'),
            failureSample=dict(saved=False,missingReason='capture-attempt-mismatch')))
        rows = self.rows()
        row = next(row for row in rows if row.get('stage') == 'automatic-failure-sample')
        self.assertEqual(row['mapAttempt'], attempt())
        self.assertEqual(row['season'], 's0')
        self.assertEqual(row['contextKey'], 's0:nightmare')
        bundle=export_safe_diagnostic_bundle(self.fixture.root,self.service.log_path.parent,
            app_version='1.0.6',build_fingerprint='TEST0001',destination=self.fixture.root/'attempt.zip')
        with zipfile.ZipFile(bundle['path']) as archive:
            payload=b''.join(archive.read(name) for name in archive.namelist() if name.endswith('.jsonl'))
        self.assertIn(b'"attemptId"',payload)
        self.assertNotIn(b'PRIVATE',payload)

    def test_repeated_preparation_opening_keeps_distinct_attempts_and_bounded_metadata(self):
        target=self.target()
        for aid in ('a'*32,'d'*32):
            diagnostic=PreparationDiagnostics(SimpleNamespace(_notify=lambda name,**data:
                ClientCoordinator._on_runtime_event(target,dict(event=name,season='s0',
                    contextKey='s0:nightmare',mode='nightmare',**data))),
                dict(_diagnosticOpeningId='b'*32,_diagnosticAttemptId=aid,inputSequence=3,recoveryId='c'*32))
            diagnostic.emit('begin','map-open-recovery')
            diagnostic.finish('map-open-recovery-exhausted')
        rows=[row for row in self.rows() if row.get('stage') == 'map-preparation']
        self.assertEqual(len(rows),4)
        self.assertEqual({r['diag']['attemptId'] for r in rows},{'a'*32,'d'*32})
        self.assertTrue(all(r['diag']['openingId']=='b'*32 for r in rows))
        self.assertTrue(all(r['diag']['recoveryId']=='c'*32 for r in rows))
        self.assertTrue(all('summary' not in r['diag'] for r in rows))

    def test_direct_capture_event_is_diagnostic_only_and_keeps_capture_outcome(self):
        target=self.target()
        for phase,reason,details in (
            ('capture-result','frame-captured',dict(capture=dict(provider='client-capture',
                backend='client-dc',foreground='verified-by-capture',clientSize=[1280,720]))),
            ('observation-result','current-frame-observed',dict(observation=dict(mapDetected=False,
                mapReady=False,floor=None,mapReason='map-ui-not-observed')))):
            ClientCoordinator._on_runtime_event(target,dict(event='map-capture-diagnostic',
                phase=phase,reason=reason,mapAttempt=attempt(),**details))
        rows=[row for row in self.rows() if row.get('stage')=='map-capture']
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['diag']['capture']['clientSize'],[1280,720])
        self.assertIs(rows[1]['diag']['observation']['mapDetected'],False)
        self.assertTrue(all(row['mapAttempt']==attempt() for row in rows))

    def test_oversized_preparation_details_cannot_erase_attempt_correlation(self):
        details=dict(openingId='b'*32,attemptId='a'*32,ordinal=1,phase='capture-result',
            inputSequence=3,recoveryId='c'*32,requestScope=dict(generation=4),probe=1,
            captureErrors=1,suppressed=0,exception=dict(type='OSError',
                stack=['x'*255]*16),capture=dict(provider='client-capture'))
        ClientCoordinator._on_runtime_event(self.target(),dict(event='map-preparation',
            reason='capture-exception',preparation=details))
        row=next(r for r in self.rows() if r.get('stage')=='map-preparation')
        self.assertEqual(row['mapAttempt'],{k:v for k,v in attempt().items() if k!='inputCreatedAt'})
        self.assertEqual(row['decision']['attemptId'],'a'*32)

    def test_receipt_problem_and_input_trace_keep_same_attempt(self):
        receipt=dict(action='map',success=False,reason='map-open-preparation-deadline',mapAttempt=attempt())
        self.service.record(operation_problem(receipt,dict(mode='nightmare',season='s0',contextKey='s0:nightmare')))
        InputDiagnostics(self.service.recordTrace).action(receipt,dict(roundActive=True,generation=4),{})
        rows=self.rows()
        problem=next(r for r in rows if 'problemGroupId' in r)
        action=next(r for r in rows if r.get('stage')=='action-receipt')
        self.assertEqual(problem['mapAttempt'],attempt())
        self.assertEqual(action['mapAttempt'],attempt())


if __name__=='__main__':unittest.main()
