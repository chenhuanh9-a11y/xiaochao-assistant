"""Receipt-owned author removal; recognition and custom-route data are never removed.

Run inspections and mutations off Tk. The caller owns the same idle resource
lease as live import, then refreshes the existing resource factory on Tk.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from star_map_import import (
    ImportRejected, LIVE_AUTHOR_IMAGE_ROOTS, PACKAGE_IDS,
    _allowed, _check_cancelled, _live_author_path, _no_links, _owned_inventory, _rows,
    _pending_upgrade, _sha, _target, _writer_locked_installation,
    verified_installed_package,
)
from legacy_author_inventory import LEGACY_AUTHOR_INVENTORY

PACKAGE_NAMES = {
    'nightmare-zhan10': '展十 · 噩梦',
    'nightmare-suju-speed': '酥居速刷 · 噩梦',
    'difficult-suju-speed': '酥居速刷 · 困难',
    'difficult-aichicu': '爱吃醋 · 困难',
    'nightmare-suju': '滋酥居 · 噩梦',
    'difficult-zhan10': '展十 · 困难',
    'nightmare-nightingale': '爱吃醋 · 噩梦',
    'nightmare-wang-qingxin': '王清心 · 噩梦',
    'nightmare-lianghapi': '凉哈皮 · 噩梦',
}


def package_inspection_message(error) -> str:
    """Explain local validation independently of online catalog availability."""
    text = str(error)
    if 'existing-data-conflict:' in text:
        path = text.split('existing-data-conflict:', 1)[1].strip()
        return ('本地已有不同版本、改动过或未登记的资源，不能直接覆盖。'
                '同包未改动文件会安全更新；本地改图请先自行导出保存，'
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


def adopt_legacy_author_package(install_root, package_id, *, cancelled=None) -> dict:
    """Register an entire approved legacy display set, without changing assets.

    This runs in the existing local-state worker. A release ZIP hash and its
    independently generated inventory authorize ownership, never folder presence.
    Publication is exclusive under the same lock as import/removal. Existing
    receipts, unknown files and core dependencies are never overwritten/claimed.
    """
    package_id = _package_id(package_id)
    root = Path(os.path.abspath(install_root))
    _no_links(root)
    _pending_upgrade(root)
    owned, digest = _owned_inventory(root, package_id)
    if digest is not None:
        return dict(status='already-managed')
    release = LEGACY_AUTHOR_INVENTORY.get(package_id)
    if not release:
        return dict(status='no-approved-legacy-release')
    rows = _rows([{key: row[key] for key in ('path', 'bytes', 'sha256')}
                  for row in release['files']])
    if not rows or any(not _allowed(package_id, row['path']) or
                       not _live_author_path(package_id, row['path']) for row in rows.values()):
        raise ImportRejected('invalid-legacy-author-inventory')
    if not any(_target(root, row['path']).exists() for row in rows.values()):
        return dict(status='not-installed')
    with _writer_locked_installation(root, require_closed=False) as (root, state):
        _pending_upgrade(root)
        _, digest = _owned_inventory(root, package_id)
        if digest is not None:
            return dict(status='already-managed')
        for other in PACKAGE_IDS - {package_id}:
            other_rows, _ = _owned_inventory(root, other)
            if rows.keys() & other_rows.keys():
                raise ImportRejected('shared-author-file-ownership')
        witnesses = {}
        for row in rows.values():
            _check_cancelled(cancelled)
            path = _target(root, row['path'])
            before = path.stat() if path.is_file() else None
            if (before is None or before.st_size != row['bytes'] or _sha(path) != row['sha256']):
                return dict(status='legacy-unverified', reason='legacy-display-set-not-exact',
                            path=row['path'])
            stat = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns):
                raise ImportRejected('legacy-author-file-changed')
            witnesses[row['path']] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        receipt = dict(packageId=package_id, revision=1, files=list(rows.values()),
                       archiveSha256=release['archiveSha256'],
                       legacyAdoption=dict(schemaVersion=1, scope='author-display-only',
                                           sourceArchive=release['archiveName'],
                                           completeDisplaySet=True))
        target = _target(root, f'work/map-packages/{package_id}.json')
        fd, temporary_name = tempfile.mkstemp(prefix='legacy-receipt-', suffix='.tmp', dir=state)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(receipt, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            for relative, before in witnesses.items():
                _check_cancelled(cancelled)
                stat = _target(root, relative).stat()
                if before != (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns):
                    raise ImportRejected('legacy-author-file-changed')
            _check_cancelled(cancelled)
            _no_links(target)
            # Complete same-volume publication; never replace a raced receipt.
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return dict(status='legacy-adopted', fileCount=len(rows), restartRequired=False)


def inspect_author_package_removal(install_root, package_id) -> dict:
    """A confirmed uninstall includes this package's verified historical copies."""
    from package_lifecycle import inspect_cleanup
    package_id = _package_id(package_id)
    _owned_inventory(Path(os.path.abspath(install_root)), package_id)
    plan = inspect_cleanup(install_root, package_id)
    if not plan['files'] and not plan['receiptSha256'] and not plan['transactions']:
        raise ImportRejected('author-package-not-managed')
    plan['title'] = PACKAGE_NAMES[package_id]
    return plan


