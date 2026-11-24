from pathlib import Path
import sys
import unittest
import runpy
import json
import os
import tempfile
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from qt_quick_style import pin_quick_controls_style
from runtime_exclusions import (
    PYTHON_MODULE_EXCLUSIONS, filter_binaries, unreferenced_qt_binaries, unused_staged_relative)


class RuntimeExclusionTests(unittest.TestCase):
    def test_only_optional_codecs_are_removed_from_binary_collection(self):
        entries = [(name, "input/" + name, "BINARY") for name in (
            "cv2/opencv_videoio_ffmpeg4120_64.dll", "PIL/_avif.cp313-win_amd64.pyd",
            "cv2/cv2.pyd", "PIL/_webp.cp313-win_amd64.pyd", "PIL/_imaging.cp313-win_amd64.pyd",
            "VCRUNTIME140.dll", "libcrypto-3.dll", "PIL/_avif_helper.dll")]
        retained, removed = filter_binaries(entries)
        self.assertEqual(removed, entries[:2])
        self.assertEqual(retained, entries[2:])

    def test_windows_case_and_separator_do_not_bypass_filter(self):
        row = (r"CV2\OPENCV_VIDEOIO_FFMPEG4120_64.DLL", "same-input", "BINARY")
        self.assertEqual(filter_binaries([row]), ([], [row]))

    def test_format_exclusions_leave_required_image_plugins_enabled(self):
        self.assertEqual(set(PYTHON_MODULE_EXCLUSIONS), {"PIL.AvifImagePlugin", "PIL._avif"})
        self.assertFalse(set(PYTHON_MODULE_EXCLUSIONS) & {
            "PIL.PngImagePlugin", "PIL.JpegImagePlugin", "PIL.WebPImagePlugin", "cv2"})

    @contextmanager
    def qt_spec(self, *, failure=None):
        """Execute the shipped Qt recipe with build IO replaced, never a PE."""
        root = Path(__file__).resolve().parents[1]
        calls = {}
        def analysis(*args, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(binaries=[], pure=[], scripts=[], datas=list(kwargs["datas"]))
        executable = SimpleNamespace(name="fixture-build/XingtuQtClientTest.exe")
        exe, collect = Mock(return_value=executable), Mock()
        hooks = ModuleType("PyInstaller.utils.hooks")
        hooks.copy_metadata = lambda name: []
        with tempfile.TemporaryDirectory() as folder:
            inputs = Path(folder) / "inputs.json"
            inputs.write_text(json.dumps(dict(sourceRoot=str(root), product="XingtuQtClientTest",
                resourceStage=str(root), resources=["assets/support/wechat.png", "assets/support/alipay.png"],
                profilePath=str(Path(folder) / "installation-profile.json"),
                manifestPath="fixture.manifest", versionInfoPath="fixture.version", iconPath="fixture.ico")))
            with patch.dict(os.environ, XINGTU_QT_BUILD_INPUTS=str(inputs)), \
                    patch.dict(sys.modules, {"PyInstaller.utils.hooks": hooks}), \
                    patch("verify_admin_manifest.verify_administrator_manifest", side_effect=failure) as verify:
                def execute():
                    return runpy.run_path(str(root / "tools/qt-client.spec"), init_globals={
                        "SPECPATH": str(root / "tools"), "Analysis": analysis,
                        "PYZ": Mock(), "EXE": exe, "COLLECT": collect,
                    })
                yield root, calls, executable, exe, collect, verify, execute

    @contextmanager
    def import_spec(self):
        """Execute the production import recipe with mocked freeze primitives."""
        root = Path(__file__).resolve().parents[1]
        calls = {}
        binaries = [(name, "fixture/" + name, "BINARY") for name in (
            "PIL/_avif.cp313-win_amd64.pyd", "PIL/_imaging.cp313-win_amd64.pyd",
            "PIL/_webp.cp313-win_amd64.pyd", "numpy/libs/blas.dll", "VCRUNTIME140.dll")]
        required = ("PIL.Image", "PIL.PngImagePlugin", "PIL.JpegImagePlugin", "numpy", "numpy.linalg")
        def analysis(*args, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(binaries=list(binaries),
                pure=[(name, "fixture.py", "PYMODULE") for name in required], scripts=[], datas=[])
        executable = SimpleNamespace(name="fixture-build/star-map-import-helper.exe")
        collect = Mock()
        with tempfile.TemporaryDirectory() as folder:
            inputs = Path(folder) / "inputs.json"
            inputs.write_text(json.dumps(dict(sourceRoot=str(root), product="星图",
                profilePath=str(Path(folder) / "installation-profile.json"))))
            with patch.dict(os.environ, XINGTU_QT_BUILD_INPUTS=str(inputs)), \
                    patch("build_qt_client.verify_invoker_manifest") as verify:
                def execute():
                    return runpy.run_path(str(root / "tools/qt-import-helper.spec"), init_globals={
                        "SPECPATH": str(root / "tools"), "Analysis": analysis,
                        "PYZ": Mock(), "EXE": Mock(return_value=executable), "COLLECT": collect,
                    })
                yield calls, binaries, collect, executable, verify, execute

    def test_import_spec_excludes_only_shared_optional_image_codecs(self):
        with self.import_spec() as (calls, _, _, _, _, execute):
            execute()
            self.assertTrue(set(PYTHON_MODULE_EXCLUSIONS) <= set(calls["excludes"]))
            self.assertFalse(set(calls["excludes"]) & {
                "PIL", "numpy", "numpy.linalg", "PIL.Image", "PIL.PngImagePlugin",
                "PIL.JpegImagePlugin", "PIL.WebPImagePlugin"})

    def test_import_spec_filters_optional_avif_but_keeps_required_native_libraries(self):
        with self.import_spec() as (_, binaries, collect, executable, verify, execute):
            execute()
            self.assertEqual(collect.call_args.args[1], binaries[1:])
            self.assertIs(collect.call_args.args[0], executable)
            verify.assert_called_once_with(executable.name)

    def test_maintained_spec_collects_support_images_when_present(self):
        with self.qt_spec() as (root, calls, executable, exe, collect, verify, execute):
            execute()
            exe.assert_called_once()
            self.assertIs(exe.call_args.kwargs["uac_admin"], True)
            verify.assert_called_once_with(executable.name)
            self.assertIs(collect.call_args.args[0], executable)
            collected = {(source, Path(target).as_posix()) for source, target in calls["datas"]}
            for name in ("wechat.png", "alipay.png"):
                path = root / "assets/support" / name
                self.assertTrue(path.is_file())
                self.assertIn((str(path), "assets/support"), collected)
            self.assertIn((str(root / "experiments/qt_quick/tutorial-chapters.json"), "."), calls["datas"])
            self.assertTrue(any(target == "tutorial" for _, target in calls["datas"]))
            self.assertTrue({"tkinter", "_tkinter", "PIL.ImageTk"} <= set(calls["excludes"]))

    def test_maintained_spec_stops_collection_when_manifest_verification_fails(self):
        with self.qt_spec(failure=ValueError("invalid embedded manifest")) as (_, _, executable, exe, collect, verify, execute):
            with self.assertRaisesRegex(ValueError, "invalid embedded manifest"):
                execute()
            self.assertIs(exe.call_args.kwargs["uac_admin"], True)
            verify.assert_called_once_with(executable.name)
            collect.assert_not_called()

    def test_maintained_spec_excludes_test_source_but_keeps_runtime_helpers(self):
        with self.qt_spec() as (root, calls, _, _, _, _, execute):
            execute()
            qt_root = root / "experiments/qt_quick"
            collected = {Path(source) for source, _ in calls["datas"]}
            tests = set(qt_root.glob("test_*.py"))
            self.assertTrue(tests, "exercise the actual recipe's test-source inputs")
            self.assertFalse(collected & tests)
            # The source-based helper and explicit acceptance probe remain
            # available. This exclusion is not a blanket Python-file trim.
            for name in ("client_info.py", "client_live_runtime.py", "probe_client.py"):
                self.assertIn(qt_root / name, collected)


class PayloadPruneTests(unittest.TestCase):
    def test_unused_payload_keeps_basic_chinese_and_image_decoders(self):
        kept = [
            "PySide6/plugins/platforms/qwindows.dll",
            "PySide6/plugins/imageformats/qjpeg.dll",
            "PySide6/plugins/imageformats/qwebp.dll",
            "PySide6/plugins/imageformats/qgif.dll",
            "PySide6/plugins/imageformats/qico.dll",
            "PySide6/plugins/tls/qschannelbackend.dll",
            "PySide6/plugins/tls/qcertonlybackend.dll",
            "PySide6/qml/QtQuick/Controls/qmldir",
            "PySide6/qml/QtQuick/Controls/Basic/Button.qml",
            "PySide6/qml/QtQuick/Controls/impl/qmldir",
            "PySide6/qml/QtQuick/Dialogs/quickimpl/qml/FileDialog.qml",
            "PySide6/qml/Qt/labs/folderlistmodel/qmldir",
            "PySide6/qml/QtQml/Models/qmldir",
            "PySide6/qml/QtQml/WorkerScript/qmldir",
            "PySide6/qml/QtTest/qmldir",
            "PySide6/translations/qtbase_zh_CN.qm",
            "PySide6/translations/qtdeclarative_zh_CN.qm",
            "PySide6/Qt6Core.dll",
            "PySide6/Qt6Quick.dll",
            "PySide6/QtNetwork.pyd",
            "PySide6/QtTest.pyd",
            "cv2/cv2.pyd",
            "cv2/data/__init__.py",
            "PIL/_webp.cp313-win_amd64.pyd",
            "_internal-not-really/csharp-grid/coreclr.dll",
        ]
        dropped = [
            "PySide6/qml/QtQuick/Controls/FluentWinUI3/Button.qml",
            "PySide6/qml/QtQuick/Controls/Material/Button.qml",
            "PySide6/qml/QtQuick/Controls/Fusion/Button.qml",
            "PySide6/qml/QtQuick/Controls/Imagine/Button.qml",
            "PySide6/qml/QtQuick/Controls/Universal/Button.qml",
            "PySide6/qml/QtQuick/Controls/Windows/Button.qml",
            "PySide6/qml/QtQuick/NativeStyle/qmldir",
            "PySide6/qml/Qt/labs/platform/qmldir",
            "PySide6/qml/QtQuick/VirtualKeyboard/qmldir",
            "PySide6/plugins/platforms/qdirect2d.dll",
            "PySide6/plugins/platforms/qoffscreen.dll",
            "PySide6/plugins/imageformats/qtiff.dll",
            "PySide6/plugins/imageformats/qpdf.dll",
            "PySide6/plugins/tls/qopensslbackend.dll",
            "PySide6/plugins/sqldrivers/qsqlite.dll",
            "PySide6/plugins/qmltooling/qmldbg_debugger.dll",
            "PySide6/translations/qtbase_de.qm",
            "PySide6/translations/qtbase_zh_TW.qm",
            r"PySide6\translations\qt_fr.qm",
            "PySide6/opengl32sw.dll",
            "cv2/data/haarcascade_frontalface_default.xml",
            "PySide6/QtCore.pyi",
            "somewhere/debug.pdb",
        ]
        self.assertTrue(all(unused_staged_relative(path) is None for path in kept), kept)
        self.assertTrue(all(unused_staged_relative(path) for path in dropped), dropped)

    def test_retains_shiboken_imports_but_removes_unused_bindings(self):
        files = [
            "PySide6/QtQuick.pyd",
            "PySide6/Qt6Quick.dll",
            "PySide6/Qt6Core.dll",
            "PySide6/QtOpenGL.pyd",
            "PySide6/Qt6OpenGL.dll",
            "PySide6/QtTest.pyd",
            "PySide6/Qt6Widgets.dll",
            "PySide6/QtWidgets.pyd",
            "PySide6/QtSvg.pyd",
            "PySide6/Qt6Svg.dll",
            "PySide6/plugins/platforms/qwindows.dll",
            "numpy.libs/libopenblas.dll",
            "csharp-grid/coreclr.dll",
        ]
        imports = {
            "PySide6/QtQuick.pyd": ["Qt6Quick.dll"],
            "PySide6/Qt6Quick.dll": ["Qt6Core.dll", "Qt6OpenGL.dll"],
            "PySide6/Qt6Core.dll": ["icuuc.dll"],
            "PySide6/QtOpenGL.pyd": ["Qt6OpenGL.dll"],
            "PySide6/Qt6OpenGL.dll": ["Qt6Core.dll"],
            "PySide6/QtTest.pyd": ["Qt6Widgets.dll", "Qt6Core.dll"],
            "PySide6/Qt6Widgets.dll": ["Qt6Core.dll"],
            "PySide6/QtWidgets.pyd": ["Qt6Widgets.dll"],
            "PySide6/QtSvg.pyd": ["Qt6Svg.dll"],
            "PySide6/Qt6Svg.dll": ["Qt6Core.dll"],
            "PySide6/plugins/platforms/qwindows.dll": ["Qt6Core.dll"],
            "numpy.libs/libopenblas.dll": [],
            "csharp-grid/coreclr.dll": [],
        }
        removed = set(unreferenced_qt_binaries(files, lambda rel: imports[rel]))
        self.assertEqual(removed, {"PySide6/QtSvg.pyd", "PySide6/Qt6Svg.dll"})

    def test_quick_controls_style_is_basic_before_qml(self):
        os.environ.pop("QT_QUICK_CONTROLS_STYLE", None)
        pin_quick_controls_style()
        self.assertEqual(os.environ["QT_QUICK_CONTROLS_STYLE"], "Basic")


if __name__ == "__main__":
    unittest.main()
