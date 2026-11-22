"""Scaffolding for install size and cold-start notes.

On Linux the Windows self-contained publish and the shipping Setup are absent,
so both numbers stay unknown. This script does not build an installer and does
not start the game. It never reads other processes' window titles.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path


def directory_bytes(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        data = file.read_bytes()
        total += len(data)
        digest.update(str(file.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
    return total, digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="record publish size when a directory exists")
    parser.add_argument("--publish", default=os.environ.get("XINGTU_PUBLISH_DIR", ""))
    parser.add_argument("--setup", default=os.environ.get("XINGTU_PYTHON_SETUP", ""))
    args = parser.parse_args()
    print("platform linux-ci" if sys.platform != "win32" else "platform windows")
    print("coldStartMs unknown")
    print("visiblePixelMs unknown")
    print("note phase-1 has no Windows present-mon endpoint; do not treat a blank process start as the product cold start")
    for label, raw in (("csharpPublish", args.publish), ("pythonSetup", args.setup)):
        if not raw:
            print(f"{label}Bytes unknown")
            print(f"{label}Sha256 unknown")
            continue
        path = Path(raw)
        if path.is_file():
            data = path.read_bytes()
            print(f"{label}Bytes {len(data)}")
            print(f"{label}Sha256 {hashlib.sha256(data).hexdigest()}")
        elif path.is_dir():
            total, digest = directory_bytes(path)
            print(f"{label}Bytes {total}")
            print(f"{label}Sha256 {digest}")
        else:
            print(f"{label}Bytes unknown")
            print(f"{label}Sha256 unknown")
    print("windowsPublishCommand dotnet publish csharp/src/StarMap.App/StarMap.App.csproj -c Release -r win-x64 --self-contained true -p:PublishTrimmed=false -p:PublishSingleFile=false")
    print("installer not-built")
    return 0


if __name__ == "__main__":
    sys.exit(main())
