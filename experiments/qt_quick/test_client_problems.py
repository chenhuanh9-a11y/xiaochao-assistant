"""Owned signal→QML details→rotating log/export and accepted-write drain only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, QMetaObject, QPointF, QUrl, Signal, Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'app'))
import client_problems as problems
from diagnostic_export import export_safe_diagnostic_bundle


class Emitter(QObject):
    errorReported = Signal(object)


def event(**extra):
    return dict(title='地图画面暂不可用', summary='未取得完整游戏画面，请重新打开游戏地图后再试。',
        raw='ValueError:live-runtime-requires-full-client-capture', phase='capture', action='recognize',
        kind='error', mode='nightmare', mapId='', floor='', captureDiagnostics={'width': 1200, 'height': 800},
        traceback='Traceback\n  File "C:/Private/Player/client.py"\nValueError:live-runtime-requires-full-client-capture', **extra)


class ProblemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        if Path('C:/Windows/Fonts/msyh.ttc').is_file():
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qt-problems-owned-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = problems.ProblemsService(SimpleNamespace(work_root=self.root/'work'),
            build_fingerprint='TEST0001', source_commit='a'*40)
        self.addCleanup(self.drain)

    def wait(self, predicate):
        end = time.monotonic()+4
        while not predicate() and time.monotonic()<end: QTest.qWait(10)
        self.assertTrue(predicate())

    def drain(self):
        self.service.shutdown(); self.wait(lambda: not self.service.pendingWrites)

    def test_signal_visible_copyable_details_log_and_redacted_export(self):
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('problemService',self.service)
        warnings=[];engine.warnings.connect(lambda rows:warnings.extend(str(row) for row in rows))
        qml=Path(__file__).with_name('owned-problems.qml')
        engine.loadData(b'import QtQuick\nimport QtQuick.Controls.Basic\nWindow { visible:true;width:700;height:600;ClientProblemsPanel { anchors.fill:parent } }',QUrl.fromLocalFile(str(qml)))
        self.assertTrue(engine.rootObjects(),warnings);window=engine.rootObjects()[0]
        emitter=Emitter();emitter.errorReported.connect(self.service.record);emitter.errorReported.emit(event())
        QTest.qWait(30)
        def find(item,name):
            if item.objectName()==name:return item
            return next((found for child in item.childItems() if (found:=find(child,name)) is not None),None)
        def click(name):
            button=find(window.contentItem(),name);self.assertIsNotNone(button,(self.service.recent,warnings))
            point=button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(20)
        click('helpProblemsToggle');click('problemOpen-1')
        detail=find(window.contentItem(),'problemDetails-1')
        self.assertTrue(detail.isVisible())
        text=detail.property('text')
        for value in ['画面采集','UTC：','ValueError:live-runtime-requires-full-client-capture','原始调用栈','未取得完整游戏画面']:
            self.assertIn(value,text)
        self.assertTrue(detail.property('readOnly'));self.assertTrue(detail.property('selectByMouse'))
        self.assertTrue(QMetaObject.invokeMethod(detail,'selectAll',Qt.DirectConnection))
        self.assertIn('ValueError:',detail.property('selectedText'))
        self.drain();rows=[json.loads(line) for line in self.service.log_path.read_text('utf-8').splitlines()]
        self.assertEqual(rows[0]['raw'],event()['raw']);self.assertEqual(rows[0]['buildFingerprint'],'TEST0001')
        self.assertEqual(rows[0]['sourceCommit'],'a'*40);self.assertTrue(rows[0]['timestampUTC'].endswith('Z'))
        bundle=export_safe_diagnostic_bundle(self.root,self.root/'work/nightmare-live',app_version='fixture',
            build_fingerprint='TEST0001',destination=self.root/'diagnostic.zip',summary={'recentProblems':self.service.recent})
        with zipfile.ZipFile(bundle['path']) as archive:
            logs=[name for name in archive.namelist() if name.startswith('logs/events-')]
            self.assertTrue(logs)
            payload=archive.read(logs[0]).decode('utf-8')
            self.assertIn(event()['raw'],payload);self.assertNotIn('C:/Private/Player',payload)
            self.assertIn('<local-path>',payload)
        self.assertFalse(warnings,warnings);window.hide();engine.deleteLater();QTest.qWait(10)

    def test_coalescing_bounded_queue_and_shutdown_drains_callback(self):
        started=threading.Event();release=threading.Event();accepted=[];threads=[]
        original=problems._write_batch
        def delayed(path,rows):
            threads.append(threading.get_ident());started.set();release.wait(3)
            original(path,rows)
        with patch.object(problems,'_write_batch',side_effect=delayed):
            self.service.record(event());self.wait(started.is_set)
            for _ in range(100): self.service.record(event())
            self.assertEqual(len(self.service.recent),1);self.assertEqual(self.service.recent[0]['count'],101)
            for index in range(90):
                row=event();row['raw']+=str(index);accepted.append(self.service.record(row))
            self.assertEqual(len(self.service.recent),32);self.assertTrue(self.service.droppedWrites)
            self.assertLessEqual(len(self.service._queue)+self.service._inflight_rows,64)
            self.service.shutdown();self.assertFalse(self.service.record(event()))
            release.set();self.assertTrue(self.service.pool.waitForDone(3000))
            self.assertTrue(self.service.pendingWrites)  # First queued completion has not run on UI yet.
            self.wait(lambda:not self.service.pendingWrites)
        self.assertTrue(all(thread!=threading.get_ident() for thread in threads))
        rows=[json.loads(line) for line in self.service.log_path.read_text('utf-8').splitlines()]
        self.assertEqual(len(rows),1+sum(accepted))

    def test_trace_deduplicates_frame_refresh_without_recent_and_drains(self):
        original=list(self.service.recent)
        recent_notifications=[]
        self.service.recentChanged.connect(lambda:recent_notifications.append(1))
        trace=dict(role='overlay',kind='publish',stage='display',action='refresh',reason='ready',
            mode='nightmare',groupid='similar-198',identityPhase='provisional',provisional=True,
            durablemap='',floor='1',round=2,cycle=7,frame=1,generation=1,token='first',
            window={'name':'reference','visible':True,'hasImage':True,'imageSource':'image://live/1'},
            diag={'available':True})
        self.assertTrue(self.service.recordTrace(trace))
        for index in range(100):
            changed=dict(trace,frame=index+2,generation=index+2,token=str(index))
            changed['window']=dict(trace['window'],imageSource='image://live/'+str(index))
            self.assertTrue(self.service.recordTrace(changed))
        self.assertTrue(self.service.recordTrace(dict(trace,kind='hide',reason='hidden',window={'name':'reference','visible':False,'hasImage':False})))
        self.assertTrue(self.service.recordTrace(trace))  # Recovery is a transition, not a duplicate of the old publish.
        self.service.shutdown();self.assertFalse(self.service.recordTrace(trace))
        self.wait(lambda:not self.service.pendingWrites)
        self.assertEqual(self.service.recent,original)
        self.assertEqual(recent_notifications,[])
        rows=[json.loads(line) for line in self.service.log_path.read_text('utf-8').splitlines()]
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['event']=='qt-runtime-transition' for row in rows))
        self.assertEqual(rows[0]['token'],'first');self.assertEqual(rows[0]['generation'],1)
        self.assertEqual(rows[1]['previousRepeatedCount'],101)
        self.assertEqual(self.service.pool.maxThreadCount(),1)


if __name__=='__main__':unittest.main()
