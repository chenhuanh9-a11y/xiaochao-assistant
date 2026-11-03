"""Presentation-only classification and Qt consumers; no native input."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from client_problem_messages import operation_problem, input_message
from client_input import NativeShortcutBackend
from client_problems import ProblemsService
import test_client_chest_auto_tap as chest_fixtures
import test_client_toast as toast_fixtures

APP=QGuiApplication.instance() or QGuiApplication([])


class UserMessageTests(unittest.TestCase):
    def test_known_reasons_explain_effect_and_next_step_without_changing_raw(self):
        cases=[('game-not-foreground','游戏不在前台','切回游戏'),
               ('capture-enable-rejected','游戏画面采集未启动','连接状态'),
               ('structure-work-obsolete','本次识别已过期','重新打开地图'),
               ('installed-resource-unavailable','当前地图资源不可用','地图资源')]
        for raw,title,next_step in cases:
            with self.subTest(raw=raw):
                receipt=dict(reason=raw,error=True,action='map',traceback='original stack')
                row=operation_problem(receipt,{})
                self.assertEqual(row['title'],title)
                self.assertIn(next_step,row['summary'])
                self.assertEqual(row['raw'],raw)
                self.assertEqual(row['traceback'],'original stack')
                self.assertEqual(row['kind'],'error')
                self.assertNotIn(raw,row['summary'])

    def test_unknown_prose_or_filename_never_invents_focus_or_package_damage(self):
        for raw in [r'PermissionError: C:\capture\foreground.png',
                    'unrecognized-package-state', 'failed to parse capture settings']:
            row=operation_problem(dict(reason=raw,error=True),{})
            self.assertEqual(row['title'],'操作未完成')
            self.assertIn('暂时无法确定原因',row['summary'])
            self.assertEqual(row['raw'],raw)
            for claim in ('游戏不在前台','资源损坏','重新安装','删除'):
                self.assertNotIn(claim,row['summary'])

    def test_input_public_message_keeps_original_diagnostic_and_runtime_flags(self):
        keys=NativeShortcutBackend(SimpleNamespace())
        try:
            raw=r'Native shortcuts unavailable: [WinError 5] C:\private\source.py'
            keys._stop('failed',raw)
            state=keys.snapshot
            self.assertEqual(state['rawMessage'],raw)
            self.assertEqual(state['message'],keys.message)
            self.assertNotIn('WinError',state['message'])
            self.assertNotIn('private',state['message'])
            self.assertFalse(state['enabled'])
            self.assertEqual(state['status'],'failed')
            self.assertEqual(state['readPermission'],'unknown')
            self.assertIn('技术详情',state['message'])
            self.assertIn('无法确认',input_message('unknown','unclassified-native-error'))
        finally:
            keys.shutdown()

    def test_chest_reports_only_real_error_and_preserves_raw_once(self):
        fixture=chest_fixtures.ChestTests();fixture.setUp()
        try:
            rows=[];fixture.adapter.errorReported.connect(rows.append)
            fixture.focus=99;fixture.enable();fixture.adapter.pollOnce()
            self.assertEqual(rows,[])
            fixture.focus=42;fixture.adapter.pollOnce()
            raw='释放按键 VK 70 失败（Windows 5）；已停止连点并有限重试。'
            fixture.service.state.update(error=raw,status=raw,enabled=False)
            fixture.adapter.pollOnce();fixture.adapter.pollOnce()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['raw'],raw)
            self.assertEqual(rows[0]['kind'],'error')
            self.assertIn('松开触发键',rows[0]['summary'])
            self.assertNotIn('VK 70',rows[0]['summary'])
            state=fixture.adapter.snapshot
            self.assertEqual(state['rawError'],raw)
            self.assertEqual(state['rawStatus'],raw)
            self.assertFalse(state['enabled'])
        finally:
            fixture.doCleanups()

    def test_unknown_chest_error_keeps_raw_in_problems_and_chinese_summary(self):
        fixture=chest_fixtures.ChestTests();fixture.setUp()
        try:
            with tempfile.TemporaryDirectory() as directory:
                service=ProblemsService(SimpleNamespace(work_root=Path(directory)))
                fixture.adapter.errorReported.connect(service.record)
                raw=r'UnexpectedNativeError: C:\private\chest.py code=123'
                fixture.service.state.update(error=raw,status=raw,enabled=False)
                fixture.adapter.pollOnce();fixture.adapter.pollOnce()
                state=fixture.adapter.snapshot
                self.assertEqual(state['rawError'],raw)
                self.assertEqual(state['rawStatus'],raw)
                self.assertIn('暂时无法确定原因',state['message'])
                self.assertNotIn('private',state['message'])
                self.assertEqual(len(service.recent),1)
                row=service.recent[0]
                self.assertEqual(row['raw'],raw)
                self.assertEqual(row['summary'],state['message'])
                self.assertIn(raw,service.detailText(row['id']))
                service.shutdown();service.pool.waitForDone(3000);APP.processEvents()
        finally:
            fixture.doCleanups()

    def test_chest_error_clear_allows_same_error_to_be_reported_again(self):
        fixture=chest_fixtures.ChestTests();fixture.setUp()
        try:
            rows=[];fixture.adapter.errorReported.connect(rows.append)
            raw='UnexpectedNativeError: recurring failure'
            fixture.service.state.update(error=raw,status=raw,enabled=False)
            fixture.adapter.pollOnce();fixture.adapter.pollOnce()
            self.assertEqual(len(rows),1)
            fixture.service.state.update(error='',status='已暂停',enabled=False)
            fixture.adapter.pollOnce()
            self.assertEqual(len(rows),1)
            self.assertEqual(fixture.adapter.snapshot['rawError'],'')
            fixture.service.state.update(error=raw,status=raw,enabled=False)
            fixture.adapter.pollOnce();fixture.adapter.pollOnce()
            self.assertEqual(len(rows),2)
            self.assertEqual([row['raw'] for row in rows],[raw,raw])
            self.assertFalse(fixture.adapter.snapshot['enabled'])
            self.assertFalse(fixture.adapter.snapshot['running'])
        finally:
            fixture.doCleanups()

    def test_coordinator_input_failure_preserves_raw_and_deduplicates_by_raw(self):
        from test_client_coordinator import CoordinatorTests
        fixture=CoordinatorTests();fixture.app=APP;fixture.setUp()
        try:
            with tempfile.TemporaryDirectory() as directory:
                service=ProblemsService(SimpleNamespace(work_root=Path(directory)))
                fixture.coordinator.errorReported.connect(service.record)
                notifications=[]
                fixture.coordinator.notificationRequested.connect(lambda *row:notifications.append(row))
                for code in (123,123,124):
                    raw=fr'UnknownNativeError: C:\private\input.py code={code}'
                    fixture.input.snapshot=dict(status='failed',rawMessage=raw,
                        message=input_message('failed',raw))
                    fixture.input.changed.emit()
                self.assertEqual(len(service.recent),2)
                self.assertEqual(len(notifications),2)
                for row in service.recent:
                    self.assertIn('UnknownNativeError',row['raw'])
                    self.assertIn(row['raw'],service.detailText(row['id']))
                    self.assertIn('技术详情',row['summary'])
                    self.assertNotIn('private',row['summary'])
                    self.assertNotIn('UnknownNativeError',row['summary'])
                self.assertEqual(notifications[-1][1],service.recent[0]['summary'])
                service.shutdown();service.pool.waitForDone(3000);APP.processEvents()
        finally:
            fixture.tearDown()

    def test_log_write_failure_has_public_summary_and_unmodified_technical_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ProblemsService(SimpleNamespace(work_root=Path(directory)))
            raw=r'[WinError 5] Access denied: C:\private\log.jsonl'
            service._finished(raw)
            self.assertIn('暂时无法保存',service.writeError)
            self.assertNotIn('WinError',service.writeError)
            self.assertEqual(service.writeErrorDetails,raw)
            service.shutdown()

    def test_toast_uses_plain_text_for_literal_error_content(self):
        fixture=toast_fixtures.ToastTests();fixture.setUp()
        try:
            toast=fixture.toast
            toast.present('<b>失败</b>','路径 <unknown> 未确认','error')
            APP.processEvents()
            title=toast.findChild(QObject,'toastTitle')
            message=toast.findChild(QObject,'toastMessage')
            self.assertEqual(title.property('text'),'<b>失败</b>')
            self.assertEqual(message.property('text'),'路径 <unknown> 未确认')
            from PySide6.QtQml import QQmlExpression
            for label in (title,message):
                expression=QQmlExpression(fixture.engine.rootContext(),label,'textFormat === 0')
                value=expression.evaluate()
                self.assertFalse(expression.hasError())
                self.assertTrue(value[0] if isinstance(value,tuple) else value)
        finally:
            fixture.doCleanups()


if __name__=='__main__':unittest.main()
