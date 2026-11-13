"""Tiny original-layout resources for current archive/UI lifecycle tests.

These synthetic canvases prove resource flow, never game recognition. Private
S1 placeholders are declared for ownership; no test treats them as calibration.
Recorded algorithm parity uses the separate real-data Session probe.
"""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

import test_export_native_base_package as nightmare
import test_export_s0_difficult_native_base_package as difficult
import test_export_s1_native_base_package as s1
from s0_nightmare_group_source_fixture import S0NightmareGroupSourceFixture


def _similarity():
    return dict(schemaVersion=1, resourceKind='native-similarity-groups', groups=[])


class OriginalS0NightmareSourceFixture:
    """Full S0N original-layout package with donor map IDs and synthetic data.

    The producer's default source has no package display name or similarity
    file. Supply those original inputs explicitly, then let its inventory and
    current exporter produce the package. These maps are synthetic and carry
    no recognition truth.
    """
    def __init__(self, root, *, ids=None):
        self.source = S0NightmareGroupSourceFixture(root, ids=ids)
        pack_path = self.source.sourceRoot / nightmare.exporter.PACK
        pack = json.loads(pack_path.read_bytes())
        pack['displayName'] = 'Synthetic original-layout nightmare'
        pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding='utf8')
        group_path = self.source.sourceRoot / 'references/native-similarity-groups.json'
        group_path.parent.mkdir(parents=True, exist_ok=True)
        group_path.write_text(json.dumps(_similarity()), encoding='utf8')
        self.source.refresh_inventory()
        self.output = self.source.output

    def export(self, **kwargs):
        return self.source.export(**kwargs)


class OriginalS0DifficultSourceFixture(difficult.S0DifficultSourceFixture):
    def export(self, **kwargs):
        bridges = 'references/fixture-original-route-bridges.json'
        self.write(bridges, dict(maps={mid:dict(floors={floor:dict(
            matrixLegacyRouteToNative=[[1,0,0],[0,1,0],[0,0,1]])
            for floor in difficult.FLOORS}) for mid in difficult.IDS}))
        self.write('references/native-similarity-groups.json', _similarity())
        self.refresh_manifest()
        manifest = json.loads(self.payloads['difficult-native-manifest.json'])
        manifest['runtimeInterfaces']['coordinateBridges'] = bridges
        manifest['runtimeInterfaces']['exitIcon'] = 'references/difficult-exit-icon.json'
        self.payloads['references/difficult-exit-icon.json'] = (self.sourceRoot /
            'references/difficult-exit-icon.json').read_bytes() if (self.sourceRoot /
            'references/difficult-exit-icon.json').is_file() else json.dumps(dict(
                exitIcon=dict(width=1, height=1, pixels='AA=='), sideDoors=[])).encode()
        self.write('difficult-native-manifest.json', manifest)
        for relative, raw in self.payloads.items():
            path = self.sourceRoot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        # The outer manifest is explicit input as in the original layout.
        self.inventory.write_text(json.dumps(dict(files={p:dict(bytes=len(raw),
            sha256=__import__('hashlib').sha256(raw).hexdigest())
            for p,raw in self.payloads.items()})), encoding='utf8')
        return super().export(original_qt=True, **kwargs)


class OriginalS1SourceFixture(s1.S1NativeBaseSourceFixture):
    def export(self, **kwargs):
        self.write_json('references/native-similarity-groups.json', _similarity())
        self.refresh_after_source_edit()
        private = self.root / 'synthetic-private-ownership-only'
        private.mkdir(exist_ok=True)
        (private/'s1_entrance_coordinate_index.json').write_text('{}', encoding='utf8')
        (private/'template_manifest.json').write_text('{}', encoding='utf8')
        buffer = BytesIO()
        Image.new('RGBA', (1,1), (0,0,0,0)).save(buffer, format='PNG')
        (private/'native_glyph_template.png').write_bytes(buffer.getvalue())
        return super().export(original_qt=True, s1_coordinate_asset_dir=private, **kwargs)


class OriginalNightmareBaseFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = nightmare.NativeBaseExporterTests()
        cls.fixture.setUp()
        cls.addClassCleanup(cls.fixture.doCleanups)
        cls.fixture.write('references/native-similarity-groups.json', _similarity())
        cls.fixture.write('references/nightmare-source-candidate-index.json',
            dict(schemaVersion=1, entries=[]))
        cls.fixture.write('references/nightmare-portable-layout-seeds.json', dict(entries=[]))
        pack = cls.fixture.read(nightmare.exporter.PACK)
        pack['displayName'] = 'Synthetic original-layout nightmare'
        cls.fixture.write(nightmare.exporter.PACK, pack)
        cls.fixture.export(original_qt=True)
        cls.archive = cls.fixture.output
        with zipfile.ZipFile(cls.archive) as archive:
            cls.members = {name:archive.read(name) for name in archive.namelist()}
