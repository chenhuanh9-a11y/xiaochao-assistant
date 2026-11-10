"""Actual projection closure; synthetic Git/resources, no freeze or Setup build."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import build_qt_client as build
import test_s1_production_builder as fixture_module


class StagedS0ClosureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.ProductionS1BuilderTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.enable_fixture()
        self.stage = self.fixture.root / 'stage'
        self.output = self.fixture.root / 'output'; self.output.mkdir()
        shutil.copytree(self.fixture.resources, self.stage / '_internal')

    def write(self, resources=None):
        with patch('client_providers.build_engine_factories', return_value=self.fixture.registry):
            return build.write_production_files_include(self.stage,
                        self.fixture.rows if resources is None else resources, self.output,
                        resource_version=self.fixture.declaration['resourceVersion'], source=self.fixture.source,
                        bundled_author_paths=getattr(self, 'author_paths', ()))

    def asset(self, name, data=b'not-approved'):
        target = self.stage / '_internal' / name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        row = dict(bytes=len(data), sha256=build.sha256(target))
        self.fixture.rows[name] = row
        return row

    def test_unreviewed_omitted_author_cannot_enter_staged_base_plane(self):
        self.asset('assets/suju/unreviewed.png')
        with self.assertRaisesRegex(ValueError, 'Staged S0 candidate plane'):
            self.write()
        self.assertFalse((self.output / 'production-files.iss').exists())

    def test_retired_partial_bundled_author_mapping_is_rejected_before_stage_writes(self):
        names = ('assets/zhan10-native/one.png', 'assets/zhan10-native/two.png')
        for name in names:
            self.asset(name)
        self.author_paths = [names[0]]
        original = self.stage / '_internal' / names[0]
        target = self.stage / build.BUNDLED_AUTHOR_ROOT / names[0]
        target.parent.mkdir(parents=True, exist_ok=True); original.rename(target)
        (self.stage / build.BUNDLED_AUTHOR_ROOT / 'inventory.json').write_text(json.dumps(dict(
            schema='xingtu-bundled-author-v1', packageId='difficult-zhan10',
            files={names[0]: self.fixture.rows[names[0]]})))
        before = {path.relative_to(self.stage).as_posix(): path.read_bytes()
                  for path in self.stage.rglob('*') if path.is_file()}
        # The old partial-relocation sample remains covered, but production now
        # rejects the entire bundled-author option before inspecting its stage.
        with self.assertRaisesRegex(ValueError, 'Production Setup no longer bundles optional author packages'):
            self.write()
        self.assertFalse((self.output / 'production-files.iss').exists())
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual({path.relative_to(self.stage).as_posix(): path.read_bytes()
                          for path in self.stage.rglob('*') if path.is_file()}, before)

    def test_source_support_in_verified_input_must_exist_in_actual_stage(self):
        name = 'assets/support/wechat.png'
        source = self.fixture.source_root / name
        source.parent.mkdir(parents=True); source.write_bytes(b'source-owned-support')
        row = dict(bytes=source.stat().st_size, sha256=build.sha256(source))
        self.fixture.lock['release']['s0BaseFiles']['_internal/' + name] = row
        # Supply the current main entry's non-resource prerequisites. These are
        # synthetic presence/version/brand inputs, never runnable client code.
        qt = self.fixture.source_root / 'experiments/qt_quick'
        qt.mkdir(parents=True)
        (qt / 'client.py').write_text('# synthetic source presence only\n', encoding='utf-8')
        (qt / 'Client.qml').write_text('// synthetic source presence only\n', encoding='utf-8')
        version = self.fixture.source_root / 'version.json'
        version.write_text(json.dumps(dict(displayVersion='1.0.0', numericVersion='1.0.0.0')), encoding='utf-8')
        icon_row = self.asset(build.BRAND_ICON, b'synthetic-brand-identity')
        icon = self.fixture.resources / build.BRAND_ICON
        icon.parent.mkdir(parents=True)
        icon.write_bytes((self.stage / '_internal' / build.BRAND_ICON).read_bytes())
        self.fixture.inventory.write_text(json.dumps(self.fixture.declaration), encoding='utf-8')
        self.fixture.lock['release']['s0BaseFiles']['_internal/' + build.BRAND_ICON] = icon_row
        # Add source bytes to tracked payload and update the exact synthetic
        # source/version lock; the real source and inventory validators run.
        self.fixture.commit(); self.fixture.source = build.source_identity(self.fixture.git('rev-parse', 'HEAD'))
        self.fixture.lock['release']['sourcePayloadSha256'] = build.source_payload_fingerprint(self.fixture.source)
        self.fixture.lock['release']['versionSha256'] = build.sha256(version)
        self.fixture.save_lock(); self.fixture.commit()
        self.fixture.source = build.source_identity(self.fixture.git('rev-parse', 'HEAD'))
        # Run today's real main configuration path. Plan-only avoids invoking
        # PyInstaller/ISCC; environment/license checks and the unrelated S0
        # package contract use stand-ins for this synthetic fixture.
        output = io.StringIO()
        planned_output = self.fixture.source_root / 'work/support-plan'
        with patch.object(build, 'environment', return_value=dict(success=True, errors=[])), \
             patch.object(build, 'notice_inputs', return_value={}), \
             patch.object(build, 'BRAND_ICON_SHA256', icon_row['sha256']), \
             patch.object(build, 'check_package', return_value=dict(success=True)), \
             patch('client_providers.build_engine_factories', return_value=self.fixture.registry), \
             redirect_stdout(output):
            self.assertEqual(build.main(['--plan-only', '--instance', 'production',
                '--source-commit', self.fixture.source['commit'], '--resource-root', str(self.fixture.resources),
                '--resource-inventory', str(self.fixture.inventory), '--iscc', str(self.fixture.root / 'unused-iscc.exe'),
                '--output-dir', str(planned_output)]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan['resourceVersion'], self.fixture.declaration['resourceVersion'])
        self.assertEqual(plan['sourceSupportAssets'], {name: row})
        self.assertEqual(plan['resourceFiles'], len(self.fixture.rows) + 1)
        self.assertEqual(plan['source']['commit'], self.fixture.source['commit'])
        self.assertFalse(planned_output.exists())
        resources = dict(self.fixture.rows, **plan['sourceSupportAssets'])
        # Both calls use main's augmented resource configuration. Only the
        # actual stage changes: first its support image is absent, then present.
        with patch.object(build, 'write_production_files_include',
                          wraps=build.write_production_files_include) as consumer:
            with self.assertRaises(ValueError) as missing:
                self.write(resources)
            self.assertEqual(str(missing.exception),
                "Resource projection differs from exact inventory: missing=['_internal/assets/support/wechat.png'], extra=[]")
            self.assertEqual(list(self.output.iterdir()), [])
            target = self.stage / '_internal' / name; target.parent.mkdir(parents=True)
            shutil.copy2(source, target)
            include, report = self.write(resources)
        self.assertEqual(consumer.call_count, 2)
        for call in consumer.call_args_list:
            self.assertIs(call.args[1], resources)
            self.assertEqual(call.kwargs['resource_version'], plan['resourceVersion'])
            self.assertIs(call.kwargs['source'], self.fixture.source)
        self.assertIn('_internal/' + name, report['families']['base'])
        self.assertEqual(report['stagedFiles']['_internal/' + name], row)
        manifest = json.loads((self.stage / report['managedBaseManifest']).read_text(encoding='utf-8'))
        self.assertIn(dict(path='_internal/' + name, **row), manifest['files'])
        self.assertTrue(any(path.endswith('/payload/_internal/' + name) for path in report['installedFiles']))
        self.assertIn(name.replace('/', '\\'), include.read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
