"""QtIFW retirement against real temporary Windows objects.

The native .dat ownership reader, registry and borrowed Setup lease are narrow
fixtures here. Source inventory, Windows handles, copy/commit, retirement
deletion, journal state, and final byte checks use the production paths.
No historical program or newly installed program is executed.
"""
from contextlib import nullcontext
from pathlib import Path
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import installer_legacy_handoff as handoff
import installer_legacy_retirement as retirement_module
import installer_native_observation as native
import installer_uninstall as uninstall
import legacy_migration as migration
import star_map_reset_helper as helper
from installer_legacy_assets import CompleteAssetTransfer
from installer_legacy_retirement import LegacyRetirement
from installer_upgrade_journal import PENDING
import test_installer_legacy_bridge as bridge_fixtures
import test_installer_legacy_handoff as handoff_fixtures
import test_installer_qtifw_inventory as qtifw_fixtures
from test_installer_native_observation import SID, link_bytes


class NarrowRegistration:
    """The only registration substitute: exact record presence/removal."""
    def __init__(self, row):
        self.row = copy.deepcopy(row)
        self.present = True
        self.removals = 0

    def verify(self, expected, *, missing=False):
        if expected != self.row:
            raise ValueError('fixture-registration-changed')
        if not self.present and not missing:
            raise ValueError('fixture-registration-missing')
        return self.present

    def remove(self, expected):
        if expected != self.row:
            raise ValueError('fixture-registration-changed')
        self.removals += 1
        self.present = False


class NativeOwnershipFixture:
    """A deliberately narrow stand-in for QtIFW .dat ownership parsing."""
    def __init__(self, source, shortcuts=None):
        self.shortcuts = dict(shortcuts or {})
        self.files = {str(path): migration._file(path)
            for path in sorted(Path(source).rglob('*')) if path.is_file()}

    def __call__(self, source, program, identity):
        if Path(source) != Path(program) or identity.get('sourceKind') != 'qtifw':
            return dict(supported=False, reason='fixture-layout-mismatch')
        return dict(supported=True, format='qtifw-native-fixture-v1', files=copy.deepcopy(self.files),
                    shortcuts=self.shortcuts)


