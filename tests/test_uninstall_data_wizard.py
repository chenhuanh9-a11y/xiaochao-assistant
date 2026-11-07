"""Production Pascal source contracts and synthetic filesystem evidence.

The scope predicate is interpreted from the real Pascal source, not a duplicate
allowlist. Lifecycle fixtures use real rename/copy operations, but do NOT execute
Pascal, Inno, Windows locking, a registry, or a real installation. Those remain
separate Windows acceptance gates documented in docs/uninstall-data-wizard.md.
"""
import hashlib
from pathlib import Path, PureWindowsPath
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'tools/qt-uninstall-data.iss').read_text('utf-8-sig')
INSTALLER = (ROOT / 'tools/qt-client.iss').read_text('utf-8-sig')


def routine(name):
    match = re.search(r'(?m)^(?:function|procedure) ' + name + r'\b', SOURCE)
    if match is None:
        raise AssertionError(name)
    following = re.search(r'(?m)^(?:function|procedure) ', SOURCE[match.end():])
    return SOURCE[match.start():match.end() + following.start()] if following else SOURCE[match.start():]


def selected(relative):
    """Evaluate the actual simple Pascal path predicate without executing code."""
    body = routine('UninstallDataFile')
    excluded = re.search(r"if Path = '([^']+)' then Exit;", body)[1]
    path = relative.lower().replace('/', '\\')
    if path == excluded:
        return False
    expression = body.split('Result := ', 2)[2].split(';', 1)[0]
    # Tokenize ONLY literals, named values, Pos and Boolean/equality operators.
    token_pattern = r"'[^']*'|\b(?:Path|Ext|Pos|and|or)\b|\d+|[(),=]"
    tokens = re.findall(token_pattern, expression)
    if re.sub(token_pattern + r'|\s+', '', expression):
        raise AssertionError('Unsupported production predicate syntax')
    cursor = 0

    def atom():
        nonlocal cursor
        token = tokens[cursor]
        cursor += 1
        if token == '(':
            value = disjunction()
            assert tokens[cursor] == ')'
            cursor += 1
        elif token == 'Pos':
            assert tokens[cursor] == '(' and tokens[cursor + 2:cursor + 5] == [',', 'Path', ')']
            needle = tokens[cursor + 1][1:-1]
            cursor += 5
            value = path.find(needle) + 1
        elif token.startswith("'"):
            value = token[1:-1]
        elif token == 'Path':
            value = path
        elif token == 'Ext':
            value = PureWindowsPath(path).suffix
        else:
            value = int(token)
        if cursor < len(tokens) and tokens[cursor] == '=':
            cursor += 1
            value = value == atom()
        return value

    def conjunction():
        nonlocal cursor
        value = atom()
        while cursor < len(tokens) and tokens[cursor] == 'and':
            cursor += 1
            other = atom()
            value = value and other
        return value

    def disjunction():
        nonlocal cursor
        value = conjunction()
        while cursor < len(tokens) and tokens[cursor] == 'or':
            cursor += 1
            other = conjunction()
            value = value or other
        return value

    value = disjunction()
    assert cursor == len(tokens)
    return value


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UninstallSourceContracts(unittest.TestCase):
    def test_watcher_stop_is_before_data_preparation_and_abort_restores_prior_choice(self):
        events = routine('CurUninstallStepChanged')
        self.assertLess(events.index('DisableGameWatcher'), events.index('PrepareUninstallData'))
        self.assertLess(events.index('if not PrepareUninstallData then Abort'),
                        events.index('CommitGameWatcherUninstall'))
        self.assertIn('RestoreGameWatcher', routine('DeinitializeUninstall'))
        self.assertNotIn('DisableGameWatcher', routine('InitializeUninstall'))
        self.assertNotIn('CommitGameWatcherUninstall', routine('PrepareUninstallData'))

    def test_upgrade_watcher_stop_restore_hooks_keep_shared_lifecycle_owner(self):
        production = INSTALLER.split('; Original per-user installer', 1)[1].split('; Private directory selection', 1)[0]
        self.assertIn('#include "qt-game-watcher-lifecycle.iss"', production)
        events = production.split('procedure CurStepChanged', 1)[1].split('procedure DeinitializeSetup', 1)[0]
        self.assertLess(events.index('DisableGameWatcher'), events.index('BeginUpgrade'))
        self.assertLess(events.index('UpgradeCommitted := True'), events.index('RestoreGameWatcher'))
        cleanup = production.split('procedure DeinitializeSetup', 1)[1].split('function ExecutableAvailable', 1)[0]
        self.assertLess(cleanup.index('UPGRADE_ROLLBACK_RESTORED'), cleanup.index('RestoreGameWatcher'))
        self.assertIn('if Restored then begin', cleanup)
        self.assertIn('else if GameWatcherRestorePending then', cleanup)
        prepare = production.split('function PrepareToInstall', 1)[1]
        self.assertLess(prepare.index('UpgradePathAllowed'), prepare.index('DisableGameWatcher'))
        self.assertIn('RestoreGameWatcher(RestoreReason)', prepare)
        for definition in ('function CaptureGameWatcher', 'function DisableGameWatcher', 'function RestoreGameWatcher'):
            self.assertNotIn(definition, production)

    def test_all_installer_code_includes_are_tracked_in_build_recipes(self):
        builder = (ROOT / 'tools/build_qt_client.py').read_text()
        rebuild = (ROOT / 'tools/rebuild_qt_installer.py').read_text()
        for filename in ('qt-uninstall-data.iss', 'qt-game-watcher-lifecycle.iss'):
            self.assertIn('TOOLS / "' + filename + '"', builder)
            self.assertIn('"tools/' + filename + '"', rebuild)

    def test_default_keep_and_no_backup_are_independent_and_silent_is_non_destructive(self):
        choose = routine('ChooseUninstallData')
        for text in ('UninstallDeleteData := False;', 'UninstallMakeBackup := False;',
                     'DeleteBox.Checked := False;', 'UninstallBackupBox.Checked := False;'):
            self.assertIn(text, choose)
        self.assertLess(choose.index('if UninstallSilent'), choose.index('CreateCustomForm'))
        self.assertIn('UninstallDeleteData := DeleteBox.Checked', choose)
        self.assertIn('UninstallMakeBackup := UninstallBackupBox.Checked', choose)
        self.assertNotIn('ParamStr', SOURCE)
        self.assertNotIn('ExpandConstant(\'{param:', SOURCE)

    def test_wizard_cancel_and_native_confirmation_precede_filesystem_changes(self):
        initialize = routine('InitializeUninstall')
        self.assertIn('Result := ChooseUninstallData', initialize)
        for operation in ('PrepareUninstallData', 'BackupUninstallResources', 'AcquireResourceLock', 'RenameFile'):
            self.assertNotIn(operation, initialize)
        choose = routine('ChooseUninstallData')
        self.assertIn('MB_YESNOCANCEL or MB_DEFBUTTON3', choose)
        self.assertIn('if Answer = IDCANCEL then Break', choose)
        events = routine('CurUninstallStepChanged')
        self.assertIn('if CurUninstallStep = usUninstall', events)
        self.assertIn('if not PrepareUninstallData then Abort', events)
        self.assertLess(events.index('if CurUninstallStep = usDone'), events.index('ClearSelectedUninstallData'))
        self.assertNotIn('ClearSelectedUninstallData', events.split('if CurUninstallStep = usDone')[0])
        self.assertIn('MB_YESNO or MB_DEFBUTTON2, IDNO', events)
        self.assertIn('UninstallDeleteData := False;', events)

    def test_backup_failure_has_retry_skip_cancel_and_reconfirms_clear_without_backup(self):
        optional = routine('OptionalUninstallBackup')
        for text in ('if not UninstallMakeBackup then Exit', 'if Answer = IDNO',
                     'if UninstallDeleteData then', 'MB_YESNO or MB_DEFBUTTON2, IDNO',
                     'UninstallMakeBackup := False', 'if Answer <> IDYES'):
            self.assertIn(text, optional)
        self.assertNotIn('UninstallDeleteData :=', optional)
        self.assertLess(routine('PrepareUninstallData').index('OptionalUninstallBackup'),
                        routine('PrepareUninstallData').index('RenameFile'))

    def test_backup_copy_is_optional_and_outside_installation_with_complete_marker(self):
        location = routine('UninstallBackupLocationAllowed')
        self.assertIn('(Pos(AppPath, Target) <> 1)', location)
        self.assertIn('UninstallFinalDirectory(Parent)', location)
        self.assertIn('(DriveType <> 2) and (DriveType <> 3)', location)
        final_path = routine('UninstallFinalDirectory')
        self.assertIn('UninstallSafeAbsolute(Path)', final_path)
        self.assertIn('UninstallGetFinalPath(Handle, Buffer, 32768, 1)', final_path)
        self.assertIn('CloseHandle(Handle)', final_path)
        backup = routine('BackupUninstallResources')
        self.assertIn('CreateDir(UninstallBackupPath)', backup)
        self.assertIn('UninstallFinalDirectory(UninstallBackupParent) <> UninstallBackupFinalParent', backup)
        self.assertIn('FileCopy(Source, Destination, True)', backup)
        self.assertIn('UninstallObjectMatches(Destination, UninstallHashes[I])', backup)
        self.assertLess(backup.index('FileCopy'), backup.index("'\\COMPLETED.txt'"))
        self.assertNotIn('FileCopy', routine('PrepareUninstallData'))
        self.assertNotIn('FileCopy', routine('RestoreUninstallResources'))

    def test_recovery_mapping_precedes_rename_and_existing_objects_are_never_overwritten(self):
        prepare = routine('PrepareUninstallData')
        self.assertLess(prepare.index("'\\paths.txt'"), prepare.index('RenameFile(Source, Destination)'))
        self.assertLess(prepare.index("'\\sha256.txt'"), prepare.index('RenameFile(Source, Destination)'))
        restore = routine('RestoreUninstallResources')
        for text in ('UninstallDeleteStarted then Exit', 'FileExists(Destination) or DirExists(Destination)',
                     'UninstallObjectMatches(Source, UninstallHashes[I])', 'RenameFile(Source, Destination)'):
            self.assertIn(text, restore)
        self.assertIn('RestoreUninstallResources', routine('DeinitializeUninstall'))
        self.assertIn('finally', routine('DeinitializeUninstall'))
        self.assertIn('ReleaseResourceLock', routine('DeinitializeUninstall'))

    def test_clear_requires_explicit_choice_and_verifies_entire_set_before_commit(self):
        clear = routine('ClearSelectedUninstallData')
        self.assertIn('if not UninstallDeleteData or not UninstallHoldingCreated then Exit', clear)
        self.assertLess(clear.index('RestoreUninstallResources'), clear.index('UninstallDeleteStarted := True'))
        self.assertLess(clear.index('UninstallDeleteStarted := True'), clear.index('DeleteFile(Path)'))
        self.assertIn('UNINSTALL_DATA_CLEAR_INCOMPLETE', clear)
        self.assertNotIn('DelTree(', SOURCE)
        self.assertNotIn('DeleteFile(ExpandConstant', SOURCE)

    def test_missing_enumeration_is_not_confused_with_access_denial_and_links_are_rejected(self):
        scan = routine('InspectUninstallDirectory')
        for text in ('Depth > 32', 'Entries > 50000', 'Find.Attributes and $400',
                     'UninstallLastError = 2', 'Error = 18', 'FindClose(Find)'):
            self.assertIn(text, scan)
        self.assertLess(scan.index('Find.Attributes and $400'), scan.index('InspectUninstallDirectory(Name'))
        self.assertIn('(Error <> 2) and (Error <> 3)', routine('UninstallSafeAbsolute'))

    def test_interrupted_holding_blocks_upgrade_and_repeated_uninstall(self):
        self.assertIn('DirExists(UninstallHoldingDirectory)', routine('InitializeUninstall'))
        production = INSTALLER.split('; Original per-user installer', 1)[1].split('; Private directory selection', 1)[0]
        self.assertIn('DirExists(UninstallHoldingDirectory)', production)
        self.assertIn('#include "qt-uninstall-data.iss"', production)
        self.assertIn('TOOLS / "qt-uninstall-data.iss"', (ROOT / 'tools/build_qt_client.py').read_text())

    def test_real_scope_keeps_lock_and_unknown_neighbours_out(self):
        for path in ('work/nightmare-memory/ui-settings.json', 'work/installer-backups/old/program.exe',
                     '_internal/assets/authors/user/map.webp', '_internal/references/author-packs/a/catalog.json',
                     '_internal/seasons/s1/difficulty2/recognition/cache.npz',
                     'assets/custom-maps/user/base.png', '_internal/managed-base-resources/manifest.json'):
            self.assertTrue(selected(path), path)
        for path in ('work/map-packages/import.lock', 'outside/notes.json', 'notes.txt',
                     '.xingtu-uninstall-retained/files/0', '_internal/python312.dll',
                     'assets/unrelated/user.png', 'work-neighbour/file.png'):
            self.assertFalse(selected(path), path)


