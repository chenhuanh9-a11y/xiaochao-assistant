"""Inactive exact production S1 resource policy, synthetic owned bytes only.

No source-release lock is populated, no installer is built or launched. Tests
patch in small complete three-mode fixtures to exercise real repair contracts.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_qt_client as build
import managed_base_resources as managed
import s1_candidate_purpose as purpose


class ProductionS1BaseResourceContracts(unittest.TestCase):
    def setUp(self):
        # Explicit disabled-policy fixture; final source policy is checked separately.
        self.enterContext(patch.object(managed, "_REVIEWED_S1_BASE_RELEASES", ()))
        self.enterContext(patch.object(purpose, "_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED", False))
        temp = tempfile.TemporaryDirectory(prefix='s1-base-owned-fixture-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.v1, self.v2 = self.fixture('fixture-v1'), self.fixture('fixture-v2')

    @staticmethod
    def row(path, data):
        return dict(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def fixture(self, version):
        data = {}
        for mode in ('difficulty2', 'difficult', 'nightmare'):
            for name in ('native-map-manifest.json', 'references/coordinate-pack.json'):
                path = '_internal/seasons/s1/' + mode + '/' + name
                data[path] = (version + ':' + mode + ':' + name).encode()
        rows = [self.row(path, value) for path, value in sorted(data.items())]
        lock = (version, tuple((row['path'], row['bytes'], row['sha256']) for row in rows))
        return dict(version=version, data=data, rows=rows, lock=lock)

    def allow(self, *fixtures):
        item = patch.object(managed, '_REVIEWED_S1_BASE_RELEASES', tuple(f['lock'] for f in fixtures))
        item.start(); self.addCleanup(item.stop)

    def stage(self, fixture):
        manifest = managed.manifest_for_rows(fixture['rows'], fixture['version'])
        for path, data in fixture['data'].items():
            target = self.root / managed.CANDIDATE / 'payload' / path
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        (self.root / managed.CANDIDATE / 'manifest.json').write_bytes(managed._encoded(manifest))
        return manifest

    def test_disabled_fixture_policy_rejects_s1_without_altering_s0(self):
        self.assertEqual(managed._REVIEWED_S1_BASE_RELEASES, ())
        self.assertIs(purpose._PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED, False)
        for row in self.v1['rows']:
            self.assertFalse(managed._base_path(row['path']))
        with self.assertRaises(managed.ManagedBaseResourceError):
            managed.manifest_for_rows(self.v1['rows'], self.v1['version'])
        row = self.row('_internal/references/legacy.json', b's0')
        result = managed.manifest_for_rows([row], 'unchanged-s0-version')
        self.assertEqual(result['files'], [row])
        self.assertEqual(result['resourceVersion'], 'unchanged-s0-version')

    def test_settings_profile_and_environment_do_not_populate_source_locks(self):
        (self.root / 'installation-profile.json').write_text(json.dumps(dict(
            productionS1Enabled=True, resourceVersion=self.v1['version'], files=self.v1['rows'])))
        with patch.dict(os.environ, {'XINGTU_PRODUCTION_S1_RESOURCES': '1'}):
            with self.assertRaises(managed.ManagedBaseResourceError):
                managed.manifest_for_rows(self.v1['rows'], self.v1['version'])

    def test_exact_complete_version_and_rows_are_accepted_only_in_fixture(self):
        self.allow(self.v1)
        result = managed.manifest_for_rows(self.v1['rows'], self.v1['version'])
        self.assertEqual(result['files'], self.v1['rows'])
        self.assertTrue(all(managed._base_path(row['path']) for row in self.v1['rows']))
        self.assertIs(purpose._PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED, False)
        with self.assertRaisesRegex(ValueError, 'does not support season resources|inventory/version differs'):
            build.production_file_projection(self.root,
                {'seasons/s1/nightmare/native-map-manifest.json': {}})

    def test_reviewed_s1_version_cannot_drop_entire_s1_inventory(self):
        self.allow(self.v1)
        s0 = self.row('_internal/references/legacy.json', b's0')
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'unreviewed-s1-base-inventory-or-version'):
            managed.manifest_for_rows([s0], self.v1['version'])
        self.assertEqual(managed.manifest_for_rows([s0], 'old-s0')['files'], [s0])

    def test_wrong_missing_or_nonstring_version_is_rejected(self):
        self.allow(self.v1)
        for version in ('', 'private-playtest', 'fixture-v2', None, True, {'version': 'fixture-v1'}):
            with self.subTest(version=version), self.assertRaisesRegex(
                    managed.ManagedBaseResourceError, 'unreviewed-s1-base-inventory-or-version'):
                managed.manifest_for_rows(self.v1['rows'], version)

    def test_missing_extra_mutated_or_mixed_files_are_rejected(self):
        self.allow(self.v1, self.v2)
        mutated = copy.deepcopy(self.v1['rows']); mutated[0]['sha256'] = 'a' * 64
        wrong_size = copy.deepcopy(self.v1['rows']); wrong_size[0]['bytes'] += 1
        mixed = copy.deepcopy(self.v1['rows']); mixed[0] = self.v2['rows'][0]
        extra = self.v1['rows'] + [self.row('_internal/seasons/s1/nightmare/references/extra.json', b'extra')]
        for rows in (self.v1['rows'][1:], extra, mutated, wrong_size, mixed):
            with self.subTest(rows=rows), self.assertRaises(managed.ManagedBaseResourceError):
                managed.manifest_for_rows(rows, self.v1['version'])
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'unreviewed-s1-base-inventory-or-version'):
            managed.manifest_for_rows(self.v2['rows'], self.v1['version'])

    def test_other_seasons_unknown_modes_user_state_executables_or_traversal_are_rejected(self):
        self.allow(self.v1)
        for path in ('_internal/seasons/s2/nightmare/native-map-manifest.json',
                     '_internal/seasons/s1/unknown/native-map-manifest.json',
                     '_internal/seasons/s1/nightmare/user-settings.json',
                     '_internal/seasons/s1/nightmare/references/injected.exe',
                     '_internal/seasons/s1/nightmare/references/../../outside.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                managed.manifest_for_rows(self.v1['rows'] + [self.row(path, b'bad')], self.v1['version'])

    def test_malformed_or_incomplete_source_policy_fails_closed(self):
        for value in ((('version', ()),), (('version', self.v1['lock'][1][:-2]),),
                      [self.v1['lock']], (('version', (('invalid/path.exe', 1, 'a' * 64),)),)):
            with self.subTest(value=value), patch.object(managed, '_REVIEWED_S1_BASE_RELEASES', value):
                with self.assertRaises(managed.ManagedBaseResourceError):
                    managed._base_path(self.v1['rows'][0]['path'])

    def test_real_repair_accepts_only_current_owned_bytes_and_preserves_s0_user_data(self):
        self.allow(self.v1, self.v2)
        preserved = {'_internal/references/s0.json': b's0',
                     'work/nightmare-memory/ui-settings.json': b'user settings',
                     '_internal/assets/custom-maps/user.png': b'user display'}
        for path, data in preserved.items():
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        self.stage(self.v1)
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'repaired')
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'healthy')
        self.stage(self.v2)
        result = managed.repair_managed_base_resources(self.root)
        self.assertEqual(result['changed'], len(self.v2['rows']))
        for path, data in dict(preserved, **self.v2['data']).items():
            self.assertEqual((self.root / path).read_bytes(), data)
        self.assertEqual(managed._manifest(self.root / managed.STATE)['resourceVersion'], self.v2['version'])

    def test_reviewed_rename_is_blocked_without_dropping_old_ownership(self):
        renamed = copy.deepcopy(self.v2)
        old_path = '_internal/seasons/s1/nightmare/references/coordinate-pack.json'
        new_path = '_internal/seasons/s1/nightmare/references/renamed-pack.json'
        renamed['data'][new_path] = renamed['data'].pop(old_path)
        renamed['rows'] = [self.row(path, value) for path, value in sorted(renamed['data'].items())]
        renamed['lock'] = (renamed['version'], tuple((row['path'], row['bytes'], row['sha256'])
                                                   for row in renamed['rows']))
        self.allow(self.v1, renamed)
        self.stage(self.v1); managed.repair_managed_base_resources(self.root)
        state_before = (self.root / managed.STATE).read_bytes()
        self.stage(renamed)
        plan = managed.plan_managed_base_resources(self.root)
        self.assertEqual(plan['status'], 'blocked')
        self.assertIn('s1-base-retirement-unreviewed', [issue['reason'] for issue in plan['issues']])
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'base-repair-preflight-blocked'):
            managed.repair_managed_base_resources(self.root)
        self.assertEqual((self.root / managed.STATE).read_bytes(), state_before)
        self.assertEqual((self.root / old_path).read_bytes(), self.v1['data'][old_path])
        self.assertFalse((self.root / new_path).exists())
        self.assertIn(old_path, managed.managed_base_inventory(self.root))

    def test_s1_to_s0_transition_cannot_silently_drop_s1_ownership(self):
        self.allow(self.v1)
        self.stage(self.v1); managed.repair_managed_base_resources(self.root)
        state_before = (self.root / managed.STATE).read_bytes()
        path = '_internal/references/new-s0.json'
        fixture = dict(version='s0-only', data={path: b's0'}, rows=[self.row(path, b's0')])
        self.stage(fixture)
        plan = managed.plan_managed_base_resources(self.root)
        self.assertEqual(plan['status'], 'blocked')
        self.assertEqual(sum(issue['reason'] == 's1-base-retirement-unreviewed'
                             for issue in plan['issues']), len(self.v1['rows']))
        with self.assertRaises(managed.ManagedBaseResourceError):
            managed.repair_managed_base_resources(self.root)
        self.assertEqual((self.root / managed.STATE).read_bytes(), state_before)
        self.assertFalse((self.root / path).exists())

    def test_corrupt_candidate_bytes_block_whole_preflight(self):
        self.allow(self.v1)
        self.stage(self.v1)
        path = self.v1['rows'][0]['path']
        (self.root / managed.CANDIDATE / 'payload' / path).write_bytes(b'corrupt')
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'base-repair-preflight-blocked'):
            managed.repair_managed_base_resources(self.root)
        self.assertFalse((self.root / managed.STATE).exists())
        self.assertFalse(any((self.root / row['path']).exists() for row in self.v1['rows']))

    def test_unrecognized_active_s1_bytes_are_preserved_and_block_entire_upgrade(self):
        self.allow(self.v1, self.v2)
        self.stage(self.v1); managed.repair_managed_base_resources(self.root)
        path = self.v1['rows'][0]['path']
        (self.root / path).write_bytes(b'unowned edit')
        before = {row['path']: (self.root / row['path']).read_bytes() for row in self.v1['rows']}
        self.stage(self.v2)
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'base-repair-preflight-blocked'):
            managed.repair_managed_base_resources(self.root)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})

    def test_forged_manifest_version_or_inventory_cannot_authorize_repair(self):
        self.allow(self.v1)
        manifest = self.stage(self.v1)
        manifest['resourceVersion'] = 'unknown-version'
        target = self.root / managed.CANDIDATE / 'manifest.json'
        target.write_bytes(managed._encoded(manifest))
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'unreviewed-s1-base-inventory-or-version'):
            managed.repair_managed_base_resources(self.root)
        self.assertFalse((self.root / managed.STATE).exists())


if __name__ == '__main__':
    unittest.main()
