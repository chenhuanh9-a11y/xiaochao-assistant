"""Build the bundled S1 map landmarks (room names and minecart exits).

Source of truth is the Android client's ``custom-map-landmarks.json`` (generated
from the game's room definitions and ``Inter_MineCartOut`` models by
``mobile/scripts/build-custom-map-landmarks.js``).  Android stores each item in
its route-editor image pixels together with ``imageToNative``; this script maps
every item into the PC structure package's game-native canvas and binds each
floor to the package's shared-structure mask digest, so a later package whose
geometry differs simply shows no landmarks on that floor.

Display annotations only: nothing here changes navigation, routes or exports.

Usage::

    python tools/maps/build_s1_map_landmarks.py \
        --android-landmarks <identityv-map>/mobile/app/src/main/assets/custom-map-landmarks.json \
        --package <困难>.xingtu-map --package <噩梦>.xingtu-map [--check]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "app" / "s1_map_landmarks.json"
SCHEMA = "xingtu-s1-map-landmarks-v1"
ANDROID_SCHEMA = "identityv-custom-map-landmarks-v1"
FLOOR_IDS = {"b1": "-1", "b2": "-2", "b3": "-3"}
# Same lists as Android assets/custom-map-landmarks.js IMPORTANT_ROOMS.
IMPORTANT_ROOMS = {
    "-1": [],
    "-2": ["维修站", "水晶房", "竞速区", "锅炉房"],
    "-3": ["寰星宫", "无火的熔炼房", "维修站", "水晶房", "跳舞房", "冶炼屋", "冲压房"],
}
MODES = {"difficult": "106", "nightmare": "107"}


def _mask_sha256(mask) -> str:
    return hashlib.sha256(np.ascontiguousarray(np.asarray(mask) > 0, dtype=np.uint8).tobytes()).hexdigest()


def _read_package(path: Path):
    with zipfile.ZipFile(path) as archive:
        def doc(name):
            return json.loads(archive.read("data/base/" + name).decode("utf-8"))
        pack = doc("references/map-pack.json")
        display = doc("references/structure-display.json")
        mode = str(pack.get("mode") or "")
        if mode not in MODES or str(pack.get("season")) != "s1":
            raise ValueError(f"不是 S1 困难/噩梦结构包：{path.name}")
        floors = {}
        for entry in display["entries"]:
            key = (str(entry["mapId"]), str(entry["floor"]))
            with np.load(io.BytesIO(archive.read("data/base/" + entry["referenceResource"]))) as npz:
                mask = npz[entry.get("displayArray") or "shared_mask"]
            canvas = entry["canvas"]
            if [int(canvas["height"]), int(canvas["width"])] != list(mask.shape[:2]):
                raise ValueError(f"结构包画布尺寸与掩码不一致：{key}")
            floors[key] = dict(width=int(canvas["width"]), height=int(canvas["height"]),
                               maskSha256=_mask_sha256(mask),
                               structureVersionKey=str(entry.get("structureVersionKey") or ""))
        return mode, str(pack["sourceVersion"]), [str(m["id"]) for m in pack["maps"]], floors


def build(android_path: Path, packages: list[Path]) -> dict:
    raw = android_path.read_bytes()
    android = json.loads(raw.decode("utf-8"))
    if android.get("schema") != ANDROID_SCHEMA or not isinstance(android.get("templates"), dict):
        raise ValueError("安卓地图标注格式无效")
    source_version = str(android["sourceVersion"])
    read = {}
    for path in packages:
        mode, version, maps, floors = _read_package(path)
        if version != source_version:
            raise ValueError(f"结构包版本 {version} 与安卓标注版本 {source_version} 不一致：{path.name}")
        read[mode] = (maps, floors)
    if set(read) != set(MODES):
        raise ValueError("需要同时提供 S1 困难与噩梦结构包")
    result = dict(schema=SCHEMA, sourceVersion=source_version,
                  androidSourceSha256=hashlib.sha256(raw).hexdigest(),
                  importantRooms=IMPORTANT_ROOMS, modes={})
    for mode, prefix in MODES.items():
        maps, floors = read[mode]
        rows = result["modes"][mode] = {}
        for mid in maps:
            template = android["templates"].get(f"season-s1-{mid}")
            if template is None:
                raise ValueError(f"安卓标注缺少地图 {mid}")
            if not mid.startswith(prefix):
                raise ValueError(f"地图 {mid} 不属于 {mode}")
            out = rows[mid] = {}
            for android_floor, row in sorted(template.items()):
                fid = FLOOR_IDS[android_floor]
                target = floors.get((mid, fid))
                if target is None:
                    raise ValueError(f"结构包缺少 {mid} {fid} 楼层")
                a, b, c, d, e, f = (float(v) for v in row["imageToNative"])
                items = []
                for item in row["items"]:
                    if item["kind"] not in ("room", "minecart") or not str(item.get("label") or "").strip():
                        raise ValueError(f"标注条目无效：{mid} {fid} {item.get('id')}")
                    x, y = float(item["x"]), float(item["y"])
                    nx, ny = a * x + b * y + c, d * x + e * y + f
                    if not (0 <= nx < target["width"] and 0 <= ny < target["height"]):
                        raise ValueError(f"标注不在原生画布内：{mid} {fid} {item['id']}")
                    items.append(dict(id=str(item["id"]), kind=item["kind"], label=str(item["label"]),
                                      x=round(nx, 2), y=round(ny, 2)))
                out[fid] = dict(width=target["width"], height=target["height"],
                                maskSha256=target["maskSha256"],
                                androidLayerMaskSha256=str(row["layerMaskSha256"]),
                                structureVersionKey=target["structureVersionKey"], items=items)
        extra = sorted(k for k in android["templates"]
                       if k.startswith("season-s1-" + prefix) and k[len("season-s1-"):] not in rows)
        if extra:
            raise ValueError(f"安卓标注里有结构包没有的地图：{extra}")
    return result


def summary(data: dict) -> dict:
    out = {}
    for mode, maps in data["modes"].items():
        floors = [f for row in maps.values() for f in row.values()]
        out[mode] = dict(maps=len(maps), floors=len(floors),
                         rooms=sum(i["kind"] == "room" for f in floors for i in f["items"]),
                         minecarts=sum(i["kind"] == "minecart" for f in floors for i in f["items"]))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--android-landmarks", required=True, type=Path)
    parser.add_argument("--package", action="append", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="只校验已生成文件是否最新")
    args = parser.parse_args(argv)
    data = build(args.android_landmarks, args.package)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != text:
            print("地图标注需要重新生成", file=sys.stderr)
            return 1
    else:
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps(dict(ok=True, output=str(args.output), **summary(data)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
