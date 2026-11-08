"""Lossless staging deduplication; native operation execution is checked separately."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'app')]
from build_qtifw_trial import PACKAGE,deduplicate_payload


class PayloadDedupTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.output=self.root/'output'
        self.data=self.output/'packages'/PACKAGE/'data/program'
        self.support=self.root/'support'
        self.support.mkdir()
        self.expected={}

    def program(self,name,content):
        path=self.data/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
        self.expected[name]=content
        return path

    def inventory(self):
        return dict(files={name:dict(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
                           for name,content in self.expected.items()})

    def test_support_and_program_duplicates_restore_the_complete_original_tree(self):
        shared=b'bootstrap-runtime\0'*5000
        private=b'program-runtime\0'*5000
        (self.support/'runtime.dll').write_bytes(shared)
        self.program('_internal/python.dll',shared)
        self.program('_import/python.dll',shared)
        self.program('_internal/numpy.dll',private)
        self.program('_import/numpy.dll',private)
        self.program('unique.txt',b'unique')
        inventory=self.inventory()
        copies,stats=deduplicate_payload(self.output,inventory,self.support)
        self.assertEqual(stats['copiedProgramFiles'],3)
        self.assertEqual(stats['omittedProgramBytes'],len(shared)*2+len(private))
        self.assertEqual({p.relative_to(self.data).as_posix() for p in self.data.rglob('*') if p.is_file()},
                         {'_import/numpy.dll','unique.txt'})
        target=self.root/'installed'
        shutil.copytree(self.data,target/'program')
        shutil.copytree(self.support,target/'_installer-support')
        for source,destination in copies:
            source=target/source.removeprefix('@TargetDir@/')
            destination=target/destination.removeprefix('@TargetDir@/')
            self.assertTrue(source.is_file())
            self.assertFalse(destination.exists())
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination)
        for name,content in self.expected.items():
            self.assertEqual((target/'program'/name).read_bytes(),content)
            self.assertEqual((target/'program'/name).stat().st_nlink,1)
        self.assertEqual(inventory,self.inventory())

    def test_unlinking_a_staged_alias_preserves_its_frozen_input(self):
        content=b'shared-input\0'*6000
        original=self.root/'frozen.dll'
        original.write_bytes(content)
        self.program('a.dll',content)
        alias=self.program('b.dll',content)
        alias.unlink()
        os.link(original,alias)
        deduplicate_payload(self.output,self.inventory(),self.support)
        self.assertFalse(alias.exists())
        self.assertEqual(original.read_bytes(),content)
        self.assertEqual((self.data/'a.dll').read_bytes(),content)

    def test_changed_alias_rejects_the_whole_plan_before_staging_deletion(self):
        content=b'expected-runtime\0'*5000
        (self.support/'source.dll').write_bytes(content)
        self.program('a.dll',content)
        bad=self.program('b.dll',content)
        inventory=self.inventory()
        bad.write_bytes(b'X'*len(content))
        with self.assertRaisesRegex(ValueError,'differs from its inventory'):
            deduplicate_payload(self.output,inventory,self.support)
        self.assertTrue((self.data/'a.dll').is_file())
        self.assertEqual(bad.read_bytes(),b'X'*len(content))


if __name__=='__main__':unittest.main()
