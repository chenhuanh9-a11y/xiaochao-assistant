"""Bounded, private-by-default diagnostics usable before Qt/native imports.

One latest-failure file, at the first writable location: installation work,
LocalAppData/Xingtu/diagnostics, then user Temp/Xingtu/diagnostics. One native
notification per report; no retries, subprocess, configuration reads or Qt.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

LOG_NAME = "qt-client-startup-error.txt"
MAX_REPORT_BYTES = 16 * 1024
_STAGES = {"validate-profile", "import-client", "run-client", "create-client"}
_RESOURCE_ACTIONS = {
    "resource-busy": "其他资源操作正在进行，请稍后重试。",
    "resource-runtime-already-active": "另一个程序实例正在使用资源，请先关闭该实例后重试。",
    "upgrade-recovery-required": "资源升级尚待恢复，不能继续资源初始化。请完成资源恢复后重试。",
    "base-recovery-required": "基础资源修复尚待恢复，不能继续资源初始化。请完成资源恢复后重试。",
    "additions-recovery-required": "资源导入尚待恢复，不能继续资源初始化。请完成资源恢复后重试。",
    "removal-recovery-required": "图包删除尚未完成，请完成资源恢复或临时文件清理后重试。",
    "startup-recovery-plan-invalid": "资源恢复计划无效。原资源已保留，请保留诊断记录。",
    "startup-recovery-plan-blocked": "待恢复资源与记录不一致，不能安全覆盖或删除。请保留诊断记录。",
    "startup-recovery-confirmation-unavailable": "无法显示资源恢复确认，未授权恢复。请保留诊断记录。",
    "startup-recovery-cancel-unconfirmed": "资源恢复交接失败，取消记录未能写入。本次启动失败退出，请保留诊断记录。",
    "recovery-plan-changed-reinspect-required": "确认期间资源恢复计划已变化，请重新启动并核对。",
    "recovery-helper-launch-not-confirmed": "资源恢复工具未确认启动，请保留诊断记录后重试。",
    "resource-linked-target": "资源目录含链接，无法安全初始化。请检查安装目录。",
    "installation-root-missing": "安装目录不存在，请使用安装器修复安装。",
    "resource-hardlinks-unavailable": "安装位置无法完成硬链接资源事务，请检查文件系统支持和目录权限。",
    "resource-cross-volume-target": "资源目录跨越多个卷，无法安全初始化。请检查安装目录。",
    "resource-unsafe-target": "资源目录不符合安全访问要求，请检查安装目录。",
    "base-candidate-manifest-missing": "安装器提供的基础资源清单缺失，请使用安装器修复安装。",
    "base-repair-preflight-blocked": "基础资源核对发现冲突或损坏，不能继续资源初始化。请保留诊断记录并完成资源修复。",
    "base-repair-failed-and-rollback-incomplete": "基础资源修复失败且恢复未完成，不能继续资源初始化。请保留诊断记录并完成资源恢复。",
    "base-rollback-incomplete": "基础资源恢复尚未完成，不能继续资源初始化。请保留诊断记录并完成资源恢复。",
    "author-upgrade-recovery-required": "作者资源升级尚待恢复，不能继续资源初始化。请完成资源恢复后重试。",
    "resource-initialization-failed": "基础资源初始化失败，请保留诊断记录并完成资源修复。",
}
for _code in (
    "invalid-base-inventory", "invalid-base-path", "invalid-base-manifest-schema",
    "invalid-base-manifest-bundle-id", "unrecognized-installed-package-receipt",
    "base-pending-pointer-changed", "invalid-base-pending-pointer", "invalid-base-repair-journal",
    "invalid-base-repair-entries", "invalid-base-repair-target", "base-repair-retained-object-changed",
    "invalid-base-repair-old-target", "invalid-base-repair-state-entry", "committed-base-file-changed",
    "committed-base-state-changed", "base-target-appeared-before-commit",
    "base-verification-before-state-failed", "base-published-object-changed",
):
    _RESOURCE_ACTIONS[_code] = _RESOURCE_ACTIONS["resource-initialization-failed"]

_ISSUE_REASONS = {
    "base-ownership-unknown": "基础资源归属未知",
    "candidate-integrity-mismatch": "安装候选资源校验失败",
    "installed-package-owned-conflict": "与已安装资源的归属冲突",
    "installed-package-dependency-conflict": "与已安装资源的依赖冲突",
    "installed-package-dependencies-unrecorded": "已安装资源未记录依赖",
    "changed-or-unwritable": "资源已变化或不可写",
    "not-base-or-duplicate": "基础资源路径无效或重复",
    "unknown-package": "资源包未登记",
}


def _causes(error):
    seen = set()
    for _ in range(3):
        if error is None or id(error) in seen:
            break
        seen.add(id(error))
        yield error
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def _resource_code(error):
    for cause in _causes(error):
        value = getattr(cause, "code", None)
        if isinstance(value, str) and value in _RESOURCE_ACTIONS:
            return value
        if type(cause).__name__ in {"ManagedBaseResourceError", "ResourceTransactionError"}:
            return "resource-initialization-failed"
    return ""


def _safe_issues(error):
    """Copy only relative resource names and finite, typed integrity metadata."""
    for cause in _causes(error):
        details = getattr(cause, "details", None)
        if not _resource_code(cause) or not isinstance(details, dict):
            continue
        issues = details.get("issues")
        if not isinstance(issues, list):
            continue
        result = []
        for issue in issues[:12]:
            if not isinstance(issue, dict):
                continue
            reason = issue.get("reason")
            row = {"reason": reason if isinstance(reason, str) and reason in _ISSUE_REASONS else "unknown"}
            path = issue.get("path")
            if (isinstance(path, str) and len(path) <= 240
                    and (path == "_internal/difficult-native-manifest.json"
                         or path.startswith(("_internal/references/", "_internal/recognition/", "_internal/assets/",
                                             "references/", "recognition/", "assets/")))
                    and re.fullmatch(r"[\w./ -]+", path)
                    and all(part and part not in {".", ".."} for part in path.split("/"))):
                row["path"] = path
            for side in ("expected", "actual"):
                data = issue.get(side)
                if not isinstance(data, dict):
                    continue
                if type(data.get("bytes")) is int and 0 <= data["bytes"] < (1 << 63):
                    row[side + ".bytes"] = data["bytes"]
                if isinstance(data.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
                    row[side + ".sha256"] = data["sha256"]
                if side == "actual" and data.get("status") in ("missing", "not-file", "hashed"):
                    row["actual.status"] = data["status"]
            result.append(row)
        count = details.get("issueCount")
        return result, count if type(count) is int and 0 <= count < 1000000 else len(issues)
    return [], 0


def _token(value, limit=100):
    # Never serialize an exception message, repr, locals, source lines or paths.
    return re.sub(r"[^A-Za-z0-9_.<>-]", "?", str(value))[:limit]


def _details(error, stage):
    code = "QT-STARTUP-FAILED"
    resource_code = _resource_code(error)
    if resource_code:
        code = "QT-STARTUP-RESOURCE"
    elif type(error).__name__ == "InstallationProfileError" or stage == "validate-profile":
        code = "QT-STARTUP-PROFILE"
    elif isinstance(error, ImportError) or stage == "import-client":
        code = "QT-STARTUP-DEPENDENCY"
    elif isinstance(error, PermissionError):
        code = "QT-STARTUP-STORAGE"
    rows = ["schema=qt-startup-diagnostic-v1", "time=" + datetime.now(timezone.utc).isoformat(),
            "code=" + code, "stage=" + (stage if stage in _STAGES else "unknown"),
            "exception=" + _token(type(error).__name__)]
    if resource_code:
        rows.append("resourceCode=" + resource_code)
        issues, count = _safe_issues(error)
        rows.append("issueCount=" + str(count))
        for index, issue in enumerate(issues):
            rows.extend("issue." + str(index) + "." + key + "=" + str(value) for key, value in issue.items())
    # Retain a bounded structured cause chain without private exception messages.
    for depth, current in enumerate(_causes(error)):
        prefix = "" if depth == 0 else "cause" + str(depth) + "."
        if depth:
            rows.append(prefix + "exception=" + _token(type(current).__name__))
        # name is a structured import attribute, not the possibly private message.
        if isinstance(current, ImportError) and isinstance(current.name, str):
            name = current.name
            if len(name) <= 150 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
                rows.append(prefix + "dependency=" + name)
        if isinstance(current, ImportError) and isinstance(current.path, str):
            name = Path(current.path).name
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.(?:dll|pyd)", name, re.IGNORECASE):
                rows.append(prefix + "dependencyBinary=" + name)
        for name in ("errno", "winerror"):
            value = getattr(current, name, None)
            if isinstance(value, int):
                rows.append(prefix + name + "=" + str(value))
    frames = []
    tb = error.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        frames.append("frame=" + _token(Path(frame.f_code.co_filename).name) + ":"
                      + str(tb.tb_lineno) + ":" + _token(frame.f_code.co_name))
        frames = frames[-8:]
        tb = tb.tb_next
    rows.extend(frames)
    return code, "\n".join(rows) + "\n"


def _candidate_directories():
    base = (Path(sys.executable).absolute().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1])
    yield base / "work"
    local = os.environ.get("LOCALAPPDATA")
    if local and Path(local).is_absolute():
        yield Path(local) / "Xingtu/diagnostics"
    try:
        yield Path(tempfile.gettempdir()) / "Xingtu/diagnostics"
    except OSError:
        pass


def _no_links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("Diagnostic path cannot use links")
        if item == path and getattr(info, "st_nlink", 1) > 1 and stat.S_ISREG(info.st_mode):
            raise OSError("Diagnostic file cannot use hard links")


def _persist(report):
    data = report.encode("utf-8")[:MAX_REPORT_BYTES]
    for directory in _candidate_directories():
        try:
            path = directory / LOG_NAME
            _no_links(path)
            directory.mkdir(parents=True, exist_ok=True)
            _no_links(path)
            # Overwrite the previous bounded report; never append unbounded logs.
            path.write_bytes(data)
            return path
        except (OSError, ValueError):
            continue
    return None


def _show_native_error(message):
    if sys.platform != "win32":
        return
    # This dependency is loaded only after a failure; Qt need not import at all.
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    box = user32.MessageBoxW
    box.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    box.restype = ctypes.c_int
    box(None, message, "星图启动失败", 0x10 | 0x10000)  # MB_ICONERROR | MB_SETFOREGROUND


def report_startup_failure(error, *, stage="run-client"):
    """Return the saved path if any; report failures must not mask the cause."""
    code, report = _details(error, stage)
    try:
        path = _persist(report)
    except Exception:
        path = None
    action = (_RESOURCE_ACTIONS[_resource_code(error)] if code == "QT-STARTUP-RESOURCE"
              else "安装身份文件缺失或损坏，请使用安装器修复安装。" if code == "QT-STARTUP-PROFILE"
              else "启动所需组件未能加载，请使用安装器修复安装。" if code == "QT-STARTUP-DEPENDENCY"
              else "无法访问启动所需目录，请检查目录的写入权限。" if code == "QT-STARTUP-STORAGE"
              else "程序无法完成启动，请保留诊断记录并联系支持。")
    location = "诊断记录：" + str(path) if path else "未能写入诊断记录：安装目录、LocalAppData 和临时目录均不可用。"
    message = "星图未能启动。\n" + action + "\n错误码：" + code + "\n" + location
    if code == "QT-STARTUP-RESOURCE":
        message += "\n资源原因：" + _resource_code(error)
        issues, count = _safe_issues(error)
        for issue in issues[:3]:
            message += "\n" + _ISSUE_REASONS.get(issue["reason"], "资源核对失败")
            if "path" in issue:
                message += "：" + issue["path"]
        if count > 3:
            message += "\n更多问题请查看诊断记录。"
    try:
        if sys.stderr is not None:
            sys.stderr.write(report + message + "\n")
    except Exception:
        pass
    if getattr(sys, "frozen", False) or sys.stderr is None:
        try:
            _show_native_error(message)
        except Exception:
            pass
    return path
