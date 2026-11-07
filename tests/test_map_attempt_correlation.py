"""Offline input/preparation evidence, real methods with native/Qt boundaries replaced.

No input hook, capture, solver or game window is started by these tests.
"""
import ast
import json
import math
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, create_autospec

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
from client_preparation_diagnostics import PreparationDiagnostics
from client_input_diagnostics import InputDiagnostics
from diagnostic_privacy import safe_record, sanitize_log_payload


def coordinator_method(name):
    source = ast.parse((ROOT / 'experiments/qt_quick/client_coordinator.py').read_text(encoding='utf-8'))
    cls = next(row for row in source.body if isinstance(row, ast.ClassDef) and row.name == 'ClientCoordinator')
    method = next(row for row in cls.body if isinstance(row, ast.FunctionDef) and row.name == name)
    method.decorator_list = []
    namespace = dict(math=math, InputDiagnostics=InputDiagnostics)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 'client_coordinator.py', 'exec'), namespace)
    return namespace[name]


def map_input_method():
    return coordinator_method('_map_input_requested')


class MapInputEvidenceTests(unittest.TestCase):
    def owner(self):
        state = dict(roundActive=False, mapOpen=False, displayAuthorized=False,
            mode='nightmare', roundId=0, generation=7, frameId='',
            windowTitle='PRIVATE OTHER APP', username='PRIVATE PLAYER')
        owner = SimpleNamespace(_closing=False, _closed=False, _bound='42',
            _map_input_sequence=0, _waiting_foreground=False, _map_was_open=False,
            _diagnostics=None, _native_windows=SimpleNamespace(foreground_window=lambda:42),
            runtime=SimpleNamespace(cancel_map_open_recovery=Mock()),
            session=SimpleNamespace(snapshot=state, dispatchWithPayload=Mock()),
            _trace=Mock(), _pending_context=False,
            resources=SimpleNamespace(busy=False, pendingKind=''),
            _map_resource_notified=set(), _cancel_pointer_drag=Mock(),
            _revoke_runtime=Mock(), _close_map_from_input=Mock(),
            _reset_map_input=Mock(), notificationRequested=SimpleNamespace(emit=Mock()),
            changed=SimpleNamespace(emit=Mock()))
        # Execute the real memory-only readiness predicate, but keep resource
        # inspection and action delivery offline with the production signature.
        owner._manual_floor_observation_ready = MethodType(
            coordinator_method('_manual_floor_observation_ready'), owner)
        owner._map_resource_problem = create_autospec(MethodType(
            coordinator_method('_map_resource_problem'), owner), return_value=None)
        return owner

    def test_accepted_intent_reaches_default_trace_without_optional_lifecycle_log(self):
        owner = self.owner()
        event = dict(sequence=1, openIntent=True, createdAt=12., hwnd=42,
                     source='hotkey', rawKey='PRIVATE KEY', foregroundPid=998)
        map_input_method()(owner, event)
        owner._trace.assert_called_once()
        args, values = owner._trace.call_args
        self.assertEqual(args, ('map-input',))
        self.assertEqual(values['decision']['inputSequence'], 1)
        self.assertEqual(values['diag']['inputSequence'], 1)
        self.assertIs(values['diag']['openIntent'], True)
        self.assertEqual(values['diag']['state']['source'], 'session-snapshot')
        self.assertIs(values['diag']['state']['roundActive'], False)
        self.assertNotIn('PRIVATE', json.dumps(values['diag']))
        self.assertNotIn('hwnd', values['diag'])
        owner._map_resource_problem.assert_called_once_with(
            owner.session.snapshot, allow_manual_floor=False)
        owner.session.dispatchWithPayload.assert_called_once_with('map',
            dict(inputSequence=1, openIntent=True, inputCreatedAt=12., inputSource='hotkey'))

    def test_failed_optional_trace_does_not_swallow_accepted_map_action(self):
        owner = self.owner()
        owner._trace.side_effect = RuntimeError('diagnostic sink failed')
        map_input_method()(owner, dict(sequence=1, openIntent=True, createdAt=12., hwnd=42))
        owner.session.dispatchWithPayload.assert_called_once()

    def test_manual_floor_readiness_selects_actual_dispatch_and_resource_contract(self):
        # The resource gate is independently stubbed as accepting here: these
        # cases test routing, not whether blocked resources are globally ready.
        cases = [({}, {}, {}, True)]
        for field, value in (('season', 's0'), ('roundActive', False),
                ('manualAlignmentSession', False), ('mapId', ''),
                ('automaticFloorObservationAvailable', False),
                ('contextPending', True), ('resourceLocked', True)):
            cases.append(({field: value}, {}, {}, False))
        cases.extend([
            ({}, {'_pending_context': True}, {}, False),
            ({}, {}, {'busy': True}, False),
            ({}, {}, {'pendingKind': 'import'}, False),
        ])
        for state_changes, owner_changes, resource_changes, ready in cases:
            with self.subTest(state=state_changes, owner=owner_changes, resources=resource_changes):
                owner = self.owner()
                owner.session.snapshot.update(season='s1', roundActive=True,
                    manualAlignmentSession=True, mapId='fixture-map',
                    automaticFloorObservationAvailable=True)
                owner.session.snapshot.update(state_changes)
                vars(owner).update(owner_changes)
                vars(owner.resources).update(resource_changes)
                map_input_method()(owner, dict(sequence=1, openIntent=True,
                    createdAt=12., hwnd=42, source='hotkey'))
                owner._map_resource_problem.assert_called_once_with(
                    owner.session.snapshot, allow_manual_floor=ready)
                payload = dict(inputSequence=1, openIntent=True,
                    inputCreatedAt=12., inputSource='hotkey')
                if ready:
                    payload['s1OpenEdge'] = True
                owner.session.dispatchWithPayload.assert_called_once_with(
                    'fit_manual_frame' if ready else 'map', payload)
                owner._cancel_pointer_drag.assert_called_once_with('map-key-open')
                owner._revoke_runtime.assert_called_once_with('map-key-open')
                owner.runtime.cancel_map_open_recovery.assert_called_once_with('new-map-input')

    def test_resource_rejection_keeps_evidence_but_never_dispatches(self):
        owner = self.owner()
        key = ('nightmare', 'resource-validation-pending')
        owner._map_resource_problem.return_value = (key, 'fixture resource unavailable')
        event = dict(sequence=1, openIntent=True, createdAt=12., hwnd=42)
        method = map_input_method()
        method(owner, event)
        method(owner, event)  # The rejected physical edge is still deduplicated.
        owner._trace.assert_called_once()
        owner._map_resource_problem.assert_called_once_with(
            owner.session.snapshot, allow_manual_floor=False)
        owner._reset_map_input.assert_called_once_with('map-input-resources-unready')
        self.assertEqual(owner._map_input_sequence, 1)
        self.assertEqual(owner._map_resource_notified, {key})
        owner.notificationRequested.emit.assert_called_once()
        owner.changed.emit.assert_called_once_with()
        owner.session.dispatchWithPayload.assert_not_called()
        owner._cancel_pointer_drag.assert_not_called()
        owner._revoke_runtime.assert_not_called()
        owner._close_map_from_input.assert_not_called()

    def test_foreign_stale_and_invalid_edges_do_not_add_input_evidence(self):
        for changed in ({'hwnd':99}, {'sequence':0}, {'openIntent':1}, {'createdAt':float('nan')}):
            with self.subTest(changed=changed):
                owner = self.owner()
                event = dict(sequence=1, openIntent=True, createdAt=12., hwnd=42, source='hotkey')
                event.update(changed)
                map_input_method()(owner, event)
                owner._trace.assert_not_called()
                owner.session.dispatchWithPayload.assert_not_called()
        owner = self.owner()
        owner._native_windows.foreground_window = lambda:99
        map_input_method()(owner, dict(sequence=1,openIntent=True,createdAt=12.,hwnd=42))
        owner._trace.assert_not_called()

    def test_close_edge_is_observed_once_and_routing_is_unchanged(self):
        owner = self.owner()
        event = dict(sequence=2, openIntent=False, createdAt=13., hwnd=42, source='escape')
        method = map_input_method()
        method(owner, event)
        method(owner, event)
        owner._trace.assert_called_once()
        self.assertIs(owner._trace.call_args.kwargs['diag']['openIntent'], False)
        owner._close_map_from_input.assert_called_once()
        owner.session.dispatchWithPayload.assert_not_called()


