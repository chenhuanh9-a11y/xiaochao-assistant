"""Isolated stdlib protocol fixtures, not Windows Setup/native-state acceptance."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch
from types import SimpleNamespace

import installer_upgrade_journal as journal


class InstallerUpgradeJournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'install'
        self.root.mkdir()
        self.sid = 'S-1-5-21-1-2-3-1000'
        self.target = dict(sourceCommit='a' * 40, displayVersion='1.1.2', numericVersion='1.0.0.24',
                           executableSha256='b' * 64, resourceInventorySha256='c' * 64)
        self.kwargs = dict(owner_sid=self.sid, session_id=str(uuid.uuid4()), target_identity=self.target,
                           report_sha256='d' * 64, plan_sha256='e' * 64)
        self.tx = str(uuid.uuid4())
        self.instance = journal.InstallerUpgradeJournal(self.root, **self.kwargs)
        self.marker = self.root / journal.PENDING
        self.directory = self.root / journal.HISTORY / self.kwargs['session_id']
        self.evidence = {'schema': 'synthetic-native-v1', 'sid': self.sid,
                         'programFiles': [{'identity': [1, 2], 'sha256': 'b' * 64}],
                         'registry': {'rawHex': '00000000', 'type': 4}, 'shortcuts': []}
        # These stand in for prior unknown/custom program/resource bytes. No
        # journal API receives authority to move or delete them.
        self.preserved = self.root / 'old-program-and-user-data'
        self.preserved.write_bytes(b'unknown original bytes')
        self.preserved_identity = self.preserved.stat().st_ino

    def ready(self):
        self.instance.begin()
        self.instance.bind_resource(self.tx, verify_resource=lambda _: True)

    def native(self):
        self.ready()
        self.instance.start_native(verify_resource=lambda _: True)

    def complete_native(self):
        self.native()
        self.instance.record_native_complete(self.evidence, verify_native=lambda *_: True)

    def finish(self):
        return self.instance.finish_complete(verify_native=lambda *_: True, verify_resource=lambda _: True)

    def generations(self):
        return sorted((self.directory / 'generations').iterdir())

    def replace_same_bytes(self, path):
        other = path.with_name(path.name + '.foreign')
        other.write_bytes(path.read_bytes())
        os.replace(other, path)

    def assert_retained(self):
        self.assertEqual(self.preserved.read_bytes(), b'unknown original bytes')
        self.assertEqual(self.preserved.stat().st_ino, self.preserved_identity)
        self.assertTrue((self.directory / 'anchor.json').exists())
        self.assertTrue((self.directory / 'pending-pointer.json').exists())
        self.assertTrue(list((self.directory / 'objects').iterdir()))

    def test_full_lifecycle_is_forward_only_and_retains_every_generation(self):
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid), {'status': 'not-needed'})
        self.ready()
        before = [(p.name, p.read_bytes(), p.stat().st_ino) for p in self.generations()]
        self.assertEqual(self.instance.status()['resourceTransactionId'], self.tx)
        self.instance.start_native(verify_resource=lambda _: True)
        self.instance.record_native_complete(self.evidence, verify_native=lambda *_: True)
        result = self.finish()
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(result['pending'])
        self.assertEqual(result['rootIdentity'], [self.root.stat().st_dev, self.root.stat().st_ino])
        self.assertEqual(result['nativeEvidenceSha256'], hashlib.sha256(journal._bounded(self.evidence)).hexdigest())
        self.assertEqual(len(self.generations()), 5)
        for name, data, inode in before:
            path = self.directory / 'generations' / name
            self.assertEqual((path.read_bytes(), path.stat().st_ino), (data, inode))
        self.assertEqual(self.finish()['status'], 'completed')
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid), {'status': 'not-needed'})
        self.assert_retained()

    def test_watcher_intent_default_is_false_not_inferred_from_files(self):
        (self.root / 'game-watcher.exe').write_bytes(b'not proof of user preference')
        self.assertIs(self.instance.begin()['restoreWatcher'], False)
        self.assertIs(journal.read_status(self.root, owner_sid=self.sid)['restoreWatcher'], False)
        self.complete_native()
        self.assertIs(self.finish()['restoreWatcher'], False)

    def test_true_watcher_intent_survives_fresh_instance_and_final_cleanup(self):
        self.instance = journal.InstallerUpgradeJournal(self.root, **dict(self.kwargs, restore_watcher=True))
        self.complete_native()
        state = journal.read_status(self.root, owner_sid=self.sid)
        self.assertIs(state['restoreWatcher'], True)
        resumed = journal.InstallerUpgradeJournal(self.root, owner_sid=state['ownerSid'],
            session_id=state['sessionId'], target_identity=state['targetIdentity'],
            report_sha256=state['reportSha256'], plan_sha256=state['planSha256'],
            restore_watcher=state['restoreWatcher'])
        result = resumed.finish_complete(verify_native=lambda *_: True, verify_resource=lambda _: True)
        self.assertIs(result['restoreWatcher'], True)
        self.assertFalse(result['pending'])
        self.assertIs(resumed.status()['restoreWatcher'], True)

    def test_watcher_intent_requires_real_bool_and_cannot_expand_in_existing_session(self):
        for value in (None, 0, 1, 'false', 'true', [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid-watcher-intent'):
                journal.InstallerUpgradeJournal(self.root, **dict(self.kwargs, restore_watcher=value))
        self.instance.begin()
        for change in (dict(restore_watcher=True),
                       dict(restore_watcher=True, owner_sid='S-1-5-21-1-2-3-1001'),
                       dict(restore_watcher=True, session_id=str(uuid.uuid4()))):
            with self.subTest(change=change):
                other = journal.InstallerUpgradeJournal(self.root, **dict(self.kwargs, **change))
                with self.assertRaises(ValueError):
                    other.abort_pre_native(verify_resource=lambda _: True)
                self.assertTrue(self.marker.exists())
        self.assertIs(self.instance.status()['restoreWatcher'], False)

    def test_watcher_intent_tampering_cannot_grant_restoration_or_clear_marker(self):
        self.complete_native()
        anchor_path = self.directory / 'anchor.json'
        anchor = json.loads(anchor_path.read_bytes())
        anchor['restoreWatcher'] = True
        anchor_path.write_bytes(journal._bounded(anchor))
        with self.assertRaisesRegex(ValueError, 'anchor-changed'):
            journal.read_status(self.root, owner_sid=self.sid)
        with self.assertRaisesRegex(ValueError, 'anchor-changed'):
            self.finish()
        self.assertTrue(self.marker.exists())

    def test_anchor_schema_rejects_numeric_watcher_intent_without_coercion(self):
        self.instance.begin()
        anchor = json.loads((self.directory / 'anchor.json').read_bytes())
        anchor['restoreWatcher'] = 1
        with self.assertRaisesRegex(ValueError, 'invalid-watcher-intent'):
            journal._check_anchor(self.root, self.directory, anchor)

    def test_read_status_contains_exact_reconstruction_bindings(self):
        self.complete_native()
        state = journal.read_status(self.root, owner_sid=self.sid)
        kwargs = dict(owner_sid=state['ownerSid'], session_id=state['sessionId'],
                      target_identity=state['targetIdentity'], report_sha256=state['reportSha256'],
                      plan_sha256=state['planSha256'])
        resumed = journal.InstallerUpgradeJournal(self.root, **kwargs)
        self.assertEqual(resumed.status(), self.instance.status())
        self.assertEqual(resumed.assert_can_forward(verify_native=lambda *_: True)['status'], 'native-complete')

    def test_begin_idempotent_only_for_same_prepared_session(self):
        initial = self.instance.begin()
        self.assertEqual(self.instance.begin(), initial)
        self.ready()
        self.instance.start_native(verify_resource=lambda _: True)
        with self.assertRaisesRegex(ValueError, 'already-started'):
            self.instance.begin()

    def test_all_fixed_bindings_rechecked_on_every_entry(self):
        self.instance.begin()
        changes = [dict(owner_sid='S-1-5-21-1-2-3-1001'), dict(session_id=str(uuid.uuid4())),
                   dict(report_sha256='f' * 64), dict(plan_sha256='f' * 64)]
        for key in self.target:
            target = dict(self.target)
            target[key] = {'sourceCommit': 'f' * 40, 'displayVersion': '1.1.3', 'numericVersion': '1.0.0.25',
                           'executableSha256': 'f' * 64, 'resourceInventorySha256': 'f' * 64}[key]
            changes.append(dict(target_identity=target))
        for change in changes:
            with self.subTest(change=change):
                other = journal.InstallerUpgradeJournal(self.root, **dict(self.kwargs, **change))
                with self.assertRaises(ValueError):
                    other.abort_pre_native(verify_resource=lambda _: True)
                self.assertTrue(self.marker.exists())

    def test_strict_constructor_inputs(self):
        for change in [dict(owner_sid='user'), dict(session_id='anything'), dict(session_id=str(uuid.uuid1())),
                       dict(report_sha256='D' * 64), dict(plan_sha256=None),
                       dict(target_identity=None), dict(target_identity=dict(self.target, extra='ignored')),
                       dict(target_identity=dict(self.target, numericVersion='1.2.3.65536')),
                       dict(target_identity=dict(self.target, sourceCommit='not-a-commit'))]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                journal.InstallerUpgradeJournal(self.root, **dict(self.kwargs, **change))
        self.assertFalse((self.root / 'work').exists())

    def test_foreign_bad_pending_never_overwritten(self):
        self.marker.parent.mkdir()
        self.marker.write_bytes(b'{broken')
        original = self.marker.stat().st_ino
        with self.assertRaises(ValueError):
            self.instance.begin()
        self.assertEqual((self.marker.read_bytes(), self.marker.stat().st_ino), (b'{broken', original))
        self.assertFalse((self.root / journal.HISTORY).exists())

    def test_native_requires_bound_verified_resource_and_uncertain_start_cannot_repeat(self):
        self.instance.begin()
        with self.assertRaisesRegex(ValueError, 'native-start-not-allowed'):
            self.instance.start_native(verify_resource=lambda _: True)
        self.instance.bind_resource(self.tx, verify_resource=lambda record: record['resourceTransactionId'] == self.tx)
        for outcome in (False, None, 1, 'yes'):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, 'resource-verification-failed'):
                self.instance.start_native(verify_resource=lambda _: outcome)
            self.assertEqual(self.instance.status()['status'], 'prepared')
        self.instance.start_native(verify_resource=lambda _: True)
        with self.assertRaisesRegex(ValueError, 'native-start-not-allowed'):
            self.instance.start_native(verify_resource=lambda _: True)
        self.assertEqual(self.instance.status()['status'], 'native-in-progress')

    def test_binding_is_verified_before_write_and_cannot_be_changed(self):
        self.instance.begin()
        with self.assertRaisesRegex(ValueError, 'resource-verification-failed'):
            self.instance.bind_resource(self.tx, verify_resource=lambda _: False)
        self.assertIsNone(self.instance.status()['resourceTransactionId'])
        state = self.instance.bind_resource(self.tx, verify_resource=lambda _: True)
        self.assertEqual(self.instance.bind_resource(self.tx, verify_resource=lambda _: True), state)
        with self.assertRaisesRegex(ValueError, 'resource-transaction-mismatch'):
            self.instance.bind_resource(str(uuid.uuid4()), verify_resource=lambda _: True)

    def test_unbound_preparation_does_not_prove_nothing_changed(self):
        self.instance.begin()
        for outcome in (False, None, 1):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, 'resource-verification-failed'):
                self.instance.abort_pre_native(verify_resource=lambda _: outcome)
            self.assertTrue(self.marker.exists())
        # Caller has reconciled the lost preparation response with the exact
        # core transaction, and proved full rollback/no pending.
        self.assertEqual(self.instance.abort_pre_native(verify_resource=lambda _: True)['status'], 'aborted')
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.instance.abort_pre_native(verify_resource=lambda _: True)['status'], 'aborted')
        self.assert_retained()

    def test_bound_pre_native_abort_and_native_abort_forbidden(self):
        self.ready()
        state = self.instance.abort_pre_native(verify_resource=lambda record: record['resourceTransactionId'] == self.tx)
        self.assertEqual(state['status'], 'aborted')
        self.assertFalse(state['pending'])
        with self.assertRaisesRegex(ValueError, 'native-start-not-allowed'):
            self.instance.start_native(verify_resource=lambda _: True)

    def test_native_in_progress_is_not_proof_of_native_completion(self):
        self.native()
        with self.assertRaisesRegex(ValueError, 'native-completion-evidence-required'):
            self.instance.assert_can_forward(verify_native=lambda *_: True)
        with self.assertRaisesRegex(ValueError, 'native-completion-evidence-required'):
            self.finish()
        with self.assertRaisesRegex(ValueError, 'abort-after-native-forbidden'):
            self.instance.abort_pre_native(verify_resource=lambda _: True)
        self.assertTrue(self.marker.exists())

    def test_record_completion_requires_exact_true_and_bounded_nonempty_json(self):
        self.native()
        for evidence in ({}, [], {'bad': float('nan')}, {'bad': 1.2}, {'bad': object()},
                         {'bad': 'x' * (journal.MAX_JSON_STRING + 1)}, {'bad': 2 ** 256}, {'bad': ('tuple',)}):
            with self.subTest(evidence=type(evidence)), self.assertRaises(ValueError):
                self.instance.record_native_complete(evidence, verify_native=lambda *_: True)
        for outcome in (None, False, 1):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, 'native-verification-failed'):
                self.instance.record_native_complete(self.evidence, verify_native=lambda *_: outcome)
        self.assertEqual(self.instance.status()['status'], 'native-in-progress')

    def test_native_inventory_long_path_keys_still_have_explicit_bound(self):
        self.native()
        relative = 'directory/' + 'a' * 32680 + '.bin'
        evidence = {'programInventory': {relative: {'bytes': 1, 'sha256': 'a' * 64}}}
        self.instance.record_native_complete(evidence, verify_native=lambda *_: True)
        self.assertIn(relative, self.instance.status()['nativeEvidence']['programInventory'])
        with self.assertRaisesRegex(ValueError, 'invalid-json-key'):
            journal._bounded({'x' * (journal.MAX_JSON_KEY + 1): 1}, evidence=True)

    def test_completion_evidence_depth_nodes_bytes_are_bounded(self):
        self.native()
        deep = {}
        for _ in range(journal.MAX_JSON_DEPTH + 2):
            deep = {'deeper': deep}
        payloads = [deep, {'large': ['x' * (1024 ** 2)] * 17}, {'many': [0] * (journal.MAX_JSON_ITEMS + 1)}]
        for evidence in payloads:
            with self.subTest(), self.assertRaises(ValueError):
                self.instance.record_native_complete(evidence, verify_native=lambda *_: True)
        self.assertEqual(self.instance.status()['status'], 'native-in-progress')

    def test_native_complete_retry_revalidates_same_evidence_only(self):
        self.complete_native()
        state = self.instance.status()
        self.assertEqual(self.instance.record_native_complete(self.evidence, verify_native=lambda *_: True), state)
        with self.assertRaisesRegex(ValueError, 'native-evidence-changed'):
            self.instance.record_native_complete(dict(self.evidence, changed=True), verify_native=lambda *_: True)
        with self.assertRaisesRegex(ValueError, 'native-verification-failed'):
            self.instance.record_native_complete(self.evidence, verify_native=lambda *_: False)
        with self.assertRaisesRegex(ValueError, 'abort-after-native-forbidden'):
            self.instance.abort_pre_native(verify_resource=lambda _: True)

    def test_current_native_must_still_match_before_forward_or_last_clear(self):
        self.complete_native()
        for action in (
            lambda: self.instance.assert_can_forward(verify_native=lambda *_: False),
            lambda: self.instance.finish_complete(verify_native=lambda *_: False, verify_resource=lambda _: True),
            lambda: self.instance.finish_complete(verify_native=lambda *_: True, verify_resource=lambda _: False),
        ):
            with self.assertRaises(ValueError):
                action()
            self.assertTrue(self.marker.exists())
            self.assertEqual(self.instance.status()['status'], 'native-complete')

    def test_real_fixture_file_replacement_after_evidence_blocks_forward(self):
        program = self.root / 'synthetic.exe'
        program.write_bytes(b'new target')
        stamp = lambda: {'identity': [program.stat().st_dev, program.stat().st_ino],
                         'sha256': hashlib.sha256(program.read_bytes()).hexdigest()}
        self.native()
        evidence = {'program': stamp(), 'target': self.target, 'ownerSid': self.sid}
        verifier = lambda record, saved: saved == {'program': stamp(), 'target': record['targetIdentity'], 'ownerSid': record['ownerSid']}
        self.instance.record_native_complete(evidence, verify_native=verifier)
        self.instance.assert_can_forward(verify_native=verifier)
        self.replace_same_bytes(program)
        with self.assertRaisesRegex(ValueError, 'native-verification-failed'):
            self.instance.assert_can_forward(verify_native=verifier)
        self.assertTrue(self.marker.exists())

    def test_completed_marker_cleanup_is_retryable_after_uncertain_response(self):
        self.complete_native()
        with patch.object(journal, '_remove_owned_marker', side_effect=OSError('lost clear')):
            with self.assertRaisesRegex(OSError, 'lost clear'):
                self.finish()
        self.assertEqual(self.instance.status()['status'], 'completed')
        self.assertTrue(self.marker.exists())
        with self.assertRaisesRegex(ValueError, 'native-verification-failed'):
            self.instance.finish_complete(verify_native=lambda *_: False, verify_resource=lambda _: True)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.finish()['pending'])
        self.assertEqual(len(self.generations()), 5)

    def test_completed_after_unlink_but_lost_flush_never_rewinds(self):
        self.complete_native()
        remove = journal._remove_owned_marker
        def uncertain(*args):
            remove(*args)
            raise OSError('lost response after unlink')
        with patch.object(journal, '_remove_owned_marker', side_effect=uncertain), self.assertRaises(OSError):
            self.finish()
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.instance.status()['status'], 'completed')
        self.assertFalse(self.finish()['pending'])
        self.assert_retained()

    def test_same_bytes_foreign_marker_never_claimed_or_cleared(self):
        self.complete_native()
        self.replace_same_bytes(self.marker)
        foreign = self.marker.stat().st_ino
        for action in (self.instance.status, self.finish, lambda: journal.read_status(self.root, owner_sid=self.sid)):
            with self.assertRaisesRegex(ValueError, 'pending-identity-changed'):
                action()
        self.assertEqual(self.marker.stat().st_ino, foreign)

    def test_same_bytes_foreign_anchor_or_generation_or_pin_blocks(self):
        self.complete_native()
        for path in (self.directory / 'anchor.json', self.generations()[-1], self.directory / 'pending-pointer.json'):
            with self.subTest(path=path.name):
                original = path.with_name(path.name + '.original')
                os.link(path, original)
                self.replace_same_bytes(path)
                with self.assertRaises(ValueError):
                    self.finish()
                os.replace(original, path)
        latest = self.generations()[-1]
        pin = self.directory / 'pins' / (json.loads(latest.read_bytes())['pinId'] + '.json')
        self.replace_same_bytes(pin)
        with self.assertRaisesRegex(ValueError, 'generation-identity-changed'):
            self.finish()
        self.assertTrue(self.marker.exists())

    def test_in_place_generation_damage_detected_even_when_hardlink_pin_changes(self):
        self.complete_native()
        path = self.generations()[-1]
        content = json.loads(path.read_bytes())
        content['nativeEvidence']['changed'] = True
        path.write_bytes(journal._bounded(content))
        with self.assertRaisesRegex(ValueError, 'generation-digest-changed'):
            self.finish()
        self.assertTrue(self.marker.exists())

    def test_copy_of_entire_history_to_another_root_is_foreign(self):
        self.complete_native()
        other = self.base / 'clone'
        # Hardlink copy preserves individual evidence identities so root identity
        # is independently required, rather than relying on copy() inode changes.
        shutil.copytree(self.root, other, copy_function=os.link)
        with self.assertRaisesRegex(ValueError, 'root-identity-changed'):
            journal.read_status(other, owner_sid=self.sid)
        self.assertTrue((other / journal.PENDING).exists())

    def test_replaced_work_or_history_directory_identity_blocks(self):
        self.instance.begin()
        history = self.root / journal.HISTORY
        moved = history.with_name('installer-upgrades-original')
        history.rename(moved)
        shutil.copytree(moved, history, copy_function=os.link)
        with self.assertRaisesRegex(ValueError, 'directory-identity-changed'):
            self.instance.status()
        self.assertTrue(self.marker.exists())

    def test_removed_latest_generation_cannot_rewind_native_to_prepared(self):
        self.native()
        self.generations()[-1].unlink()  # Simulated lost/corrupt published metadata.
        with self.assertRaisesRegex(ValueError, 'invalid-publication-proof'):
            self.instance.status()
        with self.assertRaises(ValueError):
            self.instance.abort_pre_native(verify_resource=lambda _: True)
        self.assertTrue(self.marker.exists())

    def test_windows_stat_birthtime_and_fstat_changetime_not_mixed(self):
        path = self.root / 'metadata.json'
        path.write_bytes(b'{}')
        real_stat = Path.stat
        def different_path_ctime(item, **kwargs):
            result = real_stat(item, **kwargs)
            if item == path:
                fields = {name: getattr(result, name) for name in dir(result) if name.startswith('st_')}
                fields['st_ctime_ns'] -= 123456
                return SimpleNamespace(**fields)
            return result
        with patch.object(Path, 'stat', different_path_ctime):
            data, stamp = journal._read(path)
        self.assertEqual(data, b'{}')
        self.assertEqual(stamp['identity'], [path.stat().st_dev, path.stat().st_ino])

    def test_fifo_metadata_rejected_without_blocking_open(self):
        if not hasattr(os, 'mkfifo'):
            self.skipTest('requires POSIX fixture FIFO')
        self.marker.parent.mkdir()
        os.mkfifo(self.marker)
        with self.assertRaisesRegex(ValueError, 'invalid-metadata-file'):
            self.instance.begin()

    def test_missing_marker_cannot_authorize_nonterminal_recovery(self):
        self.complete_native()
        self.marker.unlink()  # Simulated external removal, not a journal action.
        with self.assertRaisesRegex(ValueError, 'pending-missing'):
            self.instance.status()
        with self.assertRaisesRegex(ValueError, 'pending-missing'):
            self.finish()

    def test_symlink_marker_or_ancestor_rejected_without_following(self):
        self.instance.begin()
        external = self.base / 'external.json'
        external.write_bytes(self.marker.read_bytes())
        self.marker.unlink()
        try:
            self.marker.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest('host does not allow fixture symlinks')
        with self.assertRaisesRegex(ValueError, 'linked-path'):
            self.instance.status()
        self.assertTrue(external.exists())

    def test_unknown_generation_cannot_be_ignored(self):
        self.instance.begin()
        (self.directory / 'generations' / 'unknown.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'unknown-generation'):
            self.instance.status()
        self.assertTrue(self.marker.exists())

    def test_invalid_generation_chain_and_state_are_rejected(self):
        self.complete_native()
        latest = self.generations()[-1]
        original = json.loads(latest.read_bytes())
        prior = json.loads(self.generations()[-2].read_bytes())
        previous = (prior, journal._read(self.generations()[-2])[1])
        for change in ({'schema': 'other'}, {'extra': True}, {'generation': True}, {'status': []},
                       {'status': 'completed'}, {'resourceTransactionId': str(uuid.uuid4())},
                       {'nativeEvidenceSha256': 'a' * 64}, {'previous': {'broken': True}},
                       {'nativeEvidence': None}, {'upgradeId': str(uuid.uuid4())}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                journal._check_record(dict(original, **change), original['generation'], previous, original['anchorSha256'])

    def test_strict_json_duplicate_keys_and_noncanonical_encoding_rejected(self):
        for data in (b'{"x":1,"x":2}', b'{ "x": 1}', b'{"x":NaN}', b'{} trailing', b'\xff'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                journal._parse(data)

    def test_verification_callback_cannot_race_another_state_transition(self):
        self.ready()
        def changed(_):
            self.instance.abort_pre_native(verify_resource=lambda _: True)
            return True
        with self.assertRaisesRegex(ValueError, 'changed-during-verification'):
            self.instance.start_native(verify_resource=changed)
        self.assertEqual(self.instance.status()['status'], 'aborted')
        self.assertFalse(self.marker.exists())

    def test_failed_native_verifier_cannot_mutate_saved_evidence(self):
        self.native()
        def mutate(record, evidence):
            record['nativeEvidence']['extra'] = 'caller mutation'
            evidence['more'] = True
            return False
        with self.assertRaisesRegex(ValueError, 'native-verification-failed'):
            self.instance.record_native_complete(self.evidence, verify_native=mutate)
        self.assertNotIn('extra', self.evidence)
        self.assertIsNone(self.instance.status()['nativeEvidence'])

    def test_file_fsync_failure_before_marker_publication_preserves_no_active_authority(self):
        with patch.object(journal.os, 'fsync', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                self.instance.begin()
        self.assertFalse(self.marker.exists())
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid), {'status': 'not-needed'})
        self.assertEqual(self.preserved.read_bytes(), b'unknown original bytes')

    def test_begin_crash_at_each_publication_preserves_unknown_objects(self):
        # Run each breakpoint in its own installation root. If the marker was
        # published, every object it names must already be fully readable.
        original_link = journal.os.link
        for count in (1, 2, 3, 4):
            with self.subTest(link=count):
                root = self.base / ('fault-' + str(count)); root.mkdir()
                instance = journal.InstallerUpgradeJournal(root, **dict(self.kwargs, session_id=str(uuid.uuid4())))
                calls = 0
                def interrupt(source, destination, *a, **kw):
                    nonlocal calls
                    result = original_link(source, destination, *a, **kw)
                    if Path(source).parent.name.startswith('link-probe-'):
                        return result
                    calls += 1
                    if calls == count:
                        raise KeyboardInterrupt('process loss')
                    return result
                with patch.object(journal.os, 'link', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                    instance.begin()
                status = journal.read_status(root, owner_sid=self.sid)
                self.assertEqual(status['status'], 'prepared' if count == 4 else 'not-needed')

    def test_native_complete_publication_interruption_has_no_ambiguous_forward_authority(self):
        self.native()
        original_link = journal.os.link
        def lose_after_publication(source, destination, *a, **kw):
            result = original_link(source, destination, *a, **kw)
            if Path(destination).parent.name == 'generations':
                raise KeyboardInterrupt('lost native receipt')
            return result
        with patch.object(journal.os, 'link', side_effect=lose_after_publication), self.assertRaises(KeyboardInterrupt):
            self.instance.record_native_complete(self.evidence, verify_native=lambda *_: True)
        self.assertEqual(self.instance.status()['status'], 'native-complete')
        self.assertEqual(self.instance.assert_can_forward(verify_native=lambda *_: True)['nativeEvidence'], self.evidence)
        self.assertTrue(self.marker.exists())

    def test_native_complete_failure_before_publication_stays_blocked(self):
        self.native()
        original_link = journal.os.link
        def fail_before_publication(source, destination, *a, **kw):
            if Path(destination).parent.name == 'generations':
                raise OSError('no publication')
            return original_link(source, destination, *a, **kw)
        with patch.object(journal.os, 'link', side_effect=fail_before_publication), self.assertRaises(OSError):
            self.instance.record_native_complete(self.evidence, verify_native=lambda *_: True)
        self.assertEqual(self.instance.status()['status'], 'native-in-progress')
        with self.assertRaisesRegex(ValueError, 'native-completion-evidence-required'):
            self.instance.assert_can_forward(verify_native=lambda *_: True)
        # A still-live Setup may retry its explicit completion with freshly
        # observed evidence. Orphan pins remain; they are not adopted.
        self.instance.record_native_complete(self.evidence, verify_native=lambda *_: True)
        self.assertEqual(self.instance.status()['status'], 'native-complete')

    def test_completed_generation_failure_before_publication_is_retryable(self):
        self.complete_native()
        original_link = journal.os.link
        def fail(source, destination, *a, **kw):
            if Path(destination).parent.name == 'generations':
                raise OSError('not published')
            return original_link(source, destination, *a, **kw)
        with patch.object(journal.os, 'link', side_effect=fail), self.assertRaises(OSError):
            self.finish()
        self.assertEqual(self.instance.status()['status'], 'native-complete')
        self.assertFalse(self.finish()['pending'])

    def test_completed_generation_lost_flush_is_recovered_without_duplicate_transition(self):
        self.complete_native()
        sync = journal._directory_sync
        def fail(path):
            if Path(path) == self.directory / 'generations':
                raise OSError('generation flush failed')
            return sync(path)
        with patch.object(journal, '_directory_sync', side_effect=fail), self.assertRaises(OSError):
            self.finish()
        self.assertEqual(self.instance.status()['status'], 'completed')
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.finish()['pending'])
        self.assertEqual(len(self.generations()), 5)

    def test_marker_swapped_during_final_verification_is_not_cleared(self):
        self.complete_native()
        def swap(_):
            self.replace_same_bytes(self.marker)
            return True
        with self.assertRaisesRegex(ValueError, 'pending-identity-changed'):
            self.instance.finish_complete(verify_native=lambda *_: True, verify_resource=swap)
        self.assertTrue(self.marker.exists())

    def test_marker_clear_identity_recheck_does_not_unlink_foreign_equal_bytes(self):
        self.complete_native()
        expected = journal._read(self.marker)[1]
        self.replace_same_bytes(self.marker)
        with self.assertRaisesRegex(ValueError, 'pending-identity-changed'):
            journal._remove_owned_marker(self.marker, expected)
        self.assertTrue(self.marker.exists())

    def test_root_replaced_after_instance_created_rejected(self):
        self.instance.begin()
        moved = self.base / 'old-install'
        self.root.rename(moved)
        shutil.copytree(moved, self.root, copy_function=os.link)
        with self.assertRaisesRegex(ValueError, 'root-identity-changed'):
            self.instance.status()

    def test_absent_required_native_or_resource_verifier_never_succeeds(self):
        self.complete_native()
        for action in (
            lambda: self.instance.assert_can_forward(verify_native=None),
            lambda: self.instance.finish_complete(verify_native=lambda *_: True, verify_resource=None),
        ):
            with self.assertRaisesRegex(ValueError, 'verifier-required'):
                action()
            self.assertTrue(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
