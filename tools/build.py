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
        '--onedir', '--windowed', '--name', 'xiaochao-assistant',
        '--distpath', str(root / 'dist'), '--workpath', str(root / 'build' / 'pyinstaller'),
        '--specpath', str(root / 'build'), '--paths', str(root / 'app'),
        '--manifest', str(root / 'tools' / 'identityv-assistant.manifest'),
        '--add-data', f'{root / "version.json"};.',
    ]
    for name in ('THIRD_PARTY_LUCIDE_LICENSE.txt', 'THIRD_PARTY_FLUENT_LICENSE.txt', 'THIRD_PARTY_NOTICES.md'):
        command.extend(['--add-data', f'{root / name};.'])
    command.append(str(root / 'app' / 'identityv_daily_assistant.py'))
    if args.dry_run:
        print(subprocess.list2cmdline(command))
        return
    subprocess.run(command, cwd=root, check=True)
    print('Development skeleton built. Map recognition requires separate authorized resources.')


if __name__ == '__main__':
    main()
