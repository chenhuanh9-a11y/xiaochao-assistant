"""Copy the declared Android editor assets, never its app/build tree.

Inputs stay read-only. Output must be a new directory; all copied bytes are
bound to a SHA-256 inventory. Artwork/navigation stay outside source control.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
from route_scopes import SCOPES


def import_resources(android_root, output):
    android_root, output = Path(android_root).resolve(), Path(output).absolute()
    if output.exists():
        raise ValueError("Output already exists; preserve the existing route resources")
    if output.is_relative_to(android_root):
        raise ValueError("The Android source must remain read-only")
    assets = android_root / "mobile/app/src/main/assets"
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=android_root, text=True).strip()
    files, counts = {}, {}
    artwork = "pcport/readable-artwork.json"
    files[artwork] = (assets / artwork).read_bytes()
    for scope in SCOPES.values():
        # Existing PC nightmare routes retain their native planner and storage.
        if scope.id == "legacy-nightmare":
            continue
        catalog_bytes = (assets / scope.catalog).read_bytes()
        catalog = json.loads(catalog_bytes)
        files[scope.catalog] = catalog_bytes
        rows = [t for t in catalog["templates"] if t.get("difficulty") == scope.mode]
        counts[scope.id] = len(rows)
        if not 1 <= len(rows) <= scope.max_maps:
            raise ValueError("Android route catalog count mismatch")
        for row in rows:
            for floor in row["floors"]:
                for key in ("image", "navigation"):
                    relative = scope.asset_prefix + floor[key]
                    path = assets / relative
                    if (Path(relative).is_absolute() or ".." in Path(relative).parts
                            or not path.resolve().is_relative_to(assets.resolve()) or path.is_symlink()):
                        raise ValueError("Unsafe Android resource path")
                    data = path.read_bytes()
                    expected = floor.get("sha256" if key == "image" else "navigationSha256")
                    if expected and hashlib.sha256(data).hexdigest() != expected:
                        raise ValueError("Android catalog checksum mismatch: " + relative)
                    files[relative] = data
    model = ROOT / "app/android_route_model.js"
    manifest = dict(schema="xingtu-android-route-resources-v1", sourceCommit=commit,
                    routeModelSha256=hashlib.sha256(model.read_bytes()).hexdigest(), counts=counts,
                    files={name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())})
    output.mkdir(parents=True)
    try:
        for name, data in files.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    except BaseException:
        shutil.rmtree(output)
        raise
    return dict(sourceCommit=commit, files=len(files), counts=counts,
                bytes=sum(len(data) for data in files.values()), output=str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(import_resources(args.android_root, args.output), ensure_ascii=False))
