"""Qt owner for the imported store; saving a version never activates it.

Workers return values only. The UI thread owns operation state and the stable
package model. Shutdown stops admission and drains an accepted store operation.
"""
from copy import deepcopy
from pathlib import Path
import re
import threading

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, QUrl, Signal, Slot
from backend import ResourceRows


_ACTIVATION_CONTEXTS = {
    ('s0', 'nightmare'): dict(label='启用 S0 噩梦', objectSuffix='', notice='',
        activatedMessage='S0 噩梦基础结构与楼层控件已启用。'),
    ('s0', 'difficult'): dict(label='启用 S0 困难数据', objectSuffix='-s0-difficult',
        notice='识别功能尚未开放，启用后仍不能开始识别。', activatedMessage='S0 困难基础数据已启用。'),
    ('s1', 'difficult'): dict(label='启用 S1 困难数据', objectSuffix='-s1-difficult',
        notice='自动楼层识别尚未验证，启用后仍不可用。', activatedMessage='S1 困难基础数据已启用。'),
    ('s1', 'nightmare'): dict(label='启用 S1 噩梦数据', objectSuffix='-s1-nightmare',
        notice='自动楼层识别尚未验证，启用后仍不可用。', activatedMessage='S1 噩梦基础数据已启用。'),
}


class PackageRows(ResourceRows):
    fields = ('modelData',)

    def reconcile(self, rows):
        super().reconcile([dict(key=row['key'], modelData=deepcopy(row)) for row in rows])


class _Signals(QObject):
    done = Signal(int, object, object)


def _failure(error):
    code = getattr(error, 'code', None) or getattr(error, 'reason', None) or str(error)
    if not isinstance(code, str) or not re.fullmatch('[a-z0-9-]{1,120}', code):
        code = 'resource-operation-failed'
    return dict(code=code, errorType=type(error).__name__)


def _activation_refs(row, season, mode):
    from imported_resource_packages import ComponentRef
    context = f'{season}:{mode}'
    if (season, mode) not in _ACTIVATION_CONTEXTS or row.get('status') == 'damaged':
        return None
    refs = []
    for role in ('recognition-base', 'controls-profile'):
        components = [item for item in row['components']
                      if item['type'] == role and context in item['contexts']]
        if len(components) != 1:
            return None
        refs.append(ComponentRef(row['packageId'], row['resourceVersion'],
                                 components[0]['componentId'], row['manifestSha256']))
    return tuple(refs)


class _Work(QRunnable):
    def __init__(self, token, function):
        super().__init__()
        self.token, self.function = token, function
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.token, self.function(), None)
        except Exception as error:
            self.signals.done.emit(self.token, None, _failure(error))


