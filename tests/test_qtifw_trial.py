"""Actual local filesystem recovery/data boundaries, no installed user tree."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "app")]
import qtifw_trial_helper as helper
import build_qtifw_trial as builder
from resource_transaction import resource_runtime_lease, resource_write_lock
from qtifw_trial_helper import OWNER, prepare, verify, verify_program, clear_data, scoped, check_target
from build_qtifw_trial import check_archive_inventory


def program_only_stage(folder):
    """Real freeze receipts over synthetic bytes; no compiler or EXE runs."""
    build = builder.build
    stage, output = folder / 'freeze-stage', folder / 'freeze-output'
    stage.mkdir(); output.mkdir()
    source = dict(commit='a' * 40, tree='b' * 40, clean=True)
    profile = dict(product='星图', sourceCommit=source['commit'],
        resourceInstallationPolicy=build.RESOURCE_INSTALLATION_POLICY,
        installerInstance=dict(id='production', **build.INSTANCES['production']))
    version = json.loads((ROOT / 'version.json').read_text(encoding='utf8'))
    payload = {'星图.exe': b'synthetic main, never executed', 'version.json': json.dumps(version).encode(),
        'installation-profile.json': json.dumps(profile).encode(),
        '_internal/installation-profile.json': json.dumps(profile).encode(),
        '_installer-owned-files.txt': b'', '_internal/references/base.json': b'independent base, not installed',
        '_internal/' + build.BRAND_ICON: b'synthetic icon',
        '_internal/assets/brand/xingtu-round.png': b'synthetic brand image'}
    record = dict(source=source, compiledPayloadSourceCommit=source['commit'], product='星图', version=version)
    for kind in ('import', 'recovery', 'reset'):
        exe, runtime = 'star-map-' + kind + '-helper.exe', '_star_map_' + kind + '_runtime'
        values = {exe: b'synthetic helper, never executed', runtime + '/python313.dll': b'synthetic runtime'}
        payload.update(values)
        record[kind + 'Helper'] = dict(included=True, executable=exe, runtimeDirectory=runtime,
            files={name: dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for name, raw in values.items()})
    payload['XingtuGameWatcher.exe'] = b'synthetic watcher, never executed'
    record['gameWatcher'] = dict(included=True, executable='XingtuGameWatcher.exe', files={
        'XingtuGameWatcher.exe': dict(bytes=len(payload['XingtuGameWatcher.exe']),
            sha256=hashlib.sha256(payload['XingtuGameWatcher.exe']).hexdigest())})
    for name, raw in payload.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    record['releaseReport'] = build.stage_release_report(stage, product='星图', source=source, version=version,
        resource_version='fixture-independent-resources', resource_inventory_sha256='d' * 64)
    inputs = {name[len('_internal/'):]: dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
              for name, raw in payload.items() if name.startswith(('_internal/references/', '_internal/assets/'))}
    _, record['fileProjection'] = build.write_production_files_include(stage, inputs, output, source=source)
    build.write_program_ownership(stage, record)
    return stage, record, source


@unittest.skipUnless(os.name == "nt", "Win32 exclusive handles and native parent lifetime")
class UninstallLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name) / "安装 中文"
        self.program = self.target / "program"
        self.resources = self.program / "resources"
        self.program.mkdir(parents=True)
        (self.target / "trial-maintenance.exe").write_bytes(b"synthetic expected parent, never executed")

    def native_parent(self):
        # Python stands in for the native maintenance EXE. Its child runs the
        # real hold function, native locks, DuplicateHandle and all parent
        # identity/time/byte validation, substituting only the expected image.
        child_code = """
import hashlib, json, sys
from pathlib import Path
sys.path[:0] = sys.argv[2:4]
import qtifw_trial_lease as lease
actual_image = Path(sys.executable)
actual_digest = hashlib.sha256(actual_image.read_bytes()).hexdigest()
validate = lease._parent
lease._parent = lambda api, handle, pid, created, expected, digest: validate(
    api, handle, pid, created, actual_image, actual_digest)
try:
    result = dict(success=True, **lease.hold_uninstaller_lease(sys.argv[1]))
except (OSError, ValueError) as error:
    result = dict(success=False, reason=str(error))
print(json.dumps(result), flush=True)
"""
        parent_code = """
