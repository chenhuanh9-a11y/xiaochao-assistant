"""Bounded integration checks on isolated resources / import transactions."""
import argparse
import json
from pathlib import Path
import sys
import time
from PySide6.QtCore import QCoreApplication, QUrl
from backend import Backend, ROOT, inspect_package
from replay_adapter import load_pose


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    app = QCoreApplication([])
    sandbox = ROOT / "work/qt-lab-installation"
    backend = Backend(sandbox)
    checks = {}
    def wait(predicate, seconds=12):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.004)
        assert predicate(), "worker deadline"
    try:
        wait(lambda: not backend.busy)
        assert backend.count > 0
        original = list(backend.model.rows)
        mutation_count = backend.model.mutations
        backend.refresh()
        wait(lambda: not backend.busy)
        checks["same_refresh_zero_mutations"] = backend.model.mutations == mutation_count
        backend.select(original[0]["key"])
        checks["selected_binding_exact"] = backend.selectedImage == original[0]["imageUrl"]
        backend.setProfile(next(p["id"] for p in backend.profiles if p["id"] != backend.profileId))
        checks["new_profile_clears_old_preview"] = not backend.selectedImage
        backend.setMode("difficult")
        backend.setMode("nightmare")
        wait(lambda: not backend.busy)
        checks["latest_generation_only"] = backend.context.mode == "nightmare"
        invalid = ROOT / "work/invalid-qt-test.zip"
        invalid.write_bytes(b"not a zip")
        backend.importArchive(QUrl.fromLocalFile(str(invalid)).toString())
        wait(lambda: not backend.importing)
        checks["invalid_import_reports_error"] = backend.hasError
        # Authored synthetic fixture in the existing core's schema; not real game evidence.
        pose = ROOT / "work/qt-test-pose.json"
        pose.write_text(json.dumps(dict(mapId="fixture-map", routeProfileId="fixture-profile",
                                       anchorFloor="1", rootMatrix=[[1,0,40],[0,1,60],[0,0,1]],
                                       floorRelations={"2":[[1,0,8],[0,1,-3],[0,0,1]]},
                                       generation=4, reliable=True, source="synthetic-contract-test")), encoding="utf-8")
        result = load_pose(pose, "2")
        checks["reuses_core_floor_pose"] = result["matrix"] == [[1,0,48],[0,1,57],[0,0,1]]
        try:
            load_pose(pose, "B1")
            checks["missing_floor_rejected"] = False
        except ValueError:
            checks["missing_floor_rejected"] = True
        # This is a real package and transaction, but strictly a sandbox install.
        if args.archive:
            backend.importArchive(QUrl.fromLocalFile(str(args.archive.resolve())).toString())
            job_count = len(backend._jobs)
            backend.importArchive(QUrl.fromLocalFile(str(args.archive.resolve())).toString())
            checks["duplicate_import_suppressed"] = len(backend._jobs) == job_count
            wait(lambda: not backend.importing or backend._pending is not None)
            if backend._pending:
                backend.confirmImport(False)
                checks["cancel_replace_preserves_state"] = not backend.importing and backend._pending is None
            else:
                wait(lambda: not backend.busy)
                checks["real_package_import_complete"] = not backend.hasError and "已导入" in backend.message
                package_id = inspect_package(args.archive, sandbox)["packageId"]
                checks["receipt_written_only_in_sandbox"] = (sandbox / "work/map-packages" / f"{package_id}.json").is_file()
        checks["no_tk_or_live_runtime"] = "tkinter" not in sys.modules and "identityv_daily_assistant" not in sys.modules
        report = {"checks": checks, "all_pass": all(checks.values()), "boundaries": "real resource metadata and optional real ZIP; synthetic core snapshot only; no game, global hotkey or installed config"}
        path = ROOT / "work/qt-evidence/backend-checks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        return 0 if report["all_pass"] else 2
    finally:
        backend.shutdown()

if __name__ == "__main__":
    raise SystemExit(main())
