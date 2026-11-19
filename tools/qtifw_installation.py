"""Read-only discovery of registered, physically verified production QtIFW roots.

Registry strings only nominate candidates. The existing upgrade admission checks
the owner, production profile, components and every owned program byte. Neither
this module nor the directory page creates a target or launches an installation.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
GUID = re.compile(r"\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}", re.I)


def registrations():
    """Inspect both Windows registry views; never open keys for writing."""
    if os.name != "nt":
        return []
    import winreg
    rows = []
    for hive, label in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL, 0, winreg.KEY_READ | view) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        name = winreg.EnumKey(parent, index)
                        try:
                            with winreg.OpenKey(parent, name, 0, winreg.KEY_READ | view) as key:
                                values = {}
                                for field in ("DisplayName", "Publisher", "DisplayVersion", "InstallLocation", "UninstallString"):
                                    try:
                                        values[field] = winreg.QueryValueEx(key, field)[0]
                                    except FileNotFoundError:
                                        values[field] = ""
                                if values["DisplayName"] == "星图":
                                    rows.append(dict(values, guid=name, hive=label, view=view))
                        except FileNotFoundError:
                            continue  # A registration removed while enumerating is not evidence.
            except FileNotFoundError:
                continue
    return rows


def _normal(path):
    return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")


def _registered_target(row):
    if not GUID.fullmatch(str(row.get("guid", ""))) or row.get("Publisher") != "星图":
        raise ValueError("unsupported-install-registration")
    location = row.get("InstallLocation")
    if not isinstance(location, str) or not location or not Path(location).is_absolute():
        raise ValueError("invalid-registered-install-location")
    target = Path(location).absolute()
    # QtIFW writes this exact executable and switch. Do not infer an install
    # root from a download path or accept an arbitrary registry command.
    command = row.get("UninstallString", "").strip()
    match = re.fullmatch(r'"([^"\r\n]+)"\s+--start-uninstaller', command)
    if not match or _normal(match[1]) != _normal(target / "trial-maintenance.exe"):
        raise ValueError("registered-maintenance-path-mismatch")
    return target


def discover(policy, *, rows=None, active=None):
    """Return one admissible root, or an explicit selection reason.

    The one currently running production root takes precedence. Otherwise a
    unique highest verified release is the previous installation; tied latest
    roots require user choice. Enumeration order and folder names never decide.
    Tests substitute only registry/process observation, keeping byte admission.
    """
    from qtifw_upgrade import check, _read, _old_install, pending
    rows = registrations() if rows is None else rows
    candidates, rejected, conflicted = {}, [], set()
    for row in rows:
        try:
            target = _registered_target(row)
            key = _normal(target)
            if key in conflicted:
                raise ValueError("conflicting-install-registration")
            if key in candidates:
                previous = candidates[key]
                if previous["guid"].casefold() != row["guid"].casefold() or previous["hive"] != row["hive"]:
                    conflicted.add(key)
                    del candidates[key]
                    raise ValueError("conflicting-install-registration")
                continue  # HKCU's two views can expose the same registration.
            if not target.is_dir():
                raise ValueError("registered-installation-unavailable")
            recovery = pending(policy, target) is not None
            if not recovery:
                _old_install(policy, target, verify_files=False)
            report = {} if recovery else _read(target / "program/release-report.json")
            if not recovery and report.get("displayVersion") != row.get("DisplayVersion"):
                raise ValueError("registered-install-version-mismatch")
            candidates[key] = dict(target=str(target), guid=row["guid"], hive=row["hive"],
                                   version=row.get("DisplayVersion"), recovery=recovery)
        except (ValueError, OSError, KeyError) as error:
            rejected.append(dict(target=str(row.get("InstallLocation", "")), reason=str(error)))
    values = list(candidates.values())
    selection = None
    shortlist = values
    if len(values) > 1:
        if active is None:
            from game_startup_win32 import Windows
            windows = Windows()
            active = lambda root: windows.main_state(Path(root) / "program") == "present"
        running = [row for row in values if active(row["target"])]
        if len(running) == 1:
            shortlist, selection = running, "running-production-installation"
        elif not running:
            def version(row):
                value = row.get("version", "")
                if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
                    raise ValueError("registered-install-version-invalid")
                return tuple(map(int, value.split(".")))
            highest = max(map(version, values))
            latest = [row for row in values if version(row) == highest]
            shortlist = latest
            if len(latest) == 1:
                selection = "latest-verified-production-release"
        else:
            shortlist = running
    # Hash only the authoritative running/latest shortlist. Historical lower
    # installations need not be read in full to update a unique newer release.
    # Nothing is returned as valid until the complete existing admission passes.
    verified = []
    for row in shortlist:
        try:
            if check(policy, Path(row["target"]))["mode"] != "update":
                raise ValueError("registered-installation-unavailable")
            verified.append(row)
        except (ValueError, OSError, KeyError) as error:
            rejected.append(dict(target=row["target"], reason=str(error)))
    if len(verified) == 1:
        result = dict(mode="update", **verified[0], candidates=verified, rejected=rejected)
        if selection:
            result["selection"] = selection
        return result
    if len(verified) > 1:
        return dict(mode="select", reason="multiple-valid-installations", candidates=verified, rejected=rejected)
    if rejected:
        # Stale missing roots do not turn a genuinely fresh machine into an
        # error. Existing but malformed roots require an explicit decision.
        existing = [row for row in rejected if row["target"] and Path(row["target"]).exists()]
        if existing:
            return dict(mode="select", reason="registered-installation-needs-inspection",
                        candidates=[], rejected=rejected)
    return dict(mode="fresh", candidates=[], rejected=rejected)
