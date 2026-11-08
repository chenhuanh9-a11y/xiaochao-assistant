"""Actual local filesystem recovery/data boundaries, no installed user tree."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "app")]
from qtifw_trial_helper import OWNER, prepare, verify, clear_data, scoped


class TrialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name)
        (self.scope / ".qtifw-trial-scope").write_text("test-run\n", encoding="ascii")
        (self.scope / "targets").mkdir()
        self.target = self.scope / "targets/fixture"
        self.policy = dict(scope=str(self.scope), instance="test-run", payloadSource="a" * 40,
            files={name: dict(bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
                   for name, value in {"app.exe": b"whole", "_internal/runtime.dll": b"runtime"}.items()})

    def seed(self):
        prepare(self.policy, self.target)
        work = self.target / "program/work"
        work.mkdir(parents=True)
        (work / "settings.json").write_bytes(b"synthetic settings")
        (self.target / "personal.txt").write_bytes(b"outside declared data scope")
        return work

    def test_interrupted_retry_quarantines_whole_and_torn_files_without_adopting_unknowns(self):
        work = self.seed()
        (self.target / "program/app.exe").write_bytes(b"who")
        internal = self.target / "program/_internal"
        internal.mkdir()
        (internal / "runtime.dll").write_bytes(b"runtime")
        (internal / "unknown-user.txt").write_bytes(b"preserve")
        result = prepare(self.policy, self.target)
        self.assertEqual(set(result["quarantined"]), set(self.policy["files"]))
        preserved = list((self.target / "recovery-files").glob("*/app.exe"))
        self.assertEqual(preserved[0].read_bytes(), b"who")
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
        self.assertEqual((internal / "unknown-user.txt").read_bytes(), b"preserve")
        self.assertEqual(prepare(self.policy, self.target)["quarantined"], [])
        (self.target / "program/app.exe").write_bytes(b"whole")
        (internal / "runtime.dll").write_bytes(b"runtime")
        self.assertEqual(verify(self.policy, self.target), dict(verified=True, files=2))
        with self.assertRaisesRegex(ValueError, "requires-inspection"):
            prepare(self.policy, self.target)

    def test_collision_or_inno_is_rejected_before_owner_or_payload_changes(self):
        program = self.target / "program"
        program.mkdir(parents=True)
        (program / "app.exe").write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError, "unowned-program-collision"):
            prepare(self.policy, self.target)
        self.assertFalse((self.target / OWNER).exists())
        self.assertEqual((program / "app.exe").read_bytes(), b"unknown")
        (self.target / "unins000.exe").write_bytes(b"never executed")
        with self.assertRaisesRegex(ValueError, "legacy-inno"):
            prepare(self.policy, self.target)

    def test_wrong_owner_and_external_scope_are_rejected(self):
        self.seed()
        owner = self.target / OWNER
        data = json.loads(owner.read_text())
        data["instance"] = "foreign"
        owner.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "owner-mismatch"):
            prepare(self.policy, self.target)
        for candidate in (self.scope, self.scope / "targets", self.target / "../../outside"):
            with self.assertRaises(ValueError):
                scoped(self.policy, candidate)

    @unittest.skipUnless(os.name == "nt", "same-handle Windows deletion")
    def test_default_keep_then_clear_only_work_and_preserve_outside_unknowns(self):
        work = self.seed()
        self.assertEqual(clear_data(self.policy, self.target, "keep")["cleared"], 0)
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
        self.assertEqual(clear_data(self.policy, self.target, "clear")["cleared"], 1)
        self.assertFalse((work / "settings.json").exists())
        self.assertEqual((self.target / "personal.txt").read_bytes(), b"outside declared data scope")
        self.assertTrue((work / "map-packages/import.lock").exists())
        self.assertEqual(clear_data(self.policy, self.target, "clear")["cleared"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows exclusive file lease")
    def test_active_runtime_blocks_clear_without_deleting_data(self):
        work = self.seed()
        code = ("import sys;sys.path.insert(0,sys.argv[1]);from resource_transaction import resource_runtime_lease;"
                "lease=resource_runtime_lease(sys.argv[2]);print('ready',flush=True);sys.stdin.readline();lease.close()")
        process = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "app"), str(self.target / "program")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with self.assertRaisesRegex(ValueError, "resource-busy"):
                clear_data(self.policy, self.target, "clear")
            self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
        finally:
            process.communicate("finish\n", timeout=10)
        self.assertEqual(process.returncode, 0)

    @unittest.skipUnless(os.name == "nt", "Windows cleanup preflight")
    def test_hardlink_rejects_clear_before_deleting_other_files(self):
        work = self.seed()
        os.link(work / "settings.json", self.scope / "external-hardlink")
        with self.assertRaisesRegex(ValueError, "exclusive-file"):
            clear_data(self.policy, self.target, "clear")
        self.assertEqual((self.scope / "external-hardlink").read_bytes(), b"synthetic settings")


if __name__ == "__main__":
    unittest.main()
