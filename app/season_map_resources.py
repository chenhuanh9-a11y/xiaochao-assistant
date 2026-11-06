"""S1 display inventories, without identity or live capability.

Prefer an explicit offline image index. When absent, adapt the selected native
pack's hash-bound game-native PNGs. No S0 pack, author fallback, learning sample,
runtime factory, asset generation or installation write is involved.
"""
from concurrent.futures import CancelledError
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType

from map_resource_model import ResourceMap, ResourceVariant

SCHEMA = "xingtu-season-offline-maps-v1"
INDEX = "references/seasons/s1/offline-map-index.json"
SOURCE_VERSION = "2.104.3099014"
GROUPS = {105: (2, "difficulty2"), 106: (3, "difficult"), 107: (4, "nightmare")}
EXPECTED_LAYOUTS = frozenset(f"{group}{number:03}" for group in GROUPS
    for number in range(1, 16) if (group, number) != (106, 6))
PROFILE_NAMES = {"rendered": "S1 · 带门渲染图", "structure": "S1 · 结构图"}
GAME_FLOORS = {1: -4, 2: -3, 3: -2, 4: -1}
LIVE_GAPS = ("s1-floor-controls-unregistered",
             "s1-entry-coordinate-contract-unregistered")


def browse_modes(season):
    return ("nightmare", "difficult", "difficulty2") if season == "s1" else ("nightmare", "difficult")


def verify_offline_inventory(resource_root):
    """Use the production browse loader, then actually decode every unique image.

    Callable by the existing client self-check without starting a GUI or game.
    This verifies file contracts and decoding, not geometry or gameplay truth.
    """
    from PIL import Image
    reports, images = {}, set()
    for mode in browse_modes("s1"):
        context = load_season_browse_context(resource_root, mode, season="s1")
        reports[mode] = dict(available=not bool(context.error), error=context.error,
            maps=context.profiles[0].map_count if context.profiles else 0,
            layersPerImageType=context.profiles[0].available_layers if context.profiles else 0)
        for profile in context.profiles:
            for item in profile.maps:
                for variant in item.variants:
                    images.update(variant.layers.values())
    failures = []
    for path in sorted(images):
        try:
            with Image.open(path) as image:
                image.load()
        except (OSError, ValueError) as error:
            failures.append(f"{path.relative_to(Path(resource_root).resolve())}: {error}")
    return dict(ready=all(row["available"] for row in reports.values()) and not failures,
        season="s1", sourceVersion=SOURCE_VERSION, modes=reports,
        decodedImages=len(images) - len(failures), decodeFailures=failures,
        sourceHeightToGameFloor=dict(GAME_FLOORS), liveAvailable=False, liveGaps=list(LIVE_GAPS))


