"""Print package identities used for parity. Does not print filesystem paths.

Map/display packages are identified by packageId, byte size, and SHA-256 in
docs/resources-q90.lock.json. S1 production files are identified the same way
from tools/s1-production-release-lock.json. Passing a file hashes its bytes
and prints only the size and digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="identify map/resource packages by hash")
    parser.add_argument("--file", help="hash one file; the path is not printed")
    args = parser.parse_args()
    if args.file:
        data = Path(args.file).read_bytes()
        print(f"bytes {len(data)}")
        print(f"sha256 {digest_bytes(data)}")
        return 0

    q90 = json.loads((ROOT / "docs" / "resources-q90.lock.json").read_text(encoding="utf-8"))
    print(f"resourceSet {q90['resourceSet']}")
    print(f"lockSourceCodeBaseline {q90['sourceCodeBaseline']}")
    for package in q90["packages"]:
        print(f"package {package['packageId']} bytes {package['bytes']} sha256 {package['sha256']}")
    release = json.loads((ROOT / "tools" / "s1-production-release-lock.json").read_text(encoding="utf-8"))
    files = release.get("release", {}).get("files", {})
    print(f"s1ProductionFiles {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
