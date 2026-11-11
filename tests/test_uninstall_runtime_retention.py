"""The new full-uninstall choice does not change upgrade/package ownership.

Historical default-keep wizard expectations were superseded by the explicit
full-clear decision. Actual program projection still must never own work data.
"""
from pathlib import Path
import tempfile
import unittest

import build_qt_client as build

ROOT = Path(__file__).resolve().parents[1]


class RuntimeOwnershipTests(unittest.TestCase):
    def test_projection_and_ownership_reject_runtime_data_during_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            (stage / 'synthetic-program.exe').write_bytes(b'not an executable')
            self.assertEqual(build.production_file_projection(stage, {})['program'], ['synthetic-program.exe'])
            runtime = stage / 'work/installer-backups/fixture/files/0'
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b'synthetic prior program')
            with self.assertRaisesRegex(ValueError, 'Invalid staged program/resource path'):
                build.production_file_projection(stage, {})
            with self.assertRaisesRegex(ValueError, 'must not contain user work/data'):
                build.write_program_ownership(stage, {'fileProjection': {'installedFiles': [
                    'work/installer-backups/fixture/files/0']}})

    def test_default_uninstall_has_no_keep_or_scan_failure_fallback(self):
        source = (ROOT / 'tools/qt-uninstall-data.iss').read_text('utf-8-sig')
        self.assertIn('ClearOwnedInstallation;', source)
        for obsolete in ('UninstallDeleteData', 'UNINSTALL_RUNTIME_DATA_KEPT', 'InspectChosenUninstallWork'):
            self.assertNotIn(obsolete, source)
        installer = (ROOT / 'tools/qt-client.iss').read_text('utf-8-sig')
        self.assertNotRegex(installer, r'(?m)^\[UninstallDelete\]')
        self.assertIn('#include ProductionFilesInclude', installer)


if __name__ == '__main__':
    unittest.main()
