"""Real temporary filesystem and JS execution; Win32 handles alone are substituted.

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
        self.settings = self.write('nightmare-memory/ui-settings.json', b'{"sound":true}')
        self.routes = self.write('nightmare-memory/custom-routes.json', b'{"routes":[]}')
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

    def fake_mark(self, fd):
        # POSIX unlink is ONLY a test substitute for Win32 same-handle deletion.
        self.handles[fd].unlink()

    def clear(self, mark=None, opener=None, **kwargs):
        with patch.object(failure_sampling, '_open_delete_image', opener or self.fake_open), \
                patch.object(failure_sampling, '_mark_delete_image', mark or self.fake_mark):
            return helper.clear_data(self.policy, self.target, 'clear', **kwargs)

    def test_default_keep_does_not_scan_or_open_any_data(self):
        with patch.object(helper, 'data_files', side_effect=AssertionError('keep scanned')):
            self.assertEqual(helper.clear_data(self.policy, self.target, 'keep')['cleared'], 0)
        self.assertEqual(self.settings.read_bytes(), b'{"sound":true}')
        self.assertTrue(self.routes.exists())

    def test_confirmed_clear_only_exact_owned_paths_and_repeat_is_safe(self):
        self.assertEqual(self.clear()['cleared'], 2)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.routes.exists())
        self.assertEqual(self.clear()['cleared'], 0)
        self.assertEqual(self.unknown.read_bytes(), b'unknown, keep')
        self.assertEqual(self.outside.read_bytes(), b'outside, keep')

    def test_mid_clear_failure_leaves_remaining_bytes_and_can_retry(self):
        def fail_second(fd):
            if self.handles[fd] == self.routes:
                raise OSError('synthetic deletion failure')
            self.fake_mark(fd)
        with self.assertRaisesRegex(OSError, 'synthetic'):
            self.clear(fail_second)
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.routes.read_bytes(), b'{"routes":[]}')
        self.assertEqual(self.unknown.read_bytes(), b'unknown, keep')
        self.assertEqual(self.clear()['cleared'], 1)

    def test_actual_undo_entry_reports_partial_failure_and_allows_retry(self):
        from unittest.mock import Mock
        (self.scope / "qtifw-trial-policy.json").write_text(json.dumps(self.policy))
        (self.target / "trial-select-data.json").write_text(json.dumps(dict(choice="clear", operation="purge")))
        def fail_second(fd):
            if self.handles[fd] == self.routes:
                raise OSError("synthetic mid-clear failure")
            self.fake_mark(fd)
        with patch.object(helper, "__file__", str(self.scope / "helper.py")), \
                patch.object(sys, "argv", ["helper", "undo-data", str(self.target)]), \
                patch('qtifw_trial_lease.verified_uninstaller_lease', return_value=Mock()), \
                patch.object(failure_sampling, '_open_delete_image', self.fake_open), \
                patch.object(failure_sampling, '_mark_delete_image', fail_second), redirect_stdout(StringIO()):
            self.assertEqual(helper.main(), 2)
            receipt = json.loads((self.target / "trial-data.json").read_bytes())
            self.assertFalse(receipt["success"])
            self.assertEqual(receipt["status"], "incomplete")
            self.assertEqual(self.routes.read_bytes(), b'{"routes":[]}')
            with patch.object(failure_sampling, '_mark_delete_image', self.fake_mark):
                self.assertEqual(helper.main(), 0)
        self.assertTrue(json.loads((self.target / "trial-data.json").read_bytes())["success"])
        self.assertEqual(self.unknown.read_bytes(), b'unknown, keep')

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
            path.write_bytes(b'foreign replacement')
            return self.fake_open(path)
        with self.assertRaisesRegex(ValueError, 'data-changed'):
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
