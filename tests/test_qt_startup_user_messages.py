"""Default startup copy stays usable; structured evidence remains in the log."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import qt_startup_diagnostics as diagnostics
from managed_base_resources import ManagedBaseResourceError


class StartupUserMessageTests(unittest.TestCase):
    def test_resource_default_does_not_dump_codes_paths_or_guess_corruption(self):
        error = ManagedBaseResourceError('base-repair-preflight-blocked', [dict(
            reason='installed-package-owned-conflict',
            path='_internal/references/author-packs/nightingale/catalog.json',
            expected=dict(bytes=1, sha256='a' * 64),
            actual=dict(bytes=2, sha256='b' * 64, status='hashed'))])
        with tempfile.TemporaryDirectory(prefix='private-player-') as temp:
            with patch.object(diagnostics, '_candidate_directories', return_value=[Path(temp)]), \
                 patch.object(diagnostics, '_show_native_error') as show, \
                 patch.object(sys, 'stderr', None):
                path = diagnostics.report_startup_failure(error)
            message = show.call_args.args[0]
            self.assertIn('启动', message)
            self.assertIn('反馈', message)
            self.assertNotIn('损坏', message)
            for technical in ('base-repair-', '_internal/', 'a' * 64, temp, '归属', '初始化'):
                self.assertNotIn(technical, message)
            record = path.read_text('utf-8')
            self.assertIn('resourceCode=base-repair-preflight-blocked', record)
            self.assertIn('issue.0.path=_internal/references/author-packs/nightingale/catalog.json', record)
            self.assertIn('issue.0.expected.sha256=' + 'a' * 64, record)

    def test_unsupported_storage_is_not_described_as_a_corrupt_map_package(self):
        from resource_transaction import ResourceTransactionError
        error = ResourceTransactionError('resource-hardlinks-unavailable')
        with patch.object(diagnostics, '_persist', return_value=None), \
             patch.object(diagnostics, '_show_native_error') as show, \
             patch.object(sys, 'stderr', None):
            diagnostics.report_startup_failure(error)
        message = show.call_args.args[0]
        self.assertIn('安装位置', message)
        self.assertNotIn('硬链接', message)
        self.assertNotIn('损坏', message)
        self.assertNotIn('重新下载', message)
        self.assertNotIn('resource-hardlinks-unavailable', message)


if __name__ == '__main__':
    unittest.main()
