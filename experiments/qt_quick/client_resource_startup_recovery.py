"""Consent-only handoff before resource readers exist; never recovers in-process.

Caller retains its runtime resource lease until this function returns and then
exits normally. Both handled outcomes require exiting, not continuing startup.
The existing helper waits for the actual parent exit and revalidates the plan.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4


_PENDING_KINDS = {
    "upgrade-recovery-required": "upgrade",
    "author-upgrade-recovery-required": "upgrade",
    "additions-recovery-required": "additions",
    "removal-recovery-required": "removal",
}
_UPGRADE_STATES = {"prepared", "committed", "rolling-back", "rollback-incomplete", "rolled-back"}
_PLAN_FIELDS = {"transactionId", "packageId", "status", "pending", "fileCount", "blockedPaths", "planSha256"}


class StartupRecoveryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StartupRecoveryResult:
    status: str
    request_id: str = ""
    exit_code: int = 0


def _inspect(root, transaction_id=None):
    import star_map_import as importer
    from map_package_recovery_plan import inspect_locked
    with importer._writer_locked_installation(root, require_closed=False) as (locked_root, _state):
        return inspect_locked(locked_root, transaction_id)


def _validate_plan(plan, kind):
    if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS or plan.get("pending") is not True:
        raise StartupRecoveryError("startup-recovery-plan-invalid")
    try:
        transaction_id = UUID(plan["transactionId"])
        valid_id = str(transaction_id) == plan["transactionId"] and transaction_id.version == 4
    except (ValueError, TypeError, AttributeError):
        valid_id = False
    status = plan["status"]
    valid_status = isinstance(status, str) and (
        status in _UPGRADE_STATES if kind == "upgrade" else status == kind + "-pending")
    if (not valid_id or not valid_status
            or not isinstance(plan["packageId"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,95}", plan["packageId"])
            or type(plan["fileCount"]) is not int or not 0 <= plan["fileCount"] <= 20000
            or not isinstance(plan["blockedPaths"], list)
            or not isinstance(plan["planSha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", plan["planSha256"])):
        raise StartupRecoveryError("startup-recovery-plan-invalid")
    if plan["blockedPaths"]:
        # An interrupted rollback with unresolved identities is not consent to
        # remove/rewrite unknown objects. Leave it for explicit investigation.
        raise StartupRecoveryError("startup-recovery-plan-blocked")
    return dict(plan, blockedPaths=list(plan["blockedPaths"]))


def _confirmation_message(plan, kind):
    operations = {
        "upgrade": "回退中断的图包升级，恢复到本次升级前的资源。",
        "additions": "恢复中断的图包导入：未提交则撤回本次新增，已提交则完成记录清理。",
        "removal": "恢复中断的图包删除：未提交则还原本次删除，已提交则完成记录清理。",
    }
    return ("检测到尚未完成的资源事务，当前不能继续启动。\n\n"
            + operations[kind] + "\n图包：" + plan["packageId"]
            + "\n文件数：" + str(plan["fileCount"])
            + "\n事务：" + plan["transactionId"]
            + "\n计划指纹：" + plan["planSha256"][:16]
            + "\n\n确认后本程序将正常退出，由独立恢复工具重新核对记录并执行恢复。"
            + "遇到新增、被改动或归属不明的文件时将停止，不强制覆盖或删除。"
            + "恢复工具不会自动重启程序；请稍后重新启动查看结果。"
            + "\n\n选择“是”启动此恢复计划；选择“否”取消恢复并退出，保留现有文件。")


def _confirm(message):
    if sys.platform != "win32":
        raise StartupRecoveryError("startup-recovery-confirmation-unavailable")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    box = user32.MessageBoxW
    box.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    box.restype = ctypes.c_int
    # YES/NO, warning, default NO. There is no retry loop or implicit consent.
    selected = box(None, message, "星图资源恢复确认", 0x4 | 0x30 | 0x100 | 0x10000)
    if selected not in (6, 7):
        raise StartupRecoveryError("startup-recovery-confirmation-unavailable")
    return selected == 6


def _launch(root, plan, request_id):
    from star_map_recovery_helper import launch_recovery_helper
    return launch_recovery_helper(root / "star-map-recovery-helper.exe", root, plan, "recover", request_id)


def _cancel(root, request_id):
    from star_map_recovery_helper import cancel_recovery_request
    cancel_recovery_request(root, request_id)


def _persist_pending(root, plan, request_id):
    import star_map_import as importer
    path = importer._target(root, "work/map-packages/qt-recovery-pending.json")
    temporary = path.with_name(path.name + "." + request_id + ".tmp")
    data = dict(requestId=request_id, planSha256=plan["planSha256"], action="recover")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        importer._no_links(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def offer_startup_recovery(root, error, *, confirm=None, inspect=None, launch=None, persist=None, cancel=None):
    """Return None for unsupported errors, else an outcome requiring normal exit.

    Called only by production startup while it still owns its runtime lease,
    before creating providers, cache, QtClient or resource workers. Inspection
    takes a short writer lock; neither confirmation nor helper launch holds it.
    Exceptions propagate to qt_client_entry's single startup failure reporter.
    Injected functions are isolated-test boundaries, not a runtime bypass.
    """
    code = getattr(error, "code", None)
    kind = _PENDING_KINDS.get(code) if isinstance(code, str) else None
    if kind is None:
        return None
    root = Path(root).absolute()
    inspect = inspect or _inspect
    plan = _validate_plan(inspect(root), kind)
    accepted = (confirm or _confirm)(_confirmation_message(plan, kind))
    if accepted is not True:
        return StartupRecoveryResult("cancelled")
    # Recheck after consent; the helper repeats the same check after parent exit.
    if _validate_plan(inspect(root, plan["transactionId"]), kind) != plan:
        raise StartupRecoveryError("recovery-plan-changed-reinspect-required")
    request_id = uuid4().hex
    # Write only the UI receipt-correlation marker before spawning. A write
    # failure must not leave a running helper that now needs disk cancellation.
    (persist or _persist_pending)(root, plan, request_id)
    try:
        process = (launch or _launch)(root, plan, request_id)
        if (not isinstance(getattr(process, "pid", None), int) or process.pid <= 0
                or not callable(getattr(process, "poll", None)) or process.poll() is not None):
            raise StartupRecoveryError("recovery-helper-launch-not-confirmed")
    except Exception as error:
        # A launched child must not later mutate resources after a failed handoff.
        # Persist cancellation before the caller releases its lease/exits.
        try:
            (cancel or _cancel)(root, request_id)
        except Exception:
            raise StartupRecoveryError("startup-recovery-cancel-unconfirmed") from error
        raise
    return StartupRecoveryResult("helper-started", request_id)
