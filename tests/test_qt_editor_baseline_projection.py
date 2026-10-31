"""Projection only: no freeze/Setup launch and no external installation writes."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_qt_client as build


class EditorBaselineProjectionTests(unittest.TestCase):
    def test_program_fallback_has_exact_source_provenance_and_unconditional_projection(self):
        with tempfile.TemporaryDirectory(prefix='qt-editor-projection-') as folder:
            root = Path(folder)
            stage = root / 'stage'
            resources = {}
            for name, data in [('assets/custom-maps/manifest.json', b'manifest'), ('assets/custom-maps/images/map.webp', b'image'), ('assets/custom-maps/topology/map.json', b'graph')]:
                source = stage / '_internal' / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(data)
                resources[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            report = build.stage_editor_baseline(stage, resources)
            self.assertEqual(len(report['files']), 3)
            for row in report['files']:
                self.assertEqual(row['sha256'], resources[row['source']]['sha256'])
                self.assertEqual(build.sha256(stage / row['destination']), row['sha256'])
                self.assertTrue(row['destination'].startswith('_internal/qt-editor-base/'))
            include, projection = build.write_production_files_include(stage, resources, root)
            fallback = {row['destination'] for row in report['files']}
            self.assertTrue(fallback <= set(projection['families']['program']))
            self.assertEqual(len(projection['families']['editor']), 3)
            rows = [line for line in include.read_text('utf-8-sig').splitlines() if 'qt-editor-base' in line]
            self.assertEqual(len(rows), 3)
            self.assertFalse(any('Check:' in line for line in rows))
            with self.assertRaisesRegex(ValueError, 'fresh'):
                build.stage_editor_baseline(stage, resources)
            source = stage / '_internal/assets/custom-maps/manifest.json'
            source.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'source inventory'):
                build.stage_editor_baseline(stage, resources)


if __name__ == '__main__':
    unittest.main()
