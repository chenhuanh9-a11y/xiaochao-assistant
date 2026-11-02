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
if __name__=='__main__':unittest.main()
