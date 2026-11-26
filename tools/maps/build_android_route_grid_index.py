"""Refresh the S1 rows of ``app/android_route_grid_index.py`` from Android.

The S1 rows mirror the Android season route editor exactly: one row per
``season-route-editor/catalog.json`` template floor, taken from that floor's
navigation file (cols, step, width, height). Maps that Android no longer lists
(retired layouts) are dropped; same-ID layout revisions take the new sizes.
S0 rows are left untouched.

Usage::

    python tools/maps/build_android_route_grid_index.py \
        --android-assets <identityv-map>/mobile/app/src/main/assets [--check]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "app" / "android_route_grid_index.py"
S1_PREFIXES = ("106", "107")


def android_s1_grids(assets: Path) -> tuple[str, dict[tuple[str, str], tuple[int, int, int, int]]]:
    catalog = json.loads((assets / "season-route-editor" / "catalog.json").read_text(encoding="utf-8"))
    grids = {}
    for template in catalog["templates"]:
        mid = str(template["mapId"])
        if not mid.startswith(S1_PREFIXES) or template["id"] != f"season-s1-{mid}":
            raise ValueError(f"安卓模板编号异常：{template.get('id')}")
        for floor in template["floors"]:
            raw = (assets / floor["navigation"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != floor["navigationSha256"]:
                raise ValueError(f"安卓道路文件校验失败：{floor['navigation']}")
            nav = json.loads(raw.decode("utf-8"))
            row = (int(nav["cols"]), int(nav["step"]), int(nav["width"]), int(nav["height"]))
            if (row[2], row[3]) != (int(floor["width"]), int(floor["height"])) or row[0] * row[1] < row[2]:
                raise ValueError(f"安卓道路网格尺寸不一致：{mid} {floor['gameFloor']}")
            grids[(mid, str(int(floor["gameFloor"])))] = row
    return str(catalog["sourceVersion"]), grids


def render(grids: dict[tuple[str, str], tuple[int, int, int, int]]) -> str:
    namespace: dict = {}
    source = TARGET.read_text(encoding="utf-8")
    exec(compile(source, str(TARGET), "exec"), namespace)
    s0_template = {k: v for k, v in namespace["GRID_BY_TEMPLATE"].items() if not k[0].startswith("season-s1-")}
    s0_map = {k: v for k, v in namespace["GRID_BY_MAP"].items() if not k[0].startswith(S1_PREFIXES)}
    by_template = {**s0_template, **{(f"season-s1-{m}", f): v for (m, f), v in sorted(grids.items())}}
    by_map = {**s0_map, **dict(sorted(grids.items()))}
    lines = source.split("\n")
    for index, line in enumerate(lines):
        if line.startswith("GRID_BY_TEMPLATE:"):
            lines[index] = "GRID_BY_TEMPLATE: dict[tuple[str, str], tuple[int, int, int, int]] = " + repr(by_template)
        elif line.startswith("GRID_BY_MAP:"):
            lines[index] = "GRID_BY_MAP: dict[tuple[str, str], tuple[int, int, int, int]] = " + repr(by_map)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--android-assets", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="只校验网格表是否与安卓一致")
    args = parser.parse_args(argv)
    version, grids = android_s1_grids(args.android_assets)
    text = render(grids)
    if args.check:
        if TARGET.read_text(encoding="utf-8") != text:
            print("安卓路线网格表需要重新生成", file=sys.stderr)
            return 1
    else:
        TARGET.write_text(text, encoding="utf-8")
    counts = {mode: len({m for m, _ in grids if m.startswith(prefix)}) for mode, prefix in (("difficult", "106"), ("nightmare", "107"))}
    print(json.dumps(dict(ok=True, sourceVersion=version, floors=len(grids), maps=counts), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
