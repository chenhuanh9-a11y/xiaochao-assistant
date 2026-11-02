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
 def test_recycle_holds_exclusive_handle_and_failed_write_is_unconfirmed(self):
  with tempfile.TemporaryDirectory() as d:
   path=storage.slot_path(Path(d)/'events-any.jsonl','problem')
   row=dict(event='capture-error',sessionId='a'*32,pid=1,logStream=path.name,seq=1,actual=list(range(12)))
   storage.append_record(path,row,file_limit=256);storage.append_record(path,dict(row,seq=2),file_limit=256)
   inode=path.stat().st_ino;original=storage.os.ftruncate;checked=[]
   def truncate(fd,size):
    if os.fstat(fd).st_ino==inode:
     with self.assertRaises(OSError):
      with path.open('wb') as external:external.write(b'USER REPLACEMENT')
     checked.append(True)
    return original(fd,size)
   with patch.object(storage.os,'ftruncate',side_effect=truncate):
    storage.append_record(path,dict(row,seq=3),file_limit=256)
   self.assertTrue(checked)
   original_write=storage.os.write
   previous=path.with_name(path.stem+'-previous.jsonl');failed_inode=previous.stat().st_ino
   def failed(fd,data):
    if os.fstat(fd).st_ino==failed_inode:raise OSError('injected-write-denied')
    return original_write(fd,data)
   with patch.object(storage.os,'write',side_effect=failed):
    with self.assertRaises(storage.BatchStorageError) as error:
     storage.append_record(path,dict(row,seq=4),file_limit=256)
   self.assertEqual(error.exception.completed,0)
   with self.assertRaises(OSError):storage.append_record(path,dict(row,seq=5),file_limit=256)

 def test_real_process_restarts_do_not_consume_new_slots(self):
  import subprocess,sys
  code="""import json,sys
from pathlib import Path
from diagnostic_storage import slot_path,append_record
p=slot_path(Path(sys.argv[1])/'events-new-session.jsonl','problem')
for i in range(4):append_record(p,{'event':'capture-error','actual':int(sys.argv[2])},file_limit=1024)
"""
  with tempfile.TemporaryDirectory() as d:
   for session in range(40):subprocess.run([sys.executable,'-c',code,d,str(session)],check=True,capture_output=True)
   files=list(Path(d).glob('*.jsonl'));self.assertEqual(len(files),2)
   rows=[json.loads(line) for p in files for line in p.read_bytes().splitlines()]
   self.assertTrue(any(r['actual']==39 for r in rows))
   self.assertGreaterEqual(len({r['sessionId'] for r in rows}),2)
   self.assertTrue(all(len(r['sessionId'])==32 and r['pid']>0 and r['seq']>0 for r in rows))
   self.assertLessEqual(sum(p.stat().st_size for p in files),2048)

 def test_real_qt_restart_history_keeps_distinct_process_occurrences(self):
  import subprocess,sys
  from client_problem_history import summarize_problem_history
  code="""import sys,time
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication
from client_problems import ProblemsService
app=QCoreApplication([]);s=ProblemsService(SimpleNamespace(work_root=Path(sys.argv[1])))
s.record({'kind':'error','title':'synthetic'});s.shutdown()
end=time.monotonic()+10
while s.pendingWrites and time.monotonic()<end:app.processEvents();time.sleep(.002)
assert not s.pendingWrites and not s.writeError
"""
  with tempfile.TemporaryDirectory() as d:
   # Match the production history reader's canonical-path contract; Windows
   # TemporaryDirectory may return an 8.3 alias (RUNNER~1).
   root=Path(d).resolve()
   for _ in range(2):subprocess.run([sys.executable,'-c',code,str(root)],check=True,capture_output=True)
   summary=summarize_problem_history(root/'nightmare-live');result=summary['recentProblems']
   self.assertEqual(len(result),2,summary['coverage'])
   self.assertEqual(len({r['occurrenceId'] for r in result}),2)
   self.assertEqual(len({r['sessionId'] for r in result}),2)

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
