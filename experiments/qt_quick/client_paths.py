"""Installation paths: baked production identity or explicitly marked test data."""
from dataclasses import dataclass
import json
import hashlib
import stat
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])).resolve()
WORK_ROOT = ((Path(sys.executable).resolve().parent / "work") if FROZEN else RESOURCE_ROOT / "work").resolve()
APP_ROOT = RESOURCE_ROOT / "app"
INSTALL_ROOT = Path(sys.executable).resolve().parent
PRODUCTION_APP_ID = "D04394A0-DACE-4FF9-874D-3DCB43EE53D1"
PRODUCTION_PRODUCT = "第五人格小抄助手"


def _production_profile():
    # Only the build-baked profile counts; an adjacent file or CLI root cannot
    # turn a source/test executable into the original product.
    if not FROZEN:
        return False
    try:
        profile_path = RESOURCE_ROOT / "installation-profile.json"
        for item in (profile_path, *profile_path.parents):
            info = item.lstat()
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                return False
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        instance = profile.get("installerInstance", {})
        return (profile.get("product") == PRODUCTION_PRODUCT
                and instance.get("id") == "production"
                and instance.get("product") == PRODUCTION_PRODUCT
                and instance.get("appId") == PRODUCTION_APP_ID)
    except (OSError, ValueError, AttributeError, TypeError):
        return False


PRODUCTION = _production_profile()
DEFAULT_SANDBOX = INSTALL_ROOT if PRODUCTION else WORK_ROOT / ("qt-client-runtime" if FROZEN else "qt-lab-installation")


def owned_sandbox(path):
    return validate_installation_root(path)


def validate_installation_root(path):
    path = Path(path).absolute()
    _no_links(path)
    path = path.resolve()
    if PRODUCTION:
        if path != INSTALL_ROOT:
            raise ValueError("Production data must stay beside the installed executable")
        return path
    marker = path / ".qt-lab-owner"
    _no_links(marker)
    if (not path.is_relative_to(WORK_ROOT) or not marker.is_file() or marker.is_symlink()
            or marker.read_text(encoding="utf-8").strip() != "qt-quick-isolated-v1"):
        raise ValueError("Qt client data must use its own marked work directory")
    return path


def _no_links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ValueError("Runtime paths cannot use links or reparse points")


@dataclass(frozen=True)
class RuntimePaths:
    installation_root: Path
    production: bool

    @property
    def resource_root(self):
        return RESOURCE_ROOT if self.production else self.installation_root / "_internal"

    @property
    def work_root(self):
        return self.installation_root / "work"

    def memory_root(self, mode):
        if mode not in ("nightmare", "difficult"):
            raise ValueError("Unknown game mode")
        return self.work_root / (mode + "-memory")

    @property
    def settings_path(self):
        return self.memory_root("nightmare") / "ui-settings.json" if self.production else self.installation_root / "ui-settings.json"

    @property
    def custom_galleries_path(self):
        return self.settings_path.with_name("custom-galleries.json")

    @property
    def update_state_path(self):
        return self.settings_path.with_name("update-state.json") if self.production else self.installation_root / "qt-update-state.json"

    @property
    def editor_base_candidates(self):
        candidates = ([self.installation_root / "assets/custom-maps"] if self.production else [])
        return candidates + [self.resource_root / "assets/custom-maps", self.resource_root / "qt-editor-base"]

    @property
    def editor_base_root(self):
        # Cheap path lookup used by general UI services. Full family validation
        # and fallback selection belong to CustomGalleryService's worker.
        return next((p for p in self.editor_base_candidates if p.exists()), self.resource_root / "qt-editor-base")


def editor_library_usable(root):
    """Select complete, verified editor families without modifying old installs."""
    root = Path(root)
    try:
        _no_links(root)
        manifest = root / "manifest.json"
        _no_links(manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        rows = data["maps"]
        if data.get("schema") != "xingtu-custom-maps-v1" or not isinstance(rows, list) or not rows:
            return False
        for row in rows:
            if not row["mapId"] or not row["name"] or not row["floors"]:
                return False
            for floor in row["floors"].values():
                for name in ("image", "topology"):
                    asset = root / floor[name]
                    _no_links(asset)
                    if not asset.resolve().is_relative_to(root.resolve()):
                        return False
                    if hashlib.sha256(asset.read_bytes()).hexdigest() != floor[name + "Sha256"]:
                        return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def runtime_paths(root=None):
    paths = RuntimePaths(validate_installation_root(DEFAULT_SANDBOX if root is None else root), PRODUCTION)
    for path in (paths.settings_path, paths.custom_galleries_path, paths.update_state_path,
                 paths.editor_base_root, paths.resource_root, paths.work_root,
                 paths.memory_root("nightmare"), paths.memory_root("difficult")):
        _no_links(path)
    return paths
