"""Bounded, private-by-default diagnostics usable before Qt/native imports.

One latest-failure file, at the first writable location: installation work,
LocalAppData/Xingtu/diagnostics, then user Temp/Xingtu/diagnostics. One native
notification per report; no retries, subprocess, user configuration reads or Qt.
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
_STAGES = {"validate-profile", "import-client", "run-client", "instance-check", "create-client"}
_RESOURCE_ACTIONS = {
    "installer-upgrade-recovery-required": "上次安装升级尚未完成核验，暂时无法启动。请保留现有安装目录和诊断记录，重新运行安装程序核验；记录缺失或文件状态变化时，不会自动回退。",
    "resource-reset-recovery-required": "上次资源重置尚未完成核验，暂时无法启动。请保留现有安装目录和诊断记录，重新运行安装程序核验；记录缺失或文件状态变化时，不会自动回退。",
    "installed-package-receipt-rejected": "无法核对旧图包的安装记录，暂时无法启动。请保留现有图包和设置，附上诊断记录反馈，不要卸载或删除图包。",
    "upgrade-cleanup-incomplete": "图包更新已结束，但临时文件还没清理完。请按提示继续清理，已经更新的图包不会被还原。",
    "base-cleanup-incomplete": "上次修复留下的临时文件还没清理完。请关闭可能占用文件的程序后重开星图；仍出现此提示时，请附上诊断记录反馈。",
    "resource-busy": "其他资源操作正在进行，请稍后重试。",
    "resource-runtime-already-active": "另一个星图窗口正在使用地图文件，请先退出它再试。",
    "upgrade-recovery-required": "上次图包更新还没处理完，暂时无法启动。请先按恢复提示完成处理。",
    "base-recovery-required": "上次地图文件修复还没处理完，暂时无法启动。请保留诊断记录并反馈。",
    "additions-recovery-required": "上次图包导入还没处理完，暂时无法启动。请先按恢复提示完成处理。",
    "removal-recovery-required": "上次图包删除还没处理完，暂时无法启动。请先按恢复提示完成处理。",
    "startup-recovery-plan-invalid": "无法核对上次图包操作的记录，本次没有继续恢复。请保留现有文件，附上诊断记录反馈。",
    "startup-recovery-plan-blocked": "需要恢复的文件与原记录不同，本次没有继续覆盖或删除。请保留现有文件，附上诊断记录反馈。",
    "startup-recovery-confirmation-unavailable": "无法显示恢复确认窗口，本次没有开始恢复。请附上诊断记录反馈。",
    "startup-recovery-cancel-unconfirmed": "未能确认上次恢复操作是否已取消，星图暂时无法启动。请保留现有文件，附上诊断记录反馈。",
    "recovery-plan-changed-reinspect-required": "确认期间图包文件发生了变化。请重新打开星图，核对新的恢复提示后再决定。",
    "recovery-helper-launch-not-confirmed": "未能确认恢复工具是否已打开。请保留现有文件，附上诊断记录反馈。",
    "resource-linked-target": "部分地图文件的位置无法安全核对，星图暂时无法处理。请保留现有文件，附上诊断记录反馈。",
    "installation-root-missing": "找不到星图的安装目录。请确认程序没有被移动，并附上诊断记录反馈。",
    "resource-hardlinks-unavailable": "当前安装位置不支持图包更新所需的文件操作。请附上诊断记录反馈，先不要反复导入或删除图包。",
    "resource-atomic-move-unavailable": "当前安装位置无法安全提交地图文件，暂时无法完成启动。请保留现有文件并附上诊断记录反馈。",
    "resource-cross-volume-target": "地图文件分布在不同磁盘，暂时无法完成更新。请保留现有文件，附上诊断记录反馈。",
    "resource-unsafe-target": "星图无法安全访问部分地图文件，暂时停止启动。请保留现有文件，附上诊断记录反馈。",
    "base-candidate-manifest-missing": "安装文件缺少地图资源清单，暂时无法启动。请附上诊断记录反馈，不要手动删除图包。",
    "base-repair-preflight-blocked": "启动所需的地图文件与安装记录不一致，暂时无法启动。请保留现有图包，附上诊断记录反馈。",
    "base-repair-failed-and-rollback-incomplete": "地图文件修复失败，还没恢复到可用状态。请保留现有文件，附上诊断记录反馈。",
    "base-rollback-incomplete": "地图文件还没恢复到可用状态，暂时无法启动。请保留现有文件，附上诊断记录反馈。",
    "author-upgrade-recovery-required": "上次作者图包更新还没处理完，暂时无法启动。请先按恢复提示完成处理。",
    "resource-initialization-failed": "星图未能准备好启动所需的地图文件，具体原因尚未确定。请保留现有文件，附上诊断记录反馈。",
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
    "invalid-installed-package-receipt": "图包安装记录格式未通过校验",
    "invalid-installed-package-dependencies": "图包依赖记录未匹配已复核契约",
    "receipt-validation-rejected": "图包安装记录未通过校验",
    "cleanup-object-changed": "临时对象已变化",
    "cleanup-object-unknown": "存在归属未知的临时对象",
    "cleanup-object-unwritable": "临时对象无法清理",
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
                    and (path in {"_internal/difficult-native-manifest.json",
                                  "work/map-packages/pending-base-repair.json", "work/map-packages/pending-upgrade.json"}
                         or _registered_receipt_path(path)
                         or path.startswith(("_internal/references/", "_internal/recognition/", "_internal/assets/",
                                              "references/", "recognition/", "assets/",
                                              "work/map-packages/base-repairs/", "work/map-packages/upgrades/")))
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


def _registered_receipt_path(path):
    # Only source-registered package IDs, never arbitrary user JSON filenames.
    if not path.startswith("work/map-packages/") or not path.endswith(".json"):
        return False
    packages = getattr(sys.modules.get("star_map_import"), "PACKAGE_IDS", ())
    return path[len("work/map-packages/"):-len(".json")] in packages


def _safe_resource_capabilities(error):
    """Source-owned, fixed enums only, including when details are untrusted."""
    for cause in _causes(error):
        details = getattr(cause, 'details', None)
        if not isinstance(details, dict):
            continue
        rows = []
        if details.get('resourceLocation') == 'work/map-packages':
            rows.append('resourceLocation=work/map-packages')
        stage = details.get('resourceSubstage')
        if isinstance(stage, str) and stage in {'hardlink-probe', 'atomic-move-probe', 'atomic-no-replace-probe'}:
            rows.append('resourceSubstage=' + stage)
        capabilities = details.get('filesystemCapabilities')
        if isinstance(capabilities, dict):
            for name in ('hardlinks', 'atomicNoReplace'):
                value = capabilities.get(name)
                if isinstance(value, str) and value in {'unknown', 'unavailable', 'unsupported', 'verified'}:
                    rows.append('filesystem.' + name + '=' + value)
        if rows:
            return rows
    return []


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
        rows.extend(_safe_resource_capabilities(error))
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


def _fallback_directory():
    if getattr(sys, "frozen", False):
        try:
            from packaged_product_identity import baked_identity
            if baked_identity(require_qt=True) == "qt-production":
                return Path("Xingtu/diagnostics")
        except (OSError, ValueError, ImportError, AttributeError, TypeError):
            # Metadata failure must still have a report. The original formal
            # executable keeps its recovery location; private/unknown entries
            # cannot overwrite the formal fallback report.
            if Path(sys.executable).name.casefold() == "星图.exe":
                return Path("Xingtu/diagnostics")
        return Path("Xingtu/qt-test/diagnostics")
    return Path("Xingtu/diagnostics")


def _candidate_directories():
    base = (Path(sys.executable).absolute().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1])
    yield base / "work"
    local = os.environ.get("LOCALAPPDATA")
    if local and Path(local).is_absolute():
        yield Path(local) / _fallback_directory()
    try:
        yield Path(tempfile.gettempdir()) / _fallback_directory()
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


def _record_location(path):
    """Locate the report without printing an account-specific absolute path."""
    path = Path(path)
    base = (Path(sys.executable).absolute().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1])
    locations = [(base / "work", "安装目录下的 work 文件夹")]
    local = os.environ.get("LOCALAPPDATA")
    if local and Path(local).is_absolute():
        relative = _fallback_directory()
        locations.append((Path(local) / relative, "%LOCALAPPDATA%/" + relative.as_posix()))
    try:
        relative = _fallback_directory()
        locations.append((Path(tempfile.gettempdir()) / relative, "%TEMP%/" + relative.as_posix()))
    except OSError:
        pass
    label = next((label for directory, label in locations if path.parent == directory), None)
    return ("详细记录已保存：" + (label + "中的 " if label else "") + LOG_NAME
            + "。反馈时请附上此文件。")


def report_startup_failure(error, *, stage="run-client", show_dialog=True):
    """Return the saved path if any; report failures must not mask the cause."""
    code, report = _details(error, stage)
    try:
        path = _persist(report)
    except Exception:
        path = None
    action = (_RESOURCE_ACTIONS[_resource_code(error)] if code == "QT-STARTUP-RESOURCE"
              else "无法核对当前安装的版本信息。请保留诊断记录并反馈，不要手动移动或删除安装文件。" if code == "QT-STARTUP-PROFILE"
              else "启动所需的组件未能打开。请附上诊断记录反馈，便于确认缺少哪个组件。" if code == "QT-STARTUP-DEPENDENCY"
              else "星图无法读写启动所需的文件夹。请确认文件夹可访问；仍失败时请附上诊断记录反馈。" if code == "QT-STARTUP-STORAGE"
              else "程序无法完成启动，具体原因尚未确定。请附上诊断记录反馈。")
    location = (_record_location(path) if path else
                "未能写入诊断记录。反馈时请提供此提示的截图。")
    # Raw reason codes, resource paths, hashes and stack frames remain in the
    # bounded report. The native dialog has no technical-details expander.
    message = "星图未能启动。\n" + action + "\n\n" + location
    try:
        if sys.stderr is not None:
            sys.stderr.write(report + message + "\n")
    except Exception:
        pass
    if show_dialog and (getattr(sys, "frozen", False) or sys.stderr is None):
        try:
            _show_native_error(message)
        except Exception:
            pass
    return path
