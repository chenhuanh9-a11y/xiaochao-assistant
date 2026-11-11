"""Inactive builder gates and generated projection, synthetic bytes only.

No build, Setup, installation or application launch. The positive fixture uses
real temporary Git identities, real two-mode inventory parsing and real
managed-base repair, with synthetic S0/package and typed-provider stand-ins.
"""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_qt_client as build
import managed_base_resources as managed
from s1_resource_retirement import SOURCE_VERSION, TARGET_VERSION, projected_release
import s1_candidate_purpose as purpose
import test_s1_private_packaging as private_fixture
import test_qt_installer_projection as projection_fixture


class ProductionS1BuilderTests(unittest.TestCase):
    def setUp(self):
        # The synthetic source lock is disabled unless a case explicitly enables it.
        self.enterContext(patch.object(managed, "_REVIEWED_S1_BASE_RELEASES", ()))
        self.enterContext(patch.object(purpose, "_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED", False))
        temporary = tempfile.TemporaryDirectory(prefix='production-s1-builder-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.resources = self.root / 'resources'; self.resources.mkdir()
        fixture = private_fixture.PrivatePackagingTests()
        fixture.root, fixture.resources = self.root, self.resources
        self.registry = fixture.packs()
        # Preserve all three source modes before making the production projection.
        # These are synthetic bytes, not newly approved real production geometry.
        self.source_s1 = {p.relative_to(self.resources).as_posix():
            dict(bytes=p.stat().st_size, sha256=build.sha256(p))
            for p in (self.resources / 'seasons/s1').rglob('*') if p.is_file()}
        shutil.rmtree(self.resources / "seasons/s1/difficulty2")
        self.inventory = fixture.inventory()
        self.declaration = json.loads(self.inventory.read_text())
        self.declaration['resourceVersion'] = TARGET_VERSION
        self.inventory.write_text(json.dumps(self.declaration))
        self.rows = self.declaration['files']
        self.s1 = {name: row for name, row in self.rows.items() if name.startswith('seasons/')}
        self.source_root = self.root / 'source'; self.source_root.mkdir()
        self.lock_path = self.source_root / build.S1_PRODUCTION_LOCK
        self.lock_path.parent.mkdir()
        self.lock = dict(schema='xingtu-s1-production-release-lock-v1', enabled=False, release=None)
        self.save_lock()
        (self.source_root / 'version.json').write_text('{"version":"fixture-only"}')
        (self.source_root / 'tools/build_qt_client.py').write_text('# synthetic tracked builder, never executed\n')
        (self.source_root / 'payload.py').write_text('# synthetic release payload\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.commit()
        self.patch(build, 'ROOT', self.source_root)
        self.source = build.source_identity(self.git('rev-parse', 'HEAD'))
        self.fingerprint = build.source_payload_fingerprint(self.source)
        self.managed_lock = (SOURCE_VERSION, tuple(('_internal/' + name, row['bytes'], row['sha256'])
                                              for name, row in sorted(self.source_s1.items())))

    def patch(self, target, name, value):
        item = patch.object(target, name, value)
        item.start(); self.addCleanup(item.stop)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.source_root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Synthetic fixture only')

    def save_lock(self):
        self.lock_path.write_text(json.dumps(self.lock, sort_keys=True))

    def enable_fixture(self, *, managed_policy=True):
        self.lock.update(enabled=True, release=dict(resourceVersion=TARGET_VERSION,
            sourcePayloadSha256=self.fingerprint,
            versionSha256=build.sha256(self.source_root / 'version.json'), files=self.s1))
        # Source-owned published evidence is synthetic here, never cloud history.
        s0 = {'_internal/' + name: row for name, row in self.rows.items()
              if not name.startswith('seasons/') and build.resource_file_family('_internal/' + name) == 'base'}
        manifest = managed.manifest_for_rows([dict(path=name, **row) for name, row in s0.items()], 'published-fixture-s0')
        raw = json.dumps(manifest, sort_keys=True)
        self.lock['release'].update(s0BaseFiles=s0, s0Predecessors=[dict(sourceCommit='1' * 40,
            setupSha256='2' * 64, manifestSha256=build.hashlib.sha256(raw.encode()).hexdigest(), manifestUtf8=raw)])
        self.save_lock(); self.commit()
        self.source = build.source_identity(self.git('rev-parse', 'HEAD'))
        if managed_policy:
            self.patch(managed, '_REVIEWED_S1_BASE_RELEASES', (self.managed_lock,))

    def check(self, rows=None, version=TARGET_VERSION, source=None):
        return build.production_s1_release(self.rows if rows is None else rows, version,
                                           source=self.source if source is None else source)

    def validate(self):
        with patch('client_providers.build_engine_factories', return_value=self.registry), \
                patch.object(build, 'check_package', return_value=dict(success=True)):
            return build.validate_resources(self.resources, self.inventory, instance='production', source=self.source)

    def test_disabled_fixture_lock_rejects_season_inventory(self):
        actual = json.loads((Path(__file__).resolve().parents[1] / build.S1_PRODUCTION_LOCK).read_text())
        self.assertEqual(actual['schema'], 'xingtu-s1-production-release-lock-v1')
        self.assertIs(type(actual['enabled']), bool)
        self.assertEqual(managed._REVIEWED_S1_BASE_RELEASES, ())
        self.assertIs(purpose._PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED, False)
        with self.assertRaisesRegex(ValueError, 'production S1 disabled'):
            self.check()

    def test_forged_input_profile_settings_environment_do_not_enable_source_gate(self):
        self.declaration.update(productionS1Enabled=True, sourcePayloadSha256=self.fingerprint,
                                reviewed=True, liveReady=True, release=dict(files=self.rows))
        self.inventory.write_text(json.dumps(self.declaration))
        for name in ('installation-profile.json', 'settings.json'):
            (self.root / name).write_text(json.dumps(self.declaration))
        with patch.dict(os.environ, {'XINGTU_PRODUCTION_S1_RESOURCES': '1',
                                      'XINGTU_S1_PRODUCTION_LOCK': str(self.inventory)}):
            with self.assertRaisesRegex(ValueError, 'production S1 disabled'):
                self.validate()
        self.assertIs(purpose._PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED, False)

    def test_incomplete_forged_mismatched_and_retired_inventories_are_rejected(self):
        self.enable_fixture()
        missing = dict(self.rows); missing.pop(next(iter(self.s1)))
        extra = dict(self.rows, **{'seasons/s1/nightmare/references/extra.json': dict(bytes=0, sha256='a'*64)})
        changed = copy.deepcopy(self.rows); changed[next(iter(self.s1))]['sha256'] = 'a'*64
        wrong_size = copy.deepcopy(self.rows); wrong_size[next(iter(self.s1))]['bytes'] += 1
        mixed = copy.deepcopy(self.rows); mixed[next(iter(self.s1))]['sha256'] = 'b'*64
        empty = {name: row for name, row in self.rows.items() if not name.startswith('seasons/')}
        for rows, version in ((missing, TARGET_VERSION), (extra, TARGET_VERSION), (changed, TARGET_VERSION),
                              (wrong_size, TARGET_VERSION), (mixed, TARGET_VERSION), (empty, TARGET_VERSION),
                              (self.rows, 'retired-private'), (self.rows, ''), (self.rows, None)):
            with self.subTest(version=version, count=len(rows)), self.assertRaises(ValueError):
                self.check(rows, version)
        # Historical recovery authority does not grant current build eligibility.
        old_rows = copy.deepcopy(self.source_s1)
        old_rows[next(iter(old_rows))]['sha256'] = 'c'*64
        old_lock = ('retired-private', tuple(('_internal/' + n, r['bytes'], r['sha256']) for n, r in old_rows.items()))
        with patch.object(managed, '_REVIEWED_S1_BASE_RELEASES', (self.managed_lock, old_lock)):
            with self.assertRaisesRegex(ValueError, 'current source-owned release|retired S1 difficulty2'):
                self.check(dict(empty, **old_rows), 'retired-private')

    def test_production_projection_is_derived_from_complete_historical_source(self):
        self.enable_fixture()
        version, projected = projected_release(*self.managed_lock)
        self.assertEqual(version, TARGET_VERSION)
        expected = frozenset(('_internal/' + name, row['bytes'], row['sha256'])
                             for name, row in self.s1.items())
        self.assertEqual(projected, expected)
        self.assertEqual({path.split('/')[3] for path, _, _ in self.managed_lock[1]},
                         {'difficulty2', 'difficult', 'nightmare'})
        self.assertEqual(self.check()['resourceVersion'], TARGET_VERSION)
        # A free-standing two-mode lock is not source review or migration evidence.
        with patch.object(managed, '_REVIEWED_S1_BASE_RELEASES', ((TARGET_VERSION, tuple(expected)),)):
            with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'incomplete-reviewed'):
                self.check()

    def test_source_policy_and_runtime_policy_must_independently_agree(self):
        self.enable_fixture(managed_policy=False)
        with self.assertRaises(managed.ManagedBaseResourceError):
            self.check()

    def test_exact_enabled_fixture_uses_real_source_inventory_and_native_registration(self):
        self.enable_fixture()
        result = self.validate()
        self.assertEqual(result[1], self.rows)
        self.assertEqual(result[4]['s1']['files'], self.s1)
        self.assertEqual(result[4]['productionS1Release']['sourceCommit'], self.source['commit'])
        self.assertEqual(build.included_resource_contexts('production', self.rows, TARGET_VERSION, source=self.source),
                         list(build.S0_RESOURCE_CONTEXTS + build.S1_RESOURCE_CONTEXTS))
        self.assertIs(purpose._PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED, False)

    def test_byte_corruption_and_typed_provider_failure_remain_blocking(self):
        self.enable_fixture()
        name = next(iter(self.s1)); path = self.resources / name
        data = path.read_bytes(); path.write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'size/hash'):
            self.validate()
        path.write_bytes(data)
        self.registry.prepare.return_value['difficult']['available'] = False
        with self.assertRaisesRegex(ValueError, 'typed/version provider'):
            self.validate()

    def test_source_version_and_tracked_payload_mismatch_fail_closed(self):
        self.enable_fixture()
        self.assertEqual(build.source_payload_fingerprint(self.source), self.fingerprint)
        for name in ('version.json', 'payload.py', 'tools/build_qt_client.py'):
            path = self.source_root / name; original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.assertRaisesRegex(ValueError, 'clean'):
                self.check()
            self.commit(); self.source = build.source_identity(self.git('rev-parse', 'HEAD'))
            with self.assertRaisesRegex(ValueError, 'source/version'):
                self.check()
            path.write_bytes(original); self.commit()
            self.source = build.source_identity(self.git('rev-parse', 'HEAD'))
        for source in ({'commit': 'a'*40, 'clean': True}, dict(self.source, clean=False), {}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.check(source=source)

    def test_lock_schema_types_and_unreviewed_content_fail_closed(self):
        self.enable_fixture()
        original = copy.deepcopy(self.lock)
        mutations = [[], dict(original, enabled='true'), dict(original, enabled=1),
                     dict(original, enabled=False), dict(original, release={}),
                     dict(original, release=dict(original['release'], versionSha256='d'*64))]
        for lock in mutations:
            self.lock = lock; self.save_lock(); self.commit()
            self.source = build.source_identity(self.git('rev-parse', 'HEAD'))
            with self.subTest(lock=lock), self.assertRaises(ValueError):
                self.check()

    def test_s0_contexts_and_formal_appid_are_unchanged(self):
        s0 = {name: row for name, row in self.rows.items() if not name.startswith('seasons/')}
        self.assertIsNone(build.production_s1_release(s0, 'old-s0'))
        self.assertEqual(build.included_resource_contexts('production', s0, 'old-s0'), list(build.S0_RESOURCE_CONTEXTS))
        self.assertEqual(build.INSTANCES['production']['appId'], 'D04394A0-DACE-4FF9-874D-3DCB43EE53D1')
        self.assertEqual(build.INSTANCES['production']['versionSuffix'], '')

    def test_enabled_projection_omits_candidate_and_historical_repair_preserves_user_bytes(self):
        self.enable_fixture()
        stage, output = self.root / 'stage', self.root / 'output'
        shutil.copytree(self.resources, stage / '_internal'); output.mkdir()
        (stage / '星图.exe').write_bytes(b'synthetic-never-executed')
        with patch('client_providers.build_engine_factories', return_value=self.registry):
            include, report = build.write_production_files_include(stage, self.rows, output,
                                                  resource_version=TARGET_VERSION, source=self.source)
        active_s1 = {'_internal/' + name for name in self.s1}
        self.assertTrue(active_s1 <= set(report['families']['base']))
        self.assertFalse(active_s1 & set(report['installedFiles']))
        self.assertFalse(any(name.startswith(managed.CANDIDATE + '/') for name in report['installedFiles']))
        self.assertFalse(any(name.startswith('work/') for name in report['installedFiles']))
        self.assertIsNone(report['managedBaseManifest'])
        fixture = projection_fixture.InstallerProjectionTests(); fixture.stage, fixture.include = stage, include
        installed = self.root / 'installed'
        user = installed / 'work/nightmare-memory/ui-settings.json'
        user.parent.mkdir(parents=True); user.write_bytes(b'preserve-settings')
        fixture.apply_generated_projection(installed)
        self.assertFalse(any((installed / name).exists() for name in active_s1))
        # Historical recovery still consumes its original candidate contract;
        # the current Setup projection above no longer produces that contract.
        legacy_rows = [dict(path='_internal/' + name, **row) for name, row in self.rows.items()
                       if build.resource_file_family('_internal/' + name) == 'base']
        manifest = managed.manifest_for_rows(legacy_rows, TARGET_VERSION)
        candidate = installed / managed.CANDIDATE
        for row in legacy_rows:
            target = candidate / 'payload' / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.resources / row['path'][len('_internal/'):], target)
        (candidate / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        result = managed.repair_managed_base_resources(installed)
        self.assertEqual(result['status'], 'repaired')
        self.assertEqual(user.read_bytes(), b'preserve-settings')
        for name, row in self.s1.items():
            self.assertEqual(build.sha256(installed / '_internal' / name), row['sha256'])
        # The generated Inno rows never touch unknown existing active content.
        target = installed / next(iter(active_s1)); target.write_bytes(b'unowned-edit')
        fixture.apply_generated_projection(installed)
        self.assertEqual(target.read_bytes(), b'unowned-edit')
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'preflight-blocked'):
            managed.repair_managed_base_resources(installed)
        self.assertEqual(target.read_bytes(), b'unowned-edit')

    def test_staged_extra_missing_changed_or_linked_season_bytes_are_rejected(self):
        self.enable_fixture()
        stage = self.root / 'stage'; shutil.copytree(self.resources, stage / '_internal')
        name = next(iter(self.s1)); path = stage / '_internal' / name
        data = path.read_bytes()
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Staged S1 bytes'):
            build.production_file_projection(stage, self.rows, resource_version=TARGET_VERSION, source=self.source)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'exact inventory'):
            build.production_file_projection(stage, self.rows, resource_version=TARGET_VERSION, source=self.source)
        path.write_bytes(data)
        extra = stage / '_internal/seasons/s1/nightmare/references/extra.json'; extra.write_bytes(b'extra')
        with self.assertRaises(ValueError):
            build.production_file_projection(stage, self.rows, resource_version=TARGET_VERSION, source=self.source)
        extra.unlink(); path.unlink(); path.symlink_to(self.resources / name)
        with self.assertRaisesRegex(ValueError, 'links'):
            build.production_file_projection(stage, self.rows, resource_version=TARGET_VERSION, source=self.source)


if __name__ == '__main__':
    unittest.main()
