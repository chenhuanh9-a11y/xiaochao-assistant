"""Fixed process identities selected only by the profile baked into a frozen runtime.

The caller's installation root, arguments and environment never select an EXE.
Source/legacy helpers without a baked profile retain the original Tk contract.
Qt entry points explicitly require their own internal profile before any seeding.
"""
import json
from pathlib import Path
import stat
import sys

PRODUCTION_APP_ID = "D04394A0-DACE-4FF9-874D-3DCB43EE53D1"
PRODUCTION_PRODUCT = "星图"
LEGACY_EXE = "第五人格小抄助手.exe"
QT_EXE = "星图.exe"
CLOSED_EXE_NAMES = (LEGACY_EXE, QT_EXE)
_ISOLATED = {
    "client": "AF19AFD9-3016-44C9-9D20-6F216C5D8C51",
    "layout-preview": "D4B8E562-EA62-4A43-AF1D-043A5646971E",
    "s0-s1-test": "907F912D-58FD-4F84-A7A4-0C816AA70E52",
    "s1-floor-test": "3C271F5D-71FF-4EA8-9569-B8DAC30F3773",
    "s1-auto-test": "ED53B6E8-E9A0-4F21-AF75-7C8A62BE7347",
    "s1-playtest": "D9FE6D76-C31A-47AF-9281-B4614C245E92",
    "feature-test": "7A2F6C81-9B54-4DB6-A308-6E452D91F0BC",
}
PRIVATE_S1_INSTANCES = frozenset(("s0-s1-test", "s1-floor-test", "s1-auto-test", "s1-playtest", "feature-test"))


class InstallationProfileError(ValueError):
    """The frozen Qt installation cannot safely select its data root."""


def baked_identity(*, require_qt=False):
    if not getattr(sys, "frozen", False):
        return "legacy-tk"
    runtime_value = getattr(sys, "_MEIPASS", None)
    if not runtime_value:
        if require_qt:
            raise InstallationProfileError("Missing baked installation profile runtime")
        return "legacy-tk"
    runtime = Path(runtime_value)
    path = runtime / "installation-profile.json"
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400):
            raise InstallationProfileError("Baked installation profile cannot use links")
    if not path.exists():
        if require_qt:
            raise InstallationProfileError("Missing baked installation profile; repair the installation")
        return "legacy-tk"
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise InstallationProfileError("Invalid baked installation profile")
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
        instance = profile["installerInstance"]
        product = profile["product"]
        if (product == PRODUCTION_PRODUCT and instance["id"] == "production"
                and instance["product"] == product and instance["appId"] == PRODUCTION_APP_ID):
            return "qt-production"
        if (product == "XingtuQtClientTest" and instance["product"] == product and instance["id"] in _ISOLATED
                and _ISOLATED.get(instance["id"]) == instance["appId"]):
            return "qt-isolated"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise InstallationProfileError("Invalid baked installation profile")


def expected_application_executable():
    identity = baked_identity()
    if identity == "qt-production":
        return QT_EXE
    if identity == "legacy-tk":
        return LEGACY_EXE
    raise ValueError("Isolated Qt clients cannot use production resource helpers")


def qt_application_model_id():
    """Taskbar identity follows the validated baked product, never caller input."""
    identity = baked_identity(require_qt=True)
    if identity == "qt-production":
        return "Xingtu.Assistant"
    if identity == "legacy-tk":
        return "Xingtu.QtClientSource"
    profile = json.loads((Path(sys._MEIPASS) / "installation-profile.json").read_text(encoding="utf-8"))
    instance = profile["installerInstance"]["id"]
    if instance not in _ISOLATED:
        raise InstallationProfileError("Unknown isolated Qt application identity")
    return {"s0-s1-test": "Xingtu.S0S1PrivateTest", "s1-floor-test": "Xingtu.S1FloorPrivateTest",
            "s1-auto-test": "Xingtu.S1AutoPrivateTest", "s1-playtest": "Xingtu.S1Playtest",
            "feature-test": "Xingtu.FeatureTest"}.get(
        instance, "Xingtu.QtClientTest")
