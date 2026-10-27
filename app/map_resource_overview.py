"""Read-only structure preview bindings; no solver, sensor, or runtime writes."""
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


@dataclass(frozen=True)
class StructurePreview:
    map_id: str
    name: str
    floor: str
    path: Path
    array: str
    source_version: str = ""


def structure_previews(pack, limit=3):
    """Choose registered current-mode products, never an author image fallback."""
    result = []
    default = str(pack.default_entry_floor)
    if getattr(pack, "mode", "nightmare") == "difficult":
        entries = getattr(pack, "tilemap_entries", {})
        for item in pack.maps:
            floors = [floor for map_id, floor in entries if map_id == item.id]
            floor = default if default in floors else next(iter(floors), None)
            entry = entries.get((item.id, floor))
            if entry:
                result.append(StructurePreview(item.id, item.name, floor,
                    Path(pack.tilemap_root) / entry["resourcePath"], "clean_mask",
                    str(getattr(pack, "pack_id", ""))))
            if len(result) >= limit:
                break
    else:
        root = Path(pack.resource_root)
        try:
            metadata = json.loads((root / "references/nightmare-structure-display.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        entries = {(str(row["mapId"]), str(row["floor"])): row
                   for row in metadata.get("entries", [])}
        for item in pack.maps:
            floors = [floor for map_id, floor in entries if map_id == item.id]
            floor = default if default in floors else next(iter(floors), None)
            entry = entries.get((item.id, floor))
            if entry:
                result.append(StructurePreview(item.id, item.name, floor,
                    root / entry["referenceResource"], str(entry["displayArray"]),
                    str(entry.get("sourceVersion", ""))))
            if len(result) >= limit:
                break
    return tuple(result)


def load_structure_preview(asset, size):
    """Render only the manifest-declared structure array; preserve the full canvas."""
    with np.load(asset.path, allow_pickle=False) as product:
        mask = np.asarray(product[asset.array])
        if mask.ndim != 2 or mask.size == 0:
            raise ValueError("结构图数组无效")
        pixels = np.full((*mask.shape, 3), 241, dtype=np.uint8)
        pixels[mask > 0] = (57, 77, 103)
    return ImageOps.contain(Image.fromarray(pixels), size, Image.Resampling.LANCZOS)


def author_resource_version(pack, catalog):
    if getattr(pack, "mode", "nightmare") == "difficult":
        try:
            source = json.loads((Path(pack.source_root) / "manifest.json").read_text(encoding="utf-8"))
            return str(source.get("displayName") or "素材版本未标注")
        except (OSError, ValueError):
            return "素材版本未标注"
    raw = getattr(catalog, "_raw", {})
    version = raw.get("sourceVersion")
    return f"素材来源版本 {version}" if version else "素材版本未标注"
