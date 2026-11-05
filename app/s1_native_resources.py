"""S1 native resource adapter, using the existing shared structure contracts.

This module declares data and validates sources. It contains no solver,
recognition score, pixel threshold, author fallback or S0 source discovery.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from season_map_resources import EXPECTED_LAYOUTS, GROUPS, SOURCE_VERSION

S1_MODES = ("difficulty2", "difficult", "nightmare")
S1_FLOORS = ("-1", "-2", "-3", "-4")
MODE_LAYOUTS = {mode: frozenset(mid for mid in EXPECTED_LAYOUTS if int(mid[:3]) == group)
                for group, (_difficulty, mode) in GROUPS.items()}
MANIFEST = "native-map-manifest.json"
REQUIRED_INTERFACES = frozenset(("identityPack", "typedIndex", "versionBindings", "coordinatePack",
    "neutralDisplay", "identityEntries", "authorCatalog"))


def local(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("S1 resource path must be local to its exact mode root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("S1 resource escapes its exact mode root")
    return path


def read(root, relative):
    data = json.loads(local(root, relative).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("S1 resource declaration must be an object")
    return data


def registration(root, mode):
    """Small explicit declarations must validate before a cached engine is used."""
    root = Path(root).resolve()
    if mode not in S1_MODES:
        raise ValueError("S1 mode not declared")
    raw = read(root, MANIFEST)
    if ((raw.get("schemaVersion"), raw.get("season"), raw.get("mode"), raw.get("sourceVersion"))
            != (1, "s1", mode, SOURCE_VERSION)):
        raise ValueError("S1 native manifest context/source mismatch")
    interfaces = raw.get("runtimeInterfaces")
    if not isinstance(interfaces, dict) or not REQUIRED_INTERFACES <= interfaces.keys():
        raise ValueError("S1 native interfaces incomplete")
    for name in REQUIRED_INTERFACES:
        local(root, interfaces[name])
    pack = read(root, interfaces["identityPack"])
    rows = pack.get("maps") or []
    if ((pack.get("schemaVersion"), pack.get("season"), pack.get("mode")) != (1, "s1", mode)
            or pack.get("floors") != list(S1_FLOORS) or pack.get("defaultEntryFloor") != -1
            or len(rows) != len(MODE_LAYOUTS[mode]) or {row.get("id") for row in rows} != MODE_LAYOUTS[mode]):
        raise ValueError("S1 pack identity/floor scope mismatch")
    for row in rows:
        if (row.get("sourceLayout") != row["id"] or row.get("sourceVersion") != SOURCE_VERSION
                or row.get("identityProfileId") != "shared-structure"
                or set(row.get("layers", {})) != set(S1_FLOORS)):
            raise ValueError("S1 map source/layer binding mismatch")
        for relative in row["layers"].values():
            local(root, relative)
    capabilities = raw.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("sideEntries") is not False:
        raise ValueError("S1 has no declared semantic side exits")
    return raw


class S1NativeContext:
    """One mode's exact native canvases; no legacy author/coordinate bridge."""
    legacy_bindings = {}
    unbound_layers = ()

    def __init__(self, root, mode):
        import cv2
        from shared_structure_reference import SharedStructureResourceStore
        from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
        from author_route_catalog import AuthorRouteCatalog
        self.root = Path(root).resolve()
        self.manifest = registration(self.root, mode)
        self.mode = mode
        if self.manifest.get("opencvVersion") != cv2.__version__:
            raise ValueError(f"S1 OpenCV runtime mismatch: required={self.manifest.get('opencvVersion')} actual={cv2.__version__}")
        self.interfaces = self.manifest["runtimeInterfaces"]
        inventory = self.manifest.get("files")
        if not isinstance(inventory, list) or not inventory:
            raise ValueError("S1 native file/hash inventory missing")
        declared = set()
        for row in inventory:
            path = self.local(row["path"])
            if path in declared:
                raise ValueError("duplicate S1 native file")
            declared.add(path)
            if (not path.is_file() or path.stat().st_size != row["bytes"]
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]):
                raise ValueError(f"S1 native file hash mismatch: {row['path']}")
        required = {self.local(self.interfaces[name]) for name in REQUIRED_INTERFACES if name != "identityEntries"}
        entry_root = self.local(self.interfaces["identityEntries"])
        required.update(entry_root / name for name in ("entries.json", "side-entries.json", "entry-passports.json"))
        if not required <= declared:
            raise ValueError("S1 native required file missing from hash inventory")
        self.resources = SharedStructureResourceStore(self.local(self.interfaces["typedIndex"]), self.root)
        if self.resources.error:
            raise ValueError(self.resources.error)
        if read(self.root, self.interfaces["typedIndex"]).get("structureVersionBindings") != self.interfaces["versionBindings"]:
            raise ValueError("S1 typed version registry mismatch")
        for row in self.resources.entries.values():
            if self.local(row["resourcePath"]) not in declared:
                raise ValueError("S1 typed product missing from hash inventory")
        self.adapters = AuthorCoordinateAdapters(self.local(self.interfaces["coordinatePack"]))
        self.catalog = AuthorRouteCatalog(self.local(self.interfaces["authorCatalog"]), asset_base=self.root)
        if self.catalog.profiles:
            raise ValueError("S1 author catalogs require independent registration")
        self.neutral_relative = self.interfaces["neutralDisplay"]
        pack = read(self.root, self.interfaces["identityPack"])
        self.version_pack = SimpleNamespace(by_id={row["id"]: row for row in pack["maps"]},
            floors=S1_FLOORS, identity_profiles=pack["identityProfiles"],
            manifest_path=self.local(self.interfaces["identityPack"]))
        self.live_gaps = []
        if self.manifest["capabilities"].get("floorControls") is not True:
            self.live_gaps.append("s1-floor-controls-unregistered")
        # A flag alone never registers live controls. The detector contract is
        # a separate, source/observation-validated consumer implemented later.
        if not self.live_gaps:
            self.live_gaps.append("s1-floor-control-detector-unvalidated")

    def local(self, relative):
        return local(self.root, relative)

    def validate_identity_pack(self, pack):
        expected = {(key[1], key[3]): self.local(row["resourcePath"]) for key, row in self.resources.entries.items()}
        actual = {(item.id, floor): path.resolve() for item in pack.maps for floor, path in item.layers.items()}
        if (actual != expected or set(pack.by_id) != MODE_LAYOUTS[self.mode]
                or set(expected) != {(mid, floor) for mid in MODE_LAYOUTS[self.mode] for floor in S1_FLOORS}
                or pack.identity_profiles != {mid: "shared-structure" for mid in MODE_LAYOUTS[self.mode]}):
            raise ValueError("S1 typed/pack candidate scope differs")

    def validate_versions(self, versions):
        if set(versions) != set(self.resources.entries) or any(row.get("sharedError") for row in versions.values()):
            raise ValueError("S1 shared version/canvas contracts unavailable")

    def legacy_reference(self, map_id, floor):
        return None

    def attach_legacy_entry_metadata(self, locator):
        return None

    def has_reference_binding(self, map_id, floor):
        return ("shared-structure", str(map_id), "variant-01", str(floor)) in self.resources.entries
