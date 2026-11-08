from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from probe_qtifw_windows import DATA, apply_fixture_data_choice, scoped_target
from probe_qtifw_lifecycle import Probe


class WindowsProbeScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.targets = self.root / "targets"
        self.targets.mkdir()
        self.target = self.targets / "fixture"
        self.work = self.target / "work"
        self.work.mkdir(parents=True)
        for filename, value in DATA.items():
            (self.work / filename).write_bytes(value)

    def test_outside_traversal_and_root_cannot_be_mutated(self):
        sentinel = self.root / "outside.txt"
        sentinel.write_bytes(b"preserve")
        for path in (self.root, sentinel, self.targets, self.target / ".." / ".." / "outside.txt"):
            with self.assertRaises(ValueError):
                scoped_target(self.targets, path)
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_explicit_keep_and_clear_preserve_unknown_outside_work(self):
        unknown = self.target / "unknown.txt"
        unknown.write_bytes(b"preserve")
        apply_fixture_data_choice(self.targets, self.target, "keep", installation_succeeded=True)
        self.assertEqual({p.name: p.read_bytes() for p in self.work.iterdir()}, DATA)
        apply_fixture_data_choice(self.targets, self.target, "clear", installation_succeeded=True)
        self.assertFalse(self.work.exists())
        self.assertEqual(unknown.read_bytes(), b"preserve")

    def test_failed_install_or_missing_choice_never_clears_data(self):
        for choice, succeeded in (("clear", False), (None, True), ("", True)):
            with self.assertRaises(ValueError):
                apply_fixture_data_choice(self.targets, self.target, choice, installation_succeeded=succeeded)
            self.assertEqual({p.name: p.read_bytes() for p in self.work.iterdir()}, DATA)

    def test_unknown_or_modified_data_preflights_all_files(self):
        unknown = self.work / "z-personal.txt"
        unknown.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            apply_fixture_data_choice(self.targets, self.target, "clear", installation_succeeded=True)
        self.assertTrue(all((self.work / p).read_bytes() == value for p, value in DATA.items()))
        unknown.unlink()
        (self.work / "settings.json").write_bytes(b"modified")
        with self.assertRaises(ValueError):
            apply_fixture_data_choice(self.targets, self.target, "clear", installation_succeeded=True)
        self.assertTrue((self.work / "user-created.txt").exists())

    def test_hardlinked_fixture_data_is_preserved(self):
        os.link(self.work / "settings.json", self.root / "linked-settings")
        with self.assertRaises(ValueError):
            apply_fixture_data_choice(self.targets, self.target, "clear", installation_succeeded=True)
        self.assertEqual((self.root / "linked-settings").read_bytes(), DATA["settings.json"])

    @unittest.skipUnless(sys.platform == "win32", "Windows junction contract")
    def test_windows_junction_cannot_redirect_cleanup(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "settings.json").write_bytes(DATA["settings.json"])
        link = self.targets / "redirect"
        # Native mklink only creates this owned fixture junction; no shell deletion.
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        with self.assertRaises(ValueError):
            scoped_target(self.targets, link / "settings.json")
        self.assertEqual((outside / "settings.json").read_bytes(), DATA["settings.json"])

    @unittest.skipUnless(sys.platform == "win32", "Windows native log encoding")
    def test_native_ansi_log_preserves_bytes_and_decodes_inspection_copy(self):
        probe = Probe(self.root, self.root / "logs")
        probe.env = dict(os.environ)
        raw = "中文 target".encode("mbcs")
        result = probe.run("native-output", [sys.executable, "-c", "import sys;sys.stdout.buffer.write(" + repr(raw) + ")"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "中文 target")
        self.assertEqual((probe.root / "native-output.log").read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