class ImportedResourceService(QObject):
    changed = Signal()
    inventoryChanged = Signal()
    resourcesChanged = Signal()
    errorReported = Signal(object)
    importCompleted = Signal(str, str)

    def __init__(self, resolver, parent=None, *, paths=None):
        super().__init__(parent)
        self.resolver = resolver
        self.paths = paths
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.model = PackageRows()
        self._rows, self._jobs = [], {}
        self._token, self._busy, self._closed = 0, False, False
        self._kind, self._message, self._catalog_issue = '', '', ''
        self._cancel = threading.Event()
        self._lease_provider = None
        self._lease = ''
        self._startup_notice = ''

    packageModel = Property(QObject, lambda s: s.model, constant=True)
    packageRows = Property('QVariantList', lambda s: deepcopy(s._rows), notify=inventoryChanged)
    activationOptions = Property('QVariantList', lambda s: [
        dict(context=f'{season}:{mode}', season=season, mode=mode, **metadata)
        for (season, mode), metadata in _ACTIVATION_CONTEXTS.items()], constant=True)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    pendingKind = Property(str, lambda s: '', notify=changed)
    operationStage = Property(str, lambda s: 'cancelling' if s._busy and s._cancel.is_set() else s._kind, notify=changed)
    operationGeneration = Property(int, lambda s: s._token, notify=changed)
    message = Property(str, lambda s: '\n'.join(filter(None, (s._startup_notice, s._message))), notify=changed)
    canCancel = Property(bool, lambda s: s._busy and s._kind != 'recover' and not s._cancel.is_set(), notify=changed)
    pendingWrites = Property(bool, lambda s: s._busy and s._kind in {'save', 'activate', 'recover'}, notify=changed)
    recoveryRequired = Property(bool, lambda s: s._catalog_issue == 'resource-catalog-recovery-required', notify=changed)

    def setLeaseProvider(self, provider):
        if self._busy or self._lease:
            raise RuntimeError('Cannot replace an active resource lease provider')
        self._lease_provider = provider

    def setStartupCompatibilityReport(self, report):
        self._startup_notice = ('旧版资源操作记录已保留；新版资源从独立目录导入。'
                                if report.get('legacyPending') else '')
        self.changed.emit()

    def _begin(self, kind, function):
        if self._closed or self._busy:
            return False
        self._token += 1
        self._busy, self._kind = True, kind
        self._cancel = threading.Event()
        self._message = {'list': '正在读取已保存版本…', 'refresh': '正在校验已保存文件…',
                         'save': '正在校验并保存；完成后仍需启用。',
                         'activate': '正在校验并启用所选资源…',
                         'recover': '正在处理未完成的资源切换…'}[kind]
        job = _Work(self._token, lambda: function(self._cancel.is_set))
        self._jobs[self._token] = job
        job.signals.done.connect(self._complete, Qt.QueuedConnection)
        self.changed.emit()
        self.pool.start(job)
        return True

    @Slot()
    def listPackages(self):
        self._scan(False)

    @Slot()
    def refresh(self):
        self._scan(True)

    def _scan(self, verify):
        from imported_resource_inventory import list_saved_versions
        self._begin('refresh' if verify else 'list', lambda cancelled:
                    dict(inventory=list_saved_versions(self.resolver, verify=verify, cancelled=cancelled)))

    @Slot(str)
    @Slot(str, str)
    def importArchive(self, file_url, season='s0'):
        return self._import(file_url)

    @Slot(str, 'QVariantMap', int, result=bool)
    def importVerifiedArchive(self, file_url, expected, generation):
        if generation != self._token:
            return False
        return self._import(file_url, dict(expected))

    def _import(self, file_url, expected=None):
        url = QUrl(file_url)
        if not url.isLocalFile() or not url.toLocalFile():
            self._problem('resource-archive-local-file-required')
            return False
        path = Path(url.toLocalFile())
        def save(cancelled):
            from imported_resource_archive import inspect_archive, save_archive
            from imported_resource_inventory import list_saved_versions
            from imported_resource_catalog import ResourceCatalogError
            sha = None
            if expected is not None:
                if (set(expected) != {'packageId', 'bytes', 'sha256'}
                        or type(expected['bytes']) is not int or expected['bytes'] <= 0
                        or not isinstance(expected['sha256'], str)
                        or not re.fullmatch('[0-9a-f]{64}', expected['sha256'])):
                    raise ResourceCatalogError('resource-download-binding-invalid')
                sha = expected['sha256']
                inspected = inspect_archive(path, expected_archive_sha256=sha, cancelled=cancelled)
                if inspected.package_id != expected['packageId'] or path.stat().st_size != expected['bytes']:
                    raise ResourceCatalogError('resource-download-binding-invalid')
            summary = save_archive(path, self.resolver.root, expected_archive_sha256=sha, cancelled=cancelled)
            # A completed save is reported even if a cancellation arrives during
            # the following read. The immutable version may already be present.
            try:
                inventory = list_saved_versions(self.resolver)
            except (OSError, ValueError, RuntimeError):
                inventory = None
            return dict(summary=summary, inventory=inventory)
        return self._begin('save', save)

    @Slot(str, str, str, result=bool)
    def activatePackage(self, key, season, mode):
        if self._closed or self._busy:
            return False
        row = next((row for row in self._rows if row['key'] == key), None)
        refs = _activation_refs(row, season, mode) if row is not None else None
        if refs is None or self.paths is None or self._lease_provider is None:
            self._problem('resource-activation-unavailable')
            return False
        self._lease = self._lease_provider.acquireResourceLease()
        if not self._lease:
            self._message = '请先结束本局，并等待后台任务完成，再启用资源。'
            self.changed.emit()
            return False

        def activate(cancelled):
            from client_imported_activation import activate_context
            from imported_resource_inventory import list_saved_versions
            outcome, failure = None, None
            try:
                outcome = activate_context(self.resolver, self.paths, *refs,
                    season=season, mode=mode, cancelled=cancelled)
            except Exception as error:
                failure = _failure(error)
            # Observe the actual final state after success, cancellation or a
            # failed rollback. A committed change remains success if this read
            # fails; a retained pending operation must expose recovery.
            try:
                inventory = list_saved_versions(self.resolver)
            except (OSError, ValueError, RuntimeError):
                inventory = None
            return dict(activated=outcome, activatedContext=f'{season}:{mode}',
                        operationError=failure, inventory=inventory)

        if self._begin('activate', activate):
            return True
        self._release_lease()
        return False

    @Slot()
    def requestRecovery(self):
        if self._closed or self._busy or not self.recoveryRequired or self._lease_provider is None:
            return
        self._lease = self._lease_provider.acquireResourceLease()
        if not self._lease:
            self._message = '请先结束本局，并等待后台任务完成，再处理资源切换。'
            self.changed.emit()
            return
        def recover(cancelled):
            from resource_transaction import resource_write_lock
            from imported_resource_activation import recover_catalog_locked
            from imported_resource_inventory import list_saved_versions
            with resource_write_lock(self.resolver.root):
                outcome = recover_catalog_locked(self.resolver.root)
            return dict(recovered=outcome, inventory=list_saved_versions(self.resolver))
        if not self._begin('recover', recover):
            self._release_lease()

    def _release_lease(self):
        if self._lease:
            self._lease_provider.releaseResourceLease(self._lease)
            self._lease = ''

    @Slot(int, object, object)
    def _complete(self, token, value, error):
        self._jobs.pop(token, None)
        if token != self._token:
            return
        kind, self._kind, self._busy = self._kind, '', False
        self._release_lease()
        if self._closed:
            return
        if value is not None:
            inventory = value['inventory']
            self._catalog_issue = inventory.catalog_issue or '' if inventory is not None else 'resource-inventory-unavailable'
            self._set_rows(inventory.rows if inventory is not None else [])
            error = error or value.get('operationError')
        if error:
            if kind in {'list', 'refresh'}:
                self._set_rows([])
            self._problem(error['code'])
        else:
            self._message = ('资源切换尚未完成，请处理后再使用。' if self.recoveryRequired else
                             '资源选择记录无法读取；已保存的版本仍予保留。' if self._catalog_issue else
                             '已读取保存版本。' if kind == 'list' else '文件完整性校验已完成。')
            summary = value.get('summary')
            if summary is not None:
                duplicate = summary.status == 'already-saved'
                self._message = ('该版本已经保存，启用状态未改变。' if duplicate else
                                 '资源版本已保存，尚未启用。')
                if inventory is None:
                    self._message += ' 列表暂时无法读取，请重新核对。'
                self.importCompleted.emit('版本已保存', self._message)
            if kind == 'recover':
                self._message = '未完成的资源切换已处理，请重新核对可用资源。'
                self.resourcesChanged.emit()
            if kind == 'activate':
                outcome = value['activated']
                duplicate = outcome['status'] == 'already-active'
                context = tuple(value['activatedContext'].split(':'))
                self._message = ('此版本已经启用，资源选择未改变。' if duplicate
                                 else _ACTIVATION_CONTEXTS[context]['activatedMessage'])
                if context[0] == 's1':
                    self._message += ' 自动楼层识别尚未验证，暂不可用。'
                elif context == ('s0', 'difficult'):
                    self._message += ' 识别功能尚未开放，暂不能开始识别。'
                if inventory is None:
                    self._message += ' 列表暂时无法读取，请重新核对。'
                elif inventory.generation != outcome['generation']:
                    self._message = '资源选择随后发生了变化，请重新核对当前版本。'
                if not duplicate:
                    self.resourcesChanged.emit()
        self.changed.emit()

    def _set_rows(self, rows):
        decorated = []
        for row in rows:
            contexts = [f'{season}:{mode}' for season, mode in _ACTIVATION_CONTEXTS
                       if _activation_refs(row, season, mode) is not None]
            decorated.append(dict(row, canActivate=bool(contexts), activatableContexts=contexts))
        rows = decorated
        if rows != self._rows:
            self._rows = deepcopy(rows)
            self.model.reconcile(rows)
            self.inventoryChanged.emit()

    def _problem(self, code):
        self._message = ('此为旧版图包，请获取新版资源包；原文件已保留。' if code == 'legacy-format-unsupported' else
                         '此资源尚不能在当前版本启用，请获取对应的基础结构与楼层控件包。' if code == 'resource-activation-unavailable' else
                         '操作已取消；若版本已经保存，可在列表中重新核对。' if code.endswith('-cancelled') else
                         '资源操作未完成，请检查资源包或重新核对本地版本。')
        self.errorReported.emit(dict(title='资源操作未完成', summary=self._message,
                                     raw=code, errorCode=code, action='resource:' + self._kind,
                                     phase='operation', kind='error'))
        self.changed.emit()

    @Slot()
    def cancel(self):
        if self.canCancel:
            self._cancel.set()
            self.changed.emit()

    @Slot()
    def shutdown(self):
        self._closed = True
        self.cancel()
