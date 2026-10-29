"""Production pack resolution without a producer checkout or a GUI."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import nightmare_candidate as runtime


class PackagedNightmarePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resources = self.root / '_internal'
        self.manifest = self.resources / 'references/nightmare-map-pack.json'
        self.manifest.parent.mkdir(parents=True)
        self.producer = self.root / 'producer-present'
        self.producer.mkdir()
        self.payload = dict(packId='fixture', displayName='fixture', sourceRoot=str(self.producer),
            maps=[dict(id='one', name='one', direction='right', doorCount=1, shape='one',
                sideDoorCrop=[0, 0, 2, 2], oneFlow='route.png',
                layers={'1':'route.png', '2':'route.png', 'B1':'route.png'})])
        (self.producer / 'route.png').write_bytes(b'producer must not satisfy packaged validation')

    def pack(self, *, frozen=True, configured=None):
        self.manifest.write_text(json.dumps(self.payload), encoding='utf8')
        env = {} if configured is None else {'IDENTITYV_NIGHTMARE_MAPS':str(configured)}
        with patch.object(sys, 'frozen', frozen, create=True), \
                patch.object(runtime, 'USER_ROOT', self.root), patch.dict(runtime.os.environ, env, clear=True):
            return runtime.NightmarePack(self.manifest)

    def test_resource_root_manifest_diagnostic_and_layers_use_installed_root(self):
        self.payload['maps'][0]['resourceBase'] = 'resource-root'
        pack = self.pack(configured=self.producer)
        self.assertEqual(pack.source_root, self.resources)
        self.assertEqual(pack.maps[0].layers['2'], self.resources / 'route.png')
        self.assertFalse(pack.validate()['success'])

    def test_frozen_missing_assets_cannot_fall_back_to_existing_producer(self):
        pack = self.pack()
        self.assertEqual(pack.source_root, self.resources / 'assets/nightmare-maps')
        self.assertFalse(pack.validate()['success'])

    def test_explicit_external_source_remains_supported(self):
        pack = self.pack(configured=self.producer)
        self.assertEqual(pack.source_root, self.producer)
        self.assertTrue(pack.validate()['success'])

    def test_user_adjacent_assets_take_precedence(self):
        assets = self.root / 'assets/nightmare-maps'
        assets.mkdir(parents=True)
        self.assertEqual(self.pack().source_root, assets)

    def test_source_checkout_legacy_manifest_remains_supported(self):
        self.assertEqual(self.pack(frozen=False).source_root, self.producer)

    def test_unknown_resource_base_is_rejected(self):
        self.payload['maps'][0]['resourceBase'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'unsupported nightmare resource base'):
            self.pack()


if __name__ == '__main__':
    unittest.main()
