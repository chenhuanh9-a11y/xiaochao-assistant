"""Fixed diagnostic filesystem seam; no GUI/PE or real-user resources."""
from pathlib import Path
import subprocess
import sys
import unittest

APP=Path(__file__).resolve().parents[1]/'app'
class SnapshotTests(unittest.TestCase):
    def test_frozen_import_scope_remains_explicit(self):
        text=(APP/'ui_packaged_audit.py').read_text(encoding='utf-8')
        self.assertIn("state['ancestorSnapshot'] = snapshot",text)
        self.assertIn("if import_data:\n        snapshot.install_after_guard()",text)
        self.assertIn("report['ancestorMetadataSnapshot']['restore']",text)

    def test_cached_metadata_and_live_descendants_without_broadening_guard(self):
        script=r"""
import os,sys,tempfile,uuid
from pathlib import Path
sys.path.insert(0,{app!r})
from ui_packaged_audit import PathGuard
from ui_import_audit import AncestorSnapshot,no_links
root=Path(tempfile.gettempdir())/('xingtu-snapshot-unit-'+uuid.uuid4().hex)
root.mkdir();child=root/'owned';child.write_bytes(b'live')
snapshot=AncestorSnapshot(root,no_links);parent=root.parent
expected=next(iter(snapshot.metadata.values()))
try:expected.st_mode=0
except AttributeError:pass
else:raise AssertionError('mutable stat_result')
os.chdir(root);guard=PathGuard(root,[root]);guard.install()
forwarded=[];checked=os.stat
def traced(path,*a,**kw):
 forwarded.append(str(path));return checked(path,*a,**kw)
os.stat=traced
snapshot.install_after_guard()
for path,value in snapshot.metadata.items():
 assert os.stat(path,follow_symlinks=False) is value
 assert os.lstat(path) is value
assert not forwarded
no_links(root)
assert child.stat().st_size==4
child.write_bytes(b'new owned bytes');assert child.stat().st_size==15
for action in (lambda:guard.check(parent),lambda:open(parent,'rb'),lambda:os.listdir(parent),
 lambda:list(os.scandir(parent)),lambda:(parent/'outside-never-created').write_bytes(b'no'),
 lambda:os.stat(parent),lambda:os.lstat(parent/'unknown'),lambda:os.lstat('..'),
 lambda:os.stat(str(root/'..'),follow_symlinks=False),lambda:os.access(parent,os.R_OK),
 lambda:os.readlink(parent)):
 try:action()
 except PermissionError:pass
 else:raise AssertionError('outside operation escaped')
assert len(guard.attempts)==11
assert snapshot.close()['restored']
try:os.stat(parent,follow_symlinks=False)
except PermissionError:pass
else:raise AssertionError('closed seam still active')
assert all(str(root)!=path for path in snapshot.metadata)
(root/'result').write_bytes(b'ok')
os._exit(0)
""".format(app=str(APP))
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main()
