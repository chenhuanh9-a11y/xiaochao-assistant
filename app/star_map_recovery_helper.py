"""Separate recovery EXE: wait for the confirmed parent's normal exit, then act.

Reuses the import helper's real parent-handle, cancellation and atomic receipt
contract. No GUI, restart, kill, installation, network or main-EXE dispatch.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import star_map_import as importer
import star_map_import_helper as process
from map_package_recovery_plan import execute_locked

SCHEMA = "xingtu-resource-recovery-request-v1"
HELPER_NAME = "star-map-recovery-helper"
MAX_REQUEST_BYTES = 64 * 1024


def _request_path(root, request_id):
    # Same strict ID/cancel namespace as the mature import helper.
    process._cancel_path(root, request_id)
    return importer._target(Path(os.path.abspath(root)),
                            f"work/map-packages/recovery-request-{request_id}.json")


def receipt_path(root):
    return importer._target(Path(os.path.abspath(root)), "work/map-packages/last-recovery-result.json")


def cancel_recovery_request(root, request_id):
    process.cancel_helper_request(root, request_id)


def _validate_request(request, request_id):
    if (not isinstance(request, dict) or set(request) != {"schema", "requestId", "plan", "action"}
            or request["schema"] != SCHEMA or request["requestId"] != request_id
            or request["action"] not in ("recover", "rollback")):
        raise importer.ImportRejected("invalid-recovery-request")
    plan = request["plan"]
    if (not isinstance(plan, dict) or set(plan) != {"transactionId", "packageId", "status", "pending",
                                                 "fileCount", "blockedPaths", "planSha256"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("planSha256", "")))):
        raise importer.ImportRejected("invalid-recovery-plan")
    # The locked shared service additionally validates the journal, transaction
    # and every consent field against its current file identities and hashes.
    return request


def launch_recovery_helper(helper_path, install_root, plan, action, request_id, *, wait_seconds=120):
    """After UI consent, launch before normal close; caller owns close/cancel.

    The request is exclusive and immutable-by-digest for the spawned helper.
    Failure here must leave the main application open. No resource mutation.
    """
    root = Path(os.path.abspath(install_root))
    path = _request_path(root, request_id)
    expected_helper = importer._target(root, HELPER_NAME + ".exe")
    if os.path.normcase(os.path.abspath(helper_path)) != os.path.normcase(str(expected_helper)):
        raise importer.ImportRejected("recovery-helper-path-mismatch")
    request = _validate_request(dict(schema=SCHEMA, requestId=request_id, plan=plan, action=action), request_id)
    data = json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(data) > MAX_REQUEST_BYTES:
        raise importer.ImportRejected("recovery-request-too-large")
    importer._no_links(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    importer._no_links(path.parent)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return process._launch_parent_bound_helper(helper_path, root, request_id,
        ["--request-file", str(path), "--request-sha256", hashlib.sha256(data).hexdigest()],
        wait_seconds=wait_seconds)


def run_helper(*, request_file, request_sha256, install_root, request_id, parent_pid,
               parent_created, wait_seconds=120, parent_handle=None, parent_executable=None):
    root = Path(os.path.abspath(install_root))
    importer._no_links(root)
    expected_path = _request_path(root, request_id)
    result = dict(requestId=request_id, success=False, restartRequired=False,
                  scope="offline-resource-recovery; not native-game acceptance")
    try:
        expected_exe = importer._target(root, "第五人格小抄助手.exe")
        if os.path.normcase(os.path.realpath(sys.executable)) == os.path.normcase(str(expected_exe)):
            raise importer.ImportRejected("helper-must-use-separate-executable")
        if Path(os.path.abspath(request_file)) != expected_path:
            raise importer.ImportRejected("recovery-request-path-mismatch")
        importer._no_links(expected_path)
        with expected_path.open("rb") as stream:
            data = stream.read(MAX_REQUEST_BYTES + 1)
        if (len(data) > MAX_REQUEST_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", str(request_sha256))
                or hashlib.sha256(data).hexdigest() != request_sha256):
            raise importer.ImportRejected("recovery-request-changed")
        request = _validate_request(importer._json(data), request_id)
        plan, action = request["plan"], request["action"]
        result.update(planSha256=plan["planSha256"], transactionId=plan["transactionId"], action=action)
        result["parentExit"] = process.wait_for_application_exit(parent_pid, parent_created,
            expected_exe, wait_seconds, parent_handle=parent_handle,
            parent_executable=parent_executable, cancel_path=process._cancel_path(root, request_id))
        if process._cancel_path(root, request_id).exists():
            raise importer.ImportRejected("import-request-cancelled")
        # Original closed check and OS writer lock remain mandatory; a second
        # instance or changed backup/target prevents recovery after consent.
        with importer._writer_locked_installation(root, require_closed=True):
            if process._cancel_path(root, request_id).exists():
                raise importer.ImportRejected("import-request-cancelled")
            recovered = execute_locked(root, plan, action)
        result["operationResult"] = recovered
        result["restartRequired"] = bool(recovered.get("restartRequired", False))
        result["success"] = True
        code = 0
    except Exception as error:
        result.update(errorCode=type(error).__name__, reason=str(error))
        code = 2
    result["completedAtUTC"] = datetime.now(timezone.utc).isoformat()
    process._write_receipt(root, result, path=receipt_path(root))
    return code, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--parent-created", required=True)
    parser.add_argument("--parent-handle", type=int)
    parser.add_argument("--parent-executable")
    parser.add_argument("--wait-seconds", type=int, default=120)
    try:
        code, receipt = run_helper(**vars(parser.parse_args(argv)))
        if sys.stdout is not None:
            print(json.dumps(receipt, ensure_ascii=False))
        return code
    except (importer.ImportRejected, OSError) as error:
        if sys.stderr is not None:
            print(json.dumps(dict(success=False, reason=str(error), receiptWritten=False)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
