from pathlib import Path
import sys
import unittest
import runpy
from types import SimpleNamespace
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

    def test_maintained_spec_collects_support_images_when_present(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / "assets/support").is_dir():
            self.skipTest("Support-window PR not present in this checkout")
        calls = {}
        def analysis(*args, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(binaries=[], pure=[], scripts=[], datas=kwargs["datas"])
        executable = SimpleNamespace(name="fixture-build/xiaochao-assistant.exe")
        exe = Mock(return_value=executable)
        collect = Mock()
        # This is a spec-wiring fixture, not a built PE. Patch the module import
        # used by the spec; real manifest validation has separate contract tests.
        with patch("verify_admin_manifest.verify_administrator_manifest") as verify:
            runpy.run_path(str(root / "tools/xiaochao-assistant.spec"), init_globals={
                "SPECPATH": str(root / "tools"), "Analysis": analysis,
                "PYZ": lambda *a, **kw: None, "EXE": exe, "COLLECT": collect,
            })
        exe.assert_called_once()
        self.assertIs(exe.call_args.kwargs["uac_admin"], True)
        verify.assert_called_once_with(executable.name)
        self.assertIs(collect.call_args.args[0], executable)
        self.assertIn((str(root / "assets/support"), "assets/support"), calls["datas"])
        self.assertTrue((root / "assets/support/wechat.png").is_file())
        self.assertTrue((root / "assets/support/alipay.png").is_file())


if __name__ == "__main__":
    unittest.main()
