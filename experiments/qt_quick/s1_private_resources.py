"""Strict native S1 contract for isolated Setup resources; no live authorization."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import stat

S1_MODES = ("difficulty2", "difficult", "nightmare")  # Historical read scope, not runtime authority.
S1_RUNTIME_MODES = ("difficult", "nightmare")
S1_INTERFACES = dict(identityPack="references/map-pack.json",
    typedIndex="references/shared-structure-index.json", versionBindings="references/structure-version-bindings.json",
    coordinatePack="references/coordinate-pack.json", neutralDisplay="references/structure-display.json",
    identityEntries="references/entries", authorCatalog="references/author-catalog.json")


def private_profile_modes(profile):
    """Read an isolated profile's exact declaration; never grant capabilities.

    Old private profiles predate context declarations and used all three packs.
    The caller still validates every directory, schema and inventoried byte.
    """
    declared = profile.get("includedResourceContexts")
    if declared is None:
        return S1_MODES
    if (type(declared) is not list or any(type(key) is not str for key in declared)
            or len(declared) != len(set(declared))):
        raise ValueError("Invalid private resource context declaration")
    s0 = {"s0:nightmare", "s0:difficult"}
    active = {"s1:" + mode for mode in S1_RUNTIME_MODES}
    historical = {"s1:" + mode for mode in S1_MODES}
    if set(declared) not in (s0 | active, s0 | historical):
        raise ValueError("Incomplete private S1 resource context declaration")
    return S1_MODES if "s1:difficulty2" in declared else S1_RUNTIME_MODES


def _no_links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Seed resources cannot use links or reparse points")


def _native_path(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
            or PurePosixPath(name).as_posix() != name):
        raise ValueError("S1 native paths must be normalized mode-relative paths")
    path = root / name
    _no_links(path)
    if not path.resolve().is_relative_to(root):
        raise ValueError("S1 native resource escapes its exact mode root")
    return path


def validated_s1_resources(root, *, required=False, modes=S1_RUNTIME_MODES):
    """Exact native subtree gate shared by build, first-run seed and self-check.

    No discovery outside this root, capture, listeners, learning or user writes.
    Geometry availability never grants S1 automatic live authorization.
    """
    modes = tuple(modes)
    if not modes or len(set(modes)) != len(modes) or set(modes) - set(S1_MODES):
        raise ValueError("Invalid explicit S1 resource mode scope")
    root = Path(root).absolute()
    _no_links(root)
    root = root.resolve()
    seasons = root / "seasons"
    _no_links(seasons)
    if not seasons.exists():
        if required:
            raise ValueError("S0/S1 Setup requires all declared S1 native packs")
        return dict(included=False, files={}, providers={})
    if (not seasons.is_dir() or {p.name for p in seasons.iterdir()} != {"s1"}
            or not (seasons / "s1").is_dir()
            or {p.name for p in (seasons / "s1").iterdir()} != set(modes)):
        raise ValueError("Only the complete exact seasons/s1 native mode roots may be seeded")
    files = {}
    from s1_native_resources import MANIFEST, MODE_LAYOUTS, S1_FLOORS, registration
    import numpy as np
    for mode in modes:
        mode_root = seasons / "s1" / mode
        _no_links(mode_root)
        for path in mode_root.rglob("*"):
            _no_links(path)
        raw = registration(mode_root, mode)
        if raw.get("numpyVersion") != np.__version__:
            raise ValueError("S1 NumPy runtime differs from the native pack producer")
        if raw["runtimeInterfaces"] != S1_INTERFACES:
            raise ValueError("Unexpected S1 runtime interface")
        for value in raw["runtimeInterfaces"].values():
            _native_path(mode_root, value)
        rows = raw.get("files")
        if not isinstance(rows, list) or not rows:
            raise ValueError("S1 native file inventory missing")
        expected = {MANIFEST}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}
                    or type(row["bytes"]) is not int or row["bytes"] < 0
                    or not isinstance(row["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
                raise ValueError("Invalid S1 native file/hash declaration")
            name = row["path"]
            path = _native_path(mode_root, name)
            if (name in expected or PurePosixPath(name).parts[0] not in {"assets", "references"}
                    or path.suffix.lower() not in {".json", ".png", ".npz"}):
                raise ValueError("Unexpected or duplicate S1 native file: " + name)
            expected.add(name)
            if (not path.is_file() or path.stat().st_size != row["bytes"]
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]):
                raise ValueError("S1 native size/hash mismatch: " + name)
        actual = {p.relative_to(mode_root).as_posix() for p in mode_root.rglob("*") if p.is_file()}
        if actual != expected:
            raise ValueError("S1 native subtree must exactly match its manifest inventory")
        # The current native producer owns only these resources. A manifest
        # cannot claim user settings, work data or unrelated image directories.
        allowed = {MANIFEST, "references/native-similarity-groups.json",
            "references/entries/entries.json", "references/entries/side-entries.json",
            "references/entries/entry-passports.json"}
        allowed.update(value for key, value in S1_INTERFACES.items() if key != "identityEntries")
        # Optional display-only registration binds final native overlays to the
        # original room-render hash. It never changes recognition resources.
        if "references/native-display-bindings.json" in expected:
            allowed.add("references/native-display-bindings.json")
        for mid in MODE_LAYOUTS[mode]:
            for floor in S1_FLOORS:
                allowed.update((f"assets/game-native/{mid}/{floor}.png",
                    f"references/entries/{mid}-{floor}-shared.npz",
                    f"references/fast-structure/{mid}-{floor}-shared-fast.npz"))
        if expected != allowed:
            raise ValueError("S1 native inventory differs from the registered producer file scope")
        for name in sorted(expected):
            path = mode_root / name
            files[f"seasons/s1/{mode}/{name}"] = dict(bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    # Historical modes retain registration/schema/hash verification above, but
    # cannot instantiate a retired runtime provider. Only selected active modes
    # are prepared, so explicit historical-only inspection is fully read-only.
    from client_providers import build_engine_factories
    active = tuple(mode for mode in modes if mode in S1_RUNTIME_MODES)
    providers = {}
    if active:
        registry = build_engine_factories(root)
        for mode in active:
            providers[mode] = registry.prepare_context(mode, season="s1").get(mode, {})
    if not all(row.get("available") is True and row.get("prepared") is True
            and row.get("season") == "s1" and row.get("mode") == mode
            for mode, row in providers.items()):
        raise ValueError("S1 typed/version provider contract rejected: " + json.dumps(providers))
    if any(row.get("liveReady") is not False or row.get("liveAuthorization") is not False for row in providers.values()):
        raise ValueError("S1 private resources cannot authorize automatic live operation")
    return dict(included=True, files=files, providers=providers)