@unittest.skipUnless(os.name == 'nt', 'retirement handles require Windows')
class LegacyRetirementTests(unittest.TestCase):
    """Retirement uses native handles on temporary trees and real byte checks."""
    write = bridge_fixtures.LegacyBridgeTests.write
    rows = bridge_fixtures.LegacyBridgeTests.rows
    snapshot = bridge_fixtures.LegacyBridgeTests.snapshot
    install_native_fixture = handoff_fixtures.HandoffTests.install_native_fixture
    command = handoff_fixtures.HandoffTests.command

    def setUp(self):
        # Win32 handle tests need their full ancestor chain readable. Keep all
        # temporary roots under this authorized workspace, not the user Temp.
        workspace = Path(__file__).resolve().parents[1]
        container = tempfile.TemporaryDirectory(dir=workspace)
        self.addCleanup(container.cleanup)
        with patch.object(tempfile, 'tempdir', container.name):
            handoff_fixtures.HandoffTests.setUp(self)
        self.external = self.root / 'local/Xingtu/resources'
        self.assets = CompleteAssetTransfer(external_root=self.external,
            registry_reader=self.rows,
            production_roots=lambda: {str(self.source), str(self.target)})
        self.registration = NarrowRegistration(self.row)
        self.locations = self.backend.shortcut_locations()
        # Model historical shortcuts. Their link bytes and target parsing are
        # real; only the OS registration store is represented by a fixture.
        self.write('星图.exe', 'synthetic old executable; never execute')
        self.old_links = {
            str(Path(self.locations['desktop']) / '星图.lnk'): self.source / '星图.exe',
            str(Path(self.locations['programs']) / '星图/星图.lnk'): self.source / '星图.exe',
            str(Path(self.locations['programs']) / '星图/卸载星图.lnk'): self.source / 'MaintenanceTool.exe',
        }
        for name, target in self.old_links.items():
            link = Path(name)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.write_bytes(link_bytes(str(target)))
        self.inventory_fixture = NativeOwnershipFixture(self.source, {
            path: str(target) for path, target in self.old_links.items()})
        self.retirement = LegacyRetirement(registration=self.registration,
            locations=self.locations, inventory=self.inventory_fixture)

    def rows(self, _source):
        if getattr(self, 'registration', None) is not None and not self.registration.present:
            return []
        return [copy.deepcopy(self.row)]

    def _capture(self):
        identity, _ = handoff.bridge.probe_source(self.source, registry_reader=self.rows)
        manifest = dict(files=migration._inventory(self.source))
        with self._with_fixture_sid():
            plan = self.retirement.capture(self.source, identity, manifest,
                owner_sid=SID, deadline=time.monotonic() + 120,
                registry_reader=self.rows, external_root=self.external)
        return identity, manifest, plan

    def _execute(self, plan, *, verify_current=None, native_shortcuts=()):
        target = self.root / 'verified-new-install'
        target.mkdir(exist_ok=True)
        history = target / 'work/installer-upgrades' / self.args.session_id
        history.mkdir(parents=True, exist_ok=True)
        new_bytes = b'new program marker; not executed'
        (target / 'StarMap.exe').write_bytes(new_bytes)

        def real_final_verification():
            current = target / 'StarMap.exe'
            if migration._file(current) != dict(bytes=len(new_bytes),
                    sha256=hashlib.sha256(new_bytes).hexdigest()):
                raise AssertionError('new native payload changed')
            if verify_current is not None:
                verify_current()

        return self.retirement.execute(plan, target, history, owner_sid=SID,
            deadline=time.monotonic() + 120,
            verify_current=real_final_verification,
            registrations=lambda: [str(self.source), str(target)],
            native_shortcuts=list(native_shortcuts))

    def _with_fixture_sid(self):
        return patch.object(uninstall, '_owner_sid', return_value=SID)

    def test_real_tree_and_shortcuts_are_removed_after_owned_capture(self):
        _, _, plan = self._capture()
        self.assertTrue(plan['supported'])
        self.assertEqual(plan['format'], 'qtifw-native-fixture-v1')
        self.assertEqual(set(plan['shortcuts']), set(self.old_links))

        with self._with_fixture_sid():
            self.assertTrue(self._execute(plan))

        self.assertFalse(self.source.exists())
        self.assertFalse(self.registration.present)
        self.assertEqual(self.registration.removals, 1)
        self.assertTrue(all(not Path(path).exists() for path in self.old_links))

    def test_unowned_file_after_capture_blocks_before_intent_or_removal(self):
        _, _, plan = self._capture()
        extra = self.source / 'user-added-after-capture.bin'
        extra.write_bytes(b'preserve this')
        original = self.snapshot()

        with self._with_fixture_sid(), self.assertRaises(ValueError):
            self._execute(plan)

        self.assertEqual(self.snapshot(), original)
        self.assertTrue(self.registration.present)
        self.assertFalse(any(Path(self.root / 'verified-new-install').glob(
            'work/installer-upgrades/*/retirement-intent.json')))

    def test_lost_per_object_receipt_recovers_from_actual_absence(self):
        _, _, plan = self._capture()
        real_remove = retirement_module.WindowsObjects.remove
        removals = []

        def remove_then_lose_receipt(objects, path, stamp):
            real_remove(objects, path, stamp)
            removals.append(str(path))
            if len(removals) == 1:
                raise OSError('synthetic process loss after real deletion')

        with self._with_fixture_sid(), patch.object(
                retirement_module.WindowsObjects, 'remove', remove_then_lose_receipt):
            with self.assertRaisesRegex(OSError, 'synthetic process loss'):
                self._execute(plan)

        intent_files = list((self.root / 'verified-new-install').glob(
            'work/installer-upgrades/*/retirement-intent.json'))
        self.assertEqual(len(intent_files), 1)
        self.assertTrue(removals)
        with self._with_fixture_sid():
            self.assertTrue(self._execute(plan))
        self.assertFalse(self.source.exists())
        self.assertFalse(self.registration.present)

    def test_equal_byte_replacement_is_not_adopted_for_deletion(self):
        _, _, plan = self._capture()
        original = self.source / 'work/user-routes/edited.json'
        content = original.read_bytes()
        retained = original.with_suffix('.retained')
        original.rename(retained)
        original.write_bytes(content)

        with self._with_fixture_sid(), self.assertRaises(ValueError):
            self._execute(plan)

        self.assertTrue(original.exists())
        self.assertTrue(retained.exists())
        self.assertTrue(self.registration.present)

    def test_retained_new_native_shortcut_is_not_deleted(self):
        _, _, plan = self._capture()
        group = str(Path(self.locations['programs']) / '星图/星图.lnk')
        link = Path(group)
        link.write_bytes(link_bytes(str(self.root / 'verified-new-install/StarMap.exe')))

        with self._with_fixture_sid():
            self.assertTrue(self._execute(plan, native_shortcuts=[dict(path=group, enabled=True)]))

        self.assertTrue(link.exists())
        self.assertTrue(self.source.exists() is False)

    def test_unrecorded_old_shortcut_is_rejected_at_prepare(self):
        path = next(iter(self.old_links))
        self.inventory_fixture.shortcuts.pop(path)
        with self._with_fixture_sid(), self.assertRaisesRegex(ValueError,
                'shortcut-not-in-native-inventory'):
            self._capture()
        self.assertTrue(Path(path).exists())

    def test_shortcut_target_read_is_pinned_against_replacement_race(self):
        path = next(iter(self.old_links))
        original = Path(path).read_bytes()
        real_target = native._shortcut_target
        attempts = []

        def race_after_pin(data):
            with self.assertRaises(OSError):
                Path(path).write_bytes(link_bytes(str(self.root / 'attacker.exe')))
            attempts.append(True)
            return real_target(data)

        with self._with_fixture_sid(), patch.object(native, '_shortcut_target', side_effect=race_after_pin):
            self._capture()
        self.assertTrue(attempts)
        self.assertEqual(Path(path).read_bytes(), original)

    def test_read_only_backend_reads_while_protected_and_external_writer_stays_denied(self):
        protected = self.root / 'protected-current.exe'
        content = b'protected new native image; never executed'
        protected.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        objects = retirement_module.WindowsObjects(SID, time.monotonic() + 120)
        backend = native.WindowsReadOnlyBackend()
        with self._with_fixture_sid(), objects:
            objects.add(protected)
            with native.pinned_read_sharing(objects.permits_read), backend.read_session():
                row = backend.file(str(protected), required=True)
                self.assertEqual(row['sha256'], expected)
                self.assertEqual(row['bytes'], len(content))
                with self.assertRaises(OSError):
                    protected.write_bytes(b'external writer must be denied')
        protected.write_bytes(b'writer is allowed after the protected lifetime')
        self.assertEqual(protected.read_bytes(), b'writer is allowed after the protected lifetime')

    def test_retirement_capture_rejects_unowned_hard_link(self):
        self.write('linked-user-data.bin', 'content')
        original = self.source / 'linked-user-data.bin'
        os.link(original, self.source / 'linked-user-data-copy.bin')
        with self.assertRaises(ValueError):
            self._capture()
        self.assertTrue(original.exists())
        self.assertTrue((self.source / 'linked-user-data-copy.bin').exists())

    def _make_parser_owned_qtifw_layout(self):
        def canonical(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True,
                separators=(',', ':')).encode('utf-8')

        program = self.source / 'program'
        program.mkdir()
        for path in list(self.source.iterdir()):
            if path == program:
                continue
            if path.name in {'MaintenanceTool.exe', 'MaintenanceTool.dat', 'components.xml'}:
                continue
            path.rename(program / path.name)
        (self.source / 'MaintenanceTool.exe').unlink()
        (self.source / 'MaintenanceTool.dat').unlink()
        tool = self.source / 'trial-maintenance.exe'
        tool.write_bytes(b'historical QtIFW maintenance image; never execute')
        self.row['values']['UninstallString'] = [1, '"' + str(tool) + '"']
        self.registration = NarrowRegistration(self.row)
        self.write('program/星图.exe', 'synthetic old executable; never execute')
        profile_path = program / '_internal/installation-profile.json'
        profile = json.loads(profile_path.read_text(encoding='utf-8'))
        profile['sourceCommit'] = self.identity['sourceCommit']
        profile_path.write_bytes(canonical(profile))

        payload_names = ('星图.exe', 'release-report.json', '_installer-owned-files.txt',
                         '_internal/installation-profile.json', '_internal/alias.dll')
        source_report = dict(sourceCommit=self.identity['sourceCommit'],
            executableSha256=migration._file(program / '星图.exe')['sha256'])
        (program / 'release-report.json').write_bytes(canonical(source_report))
        self.write('program/_internal/alias.dll', 'recorded copy destination')
        ownership = ''.join(name + '\n' for name in payload_names).encode('utf-8')
        (program / '_installer-owned-files.txt').write_bytes(ownership)
        payload_rows = {name: migration._file(program / name) for name in payload_names}
        instance = 'retirement-fixture-' + self.args.session_id
        policy = dict(portable=True, faultsAllowed=False, fullUninstall=True,
            allowNewTargets=True, instance=instance, payloadSource=self.identity['sourceCommit'],
            files=payload_rows)
        support = self.source / '_installer-support'
        (support / '_internal').mkdir(parents=True)
        (support / 'qtifw-trial-helper.exe').write_bytes(b'synthetic support helper; never execute')
        (support / '_internal/support.dll').write_bytes(b'synthetic support library')
        (support / '_internal/qtifw-trial-policy.json').write_bytes(canonical(policy))
        owner = dict(phase='complete', instance=instance,
            payloadSource=self.identity['sourceCommit'], target=str(self.source))
        (self.source / '.qtifw-trial-owner.json').write_bytes(canonical(owner))

        package_nodes = ''.join('<Package><Name>' + name + '</Name><Version>1.0.0</Version></Package>' for name in
            (qtifw_fixtures.PROGRAM, qtifw_fixtures.DATA))
        (self.source / 'components.xml').write_text('<Packages>' + package_nodes + '</Packages>',
            encoding='utf-8')
        index = self.source / 'installerResources' / qtifw_fixtures.PROGRAM / '1.0.0data.txt'
        index.parent.mkdir(parents=True)
        extracted = [program, program / '_internal']
        extracted.extend(program / name for name in payload_names if name != '_internal/alias.dll')
        index.write_bytes(qtifw_fixtures.qstring_list([
            qtifw_fixtures.ROOT + '/' + path.relative_to(self.source).as_posix()
            for path in extracted]))
        outside = self.root / 'fixture-external'
        operations = [
            qtifw_fixtures.operation('Extract', [
                'installer://' + qtifw_fixtures.PROGRAM + '/1.0.0data.7z', qtifw_fixtures.ROOT],
                qtifw_fixtures.PROGRAM, files=('QString', qtifw_fixtures.ROOT +
                    '/installerResources/' + qtifw_fixtures.PROGRAM + '/1.0.0data.txt')),
            qtifw_fixtures.operation('Mkdir', [qtifw_fixtures.ROOT + '/_installer-support'],
                qtifw_fixtures.DATA),
            qtifw_fixtures.operation('Copy', [str(outside / 'qtifw-trial-helper.exe'),
                qtifw_fixtures.ROOT + '/_installer-support/qtifw-trial-helper.exe'], qtifw_fixtures.DATA),
            qtifw_fixtures.operation('Copy', [str(outside / 'support.dll'),
                qtifw_fixtures.ROOT + '/_installer-support/_internal/support.dll'], qtifw_fixtures.DATA),
            qtifw_fixtures.operation('Copy', [str(outside / 'qtifw-trial-policy.json'),
                qtifw_fixtures.ROOT + '/_installer-support/_internal/qtifw-trial-policy.json'], qtifw_fixtures.DATA),
            qtifw_fixtures.operation('Copy', [qtifw_fixtures.ROOT +
                '/_installer-support/_internal/support.dll', qtifw_fixtures.ROOT +
                '/program/_internal/alias.dll'], qtifw_fixtures.PROGRAM),
            qtifw_fixtures.operation('CreateShortcut', [qtifw_fixtures.ROOT +
                '/program/星图.exe', str(Path(self.locations['desktop']) / '星图.lnk')],
                qtifw_fixtures.PROGRAM),
            qtifw_fixtures.operation('CreateShortcut', [qtifw_fixtures.ROOT +
                '/program/星图.exe', str(Path(self.locations['programs']) / '星图/星图.lnk')],
                qtifw_fixtures.PROGRAM),
            qtifw_fixtures.operation('CreateShortcut', [qtifw_fixtures.ROOT +
                '/trial-maintenance.exe', str(Path(self.locations['programs']) / '星图/卸载星图.lnk')],
                qtifw_fixtures.PROGRAM),
            qtifw_fixtures.operation('Execute', ['{0}', str(outside / 'qtifw-trial-helper.exe'),
                'noop', '@TargetDir@', 'UNDOEXECUTE',
                '@TargetDir@/_installer-support/qtifw-trial-helper.exe', 'undo-data', '@TargetDir@'],
                qtifw_fixtures.DATA),
            qtifw_fixtures.operation('Execute', ['{0}', str(outside / 'qtifw-trial-helper.exe'),
                'verify-program', '@TargetDir@'], qtifw_fixtures.PROGRAM),
            qtifw_fixtures.operation('MinimumProgress', [], qtifw_fixtures.PROGRAM),
        ]
        (self.source / 'trial-maintenance.dat').write_bytes(qtifw_fixtures.dat_file(operations))
        for name, target in self.old_links.items():
            actual = program / '星图.exe' if target.name == '星图.exe' else tool
            Path(name).write_bytes(link_bytes(str(actual)))
        self.retirement = LegacyRetirement(registration=self.registration,
            locations=self.locations)

    def test_real_qtifw_dat_helper_blocks_early_retire_and_recovers_lost_response(self):
        # PR155 requires a live parent maintenance object before any action.
        # This narrow parent fixture uses a real, unique Windows mutex and
        # never creates or borrows the installed product's maintenance name.
        from test_installer_uninstall_recovery import InstallerUninstallRecoveryTests
        maintenance = InstallerUninstallRecoveryTests('runTest')
        maintenance.setUp()
        self.addCleanup(maintenance.doCleanups)
        self._make_parser_owned_qtifw_layout()
        from installer_qtifw_inventory import read_operations
        parsed = read_operations(self.source / 'trial-maintenance.dat', self.source)
        self.assertEqual(parsed.components, {qtifw_fixtures.PROGRAM, qtifw_fixtures.DATA})
        self.assertIn(self.source / 'program/星图.exe', parsed.files)

        real_handoff = handoff.LegacyHandoff
        original_json = helper._json

        def packaged_json(path):
            if Path(path).name == 'installation-profile.json':
                return dict(self.profile, sourceCommit=self.identity['sourceCommit'])
            return original_json(path)

        def factory(args, target, lease, owner):
            return real_handoff(args, target, lease, owner, assets=self.assets,
                observer=self.observer, registry_reader=self.rows,
                scope=lambda *_args, **_kwargs: nullcontext(), retirement=self.retirement)

        actions = ('prepare', 'retire-early', 'start-native', 'native-complete',
                   'commit', 'retire-unpublished-object', 'retire-lost-response', 'resume')
        before_source = self.snapshot()
        with patch.object(helper, '_json', side_effect=packaged_json), \
             patch.object(helper, 'borrowed_installer_lease', return_value=nullcontext(self.lease)), \
             patch.object(native, 'current_user_sid', return_value=SID), \
             patch.object(uninstall, '_owner_sid', return_value=SID), \
             patch.object(uninstall, '_current_user_sid', return_value=SID), \
             patch.object(uninstall, '_baked_profile', return_value=self.profile), \
             patch.object(handoff, 'LegacyHandoff', side_effect=factory):
            for index, action in enumerate(actions):
                if action == 'native-complete':
                    self.install_native_fixture()
                route_action = ('retire' if action in {'retire-early', 'retire-unpublished-object',
                                                       'retire-lost-response'} else action)
                self.args.action = 'legacy-' + route_action
                self.args.result_directory = str(self.root / ('helper-retirement-' + str(index)))
                Path(self.args.result_directory).mkdir(exist_ok=True)
                if action == 'retire-unpublished-object':
                    published = self.target / 'work/user-routes/edited.json'
                    retained = published.with_suffix('.temporarily-unpublished')
                    published.rename(retained)
                    code = helper.main(self.command(), maintenance_provider=maintenance._maintenance_provider)
                    self.assertEqual(code, 2)
                    self.assertEqual(self.snapshot(), before_source)
                    self.assertTrue(self.registration.present)
                    retained.rename(published)
                elif action == 'retire-lost-response':
                    real_retire = self.retirement.execute

                    def finish_then_lose_receipt(*args, **kwargs):
                        real_retire(*args, **kwargs)
                        raise OSError('synthetic lost retirement response')

                    with patch.object(self.retirement, 'execute', side_effect=finish_then_lose_receipt):
                        code = helper.main(self.command(), maintenance_provider=maintenance._maintenance_provider)
                    self.assertEqual(code, 2)
                    self.assertFalse(self.source.exists())
                    self.assertFalse(self.registration.present)
                else:
                    code = helper.main(self.command(), maintenance_provider=maintenance._maintenance_provider)
                detail = Path(self.args.result_directory, 'error.txt')
                if action == 'retire-early':
                    self.assertEqual(code, 2)
                    self.assertEqual(self.snapshot(), before_source)
                    self.assertTrue(self.registration.present)
                    self.assertTrue((self.target / PENDING).is_file())
                    self.assertTrue((self.target / '.legacy-upgrade-prepared').is_dir())
                elif action not in {'retire-unpublished-object', 'retire-lost-response'}:
                    self.assertEqual(code, 0, 'helper action failed: ' + action + ': ' +
                        (detail.read_text(encoding='utf-8-sig') if detail.exists() else 'no error detail'))
                import configparser
                receipt = configparser.ConfigParser()
                receipt.read(Path(self.args.result_directory) / 'result.ini', encoding='utf-16')
                if receipt.has_option('result', 'planSha256'):
                    self.args.plan_sha256 = receipt['result']['planSha256']

        self.assertEqual(receipt['result']['upgradeComplete'], 'True')
        self.assertEqual(receipt['result']['oldEntryRetired'], 'True')
        self.assertFalse(self.source.exists())
        self.assertFalse(self.registration.present)
        self.assertFalse((self.target / PENDING).exists())
        self.assertFalse((self.target / '.legacy-upgrade-prepared').exists())
        self.assertFalse((self.target / '.legacy-upgrade-intent.json').exists())
        self.assertTrue((self.target / 'work/installer-upgrades/legacy-recovery.json').is_file())


if __name__ == '__main__':
    unittest.main()