class UninstallFilesystemSimulation(unittest.TestCase):
    """A rename-based preservation model, not native Pascal execution."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='uninstall-data-fixture-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = self.folder / 'synthetic-install'
        self.values = {
            'work/nightmare-memory/ui-settings.json': b'user-settings',
            'work/map-packages/author-example.json': b'user-receipt',
            '_internal/assets/authors/example/map.png': b'user-edited-image',
            '_internal/seasons/s1/difficulty2/recognition/data.npz': b'native-data',
            'assets/custom-maps/user/route.idvm': b'user-route',
            '_internal/python312.dll': b'non-executable-program-fixture',
            'work/map-packages/import.lock': b'lock-fixture',
            'neighbour/keep.txt': b'not-owned',
        }
        for name, data in self.values.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.rows = [(name, sha(self.root / name)) for name in self.values if selected(name)]
        self.holding = self.root / '.xingtu-uninstall-retained'

    def stage(self, stop_after=None):
        (self.holding / 'files').mkdir(parents=True)
        (self.holding / 'paths.txt').write_text('\n'.join(name for name, _ in self.rows))
        (self.holding / 'sha256.txt').write_text('\n'.join(digest for _, digest in self.rows))
        for index, (name, digest) in enumerate(self.rows):
            if stop_after is not None and index == stop_after:
                return
            source = self.root / name
            self.assertEqual(sha(source), digest)
            source.rename(self.holding / 'files' / str(index))

    def restore(self):
        conflicts = []
        for index, (name, digest) in enumerate(self.rows):
            source, target = self.holding / 'files' / str(index), self.root / name
            if source.exists():
                if sha(source) != digest or target.exists():
                    conflicts.append(name)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.rename(target)
            elif not target.exists() or sha(target) != digest:
                conflicts.append(name)
        return conflicts

    def test_keep_without_backup_survives_old_uninstall_ownership_and_preserves_bytes(self):
        identities = {name: (self.root / name).stat().st_ino for name, _ in self.rows}
        self.stage()
        # Old uninstall entries include user-modified image paths. They are
        # absent during native deletion, unlike a simple optional-backup toggle.
        for name in self.values:
            if name.startswith('_internal/') and (self.root / name).exists():
                (self.root / name).unlink()
        self.assertEqual(self.restore(), [])
        for name, _ in self.rows:
            self.assertEqual((self.root / name).read_bytes(), self.values[name])
            self.assertEqual((self.root / name).stat().st_ino, identities[name])
        self.assertFalse(any(self.folder.glob('backup*')))
        self.assertEqual((self.root / 'neighbour/keep.txt').read_bytes(), b'not-owned')

    def test_partial_preparation_cancel_restores_only_staged_originals(self):
        self.stage(stop_after=2)
        self.assertEqual(self.restore(), [])
        for name, data in self.values.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def test_restoration_conflict_retains_both_objects_without_overwrite(self):
        self.stage()
        name = self.rows[0][0]
        (self.root / name).write_bytes(b'later-user-change')
        self.assertEqual(self.restore(), [name])
        self.assertEqual((self.root / name).read_bytes(), b'later-user-change')
        self.assertEqual((self.holding / 'files/0').read_bytes(), self.values[name])

    def test_clear_touches_only_snapshotted_objects_and_external_backup_survives(self):
        backup = self.folder / 'optional-backup'
        for name, digest in self.rows:
            target = backup / 'files' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / name, target)
            self.assertEqual(sha(target), digest)
        self.stage()
        new = self.root / 'work/later-file.txt'
        new.write_bytes(b'not-in-confirmed-snapshot')
        for index, (_, digest) in enumerate(self.rows):
            path = self.holding / 'files' / str(index)
            self.assertEqual(sha(path), digest)
            path.unlink()
        self.assertTrue(new.exists())
        self.assertTrue((self.root / 'neighbour/keep.txt').exists())
        for name, digest in self.rows:
            self.assertFalse((self.root / name).exists())
            self.assertEqual(sha(backup / 'files' / name), digest)


if __name__ == '__main__':
    unittest.main()
