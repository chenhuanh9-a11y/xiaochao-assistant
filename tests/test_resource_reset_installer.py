"""Installer protocol and real isolated resource fixtures, not native Setup.

The lease tests label their Windows boundary explicitly. Python protocol tests
do not execute an EXE or replace any installed product.
"""
import configparser
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools')]
import installer_resource_lease as bridge
import star_map_reset_helper as helper
import resource_transaction as lease
import resource_upgrade_reset as reset
import build_qt_client as build
import test_resource_upgrade_reset as fixtures


class InstallerLeaseProtocolTests(unittest.TestCase):
    def test_bad_parent_contract_cannot_reach_native_duplicate(self):
        for change in ({'parent_pid': os.getpid()}, {'lock_handle': 0}, {'parent_sha256': 'bad'}):
            values = dict(parent_pid=os.getppid(), lock_handle=1, parent_created='1',
                          parent_executable='unused', parent_sha256='a' * 64)
            values.update(change)
            with patch.object(bridge, '_kernel', side_effect=AssertionError('must reject first')):
                with self.assertRaisesRegex(ValueError, 'invalid-parent-contract'):
                    bridge._duplicate_verified('.', **values)

    def test_verified_object_reuses_short_lock_and_closes_after_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            owner = SimpleNamespace(closed=False)
            owner.close = lambda: setattr(owner, 'closed', True)
            with patch.object(bridge, '_duplicate_verified', return_value=owner), \
                    patch.object(lease, '_open_lock', side_effect=AssertionError('must reuse verified object')):
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    with bridge.borrowed_installer_lease(folder):
                        with lease.resource_write_lock(folder):
                            raise RuntimeError('injected')
            self.assertTrue(owner.closed)
            self.assertIsNone(lease._state(folder)[1]['runtime'])

    @unittest.skipUnless(os.name == 'nt', 'requires real Windows DuplicateHandle/share=0')
    def test_native_child_borrows_same_exclusive_object_and_parent_keeps_lock(self):
        from ctypes import wintypes as wt
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'work/map-packages/import.lock'
            path.parent.mkdir(parents=True)
            api = bridge._kernel()
            handle = api.CreateFileW(str(path), 0xc0000000, 0, None, 4, 0x80, None)
            self.assertNotIn(handle, (None, ctypes.c_void_p(-1).value))
            try:
                times = [wt.FILETIME() for _ in range(4)]
                self.assertTrue(api.GetProcessTimes(api.GetCurrentProcess(), *(ctypes.byref(t) for t in times)))
                created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
                # A Windows venv's sys.executable may be a redirector that adds
                # another process. Use the actual image both for parent identity
                # and for a direct child; keep the production ancestry guard intact.
                image = ctypes.create_unicode_buffer(32768)
                image_length = wt.DWORD(len(image))
                self.assertTrue(api.QueryFullProcessImageNameW(
                    api.GetCurrentProcess(), 0, image, ctypes.byref(image_length)))
                self.assertTrue(0 < image_length.value < len(image))
                executable = image.value
                identity = dict(parent_pid=os.getpid(), parent_created=created,
                    parent_executable=executable, parent_sha256=hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
                    lock_handle=handle)
                script = ('import json,sys; from installer_resource_lease import borrowed_installer_lease; '
                          'from resource_transaction import resource_write_lock; '
                          '\nwith borrowed_installer_lease(sys.argv[1], **json.loads(sys.argv[2])):'
                          '\n with resource_write_lock(sys.argv[1]): print("borrowed")')
                run = subprocess.run([executable, '-c', script, str(root), json.dumps(identity)],
                    env=dict(os.environ, PYTHONPATH=str(ROOT / 'app')), text=True, capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertIn('borrowed', run.stdout)
                with self.assertRaisesRegex(ValueError, 'resource-busy'):
                    lease.resource_runtime_lease(root)
            finally:
                api.CloseHandle(handle)
            with lease.resource_runtime_lease(root):
                pass


class ResourceResetHelperTests(unittest.TestCase):
    def test_unassigned_uninstaller_option_can_be_omitted(self):
        argv = ['--action', 'inspect', '--install-root', 'fixture', '--result-directory', 'fixture-result',
                '--target-report', 'fixture-report', '--target-report-sha256', 'a' * 64,
                '--source-commit', 'b' * 40, '--display-version', '1.1.2', '--numeric-version', '1.0.0.24',
                '--parent-created', '1', '--parent-executable', 'fixture-parent', '--parent-sha256', 'c' * 64,
                '--parent-pid', '1', '--lock-handle', '1', '--group-shortcut', 'group']
        with patch.object(helper, 'run', return_value=0) as execute:
            self.assertEqual(helper.main(argv), 0)
        args = execute.call_args.args[0]
        self.assertIsNone(args.uninstall_exe)
        self.assertEqual(args.group_shortcut, 'group')

    def setUp(self):
        self.case = fixtures.ResourceUpgradeResetTests('runTest')
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.session = self.root.parent / 'installer-session'
        self.session.mkdir()
        self.target = dict(sourceCommit='a' * 40, displayVersion='1.1.2', numericVersion='1.0.0.24',
                           executableSha256=hashlib.sha256(b'new program').hexdigest(),
                           resourceInventorySha256='b' * 64)
        self.args = SimpleNamespace(action='inspect', install_root=str(self.root),
            result_directory=str(self.session), candidate_directory=None, confirmed=False,
            plan_sha256=None, transaction_id=None, target_report_sha256='c' * 64)
        for name in ('parent_pid', 'parent_created', 'parent_executable', 'parent_sha256', 'lock_handle'):
            setattr(self.args, name, 'test-double-only')

    def inspect_prepare(self):
        result = helper._execute(self.args, reset, self.target)
        self.args.plan_sha256 = result['planSha256']
        self.args.action, self.args.confirmed = 'prepare', True
        result = helper._execute(self.args, reset, self.target)
        self.args.transaction_id = result['transactionId']
        return result

    def test_review_summary_and_plan_bind_target_without_mutating_resources(self):
        result = helper._execute(self.args, reset, self.target)
        plan = json.loads((self.session / 'plan.json').read_text('utf-8'))
        self.assertEqual(plan['targetIdentity'], self.target)
        self.assertEqual(result['planSha256'], plan['planSha256'])
        raw = (self.session / 'summary.txt').read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        text = raw.decode('utf-8-sig')
        for word in ('重新下载', '自建路线', '不永久删除', self.target['sourceCommit']):
            self.assertIn(word, text)
        self.assertTrue(self.case.old.exists())
        self.case.unchanged_user_data()

    def test_prepare_requires_exact_explicit_confirmation(self):
        self.args.action = 'prepare'
        self.args.plan_sha256 = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'reviewed-confirmation'):
            helper._execute(self.args, reset, self.target)
        self.assertTrue(self.case.old.exists())

    def test_lost_prepare_receipt_can_restore_only_same_plan(self):
        plan = reset.inspect_resource_upgrade(self.root, target_identity=self.target)
        snapshot = self.case.old_snapshot(plan)
        self.inspect_prepare()
        self.args.transaction_id = None  # parent never observed prepare receipt
        self.args.action = 'rollback'
        saved = self.args.plan_sha256
        self.args.plan_sha256 = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'not-this-reviewed'):
            helper._execute(self.args, reset, self.target)
        self.args.plan_sha256 = saved
        self.assertEqual(helper._execute(self.args, reset, self.target)['status'], 'rolled-back')
        self.case.assert_old_restored(snapshot)

    def test_commit_requires_installed_program_identity_and_status_resolves_lost_result(self):
        self.inspect_prepare()
        self.args.action = 'commit'
        self.case.write(self.root / '星图.exe', b'old program')
        self.case.write(self.root / 'release-report.json', b'current report')
        self.args.target_report_sha256 = hashlib.sha256(b'current report').hexdigest()
        with self.assertRaisesRegex(ValueError, 'installed-program'):
            helper._execute(self.args, reset, self.target)
        self.case.write(self.root / '星图.exe', b'new program')
        self.assertEqual(helper._execute(self.args, reset, self.target)['status'], 'committed')
        self.args.action = 'status'
        self.assertEqual(helper._execute(self.args, reset, self.target)['status'], 'committed')
        self.args.action = 'rollback'
        self.assertEqual(helper._execute(self.args, reset, self.target)['status'], 'committed')
        self.assertFalse(self.case.old.exists())
        self.case.unchanged_user_data()

    def test_target_tampering_or_wrong_compiled_profile_fails_before_lease(self):
        report = dict(sourceCommit='a' * 40, compiledPayloadSourceCommit='a' * 40,
            installerSourceCommit='a' * 40, sourceDirty=False, **{k: v for k, v in self.target.items() if k != 'sourceCommit'})
        path = self.session / 'report.json'
        path.write_text(json.dumps(report))
        self.args.target_report = str(path)
        self.args.target_report_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        for key in ('source_commit', 'display_version', 'numeric_version'):
            setattr(self.args, key, self.target[{'source_commit': 'sourceCommit', 'display_version': 'displayVersion',
                                               'numeric_version': 'numericVersion'}[key]])
        original = helper._json
        def reading(path):
            return dict(product='星图', sourceCommit='d' * 40) if Path(path).name == 'installation-profile.json' else original(path)
        with patch.object(helper, '_json', side_effect=reading):
            with self.assertRaisesRegex(ValueError, 'program-identity-mismatch'):
                helper._target(self.args)

    def test_run_publishes_ini_last_and_stale_results_are_not_overwritten(self):
        # This injected Python lease tests IPC only, not the Windows handle bridge.
        with patch.object(helper, '_target', return_value=self.target), \
                patch('installer_native_observation.current_user_sid', return_value='S-1-5-21-1-2-3-1000'), \
                patch.object(helper, 'borrowed_installer_lease', side_effect=lambda root, **_: lease.resource_runtime_lease(root)):
            self.assertEqual(helper.run(self.args), 0)
        receipt = configparser.ConfigParser()
        receipt.read(self.session / 'result.ini', encoding='utf-16')
        self.assertEqual(receipt['result']['status'], 'inspected')
        self.assertEqual(receipt['result']['action'], 'inspect')
        before = (self.session / 'result.ini').read_bytes()
        with self.assertRaisesRegex(ValueError, 'new-and-empty'):
            helper.run(self.args)
        self.assertEqual((self.session / 'result.ini').read_bytes(), before)

    def test_run_failure_has_explicit_error_receipt_and_no_false_success(self):
        detail = '盘点失败：目录 E:\\星图\\旧图包；injected-target-change'
        with patch.object(helper, '_target', side_effect=ValueError(detail)):
            self.assertEqual(helper.run(self.args), 2)
        receipt = configparser.ConfigParser()
        receipt.read(self.session / 'result.ini', encoding='utf-16')
        self.assertEqual(receipt['result']['status'], 'error')
        self.assertEqual(receipt['result']['errorCode'], 'ValueError')
        raw = (self.session / 'error.txt').read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\0', raw)
        self.assertEqual(raw.decode('utf-8-sig'), detail)
        self.assertTrue(self.case.old.exists())
        self.case.unchanged_user_data()

    def test_empty_exception_still_provides_error_details(self):
        with patch.object(helper, '_target', side_effect=ValueError()):
            self.assertEqual(helper.run(self.args), 2)
        self.assertEqual((self.session / 'error.txt').read_text('utf-8-sig'), 'ValueError')

    def test_durable_commit_with_pending_requires_confirmed_matching_program_cleanup(self):
        self.inspect_prepare()
        report = dict(self.target, installerSourceCommit=self.target['sourceCommit'])
        self.case.write(self.root / 'release-report.json', json.dumps(report).encode())
        self.case.write(self.root / '星图.exe', b'new program')
        with patch.object(reset, '_clear_pending', side_effect=OSError('injected-cleanup-failure')):
            with self.assertRaisesRegex(OSError, 'cleanup-failure'):
                reset.finish_resource_upgrade(self.root, self.args.transaction_id, setup_succeeded=True)
        self.assertTrue((self.root / reset.PENDING).exists())
        self.args.action = 'pending'
        self.assertEqual(helper._execute(self.args, reset, self.target)['status'], 'committed')
        self.args.action, self.args.confirmed = 'resume-committed', False
        with self.assertRaisesRegex(ValueError, 'only-confirmed'):
            helper._execute(self.args, reset, self.target)
        self.args.confirmed = True
        self.case.write(self.root / '星图.exe', b'wrong target')
        with self.assertRaisesRegex(ValueError, 'prior-committed-program'):
            helper._execute(self.args, reset, self.target)
        self.case.write(self.root / '星图.exe', b'new program')
        self.assertEqual(helper._execute(self.args, reset, self.target)['status'], 'committed')
        self.assertFalse((self.root / reset.PENDING).exists())
        self.assertFalse(self.case.old.exists())

    def test_next_installer_never_resumes_uncommitted_half_without_program_rollback(self):
        self.inspect_prepare()
        self.args.action, self.args.confirmed = 'resume-committed', True
        with self.assertRaisesRegex(ValueError, 'only-confirmed'):
            helper._execute(self.args, reset, self.target)
        self.assertTrue((self.root / reset.PENDING).exists())


