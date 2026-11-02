"""Credential-free, preview-only entry point with an installer build-record gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import publish_windows_notice as publisher

ROOT = Path(__file__).resolve().parents[1]


def _source_metadata(commit: str) -> tuple[str, dict]:
    """Read the frozen source, not the release-tool checkout's HEAD."""
    def git(*args):
        return subprocess.run(['git', '-C', str(ROOT), *args], check=True,
                              capture_output=True, text=True, encoding='utf-8', timeout=15).stdout.strip()
    return git('rev-parse', commit + '^{tree}'), json.loads(git('show', commit + ':version.json'))


def _qt_record_fields(record: dict, installer: Path, expected_commit: str | None):
    source = record['source']
    version = record['version']
    instance = record['installerInstance']
    if (not isinstance(expected_commit, str) or not re.fullmatch('[0-9a-f]{40}', expected_commit)
            or not isinstance(source, dict) or source.get('commit') != expected_commit
            or source.get('clean') is not True
            or not isinstance(source.get('tree'), str) or not re.fullmatch('[0-9a-f]{40}', source['tree'])
            or record.get('buildCompleted') is not True or record.get('action') != 'build-setup-only'):
        raise ValueError('Qt record requires completed clean build and explicit matching source commit')
    production = dict(id='production', appId='D04394A0-DACE-4FF9-874D-3DCB43EE53D1',
                      product='星图', installFolder='XiaochaoAssistant', versionSuffix='', runtimeDataProfile='production',
                      defaultDirName=r'{localappdata}\Programs\XiaochaoAssistant',
                      usePreviousAppDir=True, installerPrivilege='lowest')
    if (record.get('product') != '星图' or not isinstance(instance, dict)
            or any(type(instance.get(k)) is not type(v) or instance.get(k) != v for k, v in production.items())):
        raise ValueError('Qt record is not a production upgrade installer')
    tree, source_version = _source_metadata(expected_commit)
    if source['tree'] != tree or not isinstance(version, dict) or version != source_version:
        raise ValueError('Qt source tree/version mismatch')
    artifact = record['installer']
    output = Path(record['output'])
    if (not output.is_absolute() or not isinstance(artifact, dict)
            or artifact.get('fileName') != installer.name
            or installer.resolve() != (output / 'setup' / installer.name).resolve()):
        raise ValueError('Qt installer does not match recorded output/setup path')
    return version, expected_commit, artifact, 'bytes'


def validate_build_record(installer: Path, record_path: Path, expected_commit: str | None = None) -> dict:
    """Bind bytes to a trusted build owner's record, never infer version from name."""
    try:
        version = json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))
        if record_path.stat().st_size > 1024 * 1024:
            raise ValueError('oversized build record')
        record = json.loads(record_path.read_text(encoding='utf-8-sig'))
        if not isinstance(record, dict):
            raise ValueError('invalid build-record schema')
        if record.get('schema') == 'xingtu-qt-setup-build-v1':
            recorded_version, commit, artifact, size_key = _qt_record_fields(record, installer, expected_commit)
        elif record.get('schema') == 'xingtu-windows-build-record-v1':
            recorded_version, commit, artifact, size_key = record, record.get('sourceCommit'), record['installer'], 'size'
            if expected_commit is not None and expected_commit != commit:
                raise ValueError('source commit mismatch')
        else:
            raise ValueError('invalid build-record schema')
        if (recorded_version.get('displayVersion') != version['displayVersion']
                or recorded_version.get('numericVersion') != version['numericVersion']):
            raise publisher.PublishError('安装包构建记录与当前显示/数字版本不符；不能给旧包改名冒充新版。')
        if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
            raise ValueError('missing source commit')
        if (not isinstance(artifact, dict) or artifact.get('fileName') != installer.name
                or type(artifact.get(size_key)) is not int
                or not isinstance(artifact.get('sha256'), str)
                or not re.fullmatch('[0-9a-fA-F]{64}', artifact['sha256'])):
            raise ValueError('invalid artifact record')
        actual = publisher.installer_info(installer, version['displayVersion'],
                                          publisher.server_version(version['displayVersion']))
        if actual['size'] != artifact[size_key] or actual['sha256'] != artifact['sha256'].upper():
            raise publisher.PublishError('安装包内容与构建记录的大小/哈希不符，已停止。')
        return record
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise publisher.PublishError('缺少或无效的构建记录；请等待受控构建生成真实安装包及记录。') from error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='只预览 Windows 公告，不支持发布或口令配置')
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--build-record', required=True, type=Path)
    parser.add_argument('--source-commit', help='Qt 记录必需：冻结安装包的完整源码 SHA（不是工具 HEAD）')
    parser.add_argument('--notes', type=Path, default=ROOT / 'notes.txt')
    args = parser.parse_args(argv)
    try:
        record = validate_build_record(args.installer, args.build_record, args.source_commit)
        qt = record['schema'] == 'xingtu-qt-setup-build-v1'
        version = record['version'] if qt else record
        commit = record['source']['commit'] if qt else record['sourceCommit']
        print(f"安装包完整路径：{args.installer.resolve()}")
        print(f"构建显示版本：{version['displayVersion']}；数字版本：{version['numericVersion']}")
        print(f"构建源码提交：{commit}")
        # The only supported invocation is the publisher's default GET-only preview.
        # No token/environment inspection and no forwarding arbitrary extra flags.
        return publisher.main(['--installer', str(args.installer), '--notes', str(args.notes)])
    except publisher.PublishError as error:
        print(f'错误：{error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
