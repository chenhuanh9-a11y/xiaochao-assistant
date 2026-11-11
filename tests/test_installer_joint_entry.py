"""Joint legacy-migration, reset, and native-uninstall recovery boundaries.

Windows temporary trees and actual helper entry points are used. Registry and
parent-lease providers remain narrow fixtures; no product registry is written.
"""
from contextlib import nullcontext
import configparser
import copy
import ctypes
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from ctypes import wintypes
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
APP = REPO / "app"
for path in (str(APP), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import installer_uninstall as uninstall
import resource_upgrade_reset as resource_reset
import star_map_reset_helper as reset_helper
import test_installer_legacy_handoff as legacy_fixtures
import test_installer_uninstall as uninstall_fixtures
import test_installer_uninstall_recovery as recovery_fixtures
import test_resource_reset_installer as reset_fixtures
from installer_upgrade_journal import PENDING as LEGACY_PENDING, read_status


def _ini(path):
    value = configparser.ConfigParser(interpolation=None)
    value.read(path, encoding="utf-16")
    return value["result"]


def _profile(app_id=None):
    from packaged_product_identity import PRODUCTION_APP_ID, PRODUCTION_PRODUCT
    return dict(product=PRODUCTION_PRODUCT,
                installerInstance=dict(id="production", product=PRODUCTION_PRODUCT,
                                       appId=app_id or PRODUCTION_APP_ID))


def _reset_argv(action, root, result, target):
    argv = ["--action", action, "--install-root", str(root), "--result-directory", str(result),
            "--target-report", str(result / "fixture-report.json"),
            "--target-report-sha256", "c" * 64,
            "--source-commit", target["sourceCommit"],
            "--display-version", target["displayVersion"],
            "--numeric-version", target["numericVersion"],
            "--parent-created", "1", "--parent-executable", "fixture-parent.exe",
            "--parent-sha256", "d" * 64, "--parent-pid", "1", "--lock-handle", "5"]
    return argv


@unittest.skipUnless(os.name == "nt", "joint native installer behavior requires Windows")
class InstallerJointEntryTests(unittest.TestCase):
    def setUp(self):
        self.uninstall_case = uninstall_fixtures.InstallerUninstallTests("runTest")
        self.uninstall_case.setUp()
        self.addCleanup(self.uninstall_case.doCleanups)

        self.recovery_case = recovery_fixtures.InstallerUninstallRecoveryTests("runTest")
        self.recovery_case.setUp()
        self.addCleanup(self.recovery_case.doCleanups)

    def _handoff_fixture(self):
        fixture = legacy_fixtures.HandoffTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def _reset_fixture(self):
        fixture = reset_fixtures.ResourceResetHelperTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_real_pending_legacy_migration_blocks_actual_uninstall_without_deleting_evidence(self):
        handoff = self._handoff_fixture()
        handoff.native_complete()
        committed = handoff.execute("commit")
        root = handoff.target
        pending = root / LEGACY_PENDING
        self.assertTrue(pending.is_file())
        self.assertEqual(committed["status"], "new-install-verified-old-entry-pending")
        journal_state = read_status(root, owner_sid=legacy_fixtures.SID)
        self.assertEqual(journal_state["status"], "native-complete")
        self.assertEqual(journal_state["resourceAction"], "legacy")

        # Complete only the synthetic install metadata needed for the real
        # uninstall CLI preflight. These files remain inside the temp tree.
        profile = handoff.profile
        (root / "installation-profile.json").write_text(json.dumps(profile), encoding="utf-8")
        (root / "_internal" / "installation-profile.json").parent.mkdir(parents=True, exist_ok=True)
        (root / "_internal" / "installation-profile.json").write_text(json.dumps(profile), encoding="utf-8")
        helper_profile = root / "_star_map_uninstall_runtime" / "installation-profile.json"
        helper_profile.parent.mkdir(parents=True, exist_ok=True)
        helper_profile.write_text(json.dumps(profile), encoding="utf-8")
        ownership = root / "_installer-owned-files.txt"
        ownership.write_bytes(ownership.read_bytes() +
                              b"_star_map_uninstall_runtime/installation-profile.json\n")
        lock = root / "work" / "map-packages" / "import.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(b"")

        registry = uninstall_fixtures._RegistryFixture(root, sid=self.uninstall_case.sid)
        registry.rows[0]["uninstall_string"] = '"' + str(root / "unins017.exe") + '"'
        result = self.uninstall_case.case / "legacy-pending-uninstall-result"
        result.mkdir()
        before = self.recovery_case._snapshot_tree(root)
        marker_bytes = pending.read_bytes()
        journal = next((root / "work" / "installer-upgrades").rglob("legacy-commit.json"))
        journal_bytes = journal.read_bytes()
        settings = root / "work" / "nightmare-memory" / "ui-settings.json"
        settings_bytes = settings.read_bytes()

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.uninstall_case.local)}):
            code = self.uninstall_case._invoke(root, result, registry, profile)

        self.assertEqual(code, 2)
        # The actual committed handoff still has an unretired migration intent.
        # It is refused before scanning work; do not count unrelated preflight
        # errors as proof that the migration evidence boundary was exercised.
        self.assertIn("unknown-install-root-entry", (result / "error.txt").read_text("utf-8-sig"))
        self.assertIn(".legacy-upgrade-intent.json", (result / "remaining.txt").read_text("utf-8-sig"))
        self.assertEqual(self.uninstall_case._receipt(result)["mutationStarted"], "False")
        self.assertEqual(self.recovery_case._snapshot_tree(root), before)
        self.assertEqual(pending.read_bytes(), marker_bytes)
        self.assertEqual(journal.read_bytes(), journal_bytes)
        self.assertEqual(settings.read_bytes(), settings_bytes)
        self.assertEqual(registry.rows[0]["install_location"], str(root))

    def test_unknown_neighbor_survives_recovery_after_ordinary_registration_is_removed(self):
        root, _result, registry, profile, store, digest = self.recovery_case._prepare_native_ready(
            name="joint-unknown-neighbor")
        self.recovery_case._simulate_inno_finalization(root)
        registry.rows.clear()
        self.assertEqual(registry.rows, [])
        unknown = root / "added-after-native-ready.bin"
        unknown.write_bytes(b"keep this unregistered neighbor")
        tree_before = self.recovery_case._snapshot_tree(root)
        registration_before = copy.deepcopy(store.values)

        code, error, _result, _lease = self.recovery_case._recover(
            root, registry, profile, store, digest, name="joint-unknown-neighbor")

        self.assertEqual(code, 2)
        self.assertTrue(unknown.is_file())
        self.assertEqual(unknown.read_bytes(), b"keep this unregistered neighbor")
        self.assertEqual(self.recovery_case._snapshot_tree(root), tree_before)
        self.assertEqual(store.values, registration_before)
        self.assertEqual(registry.rows, [])
        self.assertIn("recovery-unknown-or-replaced-entry", error)
        self.assertIn("added-after-native-ready.bin", error)

    def test_reset_cli_holds_unique_named_maintenance_object_through_receipt(self):
        reset = self._reset_fixture()
        app_id = str(uuid.uuid4()).upper()
        profile = _profile(app_id)
        name = "Global\\XingtuMaintenance-{" + app_id + "}"
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        api.CreateMutexW.restype = wintypes.HANDLE
        api.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        api.OpenMutexW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        parent = api.CreateMutexW(None, False, name)
        self.assertTrue(parent)
        self.assertEqual(ctypes.get_last_error(), 0, "fixture mutex name must be unique")
        parent_open = True
        observations = []

        def observe(label):
            ctypes.set_last_error(0)
            handle = api.CreateMutexW(None, False, name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            existed = ctypes.get_last_error()
            api.CloseHandle(handle)
            observations.append((label, existed))
            self.assertEqual(existed, 183, "reset helper must retain the same named object")

        def target_provider(_args):
            nonlocal parent_open
            if parent_open:
                api.CloseHandle(parent)
                parent_open = False
            observe("body-entry")
            return reset.target

        original_inspect = resource_reset.inspect_resource_upgrade

        def inspect_with_observation(*args, **kwargs):
            observe("resource-inspection")
            return original_inspect(*args, **kwargs)

        original_write_new = reset_helper._write_new

        def write_receipt_with_observation(path, data):
            result = original_write_new(path, data)
            if Path(path).name == "result.ini":
                observe("receipt")
            return result

        result = reset.session.parent / "maintenance-lifetime-result"
        result.mkdir()
        argv = _reset_argv("inspect", reset.root, result, reset.target)
        try:
            with patch.dict(uninstall._IDENTITIES,
                            {"production": dict(uninstall._IDENTITIES["production"], appId=app_id)}), \
                 patch.object(uninstall, "_baked_profile", return_value=profile), \
                 patch.object(reset_helper, "_target", side_effect=target_provider), \
                 patch.object(reset_helper, "borrowed_installer_lease",
                              side_effect=lambda *_a, **_k: nullcontext(object())), \
                 patch.object(resource_reset, "inspect_resource_upgrade", side_effect=inspect_with_observation), \
                 patch.object(reset_helper, "_write_new", side_effect=write_receipt_with_observation), \
                 patch("installer_native_observation.current_user_sid", return_value=self.uninstall_case.sid):
                code = reset_helper.main(argv)
            self.assertEqual(code, 0, (result / "error.txt").read_text("utf-8-sig")
                             if (result / "error.txt").exists() else "")
            self.assertEqual(_ini(result / "result.ini")["status"], "inspected")
            self.assertEqual(observations, [("body-entry", 183),
                                            ("resource-inspection", 183),
                                            ("receipt", 183)])
            ctypes.set_last_error(0)
            reopened = api.OpenMutexW(0x00100000, False, name)
            self.assertFalse(reopened)
            self.assertEqual(ctypes.get_last_error(), 2,
                             "named maintenance object should disappear after helper returns")
        finally:
            if parent_open:
                api.CloseHandle(parent)


if __name__ == "__main__":
    unittest.main()
