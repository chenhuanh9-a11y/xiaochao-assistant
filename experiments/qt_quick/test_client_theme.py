"""Render actual gallery and Basic controls against isolated dark/light inputs.

No user instance, settings, native input, or Windows preferences are touched.
Run with QT_THEME_EVIDENCE_DIR to retain screenshots and pixel reports.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def render_case(mode, output):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_QUICK_BACKEND'] = 'software'
    from PySide6.QtCore import QObject, QPointF, Qt, QUrl, QMetaObject
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent, QQmlEngine
    from PySide6.QtTest import QTest
    from client_theme import apply_light_theme
    # The offscreen platform has no OS theme provider. Inject the palette
    # received from a dark platform, in addition to its app-local scheme hint.
    app = QGuiApplication([])
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Microsoft YaHei UI', 10))
    dark = mode.startswith('dark')
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    palette = QPalette()
    for role, color in dict(Window='#202020', WindowText='#ffffff', Base='#000000',
            Text='#ffffff', Button='#303030', ButtonText='#ffffff',
            PlaceholderText='#bbbbbb').items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    if dark:
        app.setPalette(palette)
    if mode != 'dark-unfixed':
        apply_light_theme(app)
    # Reuse the synthetic data/services, but render the production QML file.
    import test_client_gallery_layout as fixture
    gallery = fixture.GalleryLayoutTests()
    gallery.setUp()
    host = QQmlApplicationEngine()
    host.addImportPath(str(Path(__file__).parent))
    host_palette = 'Palette {base: "#000000"; text: "#ffffff"}' if mode == 'dark-unfixed' else 'ClientPalette {}'
    host.loadData(('import QtQuick; Window {width: 1000; height: 650; visible: true; palette: ' + host_palette + '}').encode(), QUrl.fromLocalFile(str(Path(__file__).with_name('ThemeHost.qml'))))
    window_host = host.rootObjects()[0]
    gallery.page.setParentItem(window_host.contentItem())
    gallery.window.hide()
    QTest.qWait(30)
    search = gallery.page.findChild(QObject, 'clientGallerySearch')
    shot = window_host.grabWindow()
    if shot.isNull():
        raise AssertionError('Gallery screenshot is empty')
    shot.save(str(output / (mode + '-gallery.png')))
    point = search.mapToScene(QPointF(search.width()-12, search.height()/2))
    search_pixel = shot.pixelColor(round(point.x()), round(point.y())).name()
    expected = '#000000' if mode == 'dark-unfixed' else '#ffffff'
    assert search_pixel == expected, (mode, search_pixel, expected)
    assert not gallery.warnings, gallery.warnings
    gallery.tearDown()
    window_host.close()
    engine = QQmlEngine()
    engine.addImportPath(str(Path(__file__).parent))
    component = QQmlComponent(engine)
    qml = b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow {
    width: 760; height: 550; visible: true; palette: ClientPalette {}
    Label { x: 24; y: 18; text: "Basic control palette / Input states"; font.pixelSize: 20 }
    TextField { objectName: "input"; x: 24; y: 64; width: 350; text: "Map search / readable text" }
    TextField { objectName: "placeholder"; x: 24; y: 122; width: 350; placeholderText: "Search maps..." }
    TextField { objectName: "disabled"; x: 24; y: 180; width: 350; text: "Disabled search"; enabled: false }
    function openCombo() { floorCombo.popup.open() }
    ComboBox { id: floorCombo; objectName: "combo"; x: 24; y: 238; width: 350; model: ["All floors", "Ground floor", "Upper floor"] }
    ComboBox { x: 24; y: 296; width: 350; enabled: false; model: ["Disabled floor picker"] }
    CheckBox { x: 24; y: 354; text: "Selected option"; checked: true }
    Button { x: 24; y: 410; text: "Disabled action"; enabled: false }
    Popup { objectName: "popup"; x: 420; y: 64; width: 300; height: 130; visible: true
        Column { spacing: 12; Label { text: "Popup / menu surface" } TextField { width: 270; placeholderText: "Popup input" } }
    }
    Dialog { objectName: "dialog"; x: 420; y: 238; width: 300; height: 230; visible: true; title: "Confirm map selection"
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label { text: "Dialog text stays readable." }
    }
}'''
    if mode == 'dark-unfixed':
        qml = qml.replace(b'ClientPalette {}', b'Palette {base: "#000000"; text: "#ffffff"; window: "#202020"; windowText: "#ffffff"; button: "#303030"; buttonText: "#ffffff"}')
    component.setData(qml, QUrl.fromLocalFile(str(Path(__file__).with_name('ThemeControls.qml'))))
    window = component.create()
    assert window, [e.toString() for e in component.errors()]
    QTest.qWait(80)
    field = window.findChild(QObject, 'input')
    field.forceActiveFocus(Qt.TabFocusReason)
    field.setProperty('selectByMouse', True)
    QTest.qWait(30)
    shot = window.grabWindow()
    assert not shot.isNull()
    shot.save(str(output / (mode + '-controls.png')))
    report = dict(mode=mode, scheme=app.styleHints().colorScheme().name,
                  gallerySearchPixel=search_pixel, simulatedPlatformPalette=dark, controls={})
    for name in ('input', 'placeholder', 'disabled', 'combo', 'popup', 'dialog'):
        obj = window.findChild(QObject, name)
        bg = obj.property('background')
        report['controls'][name] = dict(background=bg.property('color').name())
        if name in ('input', 'placeholder', 'disabled'):
            report['controls'][name]['text'] = obj.property('color').name()
    if mode != 'dark-unfixed':
        assert app.styleHints().colorScheme() in (Qt.ColorScheme.Light, Qt.ColorScheme.Unknown)
        for name, state in report['controls'].items():
            assert QColor(state['background']).lightnessF() > .85, (name, state)
        assert field.hasActiveFocus()
        assert report['controls']['disabled']['background'] == '#edf1f6'
        assert report['controls']['disabled']['text'] == '#62738c'
        assert field.property('selectionColor').name() == '#668eed'
        assert field.property('selectedTextColor').name() == '#142b4b'
        # Focus border and text contrast are visible in the rendered output.
        assert shot.pixelColor(24, 80).name() == '#668eed'
    pixels = {'input': (365, 84), 'disabled': (365, 200),
              'combo': (365, 258), 'popup': (710, 180), 'dialog': (710, 450)}
    report['pixels'] = {name: shot.pixelColor(*point).name() for name, point in pixels.items()}
    if mode != 'dark-unfixed':
        for name, color in report['pixels'].items():
            assert QColor(color).lightnessF() > .85, (name, color)
    # Exercise the real ComboBox delegate popup as well as a generic Popup.
    QMetaObject.invokeMethod(window, 'openCombo')
    QTest.qWait(40)
    combo_shot = window.grabWindow()
    combo_shot.save(str(output / (mode + '-combo-open.png')))
    report['comboPopupPixel'] = combo_shot.pixelColor(365, 300).name()
    if mode != 'dark-unfixed':
        assert QColor(report['comboPopupPixel']).lightnessF() > .85
    if mode != 'dark-unfixed':
        selected_text_pixels = sum(combo_shot.pixelColor(x, y).lightnessF() < .4
            for x in range(32, 170) for y in range(285, 315))
        assert selected_text_pixels > 20, selected_text_pixels
        report['selectedComboTextPixels'] = selected_text_pixels
        QMetaObject.invokeMethod(field, 'selectAll')
        QTest.qWait(15)
        window.grabWindow().save(str(output / (mode + '-selection.png')))
    (output / (mode + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    window.close()


class ClientThemeTests(unittest.TestCase):
    def test_dark_palette_regression_and_light_control_states(self):
        with tempfile.TemporaryDirectory(prefix='qt-theme-') as temp:
            output = Path(os.environ.get('QT_THEME_EVIDENCE_DIR', temp)).resolve()
            output.mkdir(parents=True, exist_ok=True)
            for mode in ('dark-unfixed', 'dark-fixed', 'light-fixed'):
                result = subprocess.run([sys.executable, __file__, '--render', mode, str(output)],
                    capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            dark = json.loads((output / 'dark-fixed.json').read_text())
            light = json.loads((output / 'light-fixed.json').read_text())
            self.assertEqual(dark['controls'], light['controls'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--render':
        render_case(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
