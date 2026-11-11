"""Interruption/negative paths on actual temporary trees and handoff/journal.

Only native ownership, registration, user and parent/native observation are
narrow fixtures inherited from the explicitly labelled retirement fixtures.
No Windows product registration is written; no historical image is executed.
"""
from contextlib import nullcontext
from pathlib import Path
import json
import os
import unittest
from unittest.mock import patch

import installer_legacy_handoff as handoff
import installer_legacy_retirement as retirement
import installer_uninstall as uninstall
import test_installer_legacy_retirement as fixtures
from installer_upgrade_journal import PENDING
from test_installer_native_observation import SID


@unittest.skipUnless(os.name == 'nt', 'real Windows object retirement')
class RecoveryTests(unittest.TestCase):
    def new_case(self):
        case = fixtures.LegacyRetirementTests(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def execute(self, case, action):
        case.args.action = 'legacy-' + action
        with patch.object(uninstall, '_owner_sid', return_value=SID), \
                patch.object(uninstall, '_current_user_sid', return_value=SID):
            coordinator = handoff.LegacyHandoff(case.args, case.identity, case.lease, SID,
                assets=case.assets, observer=case.observer, registry_reader=case.rows,
                retirement=case.retirement, scope=lambda *_a, **_k: nullcontext())
            result = coordinator.execute()
        case.args.plan_sha256 = result['planSha256']
        return result

    def committed(self):
        case = self.new_case()
        self.execute(case, 'prepare')
        self.execute(case, 'start-native')
        case.install_native_fixture()
        self.execute(case, 'native-complete')
        self.execute(case, 'commit')
        return case

    def test_prepared_or_uncertain_native_never_grants_retirement(self):
        case = self.new_case()
        original = case.snapshot()
        self.execute(case, 'prepare')
        for action in ('prepared', 'native-in-progress'):
            with self.subTest(state=action), self.assertRaises(ValueError):
                self.execute(case, 'retire')
            self.assertEqual(case.snapshot(), original)
            self.assertTrue(case.registration.present)
            self.assertTrue((case.target / PENDING).exists())
            if action == 'prepared':
                self.execute(case, 'start-native')

    def test_all_objects_interrupted_before_and_after_removal_are_recoverable(self):
        sample = self.new_case()
        _, _, plan = sample._capture()
        count = sum(len(tree) for tree in plan['trees'].values()) + sum(
            stamp is not None for stamp in plan['shortcuts'].values())
        real = retirement.WindowsObjects.remove
        for index in range(count):
            for after in (False, True):
                with self.subTest(index=index, after=after):
                    case = self.new_case()
                    _, _, plan = case._capture()
                    seen = []
                    def interrupted(objects, path, stamp):
                        ordinal = len(seen)
                        seen.append(str(path))
                        if ordinal == index and not after:
                            raise OSError('synthetic before object removal')
                        real(objects, path, stamp)
                        if ordinal == index and after:
                            raise OSError('synthetic after object removal')
                    with case._with_fixture_sid(), patch.object(retirement.WindowsObjects, 'remove', interrupted):
                        with self.assertRaisesRegex(OSError, 'synthetic'):
                            case._execute(plan)
                    with case._with_fixture_sid():
                        self.assertTrue(case._execute(plan))
                    self.assertFalse(case.source.exists())
                    self.assertFalse(case.registration.present)

    def test_legacy_registration_disappearing_after_intent_does_not_require_probe(self):
        case = self.committed()
        def interrupted(*_):
            case.registration.present = False
            raise OSError('synthetic registration removal before receipt')
        with patch.object(retirement.WindowsObjects, 'remove', interrupted), self.assertRaises(OSError):
            self.execute(case, 'retire')
        with patch.object(handoff.bridge, 'probe_source', side_effect=AssertionError('old source reprobe')):
            result = self.execute(case, 'resume')
        self.assertTrue(result['upgradeComplete'])
        self.assertFalse((case.target / PENDING).exists())
        self.assertFalse((case.target / '.legacy-upgrade-prepared').exists())
        self.assertFalse((case.target / '.legacy-upgrade-intent.json').exists())

    def test_new_program_changed_after_interruption_stops_remaining_deletions(self):
        case = self.committed()
        real = retirement.WindowsObjects.remove
        def interrupted(objects, path, stamp):
            real(objects, path, stamp)
            raise OSError('synthetic lost deletion receipt')
        with patch.object(retirement.WindowsObjects, 'remove', interrupted), self.assertRaises(OSError):
            self.execute(case, 'retire')
        original = case.snapshot()
        (case.target / '星图.exe').write_bytes(b'changed new image')
        with self.assertRaises(ValueError):
            self.execute(case, 'resume')
        self.assertEqual(case.snapshot(), original)
        self.assertTrue((case.target / PENDING).exists())

    def test_same_named_new_source_tree_is_not_adopted(self):
        case = self.committed()
        with patch.object(retirement.WindowsObjects, 'remove', side_effect=OSError('before deletion')):
            with self.assertRaises(OSError):
                self.execute(case, 'retire')
        retained = case.source.with_name('original-retained')
        case.source.rename(retained)
        case.source.mkdir()
        (case.source / 'personal.txt').write_bytes(b'later installation')
        with self.assertRaises(ValueError):
            self.execute(case, 'resume')
        self.assertEqual((case.source / 'personal.txt').read_bytes(), b'later installation')
        self.assertTrue((retained / 'MaintenanceTool.exe').exists())

    def test_unlisted_prepared_file_is_never_adopted_before_native(self):
        case = self.new_case()
        prepare = case.assets.prepare
        def injected(source, prepared, **bindings):
            result = prepare(source, prepared, **bindings)
            (prepared / 'personal.txt').write_bytes(b'not a migration file')
            return result
        with patch.object(case.assets, 'prepare', side_effect=injected), self.assertRaisesRegex(
                ValueError, 'unlisted-preparation-object'):
            self.execute(case, 'prepare')
        self.assertTrue((case.target / '.legacy-upgrade-prepared/personal.txt').exists())
        self.assertFalse((case.target / PENDING).exists())

    def test_new_preparation_content_blocks_all_retirement(self):
        case = self.committed()
        original = case.snapshot()
        unknown = case.target / '.legacy-upgrade-prepared/personal.txt'
        unknown.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'preparation-replaced-or-new-content'):
            self.execute(case, 'retire')
        self.assertEqual(case.snapshot(), original)
        self.assertEqual(unknown.read_bytes(), b'keep')
        self.assertTrue(case.registration.present)

    def test_completed_status_has_no_writes_and_lost_receipt_is_idempotent(self):
        case = self.committed()
        self.assertTrue(self.execute(case, 'retire')['upgradeComplete'])
        def snapshot():
            return {p.relative_to(case.target).as_posix(): p.read_bytes()
                    for p in case.target.rglob('*') if p.is_file()}
        original = snapshot()
        with patch.object(handoff, '_write_new', side_effect=AssertionError('status writes')):
            self.assertTrue(self.execute(case, 'status')['upgradeComplete'])
        self.assertEqual(snapshot(), original)
        case.args.session_id = None
        case.args.plan_sha256 = None
        self.assertTrue(self.execute(case, 'status')['upgradeComplete'])
        self.assertEqual(snapshot(), original)
        self.assertTrue(self.execute(case, 'resume')['upgradeComplete'])

    def test_exact_registry_type_or_value_change_blocks_before_deletion(self):
        case = self.committed()
        original = case.snapshot()
        case.registration.row['values']['DisplayVersion'] = [4, 112]
        with self.assertRaisesRegex(ValueError, 'registration-changed'):
            self.execute(case, 'retire')
        self.assertEqual(case.snapshot(), original)
        self.assertTrue((case.target / PENDING).exists())

    def test_native_complete_without_asset_commit_cannot_retire(self):
        case = self.new_case()
        self.execute(case, 'prepare')
        self.execute(case, 'start-native')
        case.install_native_fixture()
        self.execute(case, 'native-complete')
        original = case.snapshot()
        with self.assertRaises(ValueError):
            self.execute(case, 'retire')
        self.assertEqual(case.snapshot(), original)
        self.assertTrue(case.registration.present)

    def test_every_temporary_object_interrupted_before_and_after_removal_resumes(self):
        sample = self.committed()
        plan = json.loads((sample.target / '.legacy-upgrade-prepared/handoff-plan.json').read_bytes())
        count = len(plan['retirement']['temporary'])
        real = retirement.WindowsObjects.remove
        for index in range(count):
            for after in (False, True):
                with self.subTest(index=index, after=after):
                    case = self.committed()
                    seen = []
                    def interrupted(objects, path, stamp):
                        is_temp = Path(path).is_relative_to(case.target)
                        ordinal = len(seen)
                        if is_temp:
                            seen.append(str(path))
                        if is_temp and ordinal == index and not after:
                            raise OSError('before temporary removal')
                        real(objects, path, stamp)
                        if is_temp and ordinal == index and after:
                            raise OSError('after temporary removal')
                    with patch.object(retirement.WindowsObjects, 'remove', interrupted), self.assertRaises(OSError):
                        self.execute(case, 'retire')
                    with patch.object(handoff.bridge, 'probe_source', side_effect=AssertionError('old reprobe')):
                        self.assertTrue(self.execute(case, 'resume')['upgradeComplete'])
                    self.assertFalse((case.target / '.legacy-upgrade-prepared').exists())
                    self.assertFalse((case.target / '.legacy-upgrade-intent.json').exists())
                    self.assertFalse((case.target / PENDING).exists())

    def test_shared_external_root_added_after_commit_stops_all_deletions(self):
        case = self.new_case()
        for relative, data in (('work/map-packages/import.lock', b''),
                               ('assets/custom-maps/library.bin', b'private library')):
            path = case.external / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.execute(case, 'prepare')
        self.execute(case, 'start-native')
        case.install_native_fixture()
        self.execute(case, 'native-complete')
        self.execute(case, 'commit')
        original = case.snapshot()
        case.assets.registrations = lambda: {str(case.source), str(case.target), str(case.root / 'other-install')}
        with self.assertRaisesRegex(ValueError, 'external-root-shared'):
            self.execute(case, 'retire')
        self.assertEqual(case.snapshot(), original)
        self.assertEqual((case.external / 'assets/custom-maps/library.bin').read_bytes(), b'private library')

    def test_legacy_native_evidence_explicitly_binds_disabled_old_link(self):
        case = self.committed()
        from installer_upgrade_journal import read_status
        state = read_status(case.target, owner_sid=SID)
        deferred = state['nativeEvidence']['contract']['legacyShortcutRetirement']
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0]['planSha256'], state['planSha256'])
        self.assertEqual(Path(deferred[0]['path']), Path(case.locations['desktop']) / '星图.lnk')
        coordinator = handoff.LegacyHandoff(case.args, case.identity, case.lease, SID,
            assets=case.assets, observer=case.observer, registry_reader=case.rows,
            retirement=case.retirement, scope=lambda *_a, **_k: nullcontext())
        original = json.loads((case.target / '.legacy-upgrade-prepared/handoff-plan.json').read_bytes())
        coordinator._load_plan(original['sourceIdentity'])
        coordinator.context = case.observer.resume_context_from_evidence(state['nativeEvidence'], case.target)
        coordinator.context['retiring_shortcuts'] = []
        with self.assertRaisesRegex(ValueError, 'shortcut-retirement-binding-mismatch'):
            coordinator._native(state, state['nativeEvidence'])

    def test_deferred_old_desktop_still_rejects_unowned_alternative_entry(self):
        case = self.committed()
        original = case.snapshot()
        (Path(case.locations['desktop']) / '星图.url').write_bytes(b'unknown alternative entry')
        with self.assertRaisesRegex(ValueError, 'shortcut-collision'):
            self.execute(case, 'retire')
        self.assertEqual(case.snapshot(), original)
        self.assertTrue(case.registration.present)
        self.assertTrue((case.target / PENDING).exists())


if __name__ == '__main__':
    unittest.main()