class PreparationCorrelationTests(unittest.TestCase):
    def producer(self, payload):
        rows = []
        return PreparationDiagnostics(SimpleNamespace(_notify=lambda name, **values:rows.append(values)), payload), rows

    def test_payload_ids_link_original_and_recovery_but_each_attempt_is_distinct(self):
        payload = dict(inputSequence=4, generation=7)
        original, first_rows = self.producer(payload)
        original.emit('begin', 'map-open-preparation')
        self.assertEqual(payload['_diagnosticOpeningId'], original.opening)
        self.assertEqual(payload['_diagnosticAttemptId'], original.attempt)
        recovery_payload = dict(inputSequence=4, generation=8, recoveryId='c'*32,
                                _diagnosticOpeningId=original.opening)
        recovery, recovery_rows = self.producer(recovery_payload)
        recovery.emit('capture-start', 'capture-requested')
        recovery.finish('map-open-recovery-exhausted')
        self.assertEqual(recovery.opening, original.opening)
        self.assertNotEqual(recovery.attempt, original.attempt)
        for row in first_rows + recovery_rows:
            item = row['preparation']
            self.assertEqual(item['openingId'], original.opening)
            self.assertEqual(item['inputSequence'], 4)
            self.assertEqual(len(item['attemptId']), 32)
        for row in recovery_rows:
            self.assertEqual(row['preparation']['recoveryId'], 'c'*32)

    def test_invalid_ids_are_replaced_and_raw_payload_is_never_logged(self):
        payload = dict(_diagnosticOpeningId='PRIVATE NAME', _diagnosticAttemptId='a'*33,
                       recoveryId='PRIVATE RECOVERY', inputSequence=3, username='PRIVATE PLAYER')
        producer, rows = self.producer(payload)
        producer.emit('begin', 'map-open-preparation')
        item = rows[0]['preparation']
        self.assertRegex(item['openingId'], r'^[0-9a-f]{32}$')
        self.assertRegex(item['attemptId'], r'^[0-9a-f]{32}$')
        self.assertNotIn('recoveryId', item)
        self.assertNotIn('PRIVATE', json.dumps(item))

    def test_existing_valid_ids_are_preserved_and_budget_retains_final(self):
        payload = dict(_diagnosticOpeningId='a'*32, _diagnosticAttemptId='b'*32, inputSequence=4)
        producer, rows = self.producer(payload)
        for _ in range(10000):
            producer.emit('capture-start', 'capture-requested')
        producer.finish('map-open-preparation-deadline')
        self.assertEqual(len(rows), 72)
        self.assertEqual(rows[-1]['preparation']['suppressed'], 9929)
        self.assertEqual(rows[-1]['preparation']['attemptId'], 'b'*32)
        self.assertEqual(rows[-1]['preparation']['openingId'], 'a'*32)

    def test_begin_and_final_report_only_memory_state_without_native_or_snapshot_reads(self):
        rows=[]
        runtime=SimpleNamespace(_notify=lambda name,**values:rows.append(values),
            _active=True,_map_open=False,_capture_pending=True,
            snapshot=Mock(side_effect=AssertionError('no snapshot')),
            _capture_is_current=Mock(side_effect=AssertionError('no native guard')))
        producer=PreparationDiagnostics(runtime,dict(inputSequence=2))
        producer.emit('begin','map-open-preparation')
        producer.emit('capture-start','capture-requested')
        runtime._capture_pending=False
        producer.finish('map-open-preparation-deadline')
        self.assertEqual(rows[0]['preparation']['state'],dict(source='runtime-memory',
            roundActive=True,mapOpen=False,captureInProgress=True))
        self.assertNotIn('state',rows[1]['preparation'])
        self.assertIs(rows[-1]['preparation']['state']['captureInProgress'],False)
        self.assertTrue(all(len(row['preparation'])<=16 for row in rows))
        runtime.snapshot.assert_not_called()
        runtime._capture_is_current.assert_not_called()

    def test_attempt_schema_survives_disk_export_without_private_fields(self):
        row = dict(stage='map-input', season='s0', contextKey='s0:nightmare',
            mapAttempt=dict(openingId='a'*32, attemptId='b'*32,inputSequence=3,
                requestGeneration=4,frameCapturedMonotonicAt=7.1,
                frameUnavailableReason='capture-exception',username='PRIVATE PLAYER'),
            diag=dict(inputSequence=3,openIntent=True,inputCreatedAt=6.,
                state=dict(source='session-snapshot',roundActive=True)))
        expected = dict(row, mapAttempt={k:v for k,v in row['mapAttempt'].items() if k != 'username'})
        self.assertEqual(safe_record(row), expected)
        self.assertEqual(json.loads(sanitize_log_payload((json.dumps(row)+'\n').encode())), expected)


if __name__ == '__main__':
    unittest.main()
