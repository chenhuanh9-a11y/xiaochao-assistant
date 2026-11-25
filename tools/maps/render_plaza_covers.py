"""Render public covers from a local export of approved plaza versions and packages."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "experiments/qt_quick")]

from PySide6.QtCore import QCoreApplication

from account_service import AccountError
from client_route_preview import _preview_library, render_public_cover
from route_plaza import CONTENT_KIND, MAX_ARCHIVE_BYTES, MAX_COVER_BYTES, cover_path, convert_manifest, read_manifest
from route_scopes import scope_for


def _package_manifest(directory, row):
    package = row["package"]
    path = (directory / row["archive"]).resolve()
    size = package["object_bytes"]
    if (not path.is_relative_to(directory) or type(size) is not int
            or not 0 < size <= MAX_ARCHIVE_BYTES or path.stat().st_size != size):
        raise ValueError("路线包路径或大小不符")
    with path.open("rb") as stream:
        archive = stream.read(size + 1)
    if len(archive) != size or hashlib.sha256(archive).hexdigest() != package["sha256"]:
        raise ValueError("路线包 SHA-256 不符")
    return read_manifest(archive, package, row)


def build_covers(snapshot, directory, output, legacy_editor, scoped_editor):
    """Publish only artwork and version/mode mappings into a fresh local output."""
    works = {row["id"]: row for row in snapshot["works"]
             if row.get("status") == "approved" and row.get("content_kind") == CONTENT_KIND}
    expected = {(vid, mode): work for vid, work in works.items() for mode in work.get("modes") or ["nightmare"]}
    packages = {}
    for row in snapshot["packages"]:
        key = row["version_id"], row["package"]["mode"]
        if key in packages:
            raise ValueError(f"路线包重复登记：{key[0]} / {key[1]}")
        packages[key] = row

    output.mkdir(parents=True, exist_ok=False)
    index, rendered, missing = {}, [], []
    libraries = {}
    for (version_id, mode), work in sorted(expected.items()):
        identity = dict(version_id=version_id, mode=mode)
        try:
            path = cover_path(version_id, mode)
            if not path:
                raise ValueError("作品版本 ID 或难度无效")
            row = packages.get((version_id, mode))
            if row is None:
                raise ValueError("缺少对应版本的离线路线包")
            scenario = work.get("scenario", "lady-of-misfortune")
            if (row.get("work_id"), row.get("content_kind"), row.get("scenario", "lady-of-misfortune")) != (
                    work["work_id"], CONTENT_KIND, scenario):
                raise ValueError("离线路线包与已上架记录不符")
            scope = scope_for(scenario, mode)
            manifest = _package_manifest(directory, row)
            if scope.id not in libraries:
                editor = legacy_editor if scope.id == "legacy-nightmare" else scoped_editor
                if editor is None:
                    raise ValueError(f"缺少 {scope.label} 编辑资源")
                libraries[scope.id] = _preview_library(scope.id, editor)
            library = libraries[scope.id]
            maps, report = convert_manifest(library, manifest)
            pixels, map_id, floor = render_public_cover(library, maps, report)
            if len(pixels) > MAX_COVER_BYTES:
                raise ValueError("封面超过字节数上限")
        except (AccountError, OSError, ValueError, KeyError, TypeError) as failure:
            missing.append(dict(identity, reason=str(failure)))
            continue
        destination = output / path.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(pixels)
        index.setdefault(version_id, {})[mode] = path
        rendered.append(dict(identity, map_id=map_id, floor=floor, bytes=len(pixels)))

    result = dict(works=len(works), variants=len(expected), rendered=rendered, missing=missing,
                  cover_urls=index)
    (output / "cover-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-editor", type=Path)
    parser.add_argument("--scoped-editor", type=Path)
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])  # Keep Qt's JS planner alive for the batch.
    snapshot = args.snapshot.resolve()
    result = build_covers(json.loads(snapshot.read_text("utf-8-sig")), snapshot.parent, args.output,
                          args.legacy_editor, args.scoped_editor)
    print(f"{result['works']} works / {result['variants']} modes: "
          f"{len(result['rendered'])} rendered, {len(result['missing'])} missing")
    return 1 if not result["rendered"] or result["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
