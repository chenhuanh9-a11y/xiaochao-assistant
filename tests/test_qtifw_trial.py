"""Actual local filesystem recovery/data boundaries, no installed user tree."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "app")]
import qtifw_trial_helper as helper
from qtifw_trial_helper import OWNER, prepare, verify, verify_program, clear_data, scoped, check_target
from build_qtifw_trial import check_archive_inventory, map_inputs


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
        (work / "nightmare-memory").mkdir()
        (work / "nightmare-memory/custom-galleries.json").write_bytes(b'{"schema":"xingtu-custom-gallery-v1","galleries":[]}')
        (self.target / "personal.txt").write_bytes(b"outside declared data scope")
        return work

    def test_archive_reuse_rejects_same_source_and_count_with_different_bytes(self):
        prior = self.scope / "prior-policy.json"
        prior.write_text(json.dumps(self.policy), encoding="utf-8")
        inventory = {"files": dict(self.policy["files"])}
        check_archive_inventory(prior, self.policy["payloadSource"], inventory)
        with self.assertRaisesRegex(ValueError, "Deduplicated program archives"):
            check_archive_inventory(prior, self.policy["payloadSource"], inventory,
                                    [["@TargetDir@/program/a", "@TargetDir@/program/b"]])
        inventory["files"]["app.exe"] = dict(bytes=5, sha256=hashlib.sha256(b"other").hexdigest())
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            check_archive_inventory(prior, self.policy["payloadSource"], inventory)

    def test_portable_install_has_no_build_scope_dependency_and_keeps_owner_binding(self):
        self.policy.update(portable=True, allowNewTargets=True, fullUninstall=True)
        self.policy.pop("scope")
        (self.scope / ".qtifw-trial-scope").unlink()
        self.assertTrue(prepare(self.policy, self.target)["prepared"])
        other = self.scope / "targets/other"
        other.mkdir()
        (other / OWNER).write_bytes((self.target / OWNER).read_bytes())
        with self.assertRaisesRegex(ValueError, "owner-target-mismatch"):
            check_target(self.policy, other)
        helper.receipt(self.policy, self.target, "data", dict(success=True, status="scheduled"))
        self.assertEqual(json.loads((self.target / "trial-data.json").read_bytes())["status"], "scheduled")
        self.assertFalse((self.scope / "last-uninstall.json").exists())

    def test_portable_mode_still_rejects_nonempty_unowned_directory(self):
        self.policy.update(portable=True, allowNewTargets=True)
        self.policy.pop("scope")
        self.target.mkdir()
        original = self.target / "personal.txt"
        original.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "directory-not-empty"):
            prepare(self.policy, self.target)
        self.assertEqual(original.read_bytes(), b"keep")
        self.assertFalse((self.target / OWNER).exists())

    def test_uninstall_accepts_internal_snapshot_aliases_but_rejects_external_links(self):
        from qtifw_trial_lease import inspect_installation_objects
        self.seed()
        original = self.target / "program/work/activation.json"
        original.write_bytes(b"immutable snapshot pointer")
        internal = self.target / "program/work/pointer-witness.json"
        os.link(original, internal)
        lock = self.target / "program/work/map-packages/import.lock"
        inspect_installation_objects(self.target, lock)
        outside = self.scope / "external-pointer.json"
        os.link(original, outside)
        with self.assertRaisesRegex(ValueError, "hardlink-outside-target"):
            inspect_installation_objects(self.target, lock)
        self.assertEqual(outside.read_bytes(), b"immutable snapshot pointer")
        self.assertEqual(original.stat().st_nlink, 3)

    def test_accepted_map_delivery_is_copied_by_exact_archive_identity(self):
        archives = self.scope / "archives"
        archives.mkdir()
        original = archives / "author.zip"
        with zipfile.ZipFile(original, "w") as archive:
            archive.writestr("map-package.json", json.dumps(dict(packageId="author-example", season="s1")))
        payload = original.read_bytes()
        report = self.scope / "delivery.json"
        report.write_text(json.dumps(dict(packages=[dict(path="author.zip", outputBytes=len(payload),
                                                       sha256=hashlib.sha256(payload).hexdigest())])))
        packages, sources = map_inputs(archives, report)
        self.assertEqual(packages[0]["path"], "_installer-support/maps/author.zip")
        self.assertEqual(packages[0]["packageId"], "author-example")
        self.assertTrue(packages[0]["versionedAuthor"])
        self.assertEqual(sources, [(original, packages[0]["path"])])
        original.write_bytes(payload + b"changed")
        with self.assertRaisesRegex(ValueError, "archive changed"):
            map_inputs(archives, report)

    @unittest.skipUnless(os.name == "nt", "Win32 per-install Run entry")
    def test_uninstall_disables_own_run_entry_even_if_watcher_exe_is_missing(self):
        from game_startup import WATCHER_NAME, MAIN_NAME, set_enabled, enabled
        import game_startup_win32
        prepare(self.policy, self.target)
        program = self.target / "program"
        program.mkdir()
        for name in (WATCHER_NAME, MAIN_NAME):
            (program / name).write_bytes(b"synthetic, never executed")
        set_enabled(program, True)
        try:
            (program / WATCHER_NAME).unlink()
            (self.scope / "qtifw-trial-policy.json").write_text(json.dumps(self.policy))
            # Replace only process/SID discovery. Run registration, the existing
            # control function, and its actual removal are production code.
            windows = SimpleNamespace(interactive_user_matches=lambda: True, watcher_state=lambda path: "absent")
            with patch.object(helper, "__file__", str(self.scope / "helper.py")), \
                    patch.object(sys, "argv", ["helper", "disable-watcher", str(self.target)]), \
                    patch.object(game_startup_win32, "Windows", return_value=windows), redirect_stdout(StringIO()):
                self.assertEqual(helper.main(), 0)
            self.assertFalse(enabled(program))
        finally:
            set_enabled(program, False)

    def test_program_validation_is_read_only_and_rejects_wrong_bytes(self):
        self.seed()
        (self.target / "program/app.exe").write_bytes(b"other")
        internal = self.target / "program/_internal"
        internal.mkdir()
        (internal / "runtime.dll").write_bytes(b"runtime")
        with self.assertRaisesRegex(ValueError, "program-byte-mismatch:app.exe"):
            verify_program(self.policy, self.target)
        self.assertEqual(json.loads((self.target / OWNER).read_text())["phase"], "installing")
        (self.target / "program/app.exe").write_bytes(b"whole")
        self.assertEqual(verify_program(self.policy, self.target), dict(verified=True, files=2))
        self.assertEqual(json.loads((self.target / OWNER).read_text())["phase"], "installing")

    def test_default_keep_preserves_pending_transactions_even_with_old_full_flag(self):
        work = self.seed()
        packages = work / "map-packages"
        packages.mkdir()
        pending = packages / "pending-base-repair.json"
        pending.write_bytes(b"interrupted resource transaction")
        self.policy["fullUninstall"] = True
        (self.target / "trial-select-data.json").write_text(json.dumps(dict(operation="purge", choice="keep")))
        (self.scope / "qtifw-trial-policy.json").write_text(json.dumps(self.policy))
        with patch.object(helper, "__file__", str(self.scope / "helper.py")), \
                patch.object(sys, "argv", ["helper", "undo-data", str(self.target)]), redirect_stdout(StringIO()):
            self.assertEqual(helper.main(), 0)
        result = json.loads((self.scope / "last-uninstall.json").read_bytes())
        self.assertEqual(result["dataChoice"], "keep")
        self.assertNotIn("nativeRemovalScheduled", result)
        self.assertEqual(pending.read_bytes(), b"interrupted resource transaction")
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")

    @unittest.skipUnless(os.name == "nt", "Win32 direct native parent validation")
    def test_uninstall_lease_refuses_wrong_parent_before_creating_work(self):
        from qtifw_trial_lease import hold_uninstaller_lease
        prepare(self.policy, self.target)
        (self.target / "trial-maintenance.exe").write_bytes(b"not the live parent executable")
        with self.assertRaisesRegex(ValueError, "parent-image-mismatch"):
            hold_uninstaller_lease(self.target)
        self.assertFalse((self.target / "program").exists())

    def external_target(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.policy["allowNewTargets"] = True
        return Path(temporary.name) / "中文 安装位置" / "星图试用版"

    def test_selected_external_directory_is_read_only_until_real_prepare(self):
        target = self.external_target()
        self.assertTrue(check_target(self.policy, target)["checked"])
        self.assertFalse(target.parent.exists())
        prepare(self.policy, target)
        self.assertEqual(json.loads((target / OWNER).read_text())["target"], str(target))
        for name, data in {"app.exe": b"whole", "_internal/runtime.dll": b"runtime"}.items():
            path = target / "program" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.assertTrue(verify(self.policy, target)["verified"])
        self.assertEqual(clear_data(self.policy, target, "keep")["cleared"], 0)

    def test_files_arriving_after_directory_validation_block_real_prepare(self):
        target = self.external_target()
        check_target(self.policy, target)
        target.mkdir(parents=True)
        personal = target / "personal.txt"
        personal.write_bytes(b"do not overwrite")
        with self.assertRaisesRegex(ValueError, "not-empty"):
            prepare(self.policy, target)
        self.assertFalse((target / OWNER).exists())
        self.assertEqual(personal.read_bytes(), b"do not overwrite")

    def test_owner_cannot_be_copied_to_adopt_another_selected_directory(self):
        target = self.external_target()
        prepare(self.policy, target)
        other = target.parent / "another"
        other.mkdir()
        (other / OWNER).write_bytes((target / OWNER).read_bytes())
        with self.assertRaisesRegex(ValueError, "owner-target-mismatch"):
            prepare(self.policy, other)

    def test_selected_location_inside_existing_installation_is_rejected(self):
        target = self.external_target()
        target.parent.mkdir(parents=True)
        (target.parent / "星图.exe").write_bytes(b"existing application")
        with self.assertRaisesRegex(ValueError, "inside-existing-installation"):
            prepare(self.policy, target)
        self.assertFalse(target.exists())

    def test_broad_system_relative_and_build_directories_cannot_be_selected(self):
        self.external_target()
        for target in (self.scope, self.scope / "config", self.scope.parent,
                       Path(self.scope.anchor), Path("relative-trial")):
            with self.assertRaises(ValueError):
                check_target(self.policy, target)

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

    def test_stale_unpublished_owner_file_cannot_block_repeated_verification(self):
        self.seed()
        stale = self.target / (OWNER + ".new")
        stale.write_bytes(b"interrupted metadata write")
        (self.target / "program/app.exe").write_bytes(b"whole")
        internal = self.target / "program/_internal"
        internal.mkdir()
        (internal / "runtime.dll").write_bytes(b"runtime")
        self.assertTrue(verify(self.policy, self.target)["verified"])
        self.assertTrue(verify(self.policy, self.target)["verified"])
        self.assertEqual(stale.read_bytes(), b"interrupted metadata write")

    @unittest.skipUnless(os.name == "nt", "same-handle Windows deletion")
    def test_default_keep_then_clear_only_work_and_preserve_outside_unknowns(self):
        work = self.seed()
        self.assertEqual(clear_data(self.policy, self.target, "keep")["cleared"], 0)
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
        self.assertEqual(clear_data(self.policy, self.target, "clear")["cleared"], 1)
        self.assertFalse((work / "nightmare-memory/custom-galleries.json").exists())
        self.assertEqual((work / "settings.json").read_bytes(), b"synthetic settings")
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
        os.link(work / "nightmare-memory/custom-galleries.json", self.scope / "external-hardlink")
        with self.assertRaisesRegex(ValueError, "exclusive-file"):
            clear_data(self.policy, self.target, "clear")
        self.assertEqual((self.scope / "external-hardlink").read_bytes(), b'{"schema":"xingtu-custom-gallery-v1","galleries":[]}')


if __name__ == "__main__":
    unittest.main()
