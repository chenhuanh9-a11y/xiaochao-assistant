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
from runtime_exclusions import PYTHON_MODULE_EXCLUSIONS, filter_binaries


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

    def test_maintained_spec_collects_support_images_when_present(self):
        with self.qt_spec() as (root, calls, executable, exe, collect, verify, execute):
            execute()
            exe.assert_called_once()
            self.assertIs(exe.call_args.kwargs["uac_admin"], True)
            verify.assert_called_once_with(executable.name)
            self.assertIs(collect.call_args.args[0], executable)
            for name in ("wechat.png", "alipay.png"):
                path = root / "assets/support" / name
                self.assertTrue(path.is_file())
                self.assertIn((str(path), "assets/support"), calls["datas"])
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


if __name__ == "__main__":
    unittest.main()
