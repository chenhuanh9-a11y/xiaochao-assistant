import json,os
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
import diagnostic_storage as storage

class StorageTests(unittest.TestCase):
    def test_multibyte_and_oversize_records_are_complete_and_byte_bounded(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'safe_record', side_effect=lambda value:value):
            # Trusted Unicode fixtures isolate the UTF-8 byte writer.
            p=Path(folder)/'events-budget.jsonl'
            # Structural stack text is multibyte-safe; generic text is redacted.
            for i in range(2):
                storage.append_record(p,dict(event='capture-error',seq=i,actual='中文'*40),file_limit=512)
            storage.append_record(p,dict(event='capture-error',seq=100,actual='中文'*1000),file_limit=512)
            with self.assertRaises(OSError):
                for i in range(100):storage.append_record(p,dict(event='capture-error',seq=i,actual='中文'*40),file_limit=512)
            logs=list(Path(folder).glob('*.jsonl'))
            self.assertEqual(len(logs),2)
            rows=[]
            for log in logs:
                self.assertLessEqual(log.stat().st_size,512)
                rows.extend(json.loads(line) for line in log.read_bytes().splitlines())
            self.assertTrue(any(row.get('diagnosticRecordTruncated') for row in rows))

    def test_history_budget_refuses_growth_without_retiring_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);unknown=root/'events-unknown.jsonl';unknown.write_text('keep-user-file')
            for i in range(4):
                storage.append_record(root/f'events-owned-{i}.jsonl',dict(event='capture-error',seq=i),
                    file_limit=512,root_limit=2048,max_files=4,now=i*100,max_age=150)
            before={p.name:p.read_bytes() for p in root.glob('*.jsonl')}
            with self.assertRaises(OSError):
                storage.append_record(root/'events-new.jsonl',dict(event='capture-error'),
                    file_limit=512,root_limit=2048,max_files=4,now=1000,max_age=150)
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.glob('*.jsonl')})

    def test_unknown_changed_hardlinked_and_symlink_targets_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);p=root/'events-user.jsonl';p.write_text('unknown')
            with self.assertRaises(OSError):storage.append_record(p,{'event':'capture-error'})
            self.assertEqual(p.read_text(),'unknown')
            p=root/'events-owned.jsonl';storage.append_record(p,{'event':'capture-error'})
            p.write_text('changed-user-data')
            with self.assertRaises(OSError):storage.append_record(p,{'event':'capture-error'})
            self.assertEqual(p.read_text(),'changed-user-data')
            link=root/'events-hardlink.jsonl';os.link(p,link)
            with self.assertRaises(OSError):storage.append_record(link,{'event':'capture-error'})
            self.assertEqual(p.read_text(),'changed-user-data')

    def test_write_failure_does_not_truncate_or_claim_unknown_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);p=root/'events-fault.jsonl'
            storage.append_record(p,dict(event='capture-error',seq=1))
            before=p.read_bytes()
            with patch.object(storage,'_append',side_effect=OSError('disk-full')):
                with self.assertRaises(OSError):storage.append_record(p,dict(event='capture-error',seq=2))
            self.assertEqual(p.read_bytes(),before)
            storage.append_record(p,dict(event='capture-error',seq=3))
            self.assertEqual([json.loads(x)['seq'] for x in p.read_bytes().splitlines()],[1,3])

    def test_symlink_outside_target_and_changed_old_history_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'logs';root.mkdir();external=Path(folder)/'outside';external.mkdir()
            outside=external/'events-linked.jsonl';outside.write_text('private-user-data')
            if os.name=='nt':
                import subprocess
                junction=root/'junction'
                subprocess.run(['cmd','/c','mklink','/J',str(junction),str(external)],check=True,capture_output=True)
                link=junction/'events-linked.jsonl'
            else:
                link=root/'events-linked.jsonl';link.symlink_to(outside)
            with self.assertRaises(OSError):storage.append_record(link,{'event':'capture-error'})
            self.assertEqual(outside.read_text(),'private-user-data')
            old=root/'events-old.jsonl';storage.append_record(old,{'event':'capture-error'},now=0)
            old.write_text('changed-user-data')
            storage.append_record(root/'events-new.jsonl',{'event':'capture-error'},now=1000,max_age=1)
            self.assertEqual(old.read_text(),'changed-user-data')
            self.assertEqual(outside.read_text(),'private-user-data')

    def test_batch_uses_one_ownership_publish_and_recovers_exact_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'events-batch.jsonl'
            with patch.object(storage,'_save',wraps=storage._save) as save:
                storage.append_records(p,[dict(event='capture-error',seq=i) for i in range(16)])
                self.assertEqual(save.call_count,1)
            original=storage._append;calls=[0]
            def failing(path,data):
                calls[0]+=1
                if calls[0]==2:raise OSError('disk-full')
                return original(path,data)
            with patch.object(storage,'_append',side_effect=failing):
                with self.assertRaises(storage.BatchStorageError) as error:
                    storage.append_records(p,[dict(event='capture-error',seq=i) for i in range(16,19)])
            self.assertEqual(error.exception.completed,1)
            storage.append_record(p,dict(event='capture-error',seq=19))
            self.assertEqual([json.loads(x)['seq'] for x in p.read_bytes().splitlines()],list(range(17))+[19])

    def test_journal_failure_does_not_mislabel_confirmed_log_rows_as_lost(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'events-journal-error.jsonl'
            with patch.object(storage,'_save',side_effect=OSError('journal-denied')):
                with self.assertRaises(storage.BatchStorageError) as error:
                    storage.append_records(p,[dict(event='capture-error',seq=i) for i in range(3)])
            self.assertEqual(error.exception.completed,3)
            before=p.read_bytes()
            self.assertEqual(len(before.splitlines()),3)
            with self.assertRaises(OSError):storage.append_record(p,{'event':'capture-error'})
            self.assertEqual(p.read_bytes(),before)  # Unregistered file is preserved.

if __name__=='__main__':unittest.main()
