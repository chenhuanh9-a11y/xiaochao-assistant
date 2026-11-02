"""Windows-only real exclusive-handle ring tests; synthetic temp data only."""
import json, os, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import diagnostic_storage as storage
from diagnostic_export import export_safe_diagnostic_bundle

@unittest.skipUnless(os.name=='nt','Windows exclusive-handle recycling only')
class WindowsRingTests(unittest.TestCase):
 def test_many_sessions_reuse_two_slots_and_export_real_sessions(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);live=root/'logs';expected={}
   for session in range(100):
    logical=live/f'events-qt-session-{session}.jsonl'
    path=storage.slot_path(logical,'problem')
    expected[str(session).zfill(32)]=session
    for seq in range(3):
     storage.append_record(path,dict(event='capture-error',sessionId=str(session).zfill(32),pid=session+1,
       logStream=path.name,seq=seq+1,timestampUTC='2026-11-02T00:00:00Z',actual=session),file_limit=1024)
   files=list(live.glob('*.jsonl'));self.assertEqual(len(files),2)
   self.assertLessEqual(sum(p.stat().st_size for p in files),2048)
   rows=[json.loads(line) for p in files for line in p.read_bytes().splitlines()]
   self.assertTrue(any(r['actual']==99 for r in rows))
   self.assertTrue(all(expected[r['sessionId']]==r['actual'] for r in rows))
   bundle=export_safe_diagnostic_bundle(root,live,app_version='fixture',build_fingerprint='AAAAAAAA')
   with zipfile.ZipFile(bundle['path']) as z:manifest=json.loads(z.read('diagnostic-manifest.json'))
   self.assertEqual({r['sessionId'] for r in rows},{r['sessionId'] for r in manifest['sessions']})
 def test_external_change_refuses_recycling_without_touching_bytes(self):
  with tempfile.TemporaryDirectory() as d:
   path=storage.slot_path(Path(d)/'events-session.jsonl','problem')
   for seq in range(20):storage.append_record(path,dict(event='capture-error',seq=seq),file_limit=256)
   files=list(Path(d).glob('*.jsonl'))
   for p in files:p.write_text('USER REPLACEMENT')
   before={p:p.read_bytes() for p in files}
   with self.assertRaises(OSError):storage.append_record(path,dict(event='capture-error'),file_limit=256)
   self.assertEqual(before,{p:p.read_bytes() for p in files})
 def test_stream_and_watchdog_slots_are_disjoint(self):
  with tempfile.TemporaryDirectory() as d:
   logical=Path(d)/'events-any.jsonl'
   paths={storage.slot_path(logical,f) for f in ('problem','recognition','tk','lifecycle','startup','watchdog')}
   self.assertEqual(len(paths),6)
   for p in paths:
    for seq in range(30):storage.append_record(p,dict(event='capture-error',seq=seq),file_limit=256)
   self.assertEqual(len([p for p in Path(d).iterdir() if not p.name.startswith('.')]),12)
   self.assertLessEqual(sum(p.stat().st_size for p in Path(d).iterdir() if not p.name.startswith('.')),12*256)

if __name__=='__main__':unittest.main()
