"""Source-derived scan planning and synthetic native file-list ownership.

No Pascal, Windows uninstaller, real installation, or backup directory is run.
The historical ledger fixture represents the reviewed 161e production contract,
not a parser/claim about every possible older user's unins*.dat.
"""
import re
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_qt_client as build
SOURCE = (ROOT / 'tools/qt-uninstall-data.iss').read_text('utf-8-sig')
INSTALLER = (ROOT / 'tools/qt-client.iss').read_text('utf-8-sig')


def routine(name):
    match = re.search(r'(?m)^(?:function|procedure) ' + name + r'\b', SOURCE)
    if not match:
        return ''
    following = re.search(r'(?m)^(?:function|procedure) ', SOURCE[match.end():])
    return SOURCE[match.start():match.end() + following.start()] if following else SOURCE[match.start():]


def planned_roots(*, backup=False, delete=False):
    prepare = routine('PrepareUninstallData')
    roots = re.findall(r"InspectUninstallDirectory\('([^']+)', 0, Entries\)", prepare)
    optional = routine('InspectChosenUninstallWork')
    if optional and 'InspectChosenUninstallWork(Entries)' in prepare:
        predicate = routine('UninstallNeedsWorkScan')
        if not re.search(r'Result := UninstallMakeBackup or UninstallDeleteData;', predicate):
            raise AssertionError('Unrecognized actual work-scan predicate')
        if backup or delete:
            roots += re.findall(r"InspectUninstallDirectory\('([^']+)', 0, Entries\)", optional)
    return roots


class RuntimeRetentionTests(unittest.TestCase):
    def test_default_keep_does_not_enumerate_work_even_above_old_limit(self):
        roots = planned_roots()
        self.assertEqual(roots, ['_internal', 'assets\\custom-maps'])
        # 154759 is deliberately synthetic, not an asserted user measurement.
        work_count = 154759
        observed = []
        for root in roots:
            observed.append(root)
            if root == 'work':
                self.fail('Default retention would enumerate %d runtime entries' % work_count)
        self.assertNotIn('work', observed)

    def test_every_explicit_backup_or_clear_choice_still_includes_work(self):
        for backup, delete in ((True, False), (False, True), (True, True)):
            with self.subTest(backup=backup, delete=delete):
                self.assertIn('work', planned_roots(backup=backup, delete=delete))

    def test_actual_projection_and_ownership_both_reject_runtime_files(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-uninstall-ownership-') as folder:
            stage = Path(folder)
            (stage / 'synthetic-program.exe').write_bytes(b'not an executable')
            families = build.production_file_projection(stage, {})
            self.assertEqual(families['program'], ['synthetic-program.exe'])
            runtime = stage / 'work/installer-backups/fixture/files/0'
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b'synthetic old program')
            with self.assertRaisesRegex(ValueError, 'Invalid staged program/resource path'):
                build.production_file_projection(stage, {})
            with self.assertRaisesRegex(ValueError, 'must not contain user work/data'):
                build.write_program_ownership(stage, {'fileProjection': {'installedFiles': [
                    'work/installer-backups/fixture/files/0']}})
        production = INSTALLER.split('#ifdef ProductionInstaller\n[Tasks]', 1)[1].split('[Icons]', 1)[0]
        self.assertIn('#include ProductionFilesInclude', production)
        self.assertNotRegex(INSTALLER, r'(?m)^\[UninstallDelete\]')

    def test_reviewed_legacy_file_ledger_leaves_runtime_objects_in_place(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-legacy-uninstall-') as folder:
            root = Path(folder)
            values = {
                '_internal/assets/legacy-user-map.png': b'user-edited resource',
                '_internal/synthetic-program.dll': b'not a dll',
                'work/installer-backups/fixture/files/0': b'synthetic old program',
                'work/settings.json': b'synthetic preferences',
                'work/map-packages/import.lock': b'synthetic lock only',
                'work/unknown-user-file.txt': b'unknown runtime neighbour',
            }
            for name, data in values.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            identities = {name: (root / name).stat().st_ino for name in values if name.startswith('work/')}
            # Reviewed 161e uses exact projection and rejects all work paths.
            legacy_native_owned = ['_internal/assets/legacy-user-map.png', '_internal/synthetic-program.dll']
            self.assertTrue(all(not name.startswith('work/') for name in legacy_native_owned))
            held = root / 'synthetic-held-map'
            protected = root / legacy_native_owned[0]
            protected.rename(held)
            for name in legacy_native_owned:
                if (root / name).is_file():
                    (root / name).unlink()
            held.rename(protected)
            self.assertEqual(protected.read_bytes(), values[legacy_native_owned[0]])
            for name, identity in identities.items():
                self.assertEqual((root / name).read_bytes(), values[name])
                self.assertEqual((root / name).stat().st_ino, identity)

    def test_optional_scan_failure_requires_explicit_safe_choice_and_rewinds_partial_lists(self):
        optional = routine('InspectChosenUninstallWork')
        self.assertTrue(optional)
        self.assertIn('if not UninstallNeedsWorkScan then', optional)
        self.assertIn('MB_YESNOCANCEL or MB_DEFBUTTON3, IDCANCEL', optional)
        self.assertIn('if Answer = IDNO then', optional)
        self.assertIn('UninstallDeleteData := False;', optional)
        self.assertIn('UninstallMakeBackup := False;', optional)
        self.assertIn('Entries := SavedEntries;', optional)
        self.assertLess(optional.index('RewindUninstallScan'), optional.index('Answer := SuppressibleMsgBox'))
        rewind = routine('RewindUninstallScan')
        for name in ('UninstallFiles', 'UninstallHashes', 'UninstallDirectories'):
            self.assertIn(name + '.Delete(', rewind)
        prepare = routine('PrepareUninstallData')
        self.assertLess(prepare.index('InspectChosenUninstallWork'), prepare.index('OptionalUninstallBackup'))
        self.assertLess(prepare.index('InspectChosenUninstallWork'), prepare.index('RenameFile'))
        self.assertNotIn('DelTree(', SOURCE)

    def test_lock_pending_and_old_internal_preservation_remain_before_native_removal(self):
        events = routine('CurUninstallStepChanged')
        for call in ('AcquireResourceLock', 'pending-installer-upgrade.json',
                     'pending-resource-reset.json', 'DisableGameWatcher'):
            self.assertLess(events.index(call), events.index('PrepareUninstallData'))
        self.assertIn('RestoreUninstallResources;', events)
        self.assertIn('RestoreUninstallResources;', routine('DeinitializeUninstall'))
        scan = routine('InspectUninstallDirectory')
        self.assertIn('Entries > 50000', scan)
        self.assertIn('Find.Attributes and $400', scan)


if __name__ == '__main__':
    unittest.main()