import json, subprocess, sys
child = subprocess.run([sys.executable, '-B', '-c', *sys.argv[1:]],
    capture_output=True, text=True, encoding='utf-8')
print(json.dumps(dict(code=child.returncode, output=child.stdout, errors=child.stderr)), flush=True)
sys.stdin.read(1)
"""
        # A Windows venv python.exe is a redirector. Use the actual interpreter
        # here so the helper's direct parent is our intended maintenance stand-in.
        process = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), "-B", "-c", parent_code, child_code,
                str(self.target), str(ROOT / "tools"), str(ROOT / "app")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: process.communicate("x", timeout=10) if process.poll() is None else None)
        report = json.loads(process.stdout.readline())
        self.assertEqual(report["code"], 0, report["errors"])
        return process, json.loads(report["output"])

    def assert_busy(self, root):
        with self.assertRaisesRegex(ValueError, "resource-busy"):
            with resource_runtime_lease(root):
                pass

    def test_both_leases_survive_helper_exit_until_native_parent_exit_with_pending_catalog(self):
        pending = self.resources / "transactions/pending-catalog.json"
        pending.parent.mkdir(parents=True)
        pending.write_bytes(b"unfinished resource catalog, explicitly uninstalling")
        process, result = self.native_parent()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["roots"], 2)
        self.assertEqual(result["parentPid"], process.pid)
        self.assertEqual(result["release"], "native-parent-exit")
        self.assertIsNone(process.poll())
        self.assert_busy(self.program)
        self.assert_busy(self.resources)
        self.assertEqual(pending.read_bytes(), b"unfinished resource catalog, explicitly uninstalling")
        process.communicate("x", timeout=10)
        self.assertEqual(process.returncode, 0)
        for root in (self.program, self.resources):
            with resource_runtime_lease(root):
                pass

    def test_missing_resources_are_not_created_by_uninstall(self):
        process, result = self.native_parent()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["roots"], 1)
        self.assertFalse(self.resources.exists())
        self.assert_busy(self.program)
        process.communicate("x", timeout=10)
        self.assertFalse(self.resources.exists())
        with resource_runtime_lease(self.program):
            pass

    def test_resource_only_writer_blocks_uninstall_and_releases_partial_program_lease(self):
        self.resources.mkdir()
        with resource_write_lock(self.resources):
            process, result = self.native_parent()
            self.assertFalse(result["success"], result)
            self.assertEqual(result["reason"], "uninstall-resource-busy")
            # A resource writer in this process never owned the program root;
            # failure must close the helper's first, partially acquired lease.
            with resource_runtime_lease(self.program):
                pass
            process.communicate("x", timeout=10)

    def test_resources_with_outside_hardlink_still_block_full_uninstall(self):
        self.resources.mkdir()
        original = self.resources / "package.bin"
        original.write_bytes(b"resource bytes shared outside this installation")
        outside = self.target.parent / "outside.bin"
        os.link(original, outside)
        process, result = self.native_parent()
        self.assertFalse(result["success"], result)
        self.assertEqual(result["reason"], "installation-hardlink-outside-target")
        for root in (self.program, self.resources):
            with resource_runtime_lease(root):
                pass
        self.assertEqual(outside.read_bytes(), original.read_bytes())
        process.communicate("x", timeout=10)


class TrialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name)
        (self.scope / ".qtifw-trial-scope").write_text("test-run\n", encoding="ascii")
        (self.scope / "targets").mkdir()
        self.target = self.scope / "targets/fixture"
        self.policy = dict(scope=str(self.scope), instance="test-run", payloadSource="a" * 40,
            files={name: dict(bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
                   for name, value in {"app.exe": b"whole", "_internal/runtime.dll": b"runtime"}.items()})

    def seed(self):
        prepare(self.policy, self.target)
        work = self.target / "program/work"
        work.mkdir(parents=True)
        (work / "settings.json").write_bytes(b"synthetic settings")
        (self.target / "personal.txt").write_bytes(b"outside declared data scope")
        return work

    def test_archive_reuse_rejects_same_source_and_count_with_different_bytes(self):
        prior = self.scope / "prior-policy.json"
        prior.write_text(json.dumps(self.policy), encoding="utf-8")
        inventory = {"files": dict(self.policy["files"])}
        check_archive_inventory(prior, self.policy["payloadSource"], inventory)
        with self.assertRaisesRegex(ValueError, "Deduplicated program archives"):
            check_archive_inventory(prior, self.policy["payloadSource"], inventory,
                                    [["@TargetDir@/program/a", "@TargetDir@/program/b"]])
        inventory["files"]["app.exe"] = dict(bytes=5, sha256=hashlib.sha256(b"other").hexdigest())
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            check_archive_inventory(prior, self.policy["payloadSource"], inventory)

    def test_portable_install_has_no_build_scope_dependency_and_keeps_owner_binding(self):
        self.policy.update(portable=True, allowNewTargets=True, fullUninstall=True)
        self.policy.pop("scope")
        (self.scope / ".qtifw-trial-scope").unlink()
        self.assertTrue(prepare(self.policy, self.target)["prepared"])
        other = self.scope / "targets/other"
        other.mkdir()
        (other / OWNER).write_bytes((self.target / OWNER).read_bytes())
        with self.assertRaisesRegex(ValueError, "owner-target-mismatch"):
            check_target(self.policy, other)
        helper.receipt(self.policy, self.target, "data", dict(success=True, status="scheduled"))
        self.assertEqual(json.loads((self.target / "trial-data.json").read_bytes())["status"], "scheduled")
        self.assertFalse((self.scope / "last-uninstall.json").exists())

    def test_portable_mode_still_rejects_nonempty_unowned_directory(self):
        self.policy.update(portable=True, allowNewTargets=True)
        self.policy.pop("scope")
        self.target.mkdir()
        original = self.target / "personal.txt"
        original.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "directory-not-empty"):
            prepare(self.policy, self.target)
        self.assertEqual(original.read_bytes(), b"keep")
        self.assertFalse((self.target / OWNER).exists())

    def test_uninstall_accepts_internal_snapshot_aliases_but_rejects_external_links(self):
        from qtifw_trial_lease import inspect_installation_objects
        self.seed()
        original = self.target / "program/work/activation.json"
        original.write_bytes(b"immutable snapshot pointer")
        internal = self.target / "program/work/pointer-witness.json"
        os.link(original, internal)
        lock = self.target / "program/work/map-packages/import.lock"
        inspect_installation_objects(self.target, lock)
        outside = self.scope / "external-pointer.json"
        os.link(original, outside)
        with self.assertRaisesRegex(ValueError, "hardlink-outside-target"):
            inspect_installation_objects(self.target, lock)
        self.assertEqual(outside.read_bytes(), b"immutable snapshot pointer")
        self.assertEqual(original.stat().st_nlink, 3)

    def test_retired_map_install_cli_rejects_before_touching_target(self):
        work = self.seed()
        personal = work / 'settings.json'
        before = personal.read_bytes()
        with patch.object(sys, 'argv', ['qtifw-trial-helper', 'install-maps', str(self.target)]), \
             redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                helper.main()
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(personal.read_bytes(), before)
        self.assertFalse((self.target / '_installer-support/maps').exists())

    def test_retired_map_pack_build_flags_reject_before_staging(self):
        output = self.scope / 'new-setup-output'
        args = ['build-qtifw', '--stage', str(self.scope / 'source'),
                '--build-record', str(self.scope / 'record.json'), '--ifw-bin', str(self.scope / 'ifw'),
                '--output', str(output), '--release', '--map-pack-root', str(self.scope / 'maps'),
                '--map-pack-report', str(self.scope / 'delivery.json')]
        with patch.object(sys, 'argv', args), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                builder.main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(output.exists())

    def test_current_program_only_freeze_stages_exact_program_without_base(self):
        stage, record, source = program_only_stage(self.scope)
        output = self.scope / 'trial-stage'
        output.mkdir()
        before = {p.relative_to(stage): (p.read_bytes(), p.stat().st_ino) for p in stage.rglob('*') if p.is_file()}
        with patch.object(builder.build, '_git', return_value=''):
            compiled, inventory = builder.stage_payload(stage, record, output)
        self.assertEqual(compiled, source['commit'])
        program = output / 'packages' / builder.PACKAGE / 'data/program'
        self.assertEqual({p.relative_to(program).as_posix() for p in program.rglob('*') if p.is_file()}, set(inventory['files']))
        self.assertNotIn('_internal/references/base.json', inventory['files'])
        self.assertFalse(any('managed-base-resources' in name for name in inventory['files']))
        for name, expected in before.items():
            path = stage / name
            self.assertEqual((path.read_bytes(), path.stat().st_ino), expected)

    def test_normal_builder_emits_uninstall_data_component_without_map_seed(self):
        import xml.etree.ElementTree as ET
        stage, record, source = program_only_stage(self.scope)
        build_record = self.scope / 'freeze-record.json'
        build_record.write_text(json.dumps(record), encoding='utf8')
        output = self.scope / 'new-normal-qtifw'
        def git(*args):
            return source['commit'] if args == ('rev-parse', 'HEAD') else ''
        def external_compiler(command, **kwargs):
            # Compiler/PE validation are explicit external substitutes; all
            # staging, identities, scripts, XML and receipts execute unchanged.
            if 'PyInstaller' in command:
                helper = output / 'helper/qtifw-trial-helper'
                helper.mkdir(parents=True)
                (helper / 'qtifw-trial-helper.exe').write_bytes(b'synthetic helper')
            elif Path(command[0]).name == 'binarycreator.exe':
                Path(command[-1]).write_bytes(b'synthetic test output, never executed')
            else:
                self.fail('unexpected external command')
            return subprocess.CompletedProcess(command, 0)
        args = ['build-qtifw', '--stage', str(stage), '--build-record', str(build_record),
                '--ifw-bin', str(self.scope / 'ifw'), '--output', str(output)]
        with patch.object(sys, 'argv', args), patch.object(builder.build, '_git', side_effect=git), \
             patch.object(builder.build, 'source_identity', return_value=source), \
             patch.object(builder.build, 'verify_invoker_manifest', return_value=None), \
             patch.object(builder.subprocess, 'run', side_effect=external_compiler), redirect_stdout(StringIO()):
            builder.main()
        package = ET.parse(output / 'packages' / builder.PACKAGE / 'meta/package.xml').getroot()
        self.assertEqual(package.findtext('Dependencies'), 'org.xingtu.qtifw.trialdata')
        self.assertTrue((output / 'packages/org.xingtu.qtifw.trialdata/meta/data-component.js').is_file())
        policy = json.loads((output / 'qtifw-trial-policy.json').read_bytes())
        self.assertEqual(policy['mapPackages'], [])
        self.assertNotIn('_internal/references/base.json', policy['files'])

    def test_upgrade_release_builder_keeps_program_only_projection_and_retired_map_entries(self):
        import xml.etree.ElementTree as ET
        stage, record, source = program_only_stage(self.scope)
        build_record = self.scope / 'freeze-record.json'
        build_record.write_text(json.dumps(record), encoding='utf8')
        output, ifw = self.scope / 'upgrade-release', self.scope / 'ifw'
        ifw.mkdir()
        (ifw / 'installerbase.exe').write_bytes(b'synthetic installer base, never run')
        (ifw / 'devtool.exe').write_bytes(b'synthetic native archive reader, never run')
        def git(*args):
            return source['commit'] if args == ('rev-parse', 'HEAD') else ''
        def external_compiler(command, **kwargs):
            # Only external compilers are replaced. Exact freeze validation,
            # inventory, stage, XML, scripts and bootstrap layout remain real.
            executable = Path(command[0]).name
            if 'PyInstaller' in command:
                helper = output / 'helper/qtifw-trial-helper'
                helper.mkdir(parents=True)
                (helper / 'qtifw-trial-helper.exe').write_bytes(b'synthetic helper')
            elif executable == 'repogen.exe':
                repository = Path(command[-1])
                repository.mkdir()
                (repository / 'Updates.xml').write_text('<Updates />')
            elif executable == 'binarycreator.exe':
                path = Path(kwargs['cwd']) / 'update.rcc' if '-rcc' in command else Path(command[-1])
                path.write_bytes(b'synthetic output, never run')
            else:
                self.fail('unexpected external command: ' + executable)
            return subprocess.CompletedProcess(command, 0)
        args = ['build-qtifw', '--stage', str(stage), '--build-record', str(build_record),
                '--ifw-bin', str(ifw), '--output', str(output), '--release', '--upgrade-capable']
        with patch.object(sys, 'argv', args), patch.object(builder.build, '_git', side_effect=git), \
             patch.object(builder.build, 'source_identity', return_value=source), \
             patch.object(builder.build, 'verify_invoker_manifest', return_value=None), \
             patch.object(builder, 'administrator_installer_base', return_value=ifw / 'installerbase.exe'), \
             patch.object(builder.build, 'verify_administrator_manifest', return_value=1), \
             patch.object(builder.subprocess, 'run', side_effect=external_compiler), redirect_stdout(StringIO()):
            builder.main()
        policy = json.loads((output / 'qtifw-trial-policy.json').read_bytes())
        self.assertTrue(policy['upgradeCapable'])
        self.assertEqual(policy['targetVersion'], record['version'])
        self.assertEqual(policy['packageVersion'], builder.qtifw_component_version(record['version']['version'], release=True))
        self.assertEqual(policy['mapPackages'], [])
        self.assertNotIn('scope', policy)
        self.assertFalse(any('managed-base-resources' in name or name.endswith('/base.json') for name in policy['files']))
        for package in (builder.PACKAGE, builder.DATA_PACKAGE):
            metadata = ET.parse(output / 'packages' / package / 'meta/package.xml').getroot()
            self.assertEqual(metadata.findtext('Version'), policy['packageVersion'])
        component = (output / 'packages' / builder.PACKAGE / 'meta/component.js').read_text(encoding='utf8')
        self.assertNotIn('install-maps', component)
        self.assertIn('TrialUpgradeActive', component)
        self.assertTrue((output / 'upgrade-support/boot/repository/Updates.xml').is_file())
        self.assertTrue((output / 'upgrade-support/boot/update.rcc').is_file())
        self.assertFalse((output / 'upgrade-support/boot/native-setup.exe').exists())
        receipt = json.loads((output / 'qtifw-build-record.json').read_bytes())
        self.assertEqual(receipt['payloadSource'], source['commit'])
        self.assertEqual(receipt['qtifwPackageVersion'], policy['packageVersion'])
        self.assertFalse(receipt['installed'])
        self.assertEqual(receipt['bridgeMode'], 'native-qtifw-offline-maintenance-handoff')

    @unittest.skipUnless(os.name == "nt", "Win32 per-install Run entry")
    def test_uninstall_disables_own_run_entry_even_if_watcher_exe_is_missing(self):
        from game_startup import WATCHER_NAME, MAIN_NAME, set_enabled, enabled
        import game_startup_win32
        prepare(self.policy, self.target)
        program = self.target / "program"
        program.mkdir()
        for name in (WATCHER_NAME, MAIN_NAME):
            (program / name).write_bytes(b"synthetic, never executed")
        set_enabled(program, True)
        try:
            (program / WATCHER_NAME).unlink()
            (self.scope / "qtifw-trial-policy.json").write_text(json.dumps(self.policy))
            # Replace only process/SID discovery. Run registration, the existing
            # control function, and its actual removal are production code.
            windows = SimpleNamespace(interactive_user_matches=lambda: True, watcher_state=lambda path: "absent")
            with patch.object(helper, "__file__", str(self.scope / "helper.py")), \
                    patch.object(sys, "argv", ["helper", "disable-watcher", str(self.target)]), \
                    patch.object(game_startup_win32, "Windows", return_value=windows), redirect_stdout(StringIO()):
                self.assertEqual(helper.main(), 0)
            self.assertFalse(enabled(program))
        finally:
            set_enabled(program, False)

    def test_program_validation_is_read_only_and_rejects_wrong_bytes(self):
        self.seed()
        (self.target / "program/app.exe").write_bytes(b"other")
        internal = self.target / "program/_internal"
        internal.mkdir()
        (internal / "runtime.dll").write_bytes(b"runtime")
        with self.assertRaisesRegex(ValueError, "program-byte-mismatch:app.exe"):
            verify_program(self.policy, self.target)
        self.assertEqual(json.loads((self.target / OWNER).read_text())["phase"], "installing")
        (self.target / "program/app.exe").write_bytes(b"whole")
        self.assertEqual(verify_program(self.policy, self.target), dict(verified=True, files=2))
        self.assertEqual(json.loads((self.target / OWNER).read_text())["phase"], "installing")

    def test_full_uninstall_schedules_native_cleanup_even_with_pending_journal(self):
        work = self.seed()
        packages = work / "map-packages"
        packages.mkdir()
        pending = packages / "pending-base-repair.json"
        pending.write_bytes(b"interrupted resource transaction")
        self.policy["fullUninstall"] = True
        (self.target / "trial-select-data.json").write_text(json.dumps(dict(operation="purge", choice="clear")))
        (self.scope / "qtifw-trial-policy.json").write_text(json.dumps(self.policy))
        with patch.object(helper, "__file__", str(self.scope / "helper.py")), \
                patch.object(sys, "argv", ["helper", "undo-data", str(self.target)]), redirect_stdout(StringIO()):
            self.assertEqual(helper.main(), 0)
        result = json.loads((self.scope / "last-uninstall.json").read_bytes())
        self.assertEqual(result["status"], "scheduled")
        self.assertTrue(result["nativeRemovalScheduled"])
        self.assertEqual(pending.read_bytes(), b"interrupted resource transaction")
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")

    @unittest.skipUnless(os.name == "nt", "Win32 direct native parent validation")
    def test_uninstall_lease_refuses_wrong_parent_before_creating_work(self):
        from qtifw_trial_lease import hold_uninstaller_lease
        prepare(self.policy, self.target)
        (self.target / "trial-maintenance.exe").write_bytes(b"not the live parent executable")
        with self.assertRaisesRegex(ValueError, "parent-image-mismatch"):
            hold_uninstaller_lease(self.target)
        self.assertFalse((self.target / "program").exists())

    def external_target(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.policy["allowNewTargets"] = True
        return Path(temporary.name) / "中文 安装位置" / "星图试用版"

    def test_selected_external_directory_is_read_only_until_real_prepare(self):
        target = self.external_target()
        self.assertTrue(check_target(self.policy, target)["checked"])
        self.assertFalse(target.parent.exists())
        prepare(self.policy, target)
        self.assertEqual(json.loads((target / OWNER).read_text())["target"], str(target))
        for name, data in {"app.exe": b"whole", "_internal/runtime.dll": b"runtime"}.items():
            path = target / "program" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.assertTrue(verify(self.policy, target)["verified"])
        self.assertEqual(clear_data(self.policy, target, "keep")["cleared"], 0)

    def test_files_arriving_after_directory_validation_block_real_prepare(self):
        target = self.external_target()
        check_target(self.policy, target)
        target.mkdir(parents=True)
        personal = target / "personal.txt"
        personal.write_bytes(b"do not overwrite")
        with self.assertRaisesRegex(ValueError, "not-empty"):
            prepare(self.policy, target)
        self.assertFalse((target / OWNER).exists())
        self.assertEqual(personal.read_bytes(), b"do not overwrite")

    def test_owner_cannot_be_copied_to_adopt_another_selected_directory(self):
        target = self.external_target()
        prepare(self.policy, target)
        other = target.parent / "another"
        other.mkdir()
        (other / OWNER).write_bytes((target / OWNER).read_bytes())
        with self.assertRaisesRegex(ValueError, "owner-target-mismatch"):
            prepare(self.policy, other)

    def test_selected_location_inside_existing_installation_is_rejected(self):
        target = self.external_target()
        target.parent.mkdir(parents=True)
        (target.parent / "星图.exe").write_bytes(b"existing application")
        with self.assertRaisesRegex(ValueError, "inside-existing-installation"):
            prepare(self.policy, target)
        self.assertFalse(target.exists())

    def test_broad_system_relative_and_build_directories_cannot_be_selected(self):
        self.external_target()
        for target in (self.scope, self.scope / "config", self.scope.parent,
                       Path(self.scope.anchor), Path("relative-trial")):
            with self.assertRaises(ValueError):
                check_target(self.policy, target)

    def test_interrupted_retry_quarantines_whole_and_torn_files_without_adopting_unknowns(self):
        work = self.seed()
        (self.target / "program/app.exe").write_bytes(b"who")
        internal = self.target / "program/_internal"
        internal.mkdir()
        (internal / "runtime.dll").write_bytes(b"runtime")
        (internal / "unknown-user.txt").write_bytes(b"preserve")
        result = prepare(self.policy, self.target)
        self.assertEqual(set(result["quarantined"]), set(self.policy["files"]))
        preserved = list((self.target / "recovery-files").glob("*/app.exe"))
        self.assertEqual(preserved[0].read_bytes(), b"who")
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
        self.assertEqual((internal / "unknown-user.txt").read_bytes(), b"preserve")
        self.assertEqual(prepare(self.policy, self.target)["quarantined"], [])
        (self.target / "program/app.exe").write_bytes(b"whole")
        (internal / "runtime.dll").write_bytes(b"runtime")
        self.assertEqual(verify(self.policy, self.target), dict(verified=True, files=2))
        with self.assertRaisesRegex(ValueError, "requires-inspection"):
            prepare(self.policy, self.target)

    def test_collision_or_inno_is_rejected_before_owner_or_payload_changes(self):
        program = self.target / "program"
        program.mkdir(parents=True)
        (program / "app.exe").write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError, "unowned-program-collision"):
            prepare(self.policy, self.target)
        self.assertFalse((self.target / OWNER).exists())
        self.assertEqual((program / "app.exe").read_bytes(), b"unknown")
        (self.target / "unins000.exe").write_bytes(b"never executed")
        with self.assertRaisesRegex(ValueError, "legacy-inno"):
            prepare(self.policy, self.target)

    def test_wrong_owner_and_external_scope_are_rejected(self):
        self.seed()
        owner = self.target / OWNER
        data = json.loads(owner.read_text())
        data["instance"] = "foreign"
        owner.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "owner-mismatch"):
            prepare(self.policy, self.target)
        for candidate in (self.scope, self.scope / "targets", self.target / "../../outside"):
            with self.assertRaises(ValueError):
                scoped(self.policy, candidate)

    def test_stale_unpublished_owner_file_cannot_block_repeated_verification(self):
        self.seed()
        stale = self.target / (OWNER + ".new")
        stale.write_bytes(b"interrupted metadata write")
        (self.target / "program/app.exe").write_bytes(b"whole")
        internal = self.target / "program/_internal"
        internal.mkdir()
        (internal / "runtime.dll").write_bytes(b"runtime")
        self.assertTrue(verify(self.policy, self.target)["verified"])
        self.assertTrue(verify(self.policy, self.target)["verified"])
        self.assertEqual(stale.read_bytes(), b"interrupted metadata write")

    @unittest.skipUnless(os.name == "nt", "same-handle Windows deletion")
    def test_default_keep_then_clear_only_work_and_preserve_outside_unknowns(self):
        work = self.seed()
        self.assertEqual(clear_data(self.policy, self.target, "keep")["cleared"], 0)
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
        self.assertEqual(clear_data(self.policy, self.target, "clear")["cleared"], 1)
        self.assertFalse((work / "settings.json").exists())
        self.assertEqual((self.target / "personal.txt").read_bytes(), b"outside declared data scope")
        self.assertTrue((work / "map-packages/import.lock").exists())
        self.assertEqual(clear_data(self.policy, self.target, "clear")["cleared"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows exclusive file lease")
    def test_active_runtime_blocks_clear_without_deleting_data(self):
        work = self.seed()
        code = ("import sys;sys.path.insert(0,sys.argv[1]);from resource_transaction import resource_runtime_lease;"
                "lease=resource_runtime_lease(sys.argv[2]);print('ready',flush=True);sys.stdin.readline();lease.close()")
        process = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "app"), str(self.target / "program")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with self.assertRaisesRegex(ValueError, "resource-busy"):
                clear_data(self.policy, self.target, "clear")
            self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
        finally:
            process.communicate("finish\n", timeout=10)
        self.assertEqual(process.returncode, 0)

    @unittest.skipUnless(os.name == "nt", "Windows cleanup preflight")
    def test_hardlink_rejects_clear_before_deleting_other_files(self):
        work = self.seed()
        os.link(work / "settings.json", self.scope / "external-hardlink")
        with self.assertRaisesRegex(ValueError, "exclusive-file"):
            clear_data(self.policy, self.target, "clear")
        self.assertEqual((self.scope / "external-hardlink").read_bytes(), b"synthetic settings")


if __name__ == "__main__":
    unittest.main()
