"""Copy browsable resources into this experiment; never writes to source."""
from pathlib import Path
import argparse
import shutil

from client_paths import RESOURCE_ROOT as ROOT, WORK_ROOT

def seed(source: Path, target: Path):
    source, target = source.resolve(), target.resolve()
    if not target.is_relative_to(WORK_ROOT) or target.exists():
        raise ValueError("target must be a NEW directory beneath clone/work")
    if not (source / "_internal/references").is_dir():
        raise ValueError("source must contain _internal/references")
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
    print(target)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--target", type=Path, default=ROOT / "work/qt-lab-installation")
    args = parser.parse_args()
    seed(args.source, args.target)
