"""Malicious metadata and exception text may never reach diagnostic disk."""
import json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from async_event_log import AsyncEventLog

class DiskPrivacyTests(unittest.TestCase):
    def test_resource_error_codes_survive_without_exposing_paths(self):
        from diagnostic_privacy import safe_record
        for code in ('original-controls-parameters-not-source-equivalent',
                     'resource-migration-target-exists', 'resource-migration-interrupted'):
            with self.subTest(code=code):
                result=safe_record(dict(errorCode=code,action='resource:migrate',
                    raw='C:/Users/AliceSecret/private.dat'))
                self.assertEqual(result['errorCode'],code)
                self.assertEqual(result['action'],'resource:migrate')
                self.assertNotIn('AliceSecret',json.dumps(result))
        self.assertEqual(safe_record(dict(errorCode='AliceSecret-custom-error'))['errorCode'],
                         '<redacted-text>')

    def test_resource_codes_and_exception_chain_survive_repeated_sanitization(self):
        from diagnostic_privacy import safe_record
        code = 'imported-controls-npy-vector-normalization-invalid'
        record = dict(errorCode=code, raw=code, exceptionType='ImportRejected',
            traceback='File "C:/Users/AliceSecret/imported_controls_profile.py", line 199, in _npy_vectors\n'
                      'ValueError: PrivatePlayerSecret\nImportRejected: AliceSecret')
        cleaned = safe_record(record)
        self.assertEqual(cleaned['raw'], code)
        self.assertIn('ValueError', cleaned['traceback'])
        self.assertIn('ImportRejected', cleaned['traceback'])
        self.assertIn('imported_controls_profile.py', cleaned['traceback'])
        self.assertEqual(safe_record(cleaned), cleaned)
        self.assertNotIn('AliceSecret', json.dumps(cleaned))
        self.assertNotIn('PrivatePlayerSecret', json.dumps(cleaned))

    def test_nested_private_fields_and_exception_text_are_removed_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            writer=AsyncEventLog(Path(folder)/'events-privacy.jsonl')
            writer.emit('capture-error',dict(reason='capture-error',exceptionType='ValueError',
                raw='ValueError: username=AliceSecret gameNickname=HeroSecret other app SecretMessenger',
                traceback='Traceback\n  File "C:/Users/AliceSecret/client_live_runtime.py", line 12, in capture\n    account = "AliceSecret"\nValueError: HeroSecret',
                title='HeroSecret window',diag={'event':'AliceSecret','logStream':'AliceSecret','exceptionType':'AliceSecretError','username':'AliceSecret','foregroundHwnd':987654,
                    'foregroundPid':987655,'window':{'role':'other','pid':987656,'name':'SecretMessenger','title':'HeroSecret'},
                    'unexpectedPrivateKey':{'actual':'AliceSecret'}},threshold=.5))
            self.assertTrue(writer.flush());writer.close();writer.thread.join(1)
            raw=writer.path.read_text('utf-8'); row=json.loads(raw)
            for secret in ('AliceSecret','HeroSecret','SecretMessenger','987654','987655','987656','unexpectedPrivateKey'):
                self.assertNotIn(secret,raw)
            self.assertEqual(row['reason'],'capture-error')
            self.assertEqual(row['threshold'],.5)
            self.assertIn('ValueError',raw)
            self.assertIn('client_live_runtime.py',raw)
            self.assertIn('12',raw)

    def test_environment_versions_and_known_collection_failures_survive(self):
        from diagnostic_privacy import safe_record
        result=safe_record(dict(qtVersion='6.11.1',boundWindow=dict(status='unavailable',
            reason='Windows window query unsupported on this platform')))
        self.assertEqual(result['qtVersion'],'6.11.1')
        self.assertEqual(result['boundWindow']['reason'],'Windows window query unsupported on this platform')
        self.assertNotIn('AliceSecret',json.dumps(safe_record(dict(qtVersion='AliceSecret',reason='AliceSecret'))))

if __name__=='__main__':unittest.main()