def _load_native_browse_context(root, mode, *, cancelled):
    """Browse one registered native mode using its existing game-native PNGs.

    This is a display adapter, not a runtime factory or author installation.
    Never infer modes from filenames or look outside the selected mode root.
    """
    from map_resource_context import BrowseContext, ProfileSummary
    from s1_native_resources import MODE_LAYOUTS, S1_FLOORS, MANIFEST, REQUIRED_INTERFACES, registration

    def check():
        if cancelled():
            raise CancelledError()

    mode_root = root / "seasons" / "s1" / mode

    def local(relative):
        check()
        if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts
                or PurePosixPath(relative).as_posix() != relative):
            raise ValueError("native browse path must be normalized and mode-relative")
        path = mode_root / relative
        for parent in (path, *path.parents):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("native browse resources cannot use links or reparse points")
        if not path.resolve().is_relative_to(mode_root.resolve()):
            raise ValueError("native browse path escapes selected mode")
        return path.resolve()

    manifest = local(MANIFEST)
    if not manifest.is_file():
        raise ValueError("native browse manifest not installed")
    raw = json.loads(manifest.read_text("utf-8"))
    if ((raw.get("schemaVersion"), raw.get("season"), raw.get("mode"), raw.get("sourceVersion"))
            != (1, "s1", mode, SOURCE_VERSION)):
        raise ValueError("native browse manifest context/source mismatch")
    interfaces = raw.get("runtimeInterfaces")
    if not isinstance(interfaces, dict) or not REQUIRED_INTERFACES <= interfaces.keys():
        raise ValueError("native browse interfaces incomplete")
    for relative in interfaces.values():
        local(relative)
    inventory = raw.get("files")
    if not isinstance(inventory, list) or not inventory:
        raise ValueError("native browse file inventory missing")
    stamps = {}
    for row in inventory:
        check()
        if (not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}
                or type(row["bytes"]) is not int or row["bytes"] < 0
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
            raise ValueError("invalid native browse hash declaration")
        path = local(row["path"])
        if path in stamps:
            raise ValueError("duplicate native browse file")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != row["bytes"]:
            raise ValueError("native browse file size mismatch: " + row["path"])
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                check()
                hasher.update(block)
        after = path.stat()
        if ((before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size)
                or hasher.hexdigest() != row["sha256"]):
            raise ValueError("native browse file hash mismatch: " + row["path"])
        stamps[path] = (after.st_mtime_ns, after.st_size, row["sha256"])
    required = {local(interfaces[name]) for name in REQUIRED_INTERFACES if name != "identityEntries"}
    entry_root = interfaces["identityEntries"]
    required.update(local(f"{entry_root}/{name}") for name in
        ("entries.json", "side-entries.json", "entry-passports.json"))
    if not required <= stamps.keys():
        raise ValueError("native browse interface missing from hash inventory")
    pack_path = local(interfaces["identityPack"])
    pack = json.loads(pack_path.read_text("utf-8"))
    rows = pack["maps"]
    if ((pack.get("schemaVersion"), pack.get("season"), pack.get("mode"), pack.get("sourceVersion"))
            != (1, "s1", mode, SOURCE_VERSION) or pack.get("floors") != list(S1_FLOORS)
            or not isinstance(rows, list) or len(rows) != len(MODE_LAYOUTS[mode])
            or {row.get("id") for row in rows} != MODE_LAYOUTS[mode]):
        raise ValueError("native browse pack identity/floor scope mismatch")
    name = "S1 · 内置原生底图"
    maps = []
    for row in sorted(rows, key=lambda item: item["id"]):
        check()
        mid = row["id"]
        if (row.get("sourceLayout") != mid or row.get("sourceVersion") != SOURCE_VERSION
                or row.get("identityProfileId") != "shared-structure"
                or set(row.get("layers", {})) != set(S1_FLOORS)):
            raise ValueError("native browse map source/layer binding mismatch")
        for relative in row["layers"].values():
            if local(relative) not in stamps:
                raise ValueError("native browse structure missing from hash inventory")
        title = row.get("name", mid)
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise ValueError("invalid native browse map name")
        layers = {}
        for height, floor in sorted(GAME_FLOORS.items(), reverse=True):
            path = local(f"assets/game-native/{mid}/{floor}.png")
            if path not in stamps:
                raise ValueError("native browse image missing from hash inventory")
            layers[f"source-height-{height}"] = path
        maps.append(ResourceMap(mid, title, (ResourceVariant("native", name, layers),)))
    # Reuse the source registration contract without constructing any runtime.
    registration(mode_root, mode)
    maps = tuple(maps)
    first = next((item.id, floor, path) for item in maps
                 for variant in item.variants for floor, path in variant.layers.items())
    profile = ProfileSummary("s1-native-structure", name, None, len(maps), len(maps),
        0, 0, len(maps) * 4, len(maps) * 4, first, maps)
    return BrowseContext(mode, root, None, None, None, (profile,), MappingProxyType(stamps),
        warnings=LIVE_GAPS, season="s1", floor_metadata=MappingProxyType({
            f"source-height-{height}": MappingProxyType(dict(sourceHeight=height,
                gameFloor=floor, label=f"B{abs(floor)}")) for height, floor in GAME_FLOORS.items()}))


def native_builtin_summaries(root, *, cancelled=lambda: False):
    """Package-page status for native defaults, without author receipts/writes."""
    root = Path(root).resolve()
    if not (root / "seasons/s1").exists():
        return []
    rows = []
    names = {"difficulty2": "难度二", "difficult": "困难", "nightmare": "噩梦"}
    for mode in browse_modes("s1"):
        try:
            context = _load_native_browse_context(root, mode, cancelled=cancelled)
            profile = context.profiles[0]
            rows.append(dict(packageId=f"builtin-s1-{mode}", title=f"S1 · {names[mode]} · 内置原生底图",
                status="bundled", canRemove=False, builtin=True, season="s1", mode=mode,
                mapCount=profile.available_maps, layerCount=profile.available_layers))
        except CancelledError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            rows.append(dict(packageId=f"builtin-s1-{mode}", title=f"S1 · {names[mode]} · 内置原生底图",
                status="incomplete", canRemove=False, builtin=True, season="s1", mode=mode,
                mapCount=0, layerCount=0, error=f"{type(error).__name__}: {error}"))
    return rows


