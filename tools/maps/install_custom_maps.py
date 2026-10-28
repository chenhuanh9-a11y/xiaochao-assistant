"""Install a custom map package archive into the app's assets/custom-maps.

  python tools/maps/install_custom_maps.py --archive xingtu-custom-maps-VERSION.zip [--sha256 HEX] [--root APP_ROOT]

The archive comes from the repository Release. Every member path is checked
to stay inside the target, and every listed file is verified against the
manifest's SHA-256 values before the previous install is replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--sha256", help="expected archive SHA-256 from the Release SHA256SUMS file")
    parser.add_argument("--root", type=Path, default=ROOT, help="app root containing assets/ (default: repository)")
    args = parser.parse_args(argv)
    if args.sha256 and sha256(args.archive) != args.sha256.lower():
        parser.error("archive SHA-256 does not match")
    target = args.root / "assets" / "custom-maps"
    with tempfile.TemporaryDirectory(dir=args.root / "assets" if (args.root / "assets").is_dir() else None) as temp:
        staging = Path(temp) / "custom-maps"
        with zipfile.ZipFile(args.archive) as handle:
            for name in handle.namelist():
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or ":" in name:
                    parser.error(f"unsafe archive member: {name}")
            handle.extractall(staging)
        manifest = json.loads((staging / "manifest.json").read_text("utf-8"))
        if manifest.get("schema") != "xingtu-custom-maps-v1":
            parser.error("not a custom map package")
        checks = [("exit-icon.png", manifest["sources"]["exitIconSha256"])]
        for row in manifest["maps"]:
            for floor in row["floors"].values():
                checks += [(floor["image"], floor["imageSha256"]), (floor["topology"], floor["topologySha256"])]
        for relative, expected in checks:
            if sha256(staging / relative) != expected:
                parser.error(f"file does not match manifest: {relative}")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), target)
    print(json.dumps(dict(success=True, version=manifest["version"], maps=len(manifest["maps"]),
                          files=len(checks), target=str(target)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
