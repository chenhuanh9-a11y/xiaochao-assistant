"""Current source entry and shared maintenance tools must stay Tk-free."""
import ast
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {
    'identityv_daily_assistant', 'identityv_front_live_test', 'identityv_test_button',
    'desktop_main_ui', 'desktop_controls', 'desktop_display', 'desktop_icons',
    'desktop_lucide', 'desktop_onboarding', 'desktop_rounded', 'desktop_theme',
    'desktop_toast', 'desktop_tutorial', 'game_control_view', 'control_layout_editor',
    'custom_map_editor', 'manual_map_picker', 'manual_map_selection', 'map_resource_browser',
    'map_resources_page', 'orb_visual', 'route_status_window', 'star_brand', 'support_window',
    'ui_import_audit', 'ui_interaction_profile', 'ui_packaged_audit',
}


class SourceCleanupTests(unittest.TestCase):
    def test_retired_pages_are_absent_and_no_source_imports_them(self):
        for name in RETIRED:
            self.assertFalse((ROOT / 'app' / (name + '.py')).exists(), name)
        forbidden = RETIRED | {'tkinter', '_tkinter', 'PIL.ImageTk'}
        for directory in ('app', 'tools', 'experiments/qt_quick', 'tests'):
            for path in (ROOT / directory).rglob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or '']
                        names.extend(f'{node.module}.{alias.name}' for alias in node.names)
                    else:
                        continue
                    self.assertFalse(forbidden.intersection(names), (str(path), names))

    def test_workflows_do_not_name_retired_test_modules(self):
        modules = {p.stem for folder in ('tests', 'experiments/qt_quick', 'tools')
                   for p in (ROOT / folder).rglob('test_*.py')}
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            # Module lists are literals; ignore discovery patterns and paths.
            for name in re.findall(r"['\"](test_[a-zA-Z0-9_]+)(?:\.[a-zA-Z0-9_.]+)?['\"]",
                                   workflow.read_text()):
                self.assertIn(name, modules, f'{workflow.name}: {name}')

    def test_shared_core_and_provider_import_without_gui_or_legacy_runtime(self):
        code = (
            "import sys; "
            f"sys.path[:0] = {[str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]!r}; "
            "import manual_map_picker_core, control_layout, client_providers; "
            "assert not ({'tkinter', 'PIL.ImageTk', 'PySide6', 'identityv_daily_assistant'} & set(sys.modules))"
        )
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


class PreparationProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'tools/maps/prepare_shared_fast_structures.py'
        spec = importlib.util.spec_from_file_location('prepare_shared_cleanup_test', path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'resources'
        self.root.mkdir()
        self.output = Path(self.temp.name) / 'output'
        self.fixture()

    def write(self, relative, data):
        import json
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def fixture(self):
        import numpy as np
        import json
        binding = dict(profile="shared-structure", mapId="fixture", variant="variant-01", floor="1")
        resource_key = dict(layoutId="fixture-layout", structureRawRecordSha256="a" * 64, floor="1")
        expected = dict(structureVersionKey="fixture-layout@" + "a" * 64 + "/1",
                        sourceVersion="fixture-source", baseSnapshot="fixture-base")
        canvas = dict(shapeHW=[12, 16], width=16, height=12, floor="1", coordinateSpace="fixture-pixels")
        product = self.root / "structure.npz"
        metadata = dict(sourceKind="game-rendered-shared-structure", binding=binding, canvas=canvas,
                        roles=dict(clean="clean", validation="validation"), **expected)
        np.savez_compressed(product, metadata=np.asarray(json.dumps(metadata)),
                            clean=np.ones((12, 16), np.uint8), validation=np.ones((12, 16), np.uint8))
        self.write("references/nightmare-map-pack.json", dict(packId="fixture", displayName="fixture", maps=[
            dict(id="fixture", name="fixture", direction="unknown", doorCount=0, shape="fixture",
                 sideDoorCrop=[0, 0, 1, 1], resourceBase="resource-root", oneFlow="unused.png",
                 layers={"1": "unused.png"}, identityProfileId="shared-structure",
                 sourceLayout="fixture-layout", structureContentSha256="a" * 64, sourceVersion="fixture-source")]))
        index = "references/nightmare-shared-structure-index.json"
        self.write(index, dict(schemaVersion=1, resourceKind="shared-structure",
            structureVersionBindings="references/versions.json",
            entries=[dict(binding, resourcePath="structure.npz", **expected)]))
        self.write("references/versions.json", dict(schemaVersion=1, resourceKind="native-structure-version-bindings",
            entries=[dict(mapId="fixture", floor="1", resourceKey=resource_key, sourceVersion="fixture-source",
                sharedLookup=dict(binding, resourcePath="structure.npz"), typedReference="structure.npz",
                neutralDisplay=dict(baseResourceKey=resource_key))]))
        self.write("references/nightmare-structure-display.json", dict(schemaVersion=1,
            resourceKind="typed-shared-structure-display", indexPath=index,
            entries=[dict(mapId="fixture", floor="1", binding=binding, referenceResource="structure.npz",
                          canvas=canvas, **expected)]))
        adapter_row = dict(structureVersionKey=expected["structureVersionKey"], sourceVersion="fixture-source",
                           referenceResource="structure.npz")
        self.write("references/nightmare-canonical-coordinate-pack.json", dict(
            schema="identityv-nightmare-canonical-coordinate-pack-v1", coordinateReferenceProfileId="shared-structure",
            policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False,
                        authorizesCrossFloorProjection=False), maps={"fixture": dict(floors={"1": adapter_row})},
            profiles={"shared-structure": dict(maps={"fixture": dict(floors={"1": adapter_row})})}))

    def add_broken_unselected_map(self):
        import json
        # Separate valid declarations, but unreadable NPZ for the unselected map.
        for relative in ('references/nightmare-map-pack.json',
                         'references/nightmare-shared-structure-index.json',
                         'references/versions.json',
                         'references/nightmare-structure-display.json'):
            path = self.root / relative
            raw = json.loads(path.read_text())
            key = 'maps' if 'maps' in raw else 'entries'
            row = json.loads(json.dumps(raw[key][0]).replace('fixture', 'other').replace('structure.npz', 'other.npz'))
            raw[key].append(row)
            self.write(relative, raw)
        path = 'references/nightmare-canonical-coordinate-pack.json'
        raw = json.loads((self.root / path).read_text())
        for maps in (raw['maps'], raw['profiles']['shared-structure']['maps']):
            maps['other'] = json.loads(json.dumps(maps['fixture']).replace('fixture', 'other').replace('structure.npz', 'other.npz'))
        self.write(path, raw)
        (self.root / 'other.npz').write_bytes(b'broken unselected pixels')

    def difficult_fixture(self):
        import json
        import numpy as np
        source = json.loads((self.root / 'references/nightmare-map-pack.json').read_text())
        source.update(schemaVersion=1, mode='difficult', floors=['1'],
                      identityProfiles={'fixture': 'shared-structure'})
        source['maps'][0]['routeProfileId'] = 'shared-structure'
        source['maps'][0]['layers'] = {'1': 'structure.npz'}
        self.write('references/native-pack.json', source)
        self.write('references/native-catalog.json', dict(schemaVersion=1, profiles=[]))
        self.write('references/native-full.json', dict(entries=[]))
        row = dict(mapId='fixture', floor='1', authorizesMapIdentity=False,
                   authorizesCrossFloorProjection=False, requiresFreshNativeGeometryValidation=True,
                   structureVersionKey='fixture-layout@' + 'a' * 64 + '/1',
                   sourceVersion='fixture-source', baseSnapshot='fixture-base')
        for key in ('matrixLegacyRouteToNative', 'matrixNativeToLegacyRoute',
                    'legacyMatrixRouteToFloorLocal', 'matrixLegacyFloorLocalToNative'):
            row[key] = np.eye(3).tolist()
        self.write('references/native-bridges.json', dict(maps={'fixture': dict(floors={'1': row})}))
        self.write('difficult-native-manifest.json', dict(runtimeInterfaces=dict(
            sourceManifest='references/native-pack.json', identityPack='references/native-pack.json',
            neutralDisplay='references/nightmare-structure-display.json',
            typedIndex='references/nightmare-shared-structure-index.json',
            versionBindings='references/versions.json', authorCatalog='references/native-catalog.json',
            coordinatePack='references/nightmare-canonical-coordinate-pack.json',
            coordinateBridges='references/native-bridges.json', fullReferences='references/native-full.json',
            identityEntries='recognition/native', exitIcon='missing-exit-icon.json')))

    def test_difficult_typed_inventory_without_rank_files_keeps_bridge_gate(self):
        import json
        self.difficult_fixture()
        rows = list(self.tool.references(self.root, ['difficult']))
        self.assertEqual([(mode, mid, floor) for mode, mid, floor, _ in rows], [('difficult', 'fixture', '1')])
        self.assertIsNotNone(rows[0][3]('shared-structure', 'fixture', '1'))
        path = self.root / 'references/native-bridges.json'
        raw = json.loads(path.read_text())
        raw['maps']['fixture']['floors']['1']['matrixNativeToLegacyRoute'][0][0] = 2
        path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, 'native bridge inverse mismatch'):
            list(self.tool.references(self.root, ['difficult']))

    def test_real_typed_provider_does_not_require_rank_or_similarity_files(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        rows = list(self.tool.references(self.root, ['nightmare']))
        self.assertEqual([(mode, mid, floor) for mode, mid, floor, _ in rows], [('nightmare', 'fixture', '1')])
        reference = rows[0][3]('shared-structure', 'fixture', '1')
        self.assertIsNotNone(reference)
        self.assertEqual(reference.shape, (12, 16))
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertFalse((self.root / '.qt-saved-frame-memory').exists())

    def test_selected_generation_ignores_broken_unselected_layer_and_rank_indices(self):
        import json
        self.add_broken_unselected_map()
        self.write('references/nightmare-entries/entries.json', {'broken': True})
        self.write('references/nightmare-entries/side-entries.json', {'broken': True})
        self.write('references/native-similarity-groups.json', {'broken': True})
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(sys, 'argv', ['prepare', str(self.root), '--output-root', str(self.output),
                                     '--mode', 'nightmare', '--select', 'fixture/1']):
            self.tool.main()
        receipt = json.loads((self.output / 'generation-result.json').read_text())
        self.assertEqual([(r['mapId'], r['floor']) for r in receipt['rows']], [('fixture', '1')])
        self.assertEqual(receipt['rows'][0]['status'], 'generated')
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        provider = next(self.tool.references(self.root, ['nightmare']))[3]
        self.assertIsNone(provider('shared-structure', 'other', '1'))

    def test_selected_missing_layer_fails_explicitly_without_output(self):
        (self.root / 'structure.npz').unlink()
        with patch.object(sys, 'argv', ['prepare', str(self.root), '--output-root', str(self.output),
                                     '--mode', 'nightmare', '--select', 'fixture/1']):
            with self.assertRaisesRegex(ValueError, 'Shared reference unavailable: nightmare/fixture/1'):
                self.tool.main()
        self.assertFalse(self.output.exists())

    def test_selected_version_or_coordinate_mismatch_is_never_loaded(self):
        import json
        for relative in ('references/versions.json', 'references/nightmare-canonical-coordinate-pack.json'):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_text()
                path.write_text(original.replace('fixture-source', 'wrong-source'))
                try:
                    provider = next(self.tool.references(self.root, ['nightmare']))[3]
                    self.assertIsNone(provider('shared-structure', 'fixture', '1'))
                finally:
                    path.write_text(original)

    def test_readable_side_binding_is_still_checked(self):
        self.write('references/nightmare-entries/side-entries.json', dict(schemaVersion=1,
            mode='nightmare', profileId='zhan10-speed', semanticGaps=[], entries=[dict(
                mapId='fixture', floor='1', turns=[], unit=1, sourceKind='game-rendered-shared-structure',
                profileId='shared-structure', variantId='variant-01', structureResource='wrong.npz', canvas={})]))
        provider = next(self.tool.references(self.root, ['nightmare']))[3]
        self.assertIsNone(provider('shared-structure', 'fixture', '1'))


if __name__ == '__main__':
    unittest.main()
