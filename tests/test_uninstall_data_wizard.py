"""Inno wiring + real build inventory fixtures; native cleanup has separate tests.

The approved full-clear decision supersedes the historical keep/backup wizard.
These checks do not execute Pascal or claim a Windows installation passed.
"""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest

import build_qt_client as build

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'tools/qt-uninstall-data.iss').read_text('utf-8-sig')
INSTALLER = (ROOT / 'tools/qt-client.iss').read_text('utf-8-sig')


def routine(name):
    matches = list(re.finditer(r'(?m)^(?:function|procedure) ' + name + r'\b', SOURCE))
    if not matches:
        raise AssertionError(name)
    match = matches[-1]  # A Pascal forward declaration is not the implementation.
    following = re.search(r'(?m)^(?:function|procedure) ', SOURCE[match.end():])
    return SOURCE[match.start():match.end() + following.start()] if following else SOURCE[match.start():]


class UninstallWiringTests(unittest.TestCase):
    def test_default_full_clear_has_only_continue_or_cancel_in_both_channels(self):
        choose = routine('InitializeUninstall')
        self.assertIn('ConfirmUninstall=卸载将永久清除', INSTALLER)
        self.assertIn('更新版本请取消', INSTALLER)
        self.assertEqual(INSTALLER.count('ConfirmUninstall='), 1)
        self.assertNotIn('MsgBox(', choose.split('ConfirmUninstall', 1)[-1])
        for obsolete in ('UninstallDeleteData', 'DeleteBox', 'UninstallMakeBackup'):
            self.assertNotIn(obsolete, SOURCE)
        self.assertNotIn('CreateCustomForm', choose)
        recovery = routine('ContinueUninstallRecovery')
        self.assertLess(recovery.index('if FromSetup then begin'), recovery.index('CreateCustomForm'))
        self.assertEqual(INSTALLER.count('#include "qt-uninstall-data.iss"'), 2)
        self.assertNotIn('MB_YESNO', choose)
        self.assertIn('Result := True', choose)
        self.assertNotIn('AcquireUninstallLease', choose)

    def test_clear_precedes_native_removal_and_failure_is_not_success(self):
        event = routine('CurUninstallStepChanged')
        pre = event.split('if CurUninstallStep = usPostUninstall', 1)[0]
        self.assertLess(pre.index('AcquireUninstallLease'), pre.index('DisableGameWatcher'))
        self.assertLess(pre.index('CheckUninstallShortcuts'), pre.index('ClearOwnedInstallation'))
        self.assertLess(pre.index('DisableGameWatcher'), pre.index('PrepareUninstallRunner'))
        self.assertLess(pre.index('PrepareUninstallRunner'), pre.index('ClearOwnedInstallation'))
        self.assertLess(pre.index('ClearOwnedInstallation'), pre.index('CommitGameWatcherUninstall'))
        clear = routine('ClearOwnedInstallation')
        self.assertIn("(Code <> 0) or (GetIniString('result', 'status', '', Receipt) <> 'cleared')", clear)
        self.assertIn('RaiseException', clear)
        self.assertIn('remaining.txt', clear)
        self.assertIn('mutationStarted', clear)
        self.assertIn('UninstallMayHaveChanged := True', clear)
        self.assertIn('if UninstallMayHaveChanged or UninstallProcessUncertain then', routine('DeinitializeUninstall'))
        self.assertLess(pre.index('ClearOwnedInstallation'), pre.index('FinishUninstallData'))
        self.assertLess(pre.index('FinishUninstallData'), pre.index('CommitGameWatcherUninstall'))
        self.assertIn('MB_RETRYCANCEL, IDCANCEL', pre)
        self.assertIn('if UninstallSilent or', pre)

    def test_installed_helper_is_verified_and_copied_before_execution(self):
        copy = routine('PrepareUninstallRunner')
        for text in ('UninstallHelperInventorySHA256', 'UninstallSafeAbsolute(Source)',
                     'GetSHA256OfFile(Source)', 'FileCopy(Source, Target, True)', 'GetSHA256OfFile(Target)'):
            self.assertIn(text, copy)
        clear = routine('ClearOwnedInstallation')
        self.assertIn("RunBoundedUninstallProcess(UninstallRunnerRoot + '\\star-map-uninstall-helper.exe'", clear)
        self.assertIn('--lock-handle', clear)
        self.assertNotIn("Exec(ExpandConstant('{app}", SOURCE)
        self.assertNotIn('DelTree(', SOURCE)

    def test_foreign_or_unreadable_shortcuts_stop_before_data_clear(self):
        check = routine('CheckUninstallShortcut')
        self.assertIn('UninstallSafeAbsolute(Path)', check)
        self.assertIn('Link.TargetPath', check)
        self.assertIn('RaiseException', check)
        self.assertNotIn('DeleteFile', check)
        finish = routine('FinishUninstallEntries')
        self.assertIn('RemoveOwnedUninstallShortcut', finish)
        self.assertNotIn('RegDeleteKey', SOURCE)
        self.assertNotIn("RemoveOwnedUninstallShortcut(ExpandConstant('{group}\\卸载星图.lnk'))", finish)

    def test_locked_file_is_finalized_before_native_can_remove_retry_files(self):
        event = routine('CurUninstallStepChanged')
        post = event.split('if CurUninstallStep = usPostUninstall', 1)[1]
        finish = routine('FinishUninstallData')
        self.assertLess(finish.index('UninstallDeleteByHandle'), finish.index('ReleaseUninstallLease'))
        self.assertIn('$C0010000', routine('AcquireUninstallLease'))
        self.assertNotIn('DeleteFile(Path)', finish)
        self.assertNotIn('FinishUninstallData', post)
        self.assertIn('not RemoveDir', finish)
        self.assertLess(post.index("Path := ExpandConstant('{app}')"), post.index('ContinueUninstallRecovery(False)'))
        self.assertIn('UninstallCleanupComplete := ContinueUninstallRecovery(False)', post)
        recovery = routine('ContinueUninstallRecovery')
        self.assertIn("HasUninstallRecovery then RaiseException", recovery)
        self.assertIn("--recover --install-root", recovery)
        self.assertIn("--recovery-lease-root", recovery)
        setup = routine('InitializeSetup')
        self.assertLess(setup.index('HasUninstallRecovery'), setup.index('ContinueUninstallRecovery(True)'))
        self.assertIn('Result := False', setup)
        self.assertIn('完整清理仍须核对', INSTALLER)

    def test_worker_wait_is_bounded_and_retry_requires_confirmed_process_exit(self):
        wait = routine('RunBoundedUninstallProcess')
        self.assertIn('UninstallCreateProcess(Executable', wait)
        self.assertIn('UninstallTicks - Started >= Timeout', wait)
        self.assertIn('UninstallTerminate(Process.Process', wait)
        self.assertIn('UninstallWait(Process.Process', wait)
        self.assertIn('UninstallPumpMessages', wait)
        self.assertNotIn('ewWaitUntilTerminated', SOURCE)
        clear = routine('ClearOwnedInstallation')
        self.assertIn('--timeout-seconds 120', clear)
        self.assertIn('UninstallRunnerRoot, 135000, Code', clear)
        self.assertIn('PreviouslyChanged := UninstallMayHaveChanged', clear)
        self.assertIn('UninstallMayHaveChanged := PreviouslyChanged', clear)
        event = routine('CurUninstallStepChanged')
        self.assertLess(event.index('if UninstallProcessUncertain then begin'), event.index('MB_RETRYCANCEL'))
        self.assertIn('UninstallAttempt := UninstallAttempt + 1', routine('PrepareUninstallRunner'))

    def test_parent_does_not_pollute_a_persisted_recovery_snapshot(self):
        clear = routine('ClearOwnedInstallation')
        self.assertIn("if not HasUninstallRecovery and UninstallSafeAbsolute", clear)
        self.assertLess(clear.index('if not HasUninstallRecovery'), clear.index('SaveStringToFile'))
        acquire = routine('AcquireUninstallLease')
        self.assertLess(acquire.index('if HasUninstallRecovery then begin'), acquire.index('UninstallCreateFile'))
        recovery_branch = acquire.split('if HasUninstallRecovery then begin', 1)[1].split('end else', 1)[0]
        self.assertIn('not UninstallRecoveryIsPrepared or not FileExists(Path)', recovery_branch)
        self.assertIn('Disposition := 3', recovery_branch)
        self.assertNotIn('ForceDirectories', recovery_branch)
        event = routine('CurUninstallStepChanged')
        self.assertLess(event.index('HasUninstallRecovery and not UninstallRecoveryIsPrepared'),
                        event.index('AcquireUninstallLease'))
        initialize = routine('InitializeUninstall')
        self.assertIn('HasUninstallRecovery and not UninstallRecoveryIsPrepared', initialize)

    def test_install_and_uninstall_share_atomic_process_lifetime_maintenance_mutex(self):
        self.assertIn('if not AcquireInstallerMaintenance then Exit', routine('InitializeUninstall'))
        self.assertIn('Result := AcquireInstallerMaintenance', routine('InitializeSetup'))
        self.assertIn("ExpandConstant('Global\\XingtuMaintenance-{#InstallerAppId}')", SOURCE)
        self.assertIn('(MaintenanceMutex = 0) or (Error = 183)', SOURCE)
        self.assertNotIn('SetupMutex=', INSTALLER)
        watcher = (ROOT / 'tools/qt-game-watcher-lifecycle.iss').read_text('utf-8-sig')
        self.assertIn('if IsUninstaller then', watcher)
        self.assertIn('Directory, 15000, Code', watcher)

    def test_upgrade_keeps_existing_preserve_coordinator_and_never_calls_uninstall(self):
        production = INSTALLER.split('; Original per-user installer', 1)[1].split('; Private directory selection', 1)[0]
        prepare = production.split('function PrepareToInstall', 1)[1]
        self.assertLess(prepare.index('InstallerTargetError'), prepare.index('AcquireResourceLock'))
        self.assertLess(prepare.index('InstallerTargetError'), prepare.index('DisableGameWatcher'))
        self.assertIn('PrepareResourceReset', production)
        self.assertIn('StartNativeResourceReset', production)
        self.assertIn('CommitResourceReset', production)
        self.assertNotIn('ClearOwnedInstallation;', production)
        target = (ROOT / 'tools/qt-installer-target.iss').read_text('utf-8-sig')
        self.assertIn('资料迁移入口尚未接通', target)
        self.assertIn('维护工具', target)
        self.assertNotIn('Exec(', target)
        self.assertIn("CurrentId := ExpandConstant('{#InstallerAppId}')", target)
        self.assertIn('InstallerDirectoriesOverlap', target)

    def test_upgrade_watcher_restoration_and_recipe_coverage_remain(self):
        self.assertIn('if SafeToRestore then begin', INSTALLER)
        self.assertIn('else if GameWatcherRestorePending then', INSTALLER)
        self.assertIn('RestoreGameWatcher(RestoreReason)', INSTALLER)
        for name in ('qt-uninstall-data.iss', 'qt-installer-target.iss'):
            self.assertIn('TOOLS / "' + name + '"', (ROOT / 'tools/build_qt_client.py').read_text('utf-8'))
            self.assertIn('"tools/' + name + '"', (ROOT / 'tools/rebuild_qt_installer.py').read_text('utf-8'))

    def test_only_committed_current_upgrade_copies_are_cleaned(self):
        cleanup = INSTALLER.split('function CleanupCommittedUpgradeBackup', 1)[1].split('#include "qt-resource-reset.iss"', 1)[0]
        self.assertIn('not UpgradeStarted or not UpgradeCommitted', cleanup)
        self.assertIn('UpgradeExisting.Count - 1', cleanup)
        self.assertIn('not RemoveDir(UpgradeBackup)', cleanup)
        self.assertNotIn('DelTree', cleanup)
        event = INSTALLER.split('procedure CurStepChanged', 1)[1].split('procedure DeinitializeSetup', 1)[0]
        self.assertLess(event.index('UpgradeCommitted :='), event.index('CleanupCommittedUpgradeBackup'))
        self.assertIn('ResourceResetFailureCode := 64', event)


