"""Current Setup's temporary, parent-bound resource-upgrade helper.

No standalone unlocked mode, network, main-EXE dispatch or old-helper execution.
The baked profile identifies compiled code; a digest-pinned current Setup report
identifies the target program. User consent is an exact reviewed plan digest.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from installer_resource_lease import borrowed_installer_lease
from resource_transaction import ResourceTransactionError, _safe

MAX_JSON = 16 * 1024 * 1024


def _json(path):
    path = _safe(Path(path))
    with path.open('rb') as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError('reset-helper-json-too-large')
    return json.loads(data.decode('utf-8-sig'))


def _write_new(path, data):
    path = _safe(Path(path))
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _target(args):
    profile = _json(Path(__file__).with_name('installation-profile.json'))
    report_path = _safe(Path(args.target_report))
    if report_path.stat().st_size > MAX_JSON:
        raise ValueError('reset-target-report-too-large')
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    if not re.fullmatch(r'[0-9a-f]{64}', args.target_report_sha256) or digest != args.target_report_sha256:
        raise ValueError('reset-target-report-changed')
    report = _json(report_path)
    compiled = report.get('compiledPayloadSourceCommit', report.get('sourceCommit'))
    if (profile.get('product') != '星图' or profile.get('sourceCommit') != compiled
            or report.get('installerSourceCommit', report.get('sourceCommit')) != args.source_commit
            or not re.fullmatch(r'[0-9a-f]{40}', args.source_commit)
            or report.get('sourceDirty') is not False
            or not re.fullmatch(r'[0-9A-Za-z.+_-]{1,100}', args.display_version)
            or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', args.numeric_version)
            or report.get('displayVersion') != args.display_version
            or report.get('numericVersion') != args.numeric_version):
        raise ValueError('reset-target-program-identity-mismatch')
    target = dict(sourceCommit=args.source_commit, displayVersion=args.display_version,
                  numericVersion=args.numeric_version, executableSha256=report.get('executableSha256'),
                  resourceInventorySha256=report.get('resourceInventorySha256'))
    for name in ('executableSha256', 'resourceInventorySha256'):
        if not re.fullmatch(r'[0-9a-f]{64}', str(target[name])):
            raise ValueError('reset-target-program-identity-invalid')
    return target


def _summary(plan, target):
    old_labels = []
    for row in plan['observedIdentity']:
        observed = row.get('observed', {})
        label = observed.get('displayVersion') or observed.get('version')
        if label is not None:
            old_labels.append(str(label) if re.fullmatch(r'[0-9A-Za-z.+_-]{1,100}', str(label)) else '无效版本标记')
    old = '、'.join(sorted(set(old_labels))) or '无法确认（不会据此认定文件所有权）'
    packages = plan['packagesToDownload']
    package_notice = ('已识别需重新下载或导入的图包（' + str(len(packages)) + ' 项）：' +
                      '、'.join(packages[:12]) +
                      ('（另 ' + str(len(packages) - 12) + ' 项）' if len(packages) > 12 else '')) if packages else (
                          '未确认已启用的图包身份；未知文件仍按下方范围移入可恢复隔离区。')
    return '\r\n'.join((
        '覆盖升级将重新建立地图资源：',
        '旧版本标记（仅观察）：' + old,
        '目标版本：' + target['displayVersion'] + '；源码：' + target['sourceCommit'],
        f"原资源和图包文件：{plan['fileCount']} 个，共 {plan['bytes']:,} 字节。",
        f"已改动的登记资源：{plan['modifiedCount']} 个；未分类资源：{plan['unclassifiedCount']} 个。",
        package_notice,
        '旧资源、已下载/导入的图包及其记录将从活动目录移到可恢复隔离区，不永久删除。',
        '升级后需要重新下载或导入作者图包。设置、自建路线和自定义地图保留。',
        '隔离区：本安装目录下 work/resource-upgrades/<事务编号>。',
        '原生安装开始前取消可恢复资源；开始后只允许验证完整后向前完成，否则安全阻止继续。',
        '确认的盘点摘要：' + plan['planSha256'],
        '是否按这份盘点执行升级？否将停止本次安装。'))


def _pending_transaction(api, root, expected_plan):
    pointer = Path(root) / api.PENDING
    if not pointer.exists():
        return None
    value = _json(pointer)
    status = api.resource_upgrade_status(root, value.get('transactionId'))
    if status['planSha256'] != expected_plan:
        raise ValueError('reset-pending-is-not-this-reviewed-operation')
    return status['transactionId']


def _execute(args, api, target):
    root = Path(args.install_root)
    if args.action == 'pending':
        return api.resource_upgrade_status(root)
    if args.action == 'inspect':
        if getattr(args, 'resource_action', 'reset') == 'preserve':
            from installer_upgrade_coordinator import native_context
            from installer_native_observation import preflight_native_evidence
            context = native_context(args, target)
            preflight_native_evidence(root, **context)
            api.assert_resource_upgrade_ready(root)
            return dict(status='inspected', resourceAction='preserve',
                        planSha256=args.program_inventory_sha256, sessionId=str(uuid.uuid4()))
        plan = api.inspect_resource_upgrade(root, candidate_directory=args.candidate_directory,
                                            target_identity=target)
        _write_new(Path(args.result_directory) / 'plan.json',
                   json.dumps(plan, ensure_ascii=False, indent=2).encode('utf-8'))
        # Inno LoadStringsFromFile consumes UTF-8 text, not the UTF-16 INI
        # format used below by GetIniString. Keep a BOM for older Inno readers.
        _write_new(Path(args.result_directory) / 'summary.txt', _summary(plan, target).encode('utf-8-sig'))
        return dict(status='inspected', planSha256=plan['planSha256'], sessionId=str(uuid.uuid4()))
    if args.action == 'prepare':
        if not args.confirmed or not re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256 or ''):
            raise ValueError('reset-reviewed-confirmation-required')
        return api.prepare_resource_upgrade(root, expected_plan_sha256=args.plan_sha256,
            confirmed=True, candidate_directory=args.candidate_directory, target_identity=target)
    transaction = args.transaction_id
    if not transaction and args.action == 'rollback':
        transaction = _pending_transaction(api, root, args.plan_sha256)
        if transaction is None:
            return dict(status='not-needed')
    status = api.resource_upgrade_status(root, transaction)
    if status['planSha256'] != args.plan_sha256:
        raise ValueError('reset-transaction-does-not-match-reviewed-plan')
    if args.action == 'status':
        return status
    if args.action == 'resume-committed':
        # Only an already durable commit may be finalized by a later Setup.
        # Uncommitted interruption needs BOTH program and resource recovery.
        prior_target = status.get('targetIdentity')
        if status['status'] != 'committed' or not args.confirmed or not isinstance(prior_target, dict):
            raise ValueError('reset-only-confirmed-committed-cleanup-is-supported')
        installed = _json(_safe(root / 'release-report.json'))
        if (installed.get('installerSourceCommit', installed.get('sourceCommit')) != prior_target['sourceCommit']
                or any(installed.get(key) != prior_target[key] for key in (
                    'displayVersion', 'numericVersion', 'executableSha256', 'resourceInventorySha256'))
                or hashlib.sha256(_safe(root / '星图.exe').read_bytes()).hexdigest() != prior_target['executableSha256']):
            raise ValueError('reset-prior-committed-program-identity-mismatch')
        return api.finish_resource_upgrade(root, transaction, setup_succeeded=True)
    if args.action == 'commit':
        # ssPostInstall alone is not evidence of which program bytes survived.
        # Bind the installed program/report to this exact packaged target too.
        executable = _safe(root / '星图.exe')
        report = _safe(root / 'release-report.json')
        if (hashlib.sha256(executable.read_bytes()).hexdigest() != target['executableSha256']
                or hashlib.sha256(report.read_bytes()).hexdigest() != args.target_report_sha256):
            raise ValueError('reset-installed-program-does-not-match-target')
        return api.finish_resource_upgrade(root, transaction, setup_succeeded=True)
    if args.action == 'rollback':
        return api.finish_resource_upgrade(root, transaction, setup_succeeded=False)
    raise ValueError('reset-invalid-action')


def run(args):
    # Result files live in a fresh Setup-private directory, outside all active
    # resource roots. Never leave helper receipts among package ownership data.
    directory = _safe(Path(args.result_directory))
    if not directory.is_dir() or any(directory.iterdir()):
        raise ValueError('reset-result-directory-must-be-new-and-empty')
    result = dict(status='error', action=args.action)
    code = 2
    try:
        target = None if args.action in {'installer-pending', 'resume-native-complete'} else _target(args)
        identity = {name: getattr(args, name) for name in (
            'parent_pid', 'parent_created', 'parent_executable', 'parent_sha256', 'lock_handle')}
        with borrowed_installer_lease(args.install_root, **identity) as lease:
            import resource_upgrade_reset as api
            from installer_upgrade_coordinator import Coordinator
            from installer_upgrade_journal import read_status
            from installer_native_observation import current_user_sid
            owner_sid = current_user_sid()
            if args.action.startswith('legacy-'):
                from installer_legacy_handoff import LegacyHandoff
                # Same packaged target checks, real user and exact parent lease
                # as native upgrades. The positional legacy-prepare command
                # remains preparation-only and grants none of these actions.
                from installer_uninstall import _baked_profile, _profile_identity
                if _profile_identity(_baked_profile())[0] != 'production':
                    raise ValueError('production-windows-helper-required')
                result.update(LegacyHandoff(args, target, lease, owner_sid).execute())
            elif args.action in {'inspect', 'pending', 'resume-committed'}:
                if read_status(args.install_root, owner_sid=owner_sid)['status'] != 'not-needed':
                    raise ValueError('installer-coordinator-recovery-required')
                result.update(_execute(args, api, target))
            else:
                result.update(Coordinator(args, api, target, lease, owner_sid).execute())
        if target is not None:
            result.update(sourceCommit=target['sourceCommit'], targetReportSha256=args.target_report_sha256)
        if args.action.startswith('legacy-'):
            result.update(ownerSid=owner_sid, parentPid=args.parent_pid,
                          programInventorySha256=args.program_inventory_sha256)
        code = 0
    except Exception as error:
        result['errorCode'] = getattr(error, 'code', type(error).__name__)
        detail = str(error).strip() or str(result['errorCode'])
        _write_new(directory / 'error.txt', detail.encode('utf-8-sig'))
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser['result'] = {key: str(value) for key, value in result.items()
                        if isinstance(value, (str, int, bool))}
    # A complete INI is the last publication. A missing receipt is uncertain,
    # never an implied commit; Setup queries the durable transaction status.
    import io
    stream = io.StringIO()
    parser.write(stream)
    _write_new(directory / 'result.ini', stream.getvalue().encode('utf-16'))
    return code


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == 'legacy-prepare':
        from installer_legacy_bridge import main as prepare_legacy
        return prepare_legacy(arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('inspect', 'prepare', 'status', 'commit', 'rollback',
                                           'pending', 'resume-committed', 'installer-pending',
                                           'start-native', 'native-complete', 'resume-native-complete',
                                           'legacy-prepare', 'legacy-start-native', 'legacy-native-complete',
                                           'legacy-commit', 'legacy-status', 'legacy-cancel', 'legacy-resume',
                                           'legacy-retire'), required=True)
    for name in ('install-root', 'result-directory', 'target-report', 'target-report-sha256',
                 'source-commit', 'display-version', 'numeric-version', 'parent-created',
                 'parent-executable', 'parent-sha256'):
        parser.add_argument('--' + name, required=True)
    for name in ('parent-pid', 'lock-handle'):
        parser.add_argument('--' + name, type=int, required=True)
    parser.add_argument('--candidate-directory')
    parser.add_argument('--legacy-source')
    parser.add_argument('--resource-action', choices=('reset', 'preserve'), default='reset')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--transaction-id')
    parser.add_argument('--confirmed', action='store_true')
    for name in ('session-id', 'program-inventory', 'program-inventory-sha256',
                 'uninstall-exe', 'group-shortcut', 'uninstall-shortcut', 'desktop-shortcut'):
        parser.add_argument('--' + name)
    for name in ('restore-watcher', 'group-enabled', 'desktop-enabled'):
        parser.add_argument('--' + name, action='store_true')
    try:
        return run(parser.parse_args(argv))
    except Exception:
        return 3  # no trustworthy result receipt; Setup must not infer success


if __name__ == '__main__':
    raise SystemExit(main())
