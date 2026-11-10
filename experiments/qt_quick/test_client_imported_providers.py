"""Default factories never admit external data to trusted legacy loaders."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from client_imported_providers import build_imported_engine_factories
from client_paths import RuntimePaths


class ImportedProviderBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resolver = ResourceResolver(self.root / 'resources')
        self.factories = build_imported_engine_factories(self.resolver, RuntimePaths(self.root, False))

    def test_construction_is_lazy_and_each_empty_context_has_its_own_gaps(self):
        with patch.object(self.resolver, 'snapshot', side_effect=AssertionError('constructor IO')):
            build_imported_engine_factories(self.resolver, RuntimePaths(self.root, False))
        for season in ('s0', 's1'):
            for mode in ('nightmare', 'difficult'):
                self.factories.prepare_context(mode, season=season)
                report = self.factories.capabilities(season)[mode]
                self.assertFalse(report['available'])
                self.assertEqual(report['contextKey'], f'{season}:{mode}')
                self.assertEqual(set(report['resourceGaps']), {'recognition-base-missing', 'controls-profile-missing'})
        self.assertFalse(self.resolver.root.exists())

    def test_selected_complete_metadata_cannot_bypass_semantic_admission_or_enter_legacy_io(self):
        contexts = [dict(season=season, mode=mode) for season in ('s0', 's1') for mode in ('nightmare', 'difficult')]
        payloads = {'base': b'{"maps":[{"id":"../../escaped","resourcePath":"../outside.png"}]}', 'controls': b'{}'}
        manifest = dict(schema='xingtu-resource-package', formatVersion=1, packageId='external', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId=name, type=kind, schemaVersion=1, contexts=contexts,
                entrypoint='index.json', dependencies={}) for name, kind in
                (('base', 'recognition-base'), ('controls', 'controls-profile'))],
            files=[dict(componentId=name, path='index.json', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                   for name, raw in payloads.items()])
        archive = self.root / 'package.zip'
        with zipfile.ZipFile(archive, 'w') as zip_file:
            zip_file.writestr('manifest.json', json.dumps(manifest))
            for name, raw in payloads.items():
                zip_file.writestr(f'data/{name}/index.json', raw)
        saved = save_archive(archive, self.resolver.root)
        refs = {ref.component_id: dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
            componentId=ref.component_id, manifestSha256=ref.manifest_sha256) for ref in saved.component_refs}
        # Deliberately write an externally supplied selection. This is a negative
        # admission test, not a replacement for prospective semantic validation.
        catalog = dict(schema='xingtu-resource-catalog-v1', generation=str(uuid4()), displays=[],
            contexts={f"{row['season']}:{row['mode']}": dict(recognitionBase=refs['base'], controls=refs['controls'], reviewedGroups=None)
                      for row in contexts})
        (self.resolver.root / 'catalog.json').write_text(json.dumps(catalog))
        with patch('client_providers.EngineFactories._build', side_effect=AssertionError('legacy loader used')):
            for season in ('s0', 's1'):
                for mode in ('nightmare', 'difficult'):
                    self.factories.prepare_context(mode, season=season)
                    report = self.factories.capabilities(season)[mode]
                    self.assertFalse(report['available'])
                    self.assertEqual(report['resourceGaps'], ['native-base-schema-unsupported']
                        if (season, mode) == ('s0', 'nightmare') else ['recognition-decoder-unavailable'])
        self.assertFalse((self.root / 'escaped').exists())


if __name__ == '__main__':
    unittest.main()
