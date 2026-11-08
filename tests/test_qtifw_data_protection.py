"""Real temporary filesystem and JS execution; fault injection at move/GC seams.

No native QtIFW, actual installation, AppData or application process is accessed.
"""
import json
import os
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'app')]
import qtifw_trial_helper as helper
import map_package_upgrade as upgrade
import failure_sampling
from build_qtifw_trial import release_text


class DataProtectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='qtifw-data-protection-')
        self.addCleanup(tmp.cleanup)
        self.scope = Path(tmp.name)
        self.target = self.scope / 'targets/fixture'
        (self.scope / '.qtifw-trial-scope').write_text('fixture')
        self.policy = dict(scope=str(self.scope), instance='fixture', payloadSource='a'*40, files={})
        helper.prepare(self.policy, self.target)
        self.work = self.target / 'program/work'
        self.settings = self.write('nightmare-memory/custom-galleries.json', b'{"schema":"xingtu-custom-gallery-v1","galleries":[]}')
        self.routes = self.write('nightmare-memory/custom-routes.json', b'{"schema":"xingtu-custom-routes-v1","maps":{}}')
        self.unknown = self.write('nightmare-memory/personal.txt', b'unknown, keep')
        self.outside = self.target / 'personal.txt'
        self.outside.write_bytes(b'outside, keep')
        self.handles = {}

    def write(self, name, raw):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def fake_open(self, path):
        fd = os.open(path, os.O_RDONLY)
        self.handles[fd] = path
        return fd

    def clear(self, mark=None, opener=None, **kwargs):
        real_move = upgrade._move_owned
        def move(source, destination, row, identity, **options):
            if source in (self.settings, self.routes):
                if opener:
                    fd = opener(source)
                    os.close(fd)
                if mark:
                    mark(source)
            return real_move(source, destination, row, identity, **options)
        with patch.object(upgrade, '_move_owned', side_effect=move):
            return helper.clear_data(self.policy, self.target, 'clear', **kwargs)

    def test_default_keep_does_not_scan_or_open_any_data(self):
        with patch.object(helper, 'data_files', side_effect=AssertionError('keep scanned')):
            self.assertEqual(helper.clear_data(self.policy, self.target, 'keep')['cleared'], 0)
        self.assertEqual(self.settings.read_bytes(), b'{"schema":"xingtu-custom-gallery-v1","galleries":[]}')
        self.assertTrue(self.routes.exists())

    def test_confirmed_clear_only_exact_owned_paths_and_repeat_is_safe(self):
        self.assertEqual(self.clear()['cleared'], 2)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.routes.exists())
        self.assertEqual(self.clear()['cleared'], 0)
        self.assertFalse((self.target / helper.CLEAR_STATE).exists())
        self.assertEqual(self.unknown.read_bytes(), b'unknown, keep')
        self.assertEqual(self.outside.read_bytes(), b'outside, keep')

    def test_mid_clear_failure_restores_all_bytes_and_can_retry(self):
        def fail_second(fd):
            if fd == self.routes:
                raise OSError('synthetic deletion failure')
            return None
        with self.assertRaisesRegex(OSError, 'synthetic'):
            self.clear(fail_second)
        self.assertTrue(self.settings.exists())
        self.assertEqual(self.routes.read_bytes(), b'{"schema":"xingtu-custom-routes-v1","maps":{}}')
        self.assertEqual(self.unknown.read_bytes(), b'unknown, keep')
        self.assertEqual(self.clear()['cleared'], 2)

    def test_interrupted_move_recovers_and_retries(self):
        real = upgrade._move_owned
        def interrupt(source, destination, *args, **kwargs):
            real(source, destination, *args, **kwargs)
            raise KeyboardInterrupt()
        with patch.object(upgrade, '_move_owned', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                helper.clear_data(self.policy, self.target, 'clear')
        self.assertTrue((self.target / helper.CLEAR_STATE / 'journal.json').exists())
        self.assertEqual(self.clear()['cleared'], 2)
        self.assertFalse((self.target / helper.CLEAR_STATE).exists())
        self.assertEqual(self.clear()['cleared'], 0)

    def test_committed_cleanup_failure_is_persistent_and_retry_finishes_gc(self):
        import star_map_import as files
        with patch.object(files, '_rollback_published', side_effect=OSError('GC failure')):
            with self.assertRaisesRegex(OSError, 'GC failure'):
                self.clear()
        journal = json.loads((self.target / helper.CLEAR_STATE / 'journal.json').read_bytes())
        self.assertEqual(journal['status'], 'committed')
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.routes.exists())
        self.assertEqual(self.clear()['cleared'], 0)
        self.assertFalse((self.target / helper.CLEAR_STATE).exists())

    def test_valid_unknown_json_at_all_fixed_paths_is_preserved_in_receipt(self):
        for name in helper.SETTINGS_FILES:
            self.write(name, b'{"personal":"unknown"}')
        result = self.clear()
        self.assertEqual(result['cleared'], 0)
        self.assertEqual(len(result['preserved']), 6)
        for name in helper.SETTINGS_FILES:
            self.assertEqual((self.work / name).read_bytes(), b'{"personal":"unknown"}')

    def test_install_rollback_cannot_consume_a_clear_choice_without_purge(self):
        (self.scope / "qtifw-trial-policy.json").write_text(json.dumps(self.policy))
        (self.target / "trial-select-data.json").write_text(json.dumps(dict(choice="clear", operation="none")))
        with patch.object(helper, "__file__", str(self.scope / "helper.py")), \
                patch.object(sys, "argv", ["helper", "undo-data", str(self.target)]), \
                patch.object(helper, "clear_data", side_effect=AssertionError("rollback cleared data")), \
                redirect_stdout(StringIO()):
            self.assertEqual(helper.main(), 0)
        self.assertTrue(self.settings.exists())
        self.assertTrue(self.routes.exists())

    def test_preflight_failure_deletes_nothing(self):
        def fail_second(path):
            if path == self.routes:
                raise OSError('synthetic sharing violation')
            return self.fake_open(path)
        with self.assertRaisesRegex(OSError, 'sharing violation'):
            self.clear(opener=fail_second)
        self.assertTrue(self.settings.exists())
        self.assertTrue(self.routes.exists())

    def test_replaced_or_changed_file_after_plan_is_not_deleted(self):
        def changed(path):
            if path == self.settings:
                path.write_bytes(b'foreign replacement')
            return self.fake_open(path)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.clear(opener=changed)
        self.assertEqual(self.settings.read_bytes(), b'foreign replacement')
        self.assertTrue(self.routes.exists())

    def test_malformed_known_filename_and_unknown_nested_files_are_retained(self):
        self.settings.write_bytes(b'foreign non JSON')
        nested = self.write('map-packages/downloads/personal.txt', b'unknown download')
        self.assertEqual(self.clear()['cleared'], 1)
        self.assertEqual(self.settings.read_bytes(), b'foreign non JSON')
        self.assertEqual(nested.read_bytes(), b'unknown download')

    def test_real_map_receipt_plan_clears_owned_maps_but_keeps_unknown_and_modified(self):
        from test_map_package_removal import PackageRemovalTests, SUJU, WANG
        fixture = PackageRemovalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.install()
        fixture.install({WANG: b"synthetic-wang"}, package="nightmare-wang-qingxin", name="wang.zip")
        # Copy only synthetic test resources into our temporary owned installation.
        shutil.copytree(fixture.root, self.target / "program", dirs_exist_ok=True)
        program = self.target / "program"
        unknown = program / "_internal/assets/suju/personal.png"
        unknown.write_bytes(b"unknown map neighbour")
        (program / WANG).write_bytes(b"modified map, retain")
        before = helper.data_files(self.policy, self.target)[1]
        self.assertIn(program / SUJU, [path for path, _ in before])
        self.assertNotIn(program / WANG, [path for path, _ in before])
        self.clear()
        self.assertFalse((program / SUJU).exists())
        self.assertEqual((program / WANG).read_bytes(), b"modified map, retain")
        self.assertEqual(unknown.read_bytes(), b"unknown map neighbour")
        self.assertTrue((program / "work/map-packages/nightmare-suju.json").exists())
        self.assertEqual(self.clear()["cleared"], 0)

    def test_native_clear_requires_verified_parent_lease(self):
        with patch('qtifw_trial_lease.verified_uninstaller_lease', side_effect=ValueError('wrong parent')):
            with self.assertRaisesRegex(ValueError, 'wrong parent'):
                self.clear(native_lease=True)
        self.assertTrue(self.settings.exists())

    def test_native_clear_uses_and_closes_parent_lease_instead_of_relocking(self):
        from unittest.mock import Mock
        owner = Mock()
        with patch('qtifw_trial_lease.verified_uninstaller_lease', return_value=owner) as verify, \
                patch.object(helper, 'resource_write_lock', side_effect=AssertionError('relocked')):
            self.assertEqual(self.clear(native_lease=True)['cleared'], 2)
        verify.assert_called_once_with(self.target)
        owner.close.assert_called_once_with()

    def test_formal_render_has_no_trial_copy_and_trial_templates_are_unchanged(self):
        for name in ('controller.js', 'component.js', 'install-location.ui', 'data-component.js'):
            source = (ROOT / 'tools/qtifw_trial' / name).read_text()
            rendered = release_text(source)
            for forbidden in ('旧试用版', '测试目录', '试用', '测试'):
                self.assertNotIn(forbidden, rendered)
        self.assertIn('旧试用版', (ROOT / 'tools/qtifw_trial/component.js').read_text())
        build = (ROOT / 'tools/build_qtifw_trial.py').read_text()
        self.assertIn('RemoveTargetDir="false"', build)
        self.assertIn('text = release_text(path.read_text', build)
        for name in ('component.js', 'data-component.js'):
            source = (ROOT / 'tools/qtifw_trial' / name).read_text()
            self.assertIn('component.addOperation("Mkdir", parent, "UNDOOPERATION", "")', source)

    @unittest.skipUnless(shutil.which('node'), 'Node required for actual controller execution')
    def test_actual_js_default_confirm_cancel_cli_and_undo_boundary(self):
        subprocess.run(['node', str(ROOT / 'tests/qtifw_data_protection.js')], check=True,
                       cwd=ROOT, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
