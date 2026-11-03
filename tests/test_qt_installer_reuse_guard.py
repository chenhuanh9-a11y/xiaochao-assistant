"""Synthetic metadata/byte fixtures; never freeze or execute an EXE/Setup."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "app")]
import build_qt_client as build
import rebuild_qt_installer as rebuild
import managed_base_resources as managed


class ManagedRuntimeReuseTests(unittest.TestCase):
    def fixture(self, root):
        compiled = 'a' * 40
        data = b'synthetic compiled bytes, never executed'
        (root / 'fixture.exe').write_bytes(data)
        manifest = managed.manifest_for_rows([dict(path='_internal/references/base.json', bytes=1, sha256='a' * 64)])
        candidate = root / managed.CANDIDATE / 'manifest.json'
        candidate.parent.mkdir(parents=True)
        candidate.write_text(json.dumps(manifest), encoding='utf-8')
        for name in ('installation-profile.json', '_internal/installation-profile.json'):
            (root / name).write_text(json.dumps(dict(sourceCommit=compiled,
                resourceInstallationPolicy=build.RESOURCE_INSTALLATION_POLICY)), encoding='utf-8')
        release = dict(sourceCommit=compiled, compiledPayloadSourceCommit=compiled,
                       executableSha256=build.sha256(root / 'fixture.exe'))
        report = root / 'release-report.json'
        report.write_text(json.dumps(release), encoding='utf-8')
        prior = dict(source=dict(commit='b' * 40), compiledPayloadSourceCommit=compiled,
            product='fixture', fileProjection=dict(policy=build.RESOURCE_INSTALLATION_POLICY,
                managedBaseManifest=managed.CANDIDATE + '/manifest.json',
                managedBaseManifestSha256=build.sha256(candidate), managedBaseBundleId=manifest['bundleId']),
            releaseReport=dict(file=report.name, sha256=build.sha256(report), executableSha256=release['executableSha256']))
        for kind in ('recovery', 'import'):
            product = 'star-map-' + kind + '-helper'
            runtime = '_star_map_' + kind + '_runtime'
            inventory = {}
            for name in (product + '.exe', runtime + '/python313.dll'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(('current fixture: ' + name).encode())
                inventory[name] = dict(bytes=path.stat().st_size, sha256=build.sha256(path))
            prior[kind + 'Helper'] = dict(included=True, product=product, executable=product + '.exe',
                                         runtimeDirectory=runtime, files=inventory)
        return prior, dict(commit='c' * 40)

    def test_legacy_record_cannot_opt_in_by_changing_side_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'installation-profile.json').write_text(json.dumps(
                dict(resourceInstallationPolicy=build.RESOURCE_INSTALLATION_POLICY)), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'full freeze'):
                rebuild.validate_managed_runtime_reuse(root, {'source': {'commit': 'a' * 40}}, {'commit': 'b' * 40})

    def test_matching_managed_record_uses_compiled_source_not_last_wrapper(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prior, source = self.fixture(root)
            with mock.patch.object(build, '_git', return_value='tools/qt-client.iss\n') as git:
                self.assertEqual(rebuild.validate_managed_runtime_reuse(root, prior, source), 'a' * 40)
            git.assert_called_once_with('diff', '--name-only', 'a' * 40, 'c' * 40)

    def test_changed_compiled_runtime_rejected_even_if_wrapper_diff_is_installer_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prior, source = self.fixture(root)
            with mock.patch.object(build, '_git', return_value='app/managed_base_resources.py\n'):
                with self.assertRaisesRegex(ValueError, 'full freeze'):
                    rebuild.validate_managed_runtime_reuse(root, prior, source)

    def test_candidate_or_executable_tampering_rejects_reuse(self):
        for name in (managed.CANDIDATE + '/manifest.json', 'fixture.exe'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                prior, source = self.fixture(root)
                (root / name).write_bytes(b'changed')
                with mock.patch.object(build, '_git', return_value=''):
                    with self.assertRaisesRegex(ValueError, 'full freeze'):
                        rebuild.validate_managed_runtime_reuse(root, prior, source)

    def test_replaced_helper_or_its_runtime_rejects_reuse(self):
        for name in ('star-map-recovery-helper.exe', 'star-map-import-helper.exe',
                     '_star_map_recovery_runtime/python313.dll', '_star_map_import_runtime/python313.dll'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                prior, source = self.fixture(root)
                path = root / name
                # Same-size replacement isolates the missing digest guard.
                path.write_bytes(b'old' + path.read_bytes()[3:])
                with mock.patch.object(build, '_git', return_value=''):
                    with self.assertRaisesRegex(ValueError, 'full freeze'):
                        rebuild.validate_managed_runtime_reuse(root, prior, source)

    def test_missing_helper_inventory_or_file_rejects_reuse(self):
        for missing in ('recoveryHelper', 'importHelper', 'inventory', 'file'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                prior, source = self.fixture(root)
                if missing in ('recoveryHelper', 'importHelper'):
                    del prior[missing]
                elif missing == 'inventory':
                    prior['recoveryHelper']['files'] = {}
                else:
                    (root / '_star_map_import_runtime/python313.dll').unlink()
                with mock.patch.object(build, '_git', return_value=''):
                    with self.assertRaisesRegex(ValueError, 'full freeze'):
                        rebuild.validate_managed_runtime_reuse(root, prior, source)


if __name__ == '__main__':
    unittest.main()
