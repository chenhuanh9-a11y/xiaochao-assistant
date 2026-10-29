import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('packaged_resource_contract',
    Path(__file__).resolve().parents[1]/'tools/packaged_resource_contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class PackagedResourceContractTests(unittest.TestCase):
    def test_runtime_paths_reject_absolute_drive_relative_and_escape(self):
        with tempfile.TemporaryDirectory() as root:
            for value in ('E:/producer/a.png', 'E:producer.png', r'\\server\share\a.png',
                          '/producer/a.png', '../outside.png', r'..\outside.png', '', None):
                with self.subTest(value=value):
                    self.assertIsNotNone(contract.check_relative_path(root, value))
            self.assertIsNone(contract.check_relative_path(root, 'references/typed/map.npz'))

    def test_missing_manifests_fail_instead_of_reporting_empty_success(self):
        with tempfile.TemporaryDirectory() as root:
            report = contract.check_package(root)
            self.assertFalse(report['success'])
            self.assertTrue(report['errors'])

    def test_gate_distinguishes_runtime_paths_from_producer_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'references').mkdir()
            manifest = dict(sourceRoot='E:/producer/source', maps=[dict(resourceBase='resource-root',
                oneFlow='assets/preview.png', layers={'2':'assets/floor.png'})])
            files = {'references/nightmare-map-pack.json':manifest,
                'difficult-native-manifest.json':dict(sourceRenderer='E:/producer/tool.py',
                    runtimeInterfaces=dict(typedIndex='references/difficult-shared-structure-index.json'))}
            for name in ('nightmare', 'difficult'):
                files[f'references/{name}-shared-structure-index.json'] = dict(
                    structureVersionBindings='references/versions.json',
                    entries=[dict(resourcePath='references/product.npz')])
            def write():
                for name, value in files.items():
                    (root/name).write_text(json.dumps(value), encoding='utf8')
            write()
            self.assertTrue(contract.check_package(root)['success'])
            manifest['maps'][0]['layers']['2'] = '../developer/floor.png'
            write()
            report = contract.check_package(root)
            self.assertFalse(report['success'])
            self.assertEqual(report['errors'][0]['field'], 'maps/0/layers/2')


if __name__ == '__main__':
    unittest.main()
