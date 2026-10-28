"""Build a development executable skeleton; never collect private resources."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Print the command without building.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if sys.platform != 'win32':
        parser.error('This application uses Windows APIs; build it on Windows.')
    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', str(root / 'dist'), '--workpath', str(root / 'build' / 'pyinstaller'),
        str(root / 'tools' / 'xiaochao-assistant.spec'),
    ]
    if args.dry_run:
        print(subprocess.list2cmdline(command))
        return
    subprocess.run(command, cwd=root, check=True)
    print('Development skeleton built. Map recognition requires separate authorized resources.')


if __name__ == '__main__':
    main()
