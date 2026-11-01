"""Actual removal/OS lock/journals; all bytes are synthetic private fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import test_live_author_import as import_fixtures
from test_live_author_import import SUJU, WANG, CORE, row
import star_map_import as importer
import author_package_management as legacy
import map_package_removal as removal
import map_package_upgrade as upgrade
import map_package_recovery_plan as recovery
from resource_transaction import resource_write_lock, resource_read_lock, assert_resources_ready
import managed_base_resources as base


class PackageRemovalTests(unittest.TestCase):
    def setUp(self):
        self.f = import_fixtures.LiveAuthorImportTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root

    def install(self, payload=None, package="nightmare-suju", name="source.zip"):
        return self.f.live(self.f.package(package, payload or {SUJU: b"synthetic-display"}, name))

    def remove(self, package="nightmare-suju", **kwargs):
        plan = removal.inspect_package_removal(self.root, package)
        return removal.remove_packages(self.root, package, application_idle=True,
            expected_plan_sha256=plan["planSha256"], **kwargs)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*")
                if p.is_file() and "removals/" not in p.relative_to(self.root).as_posix()
                and p.name != "pending-removal.json"}

    def test_success_deletes_owned_payload_receipt_and_temporary_copy(self):
        self.install()
        self.f.write("_internal/assets/suju/unknown.png", b"user-unknown")
        result = self.remove()
        self.assertEqual(result["status"], "packages-removed")
        self.assertFalse((self.root / SUJU).exists())
        self.assertFalse(self.f.receipt().exists())
        self.assertEqual(list(self.root.rglob("rollback.zip")), [])
        self.assertFalse((self.root / removal.PENDING).exists())
        self.assertEqual((self.root / "_internal/assets/suju/unknown.png").read_bytes(), b"user-unknown")
        self.f.assert_base_preserved()

    def test_modified_payload_is_preserved_and_receipt_retains_provenance(self):
        self.install()
        self.f.write(SUJU, b"user-modified")
        result = self.remove()
        self.assertEqual(result["status"], "preserved-items")
        self.assertEqual(result["preserved"], [dict(path=SUJU, reason="modified-or-unverified")])
        self.assertEqual((self.root / SUJU).read_bytes(), b"user-modified")
        self.assertTrue(self.f.receipt().exists())

    def test_old_removal_history_discovered_without_active_receipt_and_unknown_file_kept(self):
        self.install()
        plan = legacy.inspect_author_package_removal(self.root, "nightmare-suju")
        old = legacy.remove_author_package_live(self.root, "nightmare-suju", application_idle=True,
            expected_receipt_sha256=plan["receiptSha256"], expected_plan_sha256=plan["planSha256"])
        backup = Path(old["backupPath"])
        (backup / "unknown.bin").write_bytes(b"unknown-history")
        rows = removal.managed_packages_snapshot(self.root)
        self.assertEqual(rows[0]["status"], "managed-remnants")
        self.remove(None)
        self.assertFalse((backup / SUJU).exists())
        self.assertFalse((backup / "installed-receipt.json").exists())
        self.assertEqual((backup / "unknown.bin").read_bytes(), b"unknown-history")

    def test_legacy_speed_backup_is_never_claimed_by_suju_prefix(self):
        speed = "_internal/assets/authors/suju-speed-nightmare/map-03-2.png"
        self.install()
        self.install({speed: b"speed"}, package="nightmare-suju-speed", name="speed.zip")
        plan = legacy.inspect_author_package_removal(self.root, "nightmare-suju-speed")
        result = legacy.remove_author_package_live(self.root, "nightmare-suju-speed", application_idle=True,
            expected_receipt_sha256=plan["receiptSha256"], expected_plan_sha256=plan["planSha256"])
        retained = Path(result["backupPath"]) / speed
        plan = removal.inspect_package_removal(self.root, "nightmare-suju")
        self.assertTrue(all(row["packageId"] == "nightmare-suju" for row in plan["entries"]))
        self.remove()
        self.assertEqual(retained.read_bytes(), b"speed")
        result = self.remove(None)
        self.assertEqual(result["packageIds"], ["nightmare-suju-speed"])
        self.assertFalse(retained.exists())

    def test_proven_download_cache_is_deleted_but_external_and_unknown_zips_remain(self):
        self.install()
        digest = json.loads(self.f.receipt().read_text("utf-8"))["archiveSha256"]
        source = self.f.folder / "source.zip"
        cached = f"work/map-packages/downloads/nightmare-suju-{digest}.zip"
        self.f.write(cached, source.read_bytes())
        unknown = "work/map-packages/downloads/user-unindexed.zip"
        self.f.write(unknown, b"unknown zip")
        self.remove()
        self.assertFalse((self.root / cached).exists())
        self.assertTrue(source.exists())
        self.assertEqual((self.root / unknown).read_bytes(), b"unknown zip")

    def test_changed_managed_cache_preserves_provenance_and_reports_partial(self):
        self.install()
        digest = json.loads(self.f.receipt().read_text("utf-8"))["archiveSha256"]
        cached = f"work/map-packages/downloads/nightmare-suju-{digest}.zip"
        self.f.write(cached, b"modified archive")
        result = self.remove()
        self.assertEqual(result["status"], "packages-partially-removed")
        self.assertEqual(result["unverifiedPreservedCount"], 1)
        self.assertTrue(self.f.receipt().exists())
        self.assertEqual((self.root / cached).read_bytes(), b"modified archive")

    def test_completed_upgrade_history_deleted_with_active_package(self):
        self.install({SUJU: b"old"}, name="old.zip")
        result = self.install({SUJU: b"new"}, name="new.zip")
        history = self.root / "work/map-packages/upgrades" / result["upgradeTransactionId"]
        self.assertTrue((history / "backup" / SUJU).exists())
        self.remove()
        self.assertFalse(history.exists())
        self.assertFalse((self.root / SUJU).exists())

    def test_all_packages_includes_hidden_registry_and_is_idempotent(self):
        self.install()
        self.install({WANG: b"second"}, package="nightmare-wang-qingxin", name="hidden.zip")
        with patch.dict(legacy.PACKAGE_NAMES, {"nightmare-suju": "one"}, clear=True):
            result = self.remove(None)
        self.assertEqual(set(result["packageIds"]), {"nightmare-suju", "nightmare-wang-qingxin"})
        self.assertFalse((self.root / WANG).exists())
        self.assertEqual(self.remove(None)["status"], "already-removed")
        self.f.assert_base_preserved()

    def test_official_manifest_and_shared_requires_are_preserved(self):
        self.install()
        manifest = base.manifest_for_rows([row(SUJU, b"synthetic-display")], "fixture")
        self.f.write(base.STATE, json.dumps(manifest).encode())
        self.remove()
        self.assertEqual((self.root / SUJU).read_bytes(), b"synthetic-display")
        self.assertTrue((self.root / base.STATE).exists())
        self.f.assert_base_preserved()

    def test_invalid_official_manifest_refuses_before_deletion(self):
        self.install()
        self.f.write(base.STATE, b'{"files":[]}')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.remove(None)
        self.assertEqual(self.snapshot(), before)

    def test_shared_dependency_of_another_package_is_retained_even_in_clear_all(self):
        sidecar = "_internal/references/author-packs/wang-qingxin/catalog.json"
        self.install({WANG: b"wang", sidecar: b"{}"}, package="nightmare-wang-qingxin", name="wang.zip")
        self.install()
        receipt = self.f.receipt()
        value = json.loads(receipt.read_text("utf-8"))
        value["requiresFiles"].append(row(sidecar, b"{}"))
        receipt.write_text(json.dumps(value), encoding="utf-8")
        result = self.remove(None)
        self.assertEqual(result["status"], "packages-removed")
        self.assertEqual((self.root / sidecar).read_bytes(), b"{}")
        self.assertFalse((self.root / WANG).exists())
        self.assertFalse((self.root / SUJU).exists())
        self.f.assert_base_preserved()

    def test_tampered_upgrade_history_refuses_before_deleting_active_files(self):
        self.install({SUJU: b"old"}, name="old.zip")
        result = self.install({SUJU: b"new"}, name="new.zip")
        retained = self.root / "work/map-packages/upgrades" / result["upgradeTransactionId"] / "new" / SUJU
        retained.write_bytes(b"tampered-history")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "retained-new-object-changed"):
            self.remove()
        self.assertEqual(self.snapshot(), before)

    def test_pending_transaction_refuses_before_deletion(self):
        self.install()
        for name in ("pending-additions.json", "pending-upgrade.json", "pending-base-repair.json"):
            target = self.root / "work/map-packages" / name
            target.write_text("{}")
            with self.assertRaisesRegex(ValueError, "recovery-required"):
                self.remove()
            target.unlink()
        self.assertTrue((self.root / SUJU).exists())

    def test_changed_confirmation_refuses(self):
        self.install()
        plan = removal.inspect_package_removal(self.root, "nightmare-suju")
        self.f.write(SUJU, b"changed")
        with self.assertRaisesRegex(ValueError, "plan-changed"):
            removal.remove_packages(self.root, "nightmare-suju", application_idle=True,
                expected_plan_sha256=plan["planSha256"])
        self.assertEqual((self.root / SUJU).read_bytes(), b"changed")

    def fail_after_first_delete(self, exception):
        original = importer._rollback_published
        deleted = []
        def delete(path, identity, digest):
            if deleted and path == self.f.receipt():
                raise exception("injected-after-first-delete")
            result = original(path, identity, digest)
            if "removals" not in path.parts:
                deleted.append(path)
            return result
        return patch.object(importer, "_rollback_published", side_effect=delete)

    def test_error_restores_all_original_bytes_and_reports_original_failure(self):
        self.install()
        before = self.snapshot()
        with self.fail_after_first_delete(OSError):
            with self.assertRaisesRegex(OSError, "injected-after-first-delete"):
                self.remove()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / removal.PENDING).exists())
        self.assertEqual(list(self.root.rglob("rollback.zip")), [])

    def interrupt(self):
        with self.fail_after_first_delete(KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.remove()

    def test_process_loss_is_blocked_then_recovers_through_shared_dispatcher(self):
        self.install()
        before = self.snapshot()
        self.interrupt()
        with self.assertRaisesRegex(ValueError, "removal-recovery-required"):
            assert_resources_ready(self.root)
        with resource_write_lock(self.root):
            plan = recovery.inspect_locked(self.root)
            self.assertEqual(plan["status"], "removal-pending")
            result = recovery.execute_locked(self.root, plan, "recover")
        self.assertEqual(result["status"], "removal-rolled-back")
        self.assertEqual(self.snapshot(), before)

    def test_actual_worker_process_exit_releases_os_lock_and_restores_original_objects(self):
        self.install()
        before = self.snapshot()
        identity = upgrade._identity((self.root / SUJU).stat())
        code = '''
import os,sys
from pathlib import Path
import map_package_removal as r
root=Path(sys.argv[1]);target=root/sys.argv[2]
plan=r.inspect_package_removal(root,'nightmare-suju')
original=r.files._rollback_published
def stop(path,identity,digest):
    result=original(path,identity,digest)
    if path==target:os._exit(91)
    return result
r.files._rollback_published=stop
r.remove_packages(root,'nightmare-suju',application_idle=True,expected_plan_sha256=plan['planSha256'])
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root), SUJU],
            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "app")),
            capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 91, result.stderr)
        with resource_write_lock(self.root):
            result = removal.recover_pending_removal_locked(self.root)
        self.assertEqual(result["status"], "removal-rolled-back")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(upgrade._identity((self.root / SUJU).stat()), identity)

    def test_recovery_preserves_unknown_replacement_and_retains_pending(self):
        self.install()
        self.interrupt()
        self.f.write(SUJU, b"unknown-new-object")
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, "rollback-conflict"):
                removal.recover_pending_removal_locked(self.root)
        self.assertEqual((self.root / SUJU).read_bytes(), b"unknown-new-object")
        self.assertTrue((self.root / removal.PENDING).exists())

    def test_failure_restores_upgrade_inode_contract_and_future_rollback(self):
        self.install({SUJU: b"old"}, name="old.zip")
        upgraded = self.install({SUJU: b"new"}, name="new.zip")
        transaction = upgraded["upgradeTransactionId"]
        before = self.snapshot()
        write = upgrade._write_journal
        def fail_commit(directory, journal):
            if directory.parent.name == "removals" and journal["status"] == "committed":
                raise OSError("injected-before-commit")
            return write(directory, journal)
        with patch.object(upgrade, "_write_journal", side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, "injected-before-commit"):
                self.remove()
        self.assertEqual(self.snapshot(), before)
        upgrade._load_journal(self.root, transaction)
        self.assertTrue(removal.inspect_package_removal(self.root, "nightmare-suju")["entries"])
        result = importer.rollback_package_upgrade(self.root, transaction, application_stopped=True)
        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual((self.root / SUJU).read_bytes(), b"old")

    def test_committed_partial_pin_cleanup_is_not_success_and_helper_finishes_it(self):
        self.install()
        original = importer._rollback_published
        failed = []
        def cleanup(path, identity, digest):
            if path.parent.name == "objects" and path.name == "1" and not failed:
                failed.append(True)
                return False
            return original(path, identity, digest)
        with patch.object(importer, "_rollback_published", side_effect=cleanup):
            with self.assertRaisesRegex(ValueError, "removal-committed-cleanup-required"):
                self.remove()
        self.assertFalse((self.root / SUJU).exists())
        self.assertFalse(self.f.receipt().exists())
        self.f.write(SUJU, b"new-unknown-object-after-commit")
        with resource_write_lock(self.root):
            plan = recovery.inspect_locked(self.root)
            result = recovery.execute_locked(self.root, plan, "recover")
        self.assertEqual(result["status"], "removal-committed")
        self.assertEqual((self.root / SUJU).read_bytes(), b"new-unknown-object-after-commit")
        self.assertFalse((self.root / removal.PENDING).exists())
        self.assertEqual(list((self.root / "work/map-packages").glob("removals/*/objects/*")), [])
        self.assertEqual(self.remove(None)["status"], "already-removed")

    def test_hardlinks_unsupported_refuses_before_mutation(self):
        self.install()
        before = self.snapshot()
        with patch("resource_transaction.os.link", side_effect=OSError("unsupported")):
            with self.assertRaisesRegex(ValueError, "hardlinks-unavailable"):
                self.remove()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / removal.PENDING).exists())

    def test_waiting_for_reader_can_cancel_without_mutating_and_retry(self):
        self.install()
        plan = removal.inspect_package_removal(self.root, "nightmare-suju")
        cancel, started, done = threading.Event(), threading.Event(), threading.Event()
        failures = []
        def remove():
            started.set()
            try:
                removal.remove_packages(self.root, "nightmare-suju", application_idle=True,
                    expected_plan_sha256=plan["planSha256"], cancelled=cancel.is_set)
            except Exception as error:
                failures.append(error)
            finally:
                done.set()
        with resource_read_lock(self.root):
            worker = threading.Thread(target=remove)
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(done.wait(.08))
            cancel.set()
            self.assertTrue(done.wait(2))
            worker.join(2)
        self.assertEqual([error.code for error in failures], ["import-cancelled"])
        self.assertTrue((self.root / SUJU).exists())
        self.assertFalse((self.root / removal.PENDING).exists())
        self.assertEqual(self.remove()["status"], "packages-removed")

    def test_interrupted_pin_preparation_restores_without_copy_or_identity_change(self):
        self.install()
        before = self.snapshot()
        link = os.link
        def interrupt(source, target, *args, **kwargs):
            if Path(target).parent.name == "objects" and Path(target).name == "1":
                raise KeyboardInterrupt("injected-pin-preparation")
            return link(source, target, *args, **kwargs)
        with patch.object(removal.os, "link", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.remove()
        with resource_write_lock(self.root):
            result = removal.recover_pending_removal_locked(self.root)
        self.assertEqual(result["status"], "removal-rolled-back")
        self.assertEqual(self.snapshot(), before)

    def test_completed_addition_stage_is_discovered_without_receipt(self):
        stage = self.root / "work/map-packages/staging-synthetic"
        stage.mkdir(parents=True)
        payload = {SUJU: b"staged-only"}
        for path, data in payload.items():
            (stage / path).parent.mkdir(parents=True, exist_ok=True)
            (stage / path).write_bytes(data)
        inventory = [row(p, b) for p, b in payload.items()]
        # Historical v1 recovery cleared its pointer but retained this proof.
        journal = dict(schema=upgrade.ADDITIONS_SCHEMA, packageId="nightmare-suju",
            transactionId=str(uuid.uuid4()), stage=stage.relative_to(self.root).as_posix(),
            rootIdentity=upgrade._identity(self.root.stat()), files=inventory,
            entries=[dict(new=r, newIdentity=upgrade._identity((stage / r["path"]).stat())) for r in inventory],
            oldReceiptSha256=None)
        (stage / "addition-journal.json").write_text(json.dumps(journal), encoding="utf-8")
        (stage / "unknown.bin").write_bytes(b"unknown")
        rows = removal.managed_packages_snapshot(self.root)
        self.assertEqual(rows[0]["packageId"], "nightmare-suju")
        self.remove(None)
        self.assertFalse((stage / SUJU).exists())
        self.assertFalse((stage / "addition-journal.json").exists())
        self.assertEqual((stage / "unknown.bin").read_bytes(), b"unknown")

    @unittest.skipUnless(os.name == "nt", "Windows junction check")
    def test_junction_target_is_rejected_without_touching_outside(self):
        self.install()
        parent = (self.root / SUJU).parent
        (self.root / SUJU).unlink()
        parent.rmdir()
        outside = self.f.folder / "outside"
        outside.mkdir()
        target = outside / Path(SUJU).name
        target.write_bytes(b"outside")
        # Fixed fixture paths only; no user directory is linked or modified.
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(parent), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaisesRegex(ValueError, "linked"):
                self.remove()
            self.assertEqual(target.read_bytes(), b"outside")
        finally:
            parent.rmdir()


if __name__ == "__main__":
    unittest.main()
