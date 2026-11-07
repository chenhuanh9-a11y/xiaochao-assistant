"""Synthetic installed-projection pins, no Windows compiler or executable launch."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'app')]
import build_qt_client as build
from installer_native_observation import program_paths_from_ownership


class ProgramInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name) / 'stage'
        self.output = Path(temporary.name) / 'output'
        self.output.mkdir()
        files = ['星图.exe', 'release-report.json', '_installer-owned-files.txt',
                 '_internal/app/module.py', '_internal/qt-editor-base/manifest.json',
                 'star-map-reset-helper.exe', '_star_map_reset_runtime/python313.dll',
                 '_internal/references/base.json', '_internal/assets/custom-maps/manifest.json',
                 '_internal/references/build-fingerprint.txt']
        for name in files:
            path = self.stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('fixture:' + name).encode())
        resources = {name[len('_internal/'):]: dict(bytes=(self.stage / name).stat().st_size,
                     sha256=build.sha256(self.stage / name)) for name in files
                     if name.startswith(('_internal/references/', '_internal/assets/'))}
        _, projection = build.write_production_files_include(self.stage, resources, self.output)
        self.record = dict(fileProjection=projection, releaseReport=dict(
            executableSha256=build.sha256(self.stage / '星图.exe')))
        build.write_program_ownership(self.stage, self.record)
        self.source = dict(commit='a' * 40)

    def write(self):
        return build.write_installer_program_inventory(self.stage, self.record, self.output, self.source)

    def test_full_installed_projection_round_trips_native_predicate(self):
        path = self.write()
        data = json.loads(path.read_bytes())
        self.assertEqual(set(data), {'schema', 'sourceCommit', 'ownershipSha256', 'ownershipBase64', 'files'})
        ownership = base64.b64decode(data['ownershipBase64'], validate=True)
        self.assertEqual(ownership, (self.stage / '_installer-owned-files.txt').read_bytes())
        self.assertEqual(set(data['files']), set(program_paths_from_ownership(ownership)))
        payload = '_internal/managed-base-resources/payload/_internal/references/base.json'
        self.assertIn(payload, data['files'])
        self.assertEqual(data['files'][payload]['sha256'], build.sha256(self.stage / '_internal/references/base.json'))
        self.assertIn('_internal/qt-editor-base/manifest.json', data['files'])
        self.assertIn('_star_map_reset_runtime/python313.dll', data['files'])
        self.assertNotIn('_internal/assets/custom-maps/manifest.json', data['files'])
        self.assertNotIn('_internal/references/base.json', data['files'])
        self.assertNotIn(path.name, self.record['fileProjection']['installedFiles'])
        self.assertEqual(self.write(), path)

    def test_program_and_candidate_tampering_or_missing_file_rejected(self):
        for name in ('星图.exe', '_internal/app/module.py', '_internal/references/base.json',
                     '_internal/qt-editor-base/manifest.json', '_star_map_reset_runtime/python313.dll'):
            path = self.stage / name
            original = path.read_bytes()
            for action in ('tamper', 'missing'):
                with self.subTest(name=name, action=action):
                    if action == 'tamper': path.write_bytes(b'x' * len(original))
                    else: path.unlink()
                    with self.assertRaises(ValueError): self.write()
                    path.write_bytes(original)

    def test_ownership_hash_and_projection_mismatch_fail_closed(self):
        ownership = self.stage / '_installer-owned-files.txt'
        original = ownership.read_bytes()
        ownership.write_bytes(original + b'extra.dll\n')
        with self.assertRaises(ValueError): self.write()
        ownership.write_bytes(original)
        self.record['fileProjection']['installedFiles'].remove('_internal/app/module.py')
        with self.assertRaisesRegex(ValueError, 'projection'): self.write()

    def test_changed_family_destination_rejected(self):
        self.record['fileProjection']['families']['base'] = []
        with self.assertRaisesRegex(ValueError, 'family mappings'): self.write()

    def test_preexisting_inventory_cannot_be_rebound(self):
        path = self.write()
        original = path.read_bytes()
        path.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'different bytes'): self.write()
        self.assertNotEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
