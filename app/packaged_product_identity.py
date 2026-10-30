"""Fixed process identities selected only by the profile baked into a frozen runtime.

The caller's installation root, arguments and environment never select an EXE.
Source/legacy helpers without a baked profile retain the original Tk contract.
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
}


def baked_identity():
    if not getattr(sys, "frozen", False):
        return "legacy-tk"
    runtime = Path(sys._MEIPASS)
    path = runtime / "installation-profile.json"
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Baked installation profile cannot use links")
    if not path.exists():
        return "legacy-tk"
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise ValueError("Invalid baked installation profile")
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
    raise ValueError("Invalid baked installation profile")


def expected_application_executable():
    identity = baked_identity()
    if identity == "qt-production":
        return QT_EXE
    if identity == "legacy-tk":
        return LEGACY_EXE
    raise ValueError("Isolated Qt clients cannot use production resource helpers")
