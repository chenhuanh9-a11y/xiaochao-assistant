"""Production QML controls, direct Qt input and rendered pixels at three DPIs.

Isolated windows and synthetic data only; no product instance or native input.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def render_case(output):
    from PySide6.QtCore import QObject, QPoint, Qt, QUrl
    from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtTest import QTest
    from PySide6.QtQuick import QQuickItem
    app = QGuiApplication([])
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Microsoft YaHei UI', 10))
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(b'''import QtQuick
import QtQuick.Controls.Basic
Window {
    width: 640; height: 360; visible: true; color: "#f1f5fc"
    AppSlider {objectName: "slider"; x: 24; y: 24; width: 300; from: 0; to: 100; value: 20; stepSize: 1}
    AppSwitch {objectName: "switch"; x: 24; y: 86; text: "Enable automatic tapping"}
    AppCheckBox {objectName: "check"; x: 24; y: 146; width: 560; text: "Save limited failure evidence for the current session"}
    Rectangle {x: 24; y: 220; width: 590; height: 100; radius: 16; color: "#244fc2"
        Row {x: 16; y: 28; spacing: 16
            AppButton {inverse: true; text: "Open controls"}
            AppButton {onColor: true; text: "Return to game"}
        }
    }
}''', QUrl.fromLocalFile(str(Path(__file__).with_name('SharedControlsProbe.qml'))))
    window = component.create()
    assert window, [error.toString() for error in component.errors()]
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    QTest.qWait(60)
    slider = window.findChild(QObject, 'slider')
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(90, 39))
    assert slider.property('pressed')
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(280, 39))
    assert not slider.property('pressed')
    assert slider.property('value') > 70
    slider.setProperty('value', 0)
    slider.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Right)
    assert slider.property('value') == 1
    QTest.keyClick(window, Qt.Key_Left)
    assert slider.property('value') == 0
    slider.setProperty('enabled', False)
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(50, 39))
    QTest.keyClick(window, Qt.Key_Right)
    assert slider.property('value') == 0
    slider.setProperty('enabled', True)
    for name in ('switch', 'check'):
        item = window.findChild(QObject, name)
        item.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Space)
        assert item.property('checked'), name
        indicator, label = item.property('indicator'), item.property('contentItem')
        # The label's actual text inset must clear the indicator at every DPI.
        assert label.x() + label.property('leftPadding') >= indicator.x() + indicator.width(), name
        item.setProperty('enabled', False)
        QTest.keyClick(window, Qt.Key_Space)
        assert item.property('checked'), name
        item.setProperty('enabled', True)
        QTest.keyClick(window, Qt.Key_Space)
        assert not item.property('checked'), name
    window.findChild(QObject, 'check').forceActiveFocus()
    QTest.qWait(160)
    shot = window.grabWindow()
    assert not shot.isNull()
    dpr = window.devicePixelRatio()
    assert shot.width() == round(window.width() * dpr), (shot.width(), dpr)
    assert shot.height() == round(window.height() * dpr)
    assert shot.save(str(output))
    assert not warnings, warnings
    window.close()
    del window, component, engine


class SharedControlsTests(unittest.TestCase):
    def test_mouse_keyboard_disable_and_label_geometry_at_100_150_200_percent(self):
        with tempfile.TemporaryDirectory(prefix='pr73-controls-') as temp:
            output = Path(os.environ.get('PR73_QT_EVIDENCE_DIR', temp))
            output.mkdir(parents=True, exist_ok=True)
            for scale in ('1', '1.5', '2'):
                with self.subTest(scale=scale):
                    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR=scale)
                    result = subprocess.run([sys.executable, '-B', __file__, '--render', str(output / (scale + '.png'))],
                        env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--render':
        render_case(Path(sys.argv[2]))
    else:
        unittest.main()
