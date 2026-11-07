"""New CI orchestration contracts only; no compiler, EXE or Setup is run here."""
from pathlib import Path
import hashlib
import importlib.util
import json
import tempfile
import subprocess
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer_compile_only', ROOT / 'tools/verify_installer_compile_only.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class CompileOnlyContractTests(unittest.TestCase):
    def test_watcher_is_never_invoked_without_an_unknown_argument(self):
        self.assertEqual(probe.cli_cases('watcher'), ((('--help',), 2),))
        for kind in ('import', 'recovery', 'reset'):
            self.assertEqual(probe.cli_cases(kind), ((('--help',), 0), ((), 2)))
        with self.assertRaises(ValueError):
            probe.cli_cases('main')

    def test_nested_frozen_code_comparison_ignores_paths_but_not_behavior(self):
        text = 'def inner(x):\n return x + 1\n'
        a = compile(text, 'source.py', 'exec')
        b = compile(text, 'frozen.py', 'exec')
        bad = compile(text.replace('+ 1', '+ 2'), 'frozen.py', 'exec')
        self.assertEqual(probe.normalized(a), probe.normalized(b))
        self.assertNotEqual(probe.normalized(a), probe.normalized(bad))
        with self.assertRaises(ValueError):
            probe.normalized(b'not code')

    def test_cli_failure_and_timeout_preserve_output_without_claiming_teardown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'probe.log'
            with patch.object(probe.subprocess, 'run', return_value=subprocess.CompletedProcess(['unused'], 9, b'out', b'error')):
                with self.assertRaises(ValueError):
                    probe.run_logged(['unused'], cwd=folder, log=path, expected=0, timeout=1)
            self.assertEqual(path.read_bytes(), b'out\nerror')
            self.assertEqual(json.loads(path.with_suffix('.json').read_text())['status'], 'failed')
            failure = subprocess.TimeoutExpired(['unused'], 1, output=b'partial', stderr=b'late')
            with patch.object(probe.subprocess, 'run', side_effect=failure):
                with self.assertRaises(subprocess.TimeoutExpired):
                    probe.run_logged(['unused'], cwd=folder, log=path, expected=0, timeout=1)
            self.assertEqual(path.read_bytes(), b'partial\nlate')
            receipt = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(receipt['status'], 'timed-out')
            self.assertFalse(receipt['ownedChildTeardownVerified'])

    def make_fixture(self, parent):
        stage, out = parent / 'stage', parent / 'output'
        stage.mkdir(); out.mkdir()
        rows = {}
        for relative, data in [('star-map-reset-helper.exe', b'synthetic helper, not PE'),
                               ('_star_map_reset_runtime/installation-profile.json', b'{}')]:
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            rows[relative] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        helpers = {'reset': dict(included=True, executable='star-map-reset-helper.exe',
            runtimeDirectory='_star_map_reset_runtime', files=rows)}
        source = dict(commit='a' * 40, tree='b' * 40, clean=True)
        profile = dict(product='星图', sourceCommit=source['commit'], syntheticCompileOnly=True)
        return stage, out, helpers, source, profile

    def test_real_projection_generators_pin_synthetic_stage_without_s1_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            stage, out, helpers, source, profile = self.make_fixture(Path(folder))
            include, defines, receipt = probe.make_synthetic_projection(stage, out, source, profile, helpers)
            text = include.read_text('utf-8-sig')
            self.assertIn('managed-base-resources\\payload\\_internal\\recognition', text)
            self.assertFalse((stage / '星图.exe').read_bytes().startswith(b'MZ'))
            self.assertIn('/DResetSourceCommit=' + source['commit'], defines)
            inventory = json.loads((out / 'reset-program-inventory.json').read_text('utf-8'))
            self.assertEqual(inventory['sourceCommit'], source['commit'])
            self.assertEqual(inventory['files']['星图.exe']['sha256'], probe.build.sha256(stage / '星图.exe'))
            report = json.loads((stage / 'release-report.json').read_text('utf-8'))
            self.assertEqual(report['resourceVersion'], 'ci-synthetic-not-release')
            self.assertFalse(receipt['resourceResetInstaller']['nativeTested'])
            self.assertFalse(any('seasons/' in row for row in inventory['files']))
            command = probe.compiler_arguments(Path('ISCC.exe'), stage, out, include, defines)
            self.assertEqual(command[1], '/O-')
            self.assertIn('/DProductionInstaller', command)
            self.assertEqual(Path(command[-1]), ROOT / 'tools/qt-client.iss')
            self.assertIn('/DInstallerAppId={{D04394A0-DACE-4FF9-874D-3DCB43EE53D1}', command)

    def test_changed_helper_is_rejected_by_existing_inventory_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            stage, out, helpers, source, profile = self.make_fixture(Path(folder))
            (stage / 'star-map-reset-helper.exe').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Resource reset helper changed'):
                probe.make_synthetic_projection(stage, out, source, profile, helpers)

    def test_provisioner_pins_vendor_portable_mode_and_both_identities(self):
        text = (ROOT / 'tools/prepare_inno_compile_ci.ps1').read_text('utf-8')
        for value in ('/PORTABLE=1', '/TASKS=""', '/NOICONS', '10592232',
                      '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732', probe.ISCC_SHA256,
                      'Get-AuthenticodeSignature', "'Valid'", 'github-hosted'):
            self.assertIn(value, text)
        self.assertNotIn('choco install', text)
        self.assertNotIn('winget install', text)
        self.assertNotIn('Set-ExecutionPolicy', text)

    def test_chinese_catalog_is_pinned_raw_bytes_with_separate_provenance(self):
        text = (ROOT / 'tools/prepare_inno_compile_ci.ps1').read_text('utf-8')
        for value in ('4adf37ed7f3fd2bd11c6836ba056e3de170fbabf',
                      'Files/Languages/Unofficial/ChineseSimplified.isl',
                      'd6a11c4490de07dad443ade668289fc954dfa1ed', '20905',
                      '7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278',
                      'unofficial-community-translation', 'supplementaryLanguage=$languageProvenance',
                      'Invoke-WebRequest -Uri $languageUrl -OutFile $languageDownload',
                      'Test-Path -LiteralPath $languageTarget'):
            self.assertIn(value, text)
        download = text.index('Invoke-WebRequest -Uri $languageUrl')
        check = text.index("throw 'Pinned Chinese language catalog bytes differ'")
        publish = text.index('Move-Item -LiteralPath $languageDownload')
        verify = text.index("throw 'Prepared Chinese language catalog bytes differ'")
        inventory = text.index('$rows = @(')
        self.assertLess(download, check)
        self.assertLess(check, publish)
        self.assertLess(publish, verify)
        self.assertLess(verify, inventory)
        self.assertNotIn('Set-Content', text[download:inventory])
        self.assertNotIn(' -Force', text[download:inventory])
        inno = (ROOT / 'tools/qt-client.iss').read_text('utf-8-sig')
        self.assertIn('compiler:Languages\\ChineseSimplified.isl', inno)

    def test_evidence_never_uploads_synthetic_executables(self):
        workflow = (ROOT / '.github/workflows/installer-compile-only-windows.yml').read_text('utf-8')
        self.assertIn('compile-only-record.json', workflow)
        self.assertIn('inno-compile-only.log', workflow)
        self.assertNotIn('path: ${{ runner.temp }}/xingtu-compile-only\n', workflow)
        self.assertNotIn('*.exe', workflow)
        self.assertNotIn('build_qt_client.py --instance production', workflow)


if __name__ == '__main__':
    unittest.main()
