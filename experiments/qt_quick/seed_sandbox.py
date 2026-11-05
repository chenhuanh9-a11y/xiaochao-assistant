"""Copy browsable resources into this experiment; never writes to source."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

from s1_private_resources import validated_s1_resources, _no_links

def seed(source: Path, target: Path):
    from client_paths import WORK_ROOT
    from packaged_product_identity import PRIVATE_S1_INSTANCES
    _no_links(source.absolute())
    _no_links(target.absolute())
    source, target = source.resolve(), target.resolve()
    if not target.is_relative_to(WORK_ROOT) or target.exists():
        raise ValueError("target must be a NEW directory beneath clone/work")
    if not (source / "_internal/references").is_dir():
        raise ValueError("source must contain _internal/references")
    profile_path = source / "_internal/installation-profile.json"
    _no_links(profile_path)
    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else {}
    native = validated_s1_resources(source / "_internal",
        required=profile.get("installerInstance", {}).get("id") in PRIVATE_S1_INSTANCES)
    # Scan before creating any target; copytree must never follow source links.
    for name in ("assets", "references", "recognition"):
        tree = source / "_internal" / name
        _no_links(tree)
        for path in tree.rglob("*"):
            _no_links(path)
    for relative in ("_internal/difficult-native-manifest.json", "_internal/version.json",
                     "version.json", "installation-profile.json"):
        _no_links(source / relative)
    target.mkdir(parents=True)
    (target / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
    for name in ("assets", "references", "recognition"):
        if (source / "_internal" / name).is_dir():
            shutil.copytree(source / "_internal" / name, target / "_internal" / name)
    for name in ("difficult-native-manifest.json", "version.json"):
        if (source / "_internal" / name).is_file():
            shutil.copy2(source / "_internal" / name, target / "_internal" / name)
    for name in ("version.json", "installation-profile.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    for name, row in native["files"].items():
        source_path = source / "_internal" / name
        destination = target / "_internal" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        if (destination.stat().st_size != row["bytes"]
                or hashlib.sha256(destination.read_bytes()).hexdigest() != row["sha256"]):
            raise ValueError("S1 resource changed during seed copy: " + name)
    print(target)

if __name__ == "__main__":
    from client_paths import RESOURCE_ROOT as ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--target", type=Path, default=ROOT / "work/qt-lab-installation")
    args = parser.parse_args()
    seed(args.source, args.target)