class UninstallHelperInventoryTests(unittest.TestCase):
    def test_final_program_filename_list_is_bound_to_the_native_compilation(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            ownership = stage / '_installer-owned-files.txt'
            ownership.write_text('program.dll\n', encoding='utf-8-sig')
            self.assertEqual(build.program_ownership_installer_defines(stage),
                             ['/DUninstallOwnershipSHA256=' + hashlib.sha256(ownership.read_bytes()).hexdigest()])
            ownership.write_text('', encoding='utf-8-sig')
            with self.assertRaises(ValueError):
                build.program_ownership_installer_defines(stage)

    def fixture(self, root):
        names = ['star-map-uninstall-helper.exe', '_star_map_uninstall_runtime/installation-profile.json']
        rows = {}
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic fixture, never execute')
            rows[name] = dict(bytes=path.stat().st_size, sha256=build.sha256(path))
        return dict(uninstallHelper=dict(included=True, executable=names[0],
            runtimeDirectory='_star_map_uninstall_runtime', files=rows))

    def test_inventory_matches_real_staged_bytes_and_native_define(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            record = self.fixture(stage)
            defines = build.uninstall_helper_installer_defines(stage, record)
            listing = stage / '_uninstall-helper-files.txt'
            self.assertEqual(defines, ['/DUninstallHelperInventorySHA256=' + hashlib.sha256(listing.read_bytes()).hexdigest()])
            rows = listing.read_text('utf-8-sig').splitlines()
            self.assertEqual(len(rows), 2)
            for row in rows:
                digest, relative = row.split(' ', 1)
                self.assertEqual(hashlib.sha256((stage / relative.replace('\\', '/')).read_bytes()).hexdigest(), digest)

    def test_private_builder_produces_required_installed_ownership_and_rejects_work(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            self.fixture(stage)
            (stage / 'XingtuQtClientTest.exe').write_bytes(b'private fixture')
            (stage / 'installation-profile.json').write_bytes(b'private profile fixture')
            build.write_private_program_ownership(stage)
            ownership = stage / '_installer-owned-files.txt'
            names = ownership.read_text('utf-8-sig').splitlines()
            self.assertIn('XingtuQtClientTest.exe', names)
            self.assertIn('installation-profile.json', names)
            self.assertIn('_installer-owned-files.txt', names)
            before = ownership.read_bytes()
            (stage / 'work').mkdir()
            (stage / 'work/settings.json').write_bytes(b'not a staged program')
            with self.assertRaisesRegex(ValueError, 'must not contain user work/data'):
                build.write_private_program_ownership(stage)
            self.assertEqual(ownership.read_bytes(), before)

    def test_changed_missing_extra_and_escaping_helpers_stop_build(self):
        for damage in ('changed', 'missing', 'extra', 'escape'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as folder:
                stage = Path(folder)
                record = self.fixture(stage)
                path = stage / '_star_map_uninstall_runtime/installation-profile.json'
                if damage == 'changed':
                    path.write_bytes(b'changed')
                elif damage == 'missing':
                    path.unlink()
                elif damage == 'extra':
                    path.with_name('unrecorded.py').write_bytes(b'unrecorded')
                else:
                    record['uninstallHelper']['files']['../external.exe'] = dict(bytes=1, sha256='a' * 64)
                with self.assertRaises(ValueError):
                    build.uninstall_helper_installer_defines(stage, record)


if __name__ == '__main__':
    unittest.main()
