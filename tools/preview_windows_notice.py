"""Credential-free, preview-only entry point with an installer build-record gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

import publish_windows_notice as publisher

ROOT = Path(__file__).resolve().parents[1]


def validate_build_record(installer: Path, record_path: Path) -> dict:
    """Bind bytes to a trusted build owner's record, never infer version from name."""
    try:
        version = json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))
        if record_path.stat().st_size > 1024 * 1024:
            raise ValueError('oversized build record')
        record = json.loads(record_path.read_text(encoding='utf-8-sig'))
        if not isinstance(record, dict) or record.get('schema') != 'xingtu-windows-build-record-v1':
            raise ValueError('invalid build-record schema')
        if (record.get('displayVersion') != version['displayVersion']
                or record.get('numericVersion') != version['numericVersion']):
            raise publisher.PublishError('安装包构建记录与当前显示/数字版本不符；不能给旧包改名冒充新版。')
        if not isinstance(record.get('sourceCommit'), str) or not re.fullmatch('[0-9a-f]{40}', record['sourceCommit']):
            raise ValueError('missing source commit')
        artifact = record['installer']
        if (not isinstance(artifact, dict) or artifact.get('fileName') != installer.name
                or type(artifact.get('size')) is not int
                or not isinstance(artifact.get('sha256'), str)
                or not re.fullmatch('[0-9a-fA-F]{64}', artifact['sha256'])):
            raise ValueError('invalid artifact record')
        actual = publisher.installer_info(installer, version['displayVersion'],
                                          publisher.server_version(version['displayVersion']))
        if actual['size'] != artifact['size'] or actual['sha256'] != artifact['sha256'].upper():
            raise publisher.PublishError('安装包内容与构建记录的大小/哈希不符，已停止。')
        return record
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise publisher.PublishError('缺少或无效的构建记录；请等待受控构建生成真实安装包及记录。') from error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='只预览 Windows 公告，不支持发布或口令配置')
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--build-record', required=True, type=Path)
    parser.add_argument('--notes', type=Path, default=ROOT / 'notes.txt')
    args = parser.parse_args(argv)
    try:
        record = validate_build_record(args.installer, args.build_record)
        print(f"安装包完整路径：{args.installer.resolve()}")
        print(f"构建显示版本：{record['displayVersion']}；数字版本：{record['numericVersion']}")
        print(f"构建源码提交：{record['sourceCommit']}")
        # The only supported invocation is the publisher's default GET-only preview.
        # No token/environment inspection and no forwarding arbitrary extra flags.
        return publisher.main(['--installer', str(args.installer), '--notes', str(args.notes)])
    except publisher.PublishError as error:
        print(f'错误：{error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