class ResourceResetPackagingTests(unittest.TestCase):
    def fixture(self, root):
        stage, output = root / 'stage', root / 'output'
        stage.mkdir(); output.mkdir()
        inventory = {}
        for name in ('star-map-reset-helper.exe', '_star_map_reset_runtime/python313.dll',
                     '_star_map_reset_runtime/installation-profile.json'):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('synthetic not executable: ' + name).encode())
            inventory[name] = dict(bytes=path.stat().st_size, sha256=build.sha256(path))
        executable = stage / '星图.exe'; executable.write_bytes(b'synthetic main executable')
        report = stage / 'release-report.json'; report.write_text('{}')
        (stage / '_installer-owned-files.txt').write_bytes(b'')
        base = stage / '_internal/references/base.json'
        base.parent.mkdir(parents=True); base.write_bytes(b'base')
        _, projection = build.write_production_files_include(stage,
            {'references/base.json': dict(bytes=4, sha256=build.sha256(base))}, output)
        record = dict(resetHelper=dict(included=True, executable='star-map-reset-helper.exe',
            runtimeDirectory='_star_map_reset_runtime', files=inventory),
            releaseReport=dict(file=report.name, sha256=build.sha256(report),
                               executableSha256=build.sha256(executable)), fileProjection=projection)
        build.write_program_ownership(stage, record)
        return stage, output, record

    def test_complete_runtime_and_target_digests_are_pinned_without_running_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            stage, output, record = self.fixture(Path(folder))
            definitions = build.resource_reset_installer_defines(stage, record, output, dict(commit='a' * 40))
            self.assertEqual(len(definitions), 7)
            self.assertIn('/DResetSourceCommit=' + 'a' * 40, definitions)
            lines = (output / 'reset-helper-inventory.txt').read_text('utf-8-sig').splitlines()
            self.assertEqual(len(lines), 3)
            self.assertFalse(record['resourceResetInstaller']['nativeTested'])

    def test_old_missing_changed_or_extra_helper_cannot_be_wrapped(self):
        for kind in ('missing', 'changed', 'extra', 'target', 'program', 'payload', 'projection'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                stage, output, record = self.fixture(Path(folder))
                if kind == 'missing': record.pop('resetHelper')
                if kind == 'changed': (stage / 'star-map-reset-helper.exe').write_bytes(b'changed')
                if kind == 'extra': (stage / '_star_map_reset_runtime/unknown.dll').write_bytes(b'unknown')
                if kind == 'target': (stage / 'release-report.json').write_bytes(b'changed')
                if kind == 'program': (stage / '星图.exe').write_bytes(b'changed')
                if kind == 'payload': (stage / '_internal/references/base.json').write_bytes(b'evil')
                if kind == 'projection': record['fileProjection']['installedFiles'].remove('星图.exe')
                with self.assertRaises(ValueError):
                    build.resource_reset_installer_defines(stage, record, output, dict(commit='a' * 40))


class ResourceResetSourceContracts(unittest.TestCase):
    def test_exec_launch_error_is_distinct_from_helper_exit_and_missing_detail(self):
        source = (ROOT / 'tools/qt-resource-reset.iss').read_text('utf-8-sig')
        call = source.split('function CallResourceReset(', 1)[1].split('function ReviewResourceReset(', 1)[0]
        launch = call.split('if not Exec(', 1)[1].split('if Code <> 0 then begin', 1)[0]
        self.assertNotIn('or (Code <> 0)', launch)
        self.assertIn('Windows 错误', launch)
        self.assertIn('SysErrorMessage(Code)', launch)
        self.assertIn('RESOURCE_RESET_LAUNCH_FAILED ', launch)
        self.assertIn('Exit;', launch)
        helper_failure = call.split('if Code <> 0 then begin', 1)[1].split("if GetIniString('result', 'action'", 1)[0]
        self.assertIn('程序退出码', helper_failure)
        self.assertIn("ResetReadText(ResultDir + '\\error.txt')", helper_failure)
        self.assertIn("GetIniString('result', 'errorCode'", helper_failure)
        self.assertIn('未生成可读错误详情', helper_failure)
        self.assertIn('RESOURCE_RESET_HELPER_FAILED ', helper_failure)
        self.assertIn('Exit;', helper_failure)
        self.assertIn("ResetReadText(ResultDir + '\\summary.txt')", source)

    def test_installer_event_order_and_unresolved_commit_are_fail_closed(self):
        source = (ROOT / 'tools/qt-client.iss').read_text('utf-8-sig')
        events = source.split('procedure CurStepChanged', 1)[1].split('procedure DeinitializeSetup', 1)[0]
        order = ['PrepareResourceReset', 'BeginUpgrade;', 'StartNativeResourceReset', 'RetireOldProgramFiles;',
                 'ResourceResetNativeCompleted :=', 'CompleteNativeResourceReset', 'CommitResourceReset', 'UpgradeCommitted :=']
        self.assertEqual(sorted(events.index(item) for item in order), [events.index(item) for item in order])
        preparation = source.split('procedure BeginUpgrade;', 1)[1].split('procedure RetireOldProgramFiles;', 1)[0]
        self.assertNotIn('DeleteFile(', preparation)
        cleanup = source.split('procedure DeinitializeSetup', 1)[1].split('function ExecutableAvailable', 1)[0]
        self.assertNotIn('FileCopy(', cleanup)
        self.assertNotIn('DeleteFile(', cleanup)
        self.assertLess(cleanup.index('ResolveResourceResetCommit'), cleanup.index('RollbackResourceReset'))
        self.assertLess(cleanup.index('RollbackResourceReset'), cleanup.index('ReleaseResourceLock'))
        self.assertIn('not ResourceResetNativeStarted and not ResourceResetNativeCompleted', cleanup)
        self.assertIn('if SafeToRestore then', cleanup)
        self.assertNotIn('then RaiseException(WatcherReason)', events.split('if CurStep = ssPostInstall')[1])
        self.assertIn('ResourceResetFailureCode := 64', events)
        self.assertIn('GetCustomSetupExitCode', source)
        resource = (ROOT / 'tools/qt-resource-reset.iss').read_text('utf-8-sig')
        for token in ('installer-pending', 'resume-native-complete', 'installerFinalized',
                      'ResetProgramInventorySha256', '--session-id ', '--restore-watcher',
                      'ResourceResetNativeStarted or ResourceResetNativeCompleted'):
            self.assertIn(token, resource)
        self.assertIn('pending-installer-upgrade.json', resource)
        self.assertIn('native-observation-evidence-budget-exceeded', resource)
        self.assertIn('尚未进入 Windows 程序安装', resource)

    def test_pascal_character_constants_never_start_preprocessor_lines(self):
        # ISPP reads a leading # as a directive even inside a Pascal expression.
        import re
        for name in ('qt-client.iss', 'qt-resource-reset.iss', 'qt-uninstall-data.iss',
                     'qt-game-watcher-lifecycle.iss'):
            with self.subTest(name=name):
                self.assertIsNone(re.search(r'^\s*#[0-9]', (ROOT / 'tools' / name).read_text(), re.MULTILINE))

    def test_code_includes_use_pascal_comments_not_section_comments(self):
        import re
        for name in ('qt-resource-reset.iss', 'qt-uninstall-data.iss', 'qt-game-watcher-lifecycle.iss'):
            with self.subTest(name=name):
                self.assertIsNone(re.search(r'^\s*;', (ROOT / 'tools' / name).read_text(), re.MULTILINE))

    def test_packaged_helper_extraction_and_lease_are_not_an_unlock_bypass(self):
        source = (ROOT / 'tools/qt-resource-reset.iss').read_text()
        for token in ("ExtractTemporaryFiles('{app}\\star-map-reset-helper.exe')", 'VerifyResetInputs;',
                      'ewWaitUntilTerminated', 'MB_YESNO or MB_DEFBUTTON2', 'if WizardSilent',
                      '--parent-created ', '--lock-handle ', 'ResetHelperInventorySHA256'):
            self.assertIn(token, source)
        self.assertNotIn('ReleaseResourceLock', source)
        self.assertNotIn('skip-lock', (ROOT / 'app/star_map_reset_helper.py').read_text())
        for text in ('DUPLICATE_CLOSE_SOURCE', 'TerminateProcess(', 'taskkill'):
            self.assertNotIn(text, (ROOT / 'app/installer_resource_lease.py').read_text())

    def test_pending_reset_blocks_uninstall_and_compiled_reuse_requires_new_helper(self):
        uninstall = (ROOT / 'tools/qt-uninstall-data.iss').read_text()
        self.assertIn('pending-resource-reset.json', uninstall.split('function InitializeUninstall', 1)[1])
        self.assertIn('pending-installer-upgrade.json', uninstall.split('function InitializeUninstall', 1)[1])
        event = uninstall.split('procedure CurUninstallStepChanged', 1)[1].split('procedure DeinitializeUninstall', 1)[0]
        self.assertLess(event.index('AcquireResourceLock'), event.index('pending-resource-reset.json'))
        self.assertLess(event.index('pending-resource-reset.json'), event.index('DisableGameWatcher'))
        self.assertLess(event.index('pending-resource-reset.json'), event.index('PrepareUninstallData'))
        rebuild = (ROOT / 'tools/rebuild_qt_installer.py').read_text()
        self.assertIn('("recovery", "import", "reset")', rebuild)
        self.assertIn('resource_reset_installer_defines', rebuild)
