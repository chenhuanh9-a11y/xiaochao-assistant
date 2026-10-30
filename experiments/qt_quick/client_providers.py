"""Lazy, explicit-root production providers for saved-frame diagnostics.

No environment overrides, development roots, Tk assistant, captures or listeners.
``prepare`` and factory invocation belong on SessionService's worker.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import json
from pathlib import Path
import sys
import threading
from client_paths import APP_ROOT

APP = str(APP_ROOT)
if APP not in sys.path:
    sys.path.insert(0, APP)


class ProviderUnavailable(RuntimeError):
    unavailable = True


def _local(root, relative):
    path = (root / str(relative)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"resource escapes explicit root: {relative}")
    return path


def _read(root, relative):
    path = _local(root, relative)
    if not path.is_file():
        raise ProviderUnavailable(f"missing-resource:{relative}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"resource metadata must be an object: {relative}")
    return data


class SharedReferenceProvider:
    """Version-checked typed references; six-entry cache, no pose/UI state."""
    def __init__(self, root, resources, versions, *, native=None):
        self.root, self.resources, self.versions, self.native = root, resources, versions, native
        self._cache = OrderedDict()
        self._lock = threading.RLock()

    def __call__(self, profile, map_id, floor):
        import numpy as np
        if self.native is not None:
            legacy = self.native.legacy_reference(map_id, floor)
            if legacy is not None:
                return legacy
        keys = [key for key in self.versions if key[1] == str(map_id) and key[3] == str(floor)]
        if len(keys) != 1:
            return None
        key = keys[0]
        version = self.versions[key]
        if version.get("sharedError"):
            return None
        with self._lock:
            reference = self._cache.get(key)
            if reference is not None:
                self._cache.move_to_end(key)
                return reference
            reference = self.resources.load(key[0], key[1], key[3], variant=key[2])
            if (reference is None or not np.any(reference.clean_mask)
                    or reference.path.resolve() != version["path"]
                    or reference.metadata.get("canvas") != version["canvas"]
                    or any(reference.metadata.get(name) != value for name, value in version["expected"].items())):
                return None
            self._cache[key] = reference
            while len(self._cache) > 6:
                self._cache.popitem(last=False)
            return reference

    def binding_metadata(self, profile, map_id, floor):
        if self.native is not None:
            legacy = self.native.legacy_reference(map_id, floor)
            if legacy is not None:
                return dict(path=legacy.path, shape=list(legacy.shape), metadata=legacy.metadata)
        keys = [key for key in self.versions if key[1] == str(map_id) and key[3] == str(floor)]
        if len(keys) != 1:
            return None
        row = self.versions[keys[0]]
        if row.get("sharedError"):
            return None
        return dict(path=row["path"], shape=row["canvas"]["shapeHW"],
                    metadata=dict(row["expected"], binding=row["binding"]))


class EngineFactories(dict):
    """A mapping of lazy per-mode factories plus cached capability reports.

    Construction performs no resource IO. ``prepare`` validates/builds on the
    caller's worker. Until then modes report resource-validation-pending.
    """
    MODES = ("nightmare", "difficult")

    def __init__(self, resource_root):
        self.root = Path(resource_root).resolve()
        self._lock = threading.RLock()
        self._build_locks = {mode: threading.Lock() for mode in self.MODES}
        self._engines = {}
        self._revision = 0
        self._reports = {mode: dict(available=False, prepared=False,
                                   reason="resource-validation-pending") for mode in self.MODES}
        super().__init__({mode: lambda mode=mode: self._get(mode) for mode in self.MODES})

    def capabilities(self):
        with self._lock:
            return copy.deepcopy(self._reports)

    def prepare(self, cancelled=None):
        for mode in self.MODES:
            if cancelled and (cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled()):
                break
            try:
                self._get(mode)
            except ProviderUnavailable:
                pass
        return self.capabilities()

    def invalidate(self):
        """Nonblocking resource-lifetime boundary; old worker builds cannot publish."""
        with self._lock:
            self._revision += 1
            self._engines.clear()
            self._reports = {mode: dict(available=False, prepared=False,
                                       reason="resource-validation-pending") for mode in self.MODES}

    def _get(self, mode):
        with self._build_locks[mode]:
            with self._lock:
                if mode in self._engines:
                    return self._engines[mode]
                if self._reports[mode]["prepared"]:
                    raise ProviderUnavailable(self._reports[mode]["reason"])
                revision = self._revision
            try:
                engine = self._build(mode)
            except Exception as error:
                reason = f"{mode}:{type(error).__name__}:{error}"
                with self._lock:
                    if revision == self._revision:
                        self._reports[mode] = dict(available=False, prepared=True, reason=reason)
                raise ProviderUnavailable(reason) from error
            with self._lock:
                if revision != self._revision:
                    raise ProviderUnavailable("resource-context-superseded")
                self._engines[mode] = engine
                self._reports[mode] = dict(available=True, prepared=True, reason="saved-frame-rank-ready",
                                       mapCount=len(engine.pack.maps), floors=list(engine.identity_floors),
                                       liveAuthorization=False, resourceRoot=str(self.root),
                                       inventory=getattr(engine, "qt_resource_inventory", {}))
            return engine

    def _build(self, mode):
        # All heavyweight production imports and resource parsing are worker-only.
        from author_route_catalog import AuthorRouteCatalog
        from map_identity_engine import MapIdentityEngine
        from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
        from nightmare_candidate import NightmarePack, NightmareMemoryStore
        from shared_structure_reference import SharedStructureResourceStore
        from shared_structure_context import load_shared_version_context

        root = self.root
        native = None
        if mode == "nightmare":
            raw = _read(root, "references/nightmare-map-pack.json")
            # NightmarePack supports developer/environment source roots. Reject
            # those manifests before constructing it, keeping every path local.
            if not raw.get("maps") or any(row.get("resourceBase") != "resource-root" for row in raw["maps"]):
                raise ValueError("nightmare manifest must bind resource-root for every map")
            for row in raw["maps"]:
                for relative in [row["oneFlow"], *row["layers"].values()]:
                    _local(root, relative)
            pack = NightmarePack(_local(root, "references/nightmare-map-pack.json"))
            resources = SharedStructureResourceStore(_local(root, "references/nightmare-shared-structure-index.json"), root)
            if resources.error:
                raise ValueError(resources.error)
            adapters = AuthorCoordinateAdapters(_local(root, "references/nightmare-canonical-coordinate-pack.json"))
            catalog = None
            # Author display is optional and never recognition evidence.
            catalog_path = _local(root, "references/nightmare-author-route-catalog.json")
            if catalog_path.is_file():
                catalog = AuthorRouteCatalog(catalog_path, asset_base=root)
            entry_root = _local(root, "references/nightmare-entries")
            neutral = "references/nightmare-structure-display.json"
            entry_relative = "references/nightmare-entries"
            profile, exit_icon = "zhan10-speed", _local(root, "references/difficult-exit-icon.json")
            version_pack = pack
            engine_type = MapIdentityEngine
        else:
            interfaces = _read(root, "difficult-native-manifest.json").get("runtimeInterfaces") or {}
            if "identityPack" not in interfaces:
                raise ProviderUnavailable("unsupported-contract:difficult native identityPack not declared")
            # Validate every runtime path before the existing context touches it.
            for name, value in interfaces.items():
                if isinstance(value, str):
                    _local(root, value)
            from difficult_native_context import DifficultNativeContext
            from difficult_native_identity import NativeDifficultPack, NativeDifficultCandidateEngine
            native = DifficultNativeContext(root)
            pack = NativeDifficultPack(root, interfaces["identityPack"], display_context=native)
            native.validate_identity_pack(pack)
            resources, adapters, catalog = native.resources, native.adapters, native.catalog
            entry_root = native.local(interfaces["identityEntries"])
            neutral, entry_relative = native.neutral_relative, ""
            profile, exit_icon = "shared-structure", native.local(interfaces["exitIcon"])
            version_pack = native.version_pack
            engine_type = NativeDifficultCandidateEngine
        for name in ("entries.json", "side-entries.json"):
            _read(root, entry_root.relative_to(root) / name)
        if not exit_icon.is_file():
            raise ProviderUnavailable(f"missing-resource:{exit_icon.relative_to(root)}")
        # Explicit, unused memory location; constructor performs no IO and rank
        # has no feedback/learning side effects. Never bind the user's history.
        memory = NightmareMemoryStore(pack, root / ".qt-saved-frame-memory" / mode,
                                      portable_seed_path=root / ".qt-no-portable-seeds.json")
        provider = SharedReferenceProvider(root, resources, {}, native=native)
        engine = engine_type(pack, identity_root=entry_root, identity_profile_id=profile,
            identity_floors=pack.floors, exit_icon_path=exit_icon,
            entry_structure_store=resources, entry_shared_reference_provider=provider,
            shared_reference_loader=provider, entry_context_is_current=lambda: True,
            memory_store=memory, native_similarity_root=root,
            native_similarity_binding_provider=provider.binding_metadata,
            structure_profile_provider=lambda: "shared-structure",
            structure_cache_context_provider=lambda: "")
        if engine.entry_identity.error:
            raise ValueError(f"entry-index-unavailable:{engine.entry_identity.error}")
        if engine.side_entry_identity is None:
            raise ValueError(f"side-entry-index-unavailable:{engine.side_entry_error}")
        if native is not None:
            native.attach_legacy_entry_metadata(engine.side_entry_identity)
        versions = load_shared_version_context(root, version_pack, resources, adapters, catalog,
            engine.side_entry_identity.entries, neutral_relative=neutral,
            author_profile_id="suju" if mode == "nightmare" else "zhan10-speed",
            entry_root_relative=entry_relative)
        failures = [f"{key[1]}/{key[3]}:{row['sharedError']}" for key, row in versions.items() if row.get("sharedError")]
        if not versions or failures:
            raise ValueError("shared-resource-contract:" + ";".join(failures[:3] or ["empty-version-inventory"]))
        if native is not None:
            native.validate_versions(versions)
        provider.versions = versions
        # Decode each typed product through its original loader, one at a time;
        # the bounded provider cache prevents retaining an entire mask atlas.
        for key in versions:
            if provider(key[0], key[1], key[3]) is None:
                raise ValueError(f"typed-product-unavailable:{key[1]}/{key[3]}")
        similarity = engine.native_similarity_index
        if similarity.error:
            raise ValueError(f"native-similarity-unavailable:{similarity.error}")
        engine.qt_resource_inventory = dict(typedLayers=len(versions),
            mainEntries=len(engine.entry_identity.entries), sideEntries=len(engine.side_entry_identity.entries),
            declaredMissingNativeLayers=(native.unbound_layers if native is not None else []),
            optionalSideDoorError=engine.side_entry_identity.side_door_error,
            optionalSideSearchError=engine.side_entry_identity.side_search_error)
        engine.qt_shared_provider = provider
        engine.qt_resource_root = root
        engine.qt_adapters = adapters
        engine.qt_catalog = catalog
        engine.qt_native_context = native
        from optional_author_displays import OptionalAuthorDisplays
        optional = OptionalAuthorDisplays(root, pack, catalog, adapters, versions, mode=mode)
        engine.qt_optional_displays = optional
        engine.qt_adapters, engine.qt_catalog = optional.adapters, optional.catalog
        if mode == "difficult":
            from difficult_map_ui import DifficultMapUI
            engine.qt_map_ui = DifficultMapUI(native.local(native.interfaces["uiControls"]))
        return engine


def build_engine_factories(resource_root):
    """Pass the exact sandbox ``_internal`` path; no parent/root discovery."""
    return EngineFactories(resource_root)
