"""Real close dialog buttons; fake window/coordinator actions, no game or install."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class ConciseCloseUiTests(unittest.TestCase):
    def test_cancel_background_and_exit_remain_distinct(self):
        code = r'''
from pathlib import Path
from PySide6.QtCore import QObject, Property, Slot, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
app=QGuiApplication([])
class Coordinator(QObject):
    calls=0
    closing=Property(bool,lambda s:False,constant=True)
    closeReady=Property(bool,lambda s:False,constant=True)
    @Slot()
    def requestClose(self): self.calls+=1
class Windows(QObject):
    calls=[]
    @Slot(str)
    def hide(self,key): self.calls.append(key); window.hide()
owner=Coordinator(); windows=Windows(); engine=QQmlApplicationEngine()
engine.rootContext().setContextProperty('clientCoordinator',owner)
engine.rootContext().setContextProperty('windows',windows)
path=Path('experiments/qt_quick/Client.qml').resolve(); source=path.read_text(encoding='utf-8')
assert '最小化到任务栏' not in source
closing='onClosing:'+source.split('onClosing:',1)[1].split('    RowLayout',1)[0]
choice_start=source.index('    Dialog {\n        id: closeChoice')
choice=source[choice_start:source.index('    Popup {\n',choice_start)]
engine.loadData(('import QtQuick\nimport QtQuick.Controls.Basic\nimport QtQuick.Layouts\nApplicationWindow { id:root; visible:true; width:600; height:500; '+closing+choice+'\n}').encode(),QUrl.fromLocalFile(str(path.with_name('concise-close-test.qml'))))
window=engine.rootObjects()[0]; dialog=window.findChild(QObject,'clientCloseChoice')
def button(name): return window.findChild(QQuickItem,name)
def click(name):
    item=button(name)
    assert item.isVisible() and item.isEnabled()
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint());QTest.qWait(40)
for name,title in [('closeToBackground','后台运行'),('closeApplication','退出星图'),('cancelClose','取消')]:
    assert button(name).property('text')==title
window.close();QTest.qWait(40)
assert dialog.property('visible') and window.isVisible() and owner.calls==0
click('cancelClose');assert not dialog.property('visible') and window.isVisible() and owner.calls==0
window.close();QTest.qWait(40);QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(40)
assert not dialog.property('visible') and window.isVisible() and owner.calls==0
window.close();QTest.qWait(40);click('closeToBackground')
assert windows.calls==['main'] and not window.isVisible() and owner.calls==0
window.show();window.close();QTest.qWait(40);click('closeApplication')
assert owner.calls==1 and not dialog.property('visible')
print('concise close actions passed')
'''
        root=Path(__file__).resolve().parents[2]
        result=subprocess.run([sys.executable,'-c',code],cwd=root,
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONUTF8='1'),
            capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('concise close actions passed',result.stdout)


if __name__=='__main__': unittest.main()
