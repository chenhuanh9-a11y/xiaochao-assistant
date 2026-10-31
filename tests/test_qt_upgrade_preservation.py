"""Isolated upgrade evidence. Never launches Setup/EXEs or touches the registry.

The optional final-stage test consumes the actual generated file projection and
the existing projection interpreter. Backup/rollback below is a filesystem
simulation of BeginUpgrade/DeinitializeSetup, NOT native Pascal acceptance.
All resource and old-program bytes are synthetic; no author images are copied.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tests'), str(ROOT / 'experiments/qt_quick')]
import test_qt_installer_projection as projection_fixture
import test_map_package_upgrade as package_fixture
import map_package_upgrade as transaction
import star_map_import as importer
import star_map_import_helper as process
import star_map_recovery_helper as helper
from map_package_recovery_plan import inspect_locked


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class FinalStagePreservationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('QT_PRESERVATION_BUILD'), 'final build directory not supplied')
    def test_final_generated_projection_preserves_old_closure_and_failure_restores_program(self):
        build = Path(os.environ['QT_PRESERVATION_BUILD']).resolve()
        record = json.loads((build / 'build-record.json').read_text('utf-8'))
        report = json.loads((build / 'production-file-projection.json').read_text('utf-8'))
        include = build / 'production-files.iss'
        self.assertEqual(digest(include), report['includeSha256'])
        stage = build / 'freeze' / record['product']
        families = report['families']
        ownership = (stage / '_installer-owned-files.txt').read_text('utf-8-sig').splitlines()
        owned_paths = [name.replace('\\', '/') for name in ownership]
        self.assertEqual(set(owned_paths), set(sum(families.values(), [])))
        self.assertEqual(len(owned_paths), len(set(owned_paths)))
        self.assertFalse(any(name == 'work' or name.startswith('work/') for name in owned_paths))
        with tempfile.TemporaryDirectory(prefix='qt-upgrade-preservation-') as folder:
            root = Path(folder) / 'installation'
            # Every bundled base/editor path gets different synthetic old bytes,
            # including metadata, binary structures, image paths and sidecars.
            protected = {name: b'synthetic-old-resource:' + name.encode('utf-8')
                         for name in families['base'] + families['editor']}
            protected.update({
                'work/nightmare-memory/ui-settings.json': b'{"unknown":{"nested":[1,"keep"]},"author":"nightingale"}\n',
                'work/map-packages/nightmare-nightingale.json': b'{"syntheticReceipt":true,"unknown":17}\n',
                'assets/custom-maps/manifest.json': b'{"customGallery":"player","unknown":true}',
                'assets/custom-maps/player/image.png': b'synthetic-player-image',
                '_internal/assets/authors/nightingale/player.png': b'synthetic-author-image',
                '_internal/references/author-packs/nightingale/player-sidecar.json': b'{"synthetic":true}',
                '_internal/references/difficult-native/player-metadata.json': b'{"syntheticDifficultClosure":true}',
            })
            for name, data in protected.items():
                write(root, name, data)
            # One new owned file is absent, exercising rollback deletion as well
            # as restoration. Old retired program and uninstaller logs are real
            # filesystem objects containing deliberately non-executable bytes.
            new_only = next(name for name in families['program'] if name.endswith('.pyd'))
            for name in families['program']:
                if name != new_only:
                    write(root, name, b'old-program:' + name.encode('utf-8'))
            retired = '_internal/retired-old-module.py'
            uninstaller = ['unins000.exe', 'unins000.dat', 'unins000.msg']
            for name in [retired, *uninstaller]:
                write(root, name, b'old-owned:' + name.encode())
            write(root, '_installer-owned-files.txt', ('\r\n'.join(
                name.replace('/', '\\') for name in [*families['program'], retired, *protected]) + '\r\n').encode('utf-8'))
            before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            selected_owned = [name for name in owned_paths if name in families['program']]
            affected = list(dict.fromkeys([*selected_owned, retired, *uninstaller]))
            backup = root / 'work/installer-backups/synthetic-failure'
            existing = [name for name in affected if (root / name).is_file()]
            for index, name in enumerate(existing):
                write(backup, f'files/{index}', (root / name).read_bytes())
            write(backup, 'affected.txt', '\n'.join(affected).encode())
            write(backup, 'existing.txt', '\n'.join(existing).encode())
            write(backup, 'ui-settings.json', protected['work/nightmare-memory/ui-settings.json'])
            (root / retired).unlink()
            interpreter = projection_fixture.InstallerProjectionTests()
            interpreter.include, interpreter.stage = include, stage
            copied = interpreter.apply_generated_projection(root)
            self.assertEqual(set(copied), set(families['program']))
            for name in copied:
                self.assertEqual(digest(root / name), digest(stage / name), name)
            for name, data in protected.items():
                self.assertEqual((root / name).read_bytes(), data, name)
            self.assertFalse((root / retired).exists())
            # Simulated failure before ssPostInstall: exact affected-list
            # restoration, preserving the original numbered backup witnesses.
            for name in affected:
                target = root / name
                if name in existing:
                    shutil.copy2(backup / 'files' / str(existing.index(name)), target)
                elif target.exists():
                    target.unlink()
            after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
                     if p.is_file() and not p.is_relative_to(backup)}
            self.assertEqual(after, before)
            self.assertEqual((backup / 'ui-settings.json').read_bytes(), protected['work/nightmare-memory/ui-settings.json'])
            print(json.dumps(dict(scope='filesystem-simulation-only', sourceCommit=record['source']['commit'],
                                  copiedProgramFiles=len(copied), preservedSyntheticFiles=len(protected),
                                  backedUpFiles=len(existing), includeSha256=digest(include))))


class RealTransactionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = package_fixture.UpgradeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        # Explicit boundary: native process inspection/parent waiting untested.
        self.enterContext(patch.object(importer, '_check_closed'))
        self.fixture.installed_old()
        self.extra = {'work/nightmare-memory/ui-settings.json': b'{"unknown":"keep"}',
                      'assets/custom-maps/player.png': b'synthetic-custom-image'}
        for name, data in self.extra.items():
            write(self.root, name, data)

    def request(self, plan, action):
        with patch.object(process, '_launch_parent_bound_helper', return_value=Mock()) as launch:
            helper.launch_recovery_helper(self.root / 'star-map-recovery-helper.exe',
                                          self.root, plan, action, 'isolated-recovery')
        args = launch.call_args.args[3]
        return dict(request_file=args[1], request_sha256=args[3], install_root=self.root,
                    request_id='isolated-recovery', parent_pid=42, parent_created='1234')

    def run_request(self, request):
        with patch.object(process, 'wait_for_application_exit', return_value='synthetic-parent-exit'):
            return helper.run_helper(**request)

    def interrupted_plan(self):
        actual_link = transaction.os.link
        def interrupt(source, target, *args, **kwargs):
            if Path(target) == self.root / package_fixture.ADDED_PATH:
                raise KeyboardInterrupt('synthetic interruption after replacement')
            return actual_link(source, target, *args, **kwargs)
        with patch.object(transaction.os, 'link', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                importer.install_package(self.fixture.new, self.root, application_stopped=True)
        with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-recovery-required'):
            importer.inspect_package(self.fixture.new, self.root)
        with importer._writer_locked_installation(self.root, require_closed=False):
            return inspect_locked(self.root)

    def test_interrupted_transaction_helper_receipt_and_qt_rescan(self):
        plan = self.interrupted_plan()
        code, result = self.run_request(self.request(plan, 'recover'))
        self.assertEqual(code, 0, result)
        self.fixture.assert_old()
        self.assertEqual(result['planSha256'], plan['planSha256'])
        self.assertEqual(result['transactionId'], plan['transactionId'])
        self.assertEqual(json.loads(helper.receipt_path(self.root).read_text('utf-8')), result)
        for name, data in self.extra.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        # Next-launch Qt adapter: receipt acknowledgement does not invalidate
        # consumers until the actual threaded resource scan has completed.
        from PySide6.QtCore import QCoreApplication
        from client_resources import ResourceService
        app = QCoreApplication.instance() or QCoreApplication([])
        # Service's isolation guard requires a marked path under this repo/work.
        (ROOT / 'work').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='qt-recovery-rescan-', dir=ROOT / 'work') as folder:
            clone = Path(folder) / 'installation'
            shutil.copytree(self.root, clone)
            write(clone, '.qt-lab-owner', b'qt-quick-isolated-v1')
            service = ResourceService(clone)
            changes = []
            service.resourcesChanged.connect(lambda: changes.append(True))
            try:
                service.recordRecoveryReceipt(result)
                self.assertEqual(changes, [])
                service.refresh()
                deadline = time.monotonic() + 15
                while service.busy and time.monotonic() < deadline:
                    app.processEvents()
                    service.pool.waitForDone(10)
                app.processEvents()
                self.assertFalse(service.busy, service.message)
                self.assertEqual(changes, [True])
                self.assertFalse(service.recoveryRequired)
                row = next(row for row in service.packageRows if row['packageId'] == 'difficult-zhan10')
                self.assertEqual(row['status'], 'installed', row)
            finally:
                service.shutdown()
                service.pool.waitForDone(15000)
                app.processEvents()

    def test_changed_target_rejects_confirmed_plan_and_preserves_conflict_and_backup(self):
        plan = self.interrupted_plan()
        request = self.request(plan, 'recover')
        custom = self.fixture.folder / 'player-edit'
        custom.write_bytes(b'player-custom-after-interruption')
        custom.replace(self.root / package_fixture.OLD_PATH)
        backup_root = self.root / 'work/map-packages/upgrades' / plan['transactionId']
        witnesses = {p: digest(p) for p in backup_root.rglob('*') if p.is_file()}
        code, result = self.run_request(request)
        self.assertEqual(code, 2)
        self.assertIn('recovery-plan-changed', result['reason'])
        self.assertEqual((self.root / package_fixture.OLD_PATH).read_bytes(), b'player-custom-after-interruption')
        self.assertTrue((self.root / transaction.PENDING).exists())
        for path, sha in witnesses.items():
            self.assertEqual(digest(path), sha, str(path))


if __name__ == '__main__':
    unittest.main()
