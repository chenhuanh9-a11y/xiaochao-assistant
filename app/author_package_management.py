"""Receipt-owned author removal; recognition and custom-route data are never removed.

Run inspections and mutations off Tk. The caller owns the same idle resource
lease as live import, then refreshes the existing resource factory on Tk.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from star_map_import import (
    ImportRejected, LIVE_AUTHOR_IMAGE_ROOTS, PACKAGE_IDS,
    _check_cancelled, _live_author_path, _no_links, _owned_inventory,
    _pending_upgrade, _sha, _target, _writer_locked_installation,
    verified_installed_package,
)

PACKAGE_NAMES = {
    'nightmare-suju': '滋酥居 · 噩梦',
    'difficult-zhan10': '展十 · 困难',
    'nightmare-nightingale': '爱吃醋 · 噩梦',
    'nightmare-wang-qingxin': '王清心 · 噩梦',
}


def package_inspection_message(error) -> str:
    """Explain local validation independently of online catalog availability."""
    text = str(error)
    if 'existing-data-conflict:' in text:
        path = text.split('existing-data-conflict:', 1)[1].strip()
        return ('本地已有不同版本或未登记的资源，不能安全覆盖。'
                '若是已管理资源，请先在“管理已安装资源”移除后重新导入；'
                f'未登记文件会保留。冲突文件：{path}')
    if 'missing-or-incompatible-dependency:' in text:
        return '资源包与当前基础地图数据不匹配，请选择适用于本版本的资源包。原资源未改动。'
    if 'upgrade-recovery-required' in text or 'pending-upgrade' in text:
        return '上次资源更新尚未完成恢复，暂不能继续导入。原文件与恢复记录已保留。'
    return f'离线资源包校验失败，原资源未改动：{text}'


def _package_id(package_id):
    if package_id not in PACKAGE_NAMES:
        raise ImportRejected('unsupported-author-package')
    return package_id


def inspect_author_package_removal(install_root, package_id) -> dict:
    """Prepare an exact immutable removal plan, never infer ownership by folder."""
    package_id = _package_id(package_id)
    root = Path(os.path.abspath(install_root))
    _no_links(root)
    _pending_upgrade(root)
    rows, receipt_sha = _owned_inventory(root, package_id)
    if not rows:
        raise ImportRejected('author-package-not-managed')
    removable = [r for r in rows.values() if _live_author_path(package_id, r['path'])]
    if not removable:
        raise ImportRejected('no-managed-author-files')
    for other in PACKAGE_IDS - {package_id}:
        other_rows, _ = _owned_inventory(root, other)
        if any(r['path'].casefold() in other_rows for r in removable):
            raise ImportRejected('shared-author-file-ownership')
    present, missing = [], []
    for row in removable:
        path = _target(root, row['path'])
        if not path.exists():
            missing.append(row['path'])
        elif not path.is_file() or path.stat().st_size != row['bytes'] or _sha(path) != row['sha256']:
            raise ImportRejected('modified-author-file: ' + row['path'])
        else:
            present.append(dict(row))
    return dict(packageId=package_id, title=PACKAGE_NAMES[package_id],
                receiptSha256=receipt_sha, files=present, missingFiles=missing,
                preservedCoreFiles=len(rows)-len(removable), fileCount=len(present),
                bytes=sum(r['bytes'] for r in present), restartRequired=False)


def managed_author_packages_snapshot(install_root, *, cancelled=None) -> list[dict]:
    """Local state remains available even when there is no online catalog."""
    root = Path(os.path.abspath(install_root))
    result = []
    for package_id, title in PACKAGE_NAMES.items():
        _check_cancelled(cancelled)
        item = dict(packageId=package_id, title=title,
                    mode='difficult' if package_id.startswith('difficult-') else 'nightmare',
                    canRemove=False)
        state = verified_installed_package(root, package_id, cancelled=cancelled)
        item.update(state)
        try:
            plan = inspect_author_package_removal(root, package_id)
        except (ImportRejected, OSError) as error:
            item['reason'] = str(error)
            if state['status'] == 'not-installed':
                # Existence is only a warning, never ownership or deletion authority.
                unowned = False
                try:
                    for prefix in LIVE_AUTHOR_IMAGE_ROOTS[package_id]:
                        folder = _target(root, prefix.rstrip('/'))
                        if folder.is_dir():
                            for path in folder.rglob('*'):
                                _no_links(path)
                                if path.is_file() and path.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}:
                                    unowned = True
                                    break
                        if unowned:
                            break
                except (ImportRejected, OSError) as scan_error:
                    item.update(status='unverified', reason=str(scan_error))
                if unowned:
                    item.update(status='unmanaged', reason='author-files-without-receipt')
        else:
            item.update(canRemove=True, fileCount=plan['fileCount'], bytes=plan['bytes'],
                        receiptSha256=plan['receiptSha256'])
        result.append(item)
    return result


def remove_author_package_live(install_root, package_id, *, application_idle=False,
                               expected_receipt_sha256=None, cancelled=None) -> dict:
    """Move owned display files into a recoverable backup, committing receipt last.

    The full receipt is archived even when it also names core data. Those core
    files stay in place. A subsequent import verifies and reuses their bytes.
    No recursive delete is used; modified/unowned/shared files fail closed.
    """
    if application_idle is not True:
        raise ImportRejected('live-removal-requires-idle')
    if not expected_receipt_sha256:
        raise ImportRejected('removal-confirmation-required')
    with _writer_locked_installation(install_root, require_closed=False) as (root, state):
        plan = inspect_author_package_removal(root, package_id)
        if plan['receiptSha256'] != expected_receipt_sha256:
            raise ImportRejected('installed-receipt-changed')
        _check_cancelled(cancelled)
        parent = _target(root, 'work/map-package-removals')
        parent.mkdir(parents=True, exist_ok=True)
        _no_links(parent)
        backup = Path(tempfile.mkdtemp(prefix=package_id+'-', dir=parent))
        moved = []
        try:
            for row in plan['files']:
                _check_cancelled(cancelled)
                source = _target(root, row['path'])
                if not source.is_file() or source.stat().st_size != row['bytes'] or _sha(source) != row['sha256']:
                    raise ImportRejected('author-file-changed-during-removal')
                target = _target(backup, row['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                _no_links(target.parent)
                os.replace(source, target)
                moved.append((source, target))
                if target.stat().st_size != row['bytes'] or _sha(target) != row['sha256']:
                    raise ImportRejected('author-file-changed-during-removal')
            _check_cancelled(cancelled)
            receipt = _target(root, f'work/map-packages/{package_id}.json')
            if _sha(receipt) != expected_receipt_sha256:
                raise ImportRejected('installed-receipt-changed')
            os.replace(receipt, _target(backup, 'installed-receipt.json'))
        except Exception:
            for original, saved in reversed(moved):
                if original.exists():
                    raise ImportRejected('removal-rollback-conflict-backup-retained')
                _no_links(original)
                os.replace(saved, original)
            raise
        return dict(status='author-removed-live', packageId=package_id,
                    removedFiles=len(moved), preservedCoreFiles=plan['preservedCoreFiles'],
                    backupPath=str(backup), restartRequired=False)
