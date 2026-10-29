"""Read-only structure preview bindings; no solver, sensor, or runtime writes."""
from dataclasses import dataclass
import json
from pathlib import Path
from zipfile import BadZipFile
import zlib

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
    native = getattr(pack, 'display_context', None)
    if native is not None:
        for item in pack.maps:
            floor = default if default in item.layers else next(iter(item.layers), None)
            entry = native.resources.entries.get(('shared-structure', item.id, 'variant-01', floor))
            array = 'shared_mask'
            if entry is None:
                entry = next((row for key, row in native.legacy_bindings.items()
                              if key[1] == item.id and key[3] == floor), None)
                array = 'clean_mask'
            if entry is not None:
                result.append(StructurePreview(item.id, item.name, floor,
                    native.local(entry['resourcePath']), array, str(entry.get('sourceVersion', ''))))
            if len(result) >= limit:
                break
    elif getattr(pack, "mode", "nightmare") == "difficult":
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
    try:
        with np.load(asset.path, allow_pickle=False) as product:
            mask = np.asarray(product[asset.array])
            if mask.ndim != 2 or mask.size == 0:
                raise ValueError("结构图数组无效")
            pixels = np.full((*mask.shape, 3), 241, dtype=np.uint8)
            pixels[mask > 0] = (57, 77, 103)
    except (BadZipFile, zlib.error, EOFError) as error:
        raise ValueError("结构图文件损坏") from error
    return ImageOps.contain(Image.fromarray(pixels), size, Image.Resampling.LANCZOS)


def author_resource_version(pack, catalog):
    native = getattr(pack, 'display_context', None)
    if native is not None:
        versions = sorted({str(row['sourceVersion']) for row in native.resources.entries.values()
                           if row.get('sourceVersion')})
        legacy = sorted({str(row['sourceVersion']) for row in native.legacy_bindings.values()
                         if row.get('sourceVersion')})
        parts = ([f"素材来源版本 {', '.join(versions)}"] if versions else [])
        if legacy:
            parts.append(f"历史结构兼容 {', '.join(legacy)}")
        return ' · '.join(parts) or '素材版本未标注'
    if getattr(pack, "mode", "nightmare") == "difficult":
        try:
            source = json.loads((Path(pack.source_root) / "manifest.json").read_text(encoding="utf-8"))
            return str(source.get("displayName") or "素材版本未标注")
        except (OSError, ValueError):
            return "素材版本未标注"
    raw = getattr(catalog, "_raw", {})
    version = raw.get("sourceVersion")
    return f"素材来源版本 {version}" if version else "素材版本未标注"
