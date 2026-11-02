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
            for i in range(100):
                storage.append_record(p,dict(event='capture-error',seq=i,actual='中文'*40),file_limit=512)
            storage.append_record(p,dict(event='capture-error',seq=100,actual='中文'*1000),file_limit=512)
            logs=list(Path(folder).glob('*.jsonl'))
            self.assertEqual(len(logs),2)
            for log in logs:
                self.assertLessEqual(log.stat().st_size,512)
                for line in log.read_bytes().splitlines():json.loads(line)
            self.assertTrue(json.loads(p.read_bytes().splitlines()[-1])['diagnosticRecordTruncated'])

    def test_history_budget_and_age_only_retire_verified_owned_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);unknown=root/'events-unknown.jsonl';unknown.write_text('keep-user-file')
            for i in range(12):
                storage.append_record(root/f'events-owned-{i}.jsonl',dict(event='capture-error',seq=i),
                    file_limit=512,root_limit=2048,max_files=4,now=i*100,max_age=150)
            self.assertEqual(unknown.read_text(),'keep-user-file')
            owned=json.loads((root/storage.REGISTRY).read_text())['files']
            self.assertLessEqual(len(owned),4)
            self.assertLessEqual(sum(x['bytes'] for x in owned.values()),2048)
            self.assertTrue(all(1100-x['writtenAt']<=150 for x in owned.values()))

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

if __name__=='__main__':unittest.main()
