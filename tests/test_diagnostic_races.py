import json, os, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch
import diagnostic_storage as s
from async_event_log import AsyncEventLog
from diagnostic_privacy import safe_record
from hang_diagnostics import HangWatchdog

class RaceTests(unittest.TestCase):
 def test_swap_before_append_preserves_outside(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'events-race.jsonl';outside=Path(d)/'outside';outside.write_text('PRIVATE')
   s.append_record(p,{'event':'capture-error'});original=s._append
   def swap(path,data):
    path.unlink()
    if os.name=='nt':os.link(outside,path)
    else:path.symlink_to(outside)
    return original(path,data)
   with patch.object(s,'_append',side_effect=swap):
    with self.assertRaises(OSError):s.append_record(p,{'event':'capture-error'})
   self.assertEqual(outside.read_text(),'PRIVATE')
 def test_no_historical_deletion_even_when_capacity_exhausted(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'events-old.jsonl';s.append_record(p,{'event':'capture-error'},now=0)
   before=p.read_bytes()
   with self.assertRaises(OSError):s.append_record(Path(d)/'events-new.jsonl',{'event':'capture-error'},max_files=1,now=100,max_age=1)
   self.assertEqual(p.read_bytes(),before)
 def test_replaced_regular_file_is_not_adopted(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'events-race.jsonl';s.append_record(p,{'event':'capture-error'})
   original=s._append;before=p.read_bytes();replacement=Path(d)/'replacement';replacement.write_bytes(before)
   def swap(path,data):
    os.replace(replacement,path)
    return original(path,data)
   with patch.object(s,'_append',side_effect=swap):
    with self.assertRaises(OSError):s.append_record(p,{'event':'capture-error'})
   self.assertEqual(p.read_bytes(),before)
 def test_journal_replacement_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'events-race.jsonl';s.append_record(p,{'event':'capture-error'})
   original=s._save;journal=Path(d)/s.REGISTRY
   def swap(root,files):
    journal.unlink();journal.write_text('NEW USER FILE')
    return original(root,files)
   with patch.object(s,'_save',side_effect=swap):
    with self.assertRaises(OSError):s.append_record(p,{'event':'capture-error'})
   self.assertEqual(journal.read_text(),'NEW USER FILE')
 def test_shared_writer_journal_failure_counts_confirmed_row(self):
  with tempfile.TemporaryDirectory() as d:
   w=AsyncEventLog(Path(d)/'events.jsonl')
   with patch.object(s,'_save',side_effect=OSError('journal-denied')):
    w.emit('capture-error',{});self.assertTrue(w.flush(2))
   w.close();w.thread.join(1)
   self.assertEqual(len(w.path.read_bytes().splitlines()),1)
   self.assertEqual(w.status()['diagnosticLoss']['lostTotal'],0)
   self.assertEqual(w.status()['failed'],1)
 def test_floor_score_context_only(self):
  row=safe_record({'floorRead':{'scores':{'-1':.2,'1':.3,'2':.4,'99':.5,'Alice':.9}},'scores':{'-1':.8}})
  self.assertEqual(row['floorRead']['scores'],{'-1':.2,'1':.3,'2':.4})
  self.assertEqual(row['scores'],{})
 def test_production_floor_read_bounded_writer_keeps_scores(self):
  import numpy as np
  from map_interface_observation import detect_selected_floor
  from recognition_diagnostics import bounded_recognition
  with tempfile.TemporaryDirectory() as d:
   read=detect_selected_floor(np.zeros((720,1280,3),np.uint8))
   bounded=bounded_recognition({'floorRead':read})
   p=Path(d)/'events-floor.jsonl';w=AsyncEventLog(p)
   w.emit('capture-error',{'recognitionDiagnostics':bounded});self.assertTrue(w.flush(2));w.close();w.thread.join(1)
   row=json.loads(p.read_bytes())
   self.assertEqual(row['recognitionDiagnostics']['floorRead']['scores'],read['scores'])
   self.assertEqual(set(read['scores']),{'-1','1','2'})
 def test_watchdog_progress_while_event_write_blocked(self):
  with tempfile.TemporaryDirectory() as d:
   entered=threading.Event();release=threading.Event();original=s._append
   def blocked(path,data):
    if path.name=='events.jsonl':entered.set();release.wait(3)
    return original(path,data)
   with patch.object(s,'_append',side_effect=blocked):
    w=AsyncEventLog(Path(d)/'events.jsonl');w.emit('capture-error',{});self.assertTrue(entered.wait(1))
    dog=HangWatchdog(output_dir=Path(d),process_id=123,build_fingerprint='ABC')
    t=threading.Thread(target=dog._write_snapshot,args=(1,),daemon=True);t.start();t.join(.5)
    try:self.assertFalse(t.is_alive());self.assertTrue(list(Path(d).rglob('thread-stacks-*.jsonl')))
    finally:release.set();w.flush(3);w.close();t.join(3)
 def test_watchdog_reserved_budget_and_export(self):
  from diagnostic_export import safe_diagnostic_log_files
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   for pid in range(8):
    dog=HangWatchdog(output_dir=root,process_id=pid,build_fingerprint='ABC',state_getter=lambda:{'username':'PRIVATE'})
    dog._write_snapshot(1)
   files=list((root/'watchdog').glob('*.jsonl'))
   self.assertLessEqual(len(files),4);self.assertLessEqual(sum(p.stat().st_size for p in files),256*1024)
   self.assertTrue(files)
   for p in files:self.assertNotIn(b'PRIVATE',p.read_bytes());json.loads(p.read_bytes())
   self.assertEqual(set(safe_diagnostic_log_files(root)),set(files))
if __name__=='__main__':unittest.main()


