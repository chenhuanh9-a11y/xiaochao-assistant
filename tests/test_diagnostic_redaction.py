"""Real ZIP export with synthetic data only; never reads player diagnostics."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from diagnostic_export import export_safe_diagnostic_bundle


class DiagnosticRedactionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.live = self.root / 'live'
        self.live.mkdir()

    def export(self, summary=None):
        return export_safe_diagnostic_bundle(self.root, self.live,
            app_version='fixture', build_fingerprint='FIXTURE', summary=summary)['path']

    def test_jsonl_and_summary_preserve_traceback_quotes_and_structure(self):
        trace = ('Traceback (most recent call last):\n'
                 '  File "C:\\Users\\Private Player\\map.py", line 7, in solve\n'
                 '    raise ValueError("bad \\\\ value")\nValueError: sample failure\n')
        row = dict(event='problem', traceback=trace, count=2,
                   nested=[dict(path=r'\\server\PrivateShare\map.png'), None, True, 42],
                   quote='He said "retry"; slash \\ is literal')
        data = '\n'.join(json.dumps(dict(row, sequence=n), ensure_ascii=False) for n in (1, 2)) + '\n'
        source = self.live / 'events-fixture.jsonl'
        source.write_text(data, 'utf-8')
        with zipfile.ZipFile(self.export(dict(recentProblems=[row]))) as archive:
            rows = [json.loads(line) for line in archive.read('logs/events-fixture.jsonl').splitlines()]
            summary = json.loads(archive.read('qt-client-summary.json'))
            for value in [*rows, summary['recentProblems'][0]]:
                self.assertEqual(value['count'], 2)
                self.assertEqual(value['nested'][1:], [None, True, 42])
                self.assertEqual(value['quote'], row['quote'])
                self.assertIn('line 7, in solve', value['traceback'])
                self.assertIn('ValueError: sample failure', value['traceback'])
                self.assertEqual(value['traceback'].count('\n'), trace.count('\n'))
                self.assertIn('<local-path>', value['traceback'])
            payload = archive.read('logs/events-fixture.jsonl') + archive.read('qt-client-summary.json')
            self.assertNotIn(b'Private Player', payload)
            self.assertNotIn(b'PrivateShare', payload)
        self.assertEqual(source.read_text('utf-8'), data)

    def test_nested_paths_in_values_and_keys_do_not_leak_or_drop_entries(self):
        row = {r'C:\Users\First\a': dict(path="D:/O'Neil/秘密地图.png"),
               r'C:\Users\Second\b': [r'\\server\Hidden\b', {'ok': 'unchanged'}],
               '<local-path>': 'preexisting', 'number': 3}
        source = self.live / 'events-pretty.jsonl'
        source.write_text(json.dumps(row, ensure_ascii=False, indent=2), 'utf-8')
        with zipfile.ZipFile(self.export(row)) as archive:
            for member in ('logs/events-pretty.jsonl', 'qt-client-summary.json'):
                payload = archive.read(member)
                parsed = json.loads(payload)
                self.assertEqual(len(parsed), len(row))
                self.assertEqual(parsed['number'], 3)
                self.assertEqual(parsed['<local-path>'], 'preexisting')
                for secret in ('First', 'Second', "O'Neil", '秘密地图', 'Hidden'):
                    self.assertNotIn(secret, payload.decode('utf-8'))

    def test_plain_text_keeps_frame_lines_and_error_type(self):
        text = ('Traceback:\n  File "C:\\Private Person\\frame.py", line 12\n'
                "  File 'D:/Private Other/frame.py', line 13\n"
                'ValueError: sample\nURL https://example.test/help\n')
        (self.live / 'startup-error-fixture.log').write_text(text, 'utf-8')
        with zipfile.ZipFile(self.export()) as archive:
            result = archive.read('logs/startup-error-fixture.log').decode('utf-8')
        for expected in ('line 12', 'line 13', 'ValueError: sample', 'https://example.test/help'):
            self.assertIn(expected, result)
        self.assertNotIn('Private Person', result)
        self.assertNotIn('Private Other', result)

    def test_partial_line_does_not_break_other_jsonl_records(self):
        text = '{"path":"C:/Hidden/incomplete\n' + json.dumps(dict(path=r'C:\Hidden\ok', n=2)) + '\n'
        (self.live / 'events-partial.jsonl').write_text(text, 'utf-8')
        with zipfile.ZipFile(self.export()) as archive:
            payload = archive.read('logs/events-partial.jsonl')
        self.assertNotIn(b'Hidden', payload)
        self.assertEqual(json.loads(payload.splitlines()[1])['n'], 2)

    def test_unicode_separators_inside_jsonl_strings_are_not_record_boundaries(self):
        for separator in ('\u2028', '\u2029', '\u0085'):
            with self.subTest(separator=repr(separator)):
                trace = 'prefix' + separator + 'File "C:\\Secret\\frame.py", line 7\nValueError: sample'
                text = '\r\n'.join(json.dumps(dict(n=n, traceback=trace), ensure_ascii=False) for n in (1, 2)) + '\r\n'
                (self.live / 'events-unicode.jsonl').write_bytes(text.encode('utf-8'))
                with zipfile.ZipFile(self.export()) as archive:
                    payload = archive.read('logs/events-unicode.jsonl').decode('utf-8')
                rows = [json.loads(line) for line in payload.split('\n') if line.strip()]
                self.assertEqual([r['n'] for r in rows], [1, 2])
                self.assertTrue(all(separator in r['traceback'] for r in rows))
                self.assertNotIn('Secret', payload)

    def test_single_quoted_paths_with_apostrophes_are_fully_redacted(self):
        for path in ("D:/O'Neil/PrivateSecret/frame.py", "D:/O' Neil/PrivateSecret/frame.py",
                     "D:/O', line 9/PrivateSecret/frame.py",
                     "D:/O', line 9, in pretend/PrivateSecret/frame.py",
                     "\\\\server\\O'Neil\\PrivateSecret\\frame.py"):
            with self.subTest(path=path):
                trace = "File '" + path + "', line 15, in 'solve'\nValueError: sample"
                row = dict(traceback=trace, message="OSError: '" + path + "': failed")
                (self.live / 'events-quotes.jsonl').write_text(json.dumps(row) + '\n', 'utf-8')
                (self.live / 'startup-error-quotes.log').write_text(trace, 'utf-8')
                with zipfile.ZipFile(self.export(row)) as archive:
                    for member in ('logs/events-quotes.jsonl', 'qt-client-summary.json'):
                        raw = archive.read(member).decode('utf-8')
                        parsed = json.loads(raw)
                        self.assertIn("line 15, in 'solve'", parsed['traceback'])
                        self.assertIn('ValueError: sample', parsed['traceback'])
                        self.assertIn('OSError:', parsed['message'])
                        self.assertNotIn('PrivateSecret', raw)
                        self.assertNotIn('Neil', raw)
                    raw = archive.read('logs/startup-error-quotes.log').decode('utf-8')
                    self.assertNotIn('PrivateSecret', raw)
                    self.assertIn("line 15, in 'solve'", raw)


if __name__ == '__main__':
    unittest.main()