def _load_season_browse_context(root, mode, *, season, cancelled=lambda: False):
    # Imported here to keep the existing browse API and immutable value objects.
    from map_resource_context import BrowseContext, ProfileSummary
    root = Path(root).resolve()
    stamps = {}

    def check():
        if cancelled():
            raise CancelledError()

    def local(relative):
        check()
        if not isinstance(relative, str) or "\\" in relative:
            raise ValueError("offline asset path must be relative POSIX")
        rel = PurePosixPath(relative)
        if rel.is_absolute() or any(part in ("..", ".") for part in rel.parts) or ":" in relative:
            raise ValueError("offline asset path escapes resource root")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("offline asset path escapes resource root")
        return path

    def asset(row, *, image):
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("offline asset requires path and sha256")
        relative, digest = row["path"], row["sha256"]
        if not isinstance(relative, str) or not relative.startswith("assets/seasons/s1/"):
            raise ValueError("offline asset must belong to assets/seasons/s1")
        path = local(relative)
        if not path.is_relative_to(root / "assets/seasons/s1"):
            raise ValueError("offline asset resolved outside its S1 namespace")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("offline asset sha256 is invalid")
        if image and path.suffix.lower() not in {".png", ".webp"}:
            raise ValueError("offline preview must be PNG or WebP")
        if not image and path.suffix.lower() != ".npz":
            raise ValueError("offline structure resource must be NPZ")
        # Hash on the browse worker; do not trust the index as file availability.
        before = path.stat()
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                check()
                hasher.update(block)
        after = path.stat()
        if ((before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size)
                or hasher.hexdigest() != digest):
            raise ValueError(f"offline asset hash mismatch: {relative}")
        stamps[path] = (after.st_mtime_ns, after.st_size, digest)
        return path

    try:
        check()
        if season != "s1" or mode not in browse_modes(season):
            raise ValueError("unknown offline browse context")
        index = local(INDEX)
        try:
            (root / INDEX).lstat()
        except FileNotFoundError:
            return _load_native_browse_context(root, mode, cancelled=cancelled)
        raw = json.loads(index.read_text(encoding="utf-8"))
        if (raw.get("schema") != SCHEMA or raw.get("season") != season
                or raw.get("sourceVersion") != SOURCE_VERSION):
            raise ValueError("offline inventory season/schema/source version mismatch")
        rows = raw["maps"]
        if not isinstance(rows, list) or len(rows) != len(EXPECTED_LAYOUTS):
            raise ValueError("offline inventory must declare all 44 layouts")
        maps = {kind: [] for kind in PROFILE_NAMES}
        seen = set()
        for row in rows:
            check()
            mid, layout, group = row["mapId"], row["sourceLayout"], row["sourceGroup"]
            if (not isinstance(mid, str) or mid != layout or mid not in EXPECTED_LAYOUTS or mid in seen
                    or type(group) is not int or group not in GROUPS or not mid.startswith(str(group))
                    or (row["sourceDifficulty"], row["mode"]) != GROUPS[group]
                    or type(row["sourceDifficulty"]) is not int):
                raise ValueError("offline map identity/mode is not registered")
            seen.add(mid)
            name = row.get("name", mid)
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise ValueError("invalid offline map name")
            floors = row["floors"]
            if (not isinstance(floors, list) or len(floors) != 4
                    or any(type(f.get("sourceHeight")) is not int for f in floors)
                    or {f["sourceHeight"] for f in floors} != {1, 2, 3, 4}):
                raise ValueError("offline map must declare each source height 1..4 once")
            layers = {kind: {} for kind in PROFILE_NAMES}
            # Validate the complete declared inventory, then expose one mode.
            # Match the game's descending-height control order; B1 is the
            # useful full preview, while B4 can contain only the core room.
            for floor in sorted(floors, key=lambda f: f["sourceHeight"], reverse=True):
                key = f"source-height-{floor['sourceHeight']}"
                expected_floor = GAME_FLOORS[floor["sourceHeight"]]
                if (type(floor.get("gameFloor")) is not int or floor["gameFloor"] != expected_floor
                        or floor.get("label") != f"{expected_floor}层"):
                    raise ValueError("offline source-height/game-floor binding mismatch")
                for kind in PROFILE_NAMES:
                    layers[kind][key] = asset(floor[kind], image=True)
                if "structureNpz" in floor:
                    asset(floor["structureNpz"], image=False)
            if row["mode"] == mode:
                for kind in PROFILE_NAMES:
                    maps[kind].append(ResourceMap(mid, name,
                        (ResourceVariant(kind, PROFILE_NAMES[kind], layers[kind]),), structure=True))
        if seen != EXPECTED_LAYOUTS:
            raise ValueError("offline layout coverage mismatch")
        profiles = []
        for kind, name in PROFILE_NAMES.items():
            items = tuple(sorted(maps[kind], key=lambda item: item.id))
            first = next(((item.id, floor, path) for item in items
                          for variant in item.variants for floor, path in variant.layers.items()), None)
            profiles.append(ProfileSummary(f"s1-offline-{kind}", name, None,
                len(items), len(items), 0, 0, len(items) * 4, len(items) * 4, first, items))
        return BrowseContext(mode, root, None, None, None, tuple(profiles),
            MappingProxyType(stamps), warnings=LIVE_GAPS, season=season,
            floor_metadata=MappingProxyType({f"source-height-{height}": MappingProxyType(dict(
                sourceHeight=height, gameFloor=game_floor, label=f"{game_floor}层"))
                for height, game_floor in GAME_FLOORS.items()}))
    except CancelledError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return BrowseContext(mode, root, None, None, None, (), MappingProxyType({}),
            error=f"S1 资源待补齐 · offline-inventory-unavailable: {type(error).__name__}: {error}", season=season)


def load_season_browse_context(root, mode, *, season, cancelled=lambda: False):
    context=_load_season_browse_context(root,mode,season=season,cancelled=cancelled)
    if season=='s1' and not context.error:
        if cancelled():
            raise CancelledError()
        from s1_author_displays import append_browse_profile
        context=append_browse_profile(context)
        if cancelled():
            raise CancelledError()
    return context
