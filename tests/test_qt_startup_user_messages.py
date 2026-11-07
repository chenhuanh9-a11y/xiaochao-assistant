"""Default startup copy stays usable; structured evidence remains in the log."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import qt_startup_diagnostics as diagnostics
from managed_base_resources import ManagedBaseResourceError


class StartupUserMessageTests(unittest.TestCase):
    def test_receipt_rejection_names_only_registered_record_in_private_log(self):
        error = ManagedBaseResourceError('installed-package-receipt-rejected', [dict(
            reason='invalid-installed-package-dependencies', path='work/map-packages/nightmare-suju.json'),
            dict(reason='receipt-validation-rejected', path='work/map-packages/private-player-settings.json')])
        with tempfile.TemporaryDirectory(prefix='private-player-') as temp:
            with patch.object(diagnostics, '_candidate_directories', return_value=[Path(temp)]), \
                 patch.object(diagnostics, '_show_native_error') as show, \
                 patch.object(sys, 'stderr', None):
                path = diagnostics.report_startup_failure(error)
            record = path.read_text('utf-8')
            self.assertIn('resourceCode=installed-package-receipt-rejected', record)
            self.assertIn('issue.0.reason=invalid-installed-package-dependencies', record)
            self.assertIn('issue.0.path=work/map-packages/nightmare-suju.json', record)
            self.assertNotIn('private-player', record)
            message = show.call_args.args[0]
            self.assertIn('不要卸载', message)
            self.assertNotIn('nightmare-suju', message)
            self.assertNotIn('损坏', message)

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

    def test_installer_and_reset_blocks_keep_exact_codes_and_safe_actions(self):
        from resource_transaction import ResourceTransactionError
        for code in ('installer-upgrade-recovery-required', 'resource-reset-recovery-required'):
            for error_type in (ManagedBaseResourceError, ResourceTransactionError):
                with self.subTest(code=code, error_type=error_type.__name__), \
                     tempfile.TemporaryDirectory() as temp:
                    with patch.object(diagnostics, '_candidate_directories', return_value=[Path(temp)]), \
                         patch.object(diagnostics, '_show_native_error') as show, \
                         patch.object(sys, 'stderr', None):
                        path = diagnostics.report_startup_failure(error_type(code))
                    record = path.read_text('utf-8')
                    self.assertIn('resourceCode=' + code, record)
                    message = show.call_args.args[0]
                    for wording in ('暂时无法启动', '保留', '安装目录', '诊断记录',
                                    '重新运行安装程序', '核验', '不会自动回退'):
                        self.assertIn(wording, message)
                    for wording in ('具体原因尚未确定', '删除标记', '旧版程序', code):
                        self.assertNotIn(wording, message)

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
