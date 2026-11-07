"""Final staged S0 plane must equal the reviewed closure, not raw input intent."""
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

    def write(self):
        with patch('client_providers.build_engine_factories', return_value=self.fixture.registry):
            return build.write_production_files_include(self.stage, self.fixture.rows, self.output,
                        resource_version='fixture', source=self.fixture.source,
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

    def test_partial_bundled_author_mapping_cannot_leave_active_base_copy(self):
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
        with self.assertRaisesRegex(ValueError, 'Staged S0 candidate plane'):
            self.write()
        self.assertFalse((self.output / 'production-files.iss').exists())

    def test_source_support_in_verified_input_must_exist_in_actual_stage(self):
        name = 'assets/support/wechat.png'
        source = self.fixture.source_root / name
        source.parent.mkdir(parents=True); source.write_bytes(b'source-owned-support')
        row = dict(bytes=source.stat().st_size, sha256=build.sha256(source))
        self.fixture.lock['release']['s0BaseFiles']['_internal/' + name] = row
        # Add source bytes to tracked payload, recompute the synthetic lock digest.
        self.fixture.commit(); self.fixture.source = build.source_identity(self.fixture.git('rev-parse', 'HEAD'))
        self.fixture.lock['release']['sourcePayloadSha256'] = build.source_payload_fingerprint(self.fixture.source)
        self.fixture.save_lock(); self.fixture.commit()
        self.fixture.source = build.source_identity(self.fixture.git('rev-parse', 'HEAD'))
        # The input helper sees source support, but the synthetic assembled stage
        # deliberately lacks it. It must not claim a complete candidate closure.
        with self.assertRaisesRegex(ValueError, 'Staged S0 candidate plane'):
            self.write()
        self.fixture.rows[name] = row
        target = self.stage / '_internal' / name; target.parent.mkdir(parents=True)
        shutil.copy2(source, target)
        _, report = self.write()
        self.assertIn('_internal/' + name, report['families']['base'])


if __name__ == '__main__':
    unittest.main()
