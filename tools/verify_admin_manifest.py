"""Fail a Windows main-executable build unless its embedded manifest requests UAC."""
from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def verify_manifest_xml(xml: bytes) -> None:
    root = ET.fromstring(xml)
    levels = root.findall('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
    if len(levels) != 1 or levels[0].attrib != {
        'level': 'requireAdministrator', 'uiAccess': 'false',
    }:
        raise ValueError('Main executable must request requireAdministrator with uiAccess=false')


def verify_administrator_manifest(executable: str | Path) -> int:
    """Read RT_MANIFEST bytes without loading or running the executable."""
    import pefile  # A PyInstaller Windows build dependency, not an app dependency.

    count = 0
    with pefile.PE(str(executable)) as pe:
        resources = getattr(pe, 'DIRECTORY_ENTRY_RESOURCE', None)
        for resource in resources.entries if resources else ():
            if resource.id != 24:  # RT_MANIFEST
                continue
            for entry in resource.directory.entries:
                if entry.id != 1:  # CREATEPROCESS_MANIFEST_RESOURCE_ID
                    continue
                for language in entry.directory.entries:
                    data = language.data.struct
                    verify_manifest_xml(pe.get_data(data.OffsetToData, data.Size))
                    count += 1
    if not count:
        raise ValueError('Main executable has no embedded process manifest (RT_MANIFEST #1)')
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    count = verify_administrator_manifest(args.executable)
    print(f'PASS: {args.executable}: {count} embedded administrator manifest(s)')


if __name__ == '__main__':
    main()