def managed_author_packages_snapshot(install_root, *, cancelled=None) -> list[dict]:
    """Local state remains available even when there is no online catalog."""
    root = Path(os.path.abspath(install_root))
    result = []
    for package_id, title in PACKAGE_NAMES.items():
        _check_cancelled(cancelled)
        item = dict(packageId=package_id, title=title,
                    mode='difficult' if package_id.startswith('difficult-') else 'nightmare',
                    canRemove=False)
        adoption = None
        try:
            adoption = adopt_legacy_author_package(root, package_id, cancelled=cancelled)
        except (ImportRejected, OSError) as error:
            adoption = dict(status='legacy-unverified', reason=str(error))
        _check_cancelled(cancelled)
        state = verified_installed_package(root, package_id, cancelled=cancelled)
        item.update(state)
        try:
            rows, receipt_sha = _owned_inventory(root, package_id)
            removable = [row for row in rows.values() if _live_author_path(package_id, row['path'])]
            if not removable:
                raise ImportRejected('author-package-not-managed')
            modified, unchanged = [], removable
            if state['status'] != 'installed':
                unchanged = []
                for row in removable:
                    _check_cancelled(cancelled)
                    path = _target(root, row['path'])
                    if path.is_file():
                        if path.stat().st_size == row['bytes'] and _sha(path) == row['sha256']:
                            unchanged.append(row)
                        else:
                            modified.append(row['path'])
            plan = dict(fileCount=len(unchanged), bytes=sum(row['bytes'] for row in unchanged),
                        modifiedFiles=modified, receiptSha256=receipt_sha)
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
                    item.update(status='unmanaged', reason=(adoption or {}).get('reason') or
                                'author-files-without-receipt')
        else:
            item.update(canRemove=True, fileCount=plan['fileCount'], bytes=plan['bytes'],
                        modifiedFiles=len(plan['modifiedFiles']), receiptSha256=plan['receiptSha256'])
        result.append(item)
    return result


def remove_author_package_live(install_root, package_id, *, application_idle=False,
                               expected_receipt_sha256=None, expected_plan_sha256=None, cancelled=None) -> dict:
    """Uninstall verified package bytes; preserve edits and discard temporary rollback data."""
    from package_lifecycle import cleanup_live
    _package_id(package_id)
    if application_idle is not True:
        raise ImportRejected('live-removal-requires-idle')
    if expected_receipt_sha256 is None:
        raise ImportRejected('removal-confirmation-required')
    if not expected_plan_sha256:
        raise ImportRejected('removal-plan-confirmation-required')
    _, current = _owned_inventory(Path(os.path.abspath(install_root)), package_id)
    if (current or '') != expected_receipt_sha256:
        raise ImportRejected('installed-receipt-changed')
    return cleanup_live(install_root, package_id, application_idle=application_idle,
                        expected_plan_sha256=expected_plan_sha256, cancelled=cancelled)
