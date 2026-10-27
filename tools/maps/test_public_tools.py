"""Asset-free checks for the public generator's output and preservation contract."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'app'))
from shared_structure_reference import load_shared_structure
from fast_structure_alignment import load_prepared


class PublicGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='xiaochao-public-tool-fixture-')
        self.root = Path(self.temporary.name)
        self.source = self.root/'source'
        self.output = self.root/'output'
        refs = self.source/'references'
        (refs/'shared-structure').mkdir(parents=True)
        self.npz = refs/'shared-structure'/'fixture.npz'
        mask = np.zeros((96,96), dtype=np.uint8)
        mask[16:80,16:80] = 1
        mask[32:64,32:64] = 0
        metadata = dict(sourceKind='game-rendered-shared-structure',
                        roles=dict(clean='shared_mask',validation='shared_mask'))
        np.savez_compressed(self.npz, shared_mask=mask, metadata=json.dumps(metadata))
        (refs/'nightmare-shared-structure-index.json').write_text(json.dumps(dict(entries=[dict(
            resourcePath='references/shared-structure/fixture.npz',mapId='fixture',floor='1')])), 'utf-8')
        self.before = self.hashes()

    def tearDown(self):
        self.temporary.cleanup()

    def hashes(self):
        return {str(p.relative_to(self.source)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob('*') if p.is_file()}

    def run_tool(self, output=None):
        result = subprocess.run([sys.executable,str(REPO/'tools/maps/prepare_fast_structures.py'),
            str(self.source),'--output-root',str(output or self.output)],capture_output=True,text=True,timeout=30)
        self.assertEqual(self.before,self.hashes(),'Source resources were changed')
        return result

    def test_generation_readback_and_idempotence(self):
        first=self.run_tool()
        self.assertEqual(first.returncode,0,first.stderr)
        self.assertEqual(json.loads(first.stdout)['generated'],1)
        source=load_shared_structure(self.npz)
        reference=replace(source,path=self.output/'references/shared-structure/fixture.npz')
        self.assertEqual(load_prepared(reference).distance.dtype,np.uint16)
        second=self.run_tool()
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual(json.loads(second.stdout)['generated'],0)

    def test_conflicting_output_is_preserved(self):
        target=self.output/'references/fast-structure/fixture-fast.npz'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'incompatible synthetic existing file')
        result=self.run_tool()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(target.read_bytes(),b'incompatible synthetic existing file')

    def test_source_or_nested_output_is_rejected(self):
        for target in (self.source,self.source/'nested-output'):
            result=self.run_tool(target)
            self.assertEqual(result.returncode,2)
            self.assertFalse((target/'references/fast-structure').exists())


if __name__ == '__main__':
    unittest.main()
