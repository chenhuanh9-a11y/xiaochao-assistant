import sys,json,tempfile,unittest,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from star_map_import import verified_installed_package
class Installed(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.pid='nightmare-suju';self.sha='a'*64
  self.file=self.root/'_internal/assets/suju/nightmare-03/floor-2.png';self.file.parent.mkdir(parents=True);self.file.write_bytes(b'pixels')
  self.receipt=self.root/'work/map-packages/nightmare-suju.json';self.receipt.parent.mkdir(parents=True)
 def write(self):
  self.receipt.write_text(json.dumps(dict(packageId=self.pid,revision=1,archiveSha256=self.sha,files=[dict(path=self.file.relative_to(self.root).as_posix(),bytes=6,sha256=hashlib.sha256(b'pixels').hexdigest())])))
 def check(self,sha=None,**kw):return verified_installed_package(self.root,self.pid,sha,**kw)['status']
 def test_download_without_receipt_is_not_installed(self):self.assertEqual(self.check(),'not-installed')
 def test_verified_and_duplicate(self):
  self.write();self.assertEqual(self.check(self.sha),'installed');self.assertEqual(self.check(self.sha),'installed')
 def test_update(self):self.write();self.assertEqual(self.check('b'*64),'update-available')
 def test_missing_or_changed_layer(self):
  self.write();self.file.write_bytes(b'broken');self.assertEqual(self.check(),'incomplete');self.file.unlink();self.assertEqual(self.check(),'incomplete')
 def test_bad_receipt_and_pending_upgrade(self):
  self.receipt.write_text('{}');self.assertEqual(self.check(),'unverified');self.write();(self.receipt.parent/'pending-upgrade.json').write_text('{}');self.assertEqual(self.check(),'unverified')
 def test_cancelled_verification(self):self.write();self.assertEqual(self.check(cancelled=lambda:True),'unverified')
if __name__=='__main__':unittest.main()
