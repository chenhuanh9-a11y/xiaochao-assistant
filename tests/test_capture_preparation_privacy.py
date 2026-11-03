"""Closed disk/export schema keeps preparation evidence, never foreign identity."""
import json
import unittest
from diagnostic_privacy import safe_record, sanitize_log_payload


class CapturePreparationPrivacyTests(unittest.TestCase):
    def record(self):
        return dict(event='map-preparation', reason='capture-safe-fallback-unsupported',
            preparation=dict(openingId='a'*32, ordinal=2, phase='capture-result', inputSequence=3,
                probe=1, captureErrors=1, exception=dict(type='CaptureFallbackUnsupported', errno=5,
                    reason='capture-safe-fallback-unsupported'),
                capture=dict(provider='client-capture', backend='client-region-pipeline', phase='acquisition',
                    foreground='verified-before-capture', captureIntent='full-client',
                    captureCapability=dict(windowsBuild=17763, minimumWindowsBuild=19041,
                        safeFallback='unsupported', clientDc='failed', fallbackRequired=True),
                    fallbackTrigger=dict(type='OSError', reason='client-dc-native-error', errno=5, winerror=5))))

    def test_preparation_nested_capability_survives_disk_and_export(self):
        row = self.record()
        self.assertEqual(safe_record(row), row)
        payload = (json.dumps(row)+'\n').encode()
        self.assertEqual(json.loads(sanitize_log_payload(payload)), row)
        self.assertEqual(safe_record(safe_record(row)), row)

    def test_classified_exception_type_survives_without_its_private_message(self):
        value = safe_record(dict(raw='CaptureFallbackUnsupported: C:/private/details'))
        self.assertEqual(value['raw'], 'CaptureFallbackUnsupported: <redacted-message>')

    def test_nested_metadata_cannot_bypass_existing_privacy_filters(self):
        row = self.record()
        capture = row['preparation']['capture']
        for container in (row['preparation'], capture, capture['captureCapability'], capture['fallbackTrigger']):
            container.update(path='C:/private/log', account='private-person', arbitraryKey='private-detail',
                foregroundHwnd=999, foregroundPid=123, pid=321, hwnd=999,
                title='private window title', message='private text', detail={'path':'C:/private/file'})
        capture['boundWindow'] = dict(role='other', hwnd=999, pid=123, name='private app')
        saved = safe_record(row)
        encoded = json.dumps(saved)
        for private in ('private', 'foregroundHwnd', 'foregroundPid', 'arbitraryKey', '999', '123', '321'):
            self.assertNotIn(private, encoded)
        self.assertEqual(saved['preparation']['capture']['boundWindow'], {'role':'other'})
        capability = saved['preparation']['capture']['captureCapability']
        self.assertEqual(capability['minimumWindowsBuild'], 19041)
        self.assertEqual(saved['preparation']['exception']['type'], 'CaptureFallbackUnsupported')


if __name__ == '__main__':
    unittest.main()
