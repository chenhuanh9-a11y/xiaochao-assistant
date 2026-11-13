"""Qt owner for imports and their coordinated resource activation.

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
    ('s0', 'difficult'): dict(label='启用 S0 困难', objectSuffix='-s0-difficult',
        notice='可扫描结构候选，地图需手动确认。', activatedMessage='S0 困难基础结构与楼层控件已启用。'),
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


def _display_options(row):
    options = []
    for component in row['components']:
        contexts = component['contexts']
        if component['type'] != 'display' or len(contexts) != 1:
            continue
        context = tuple(contexts[0].split(':'))
        if context not in _ACTIVATION_CONTEXTS:
            continue
        season, mode = context
        name = f'{season.upper()} ' + ('困难' if mode == 'difficult' else '噩梦')
        options.append(dict(key=component['componentId'], label=f'启用 {name}作者显示',
                            context=contexts[0], available=row['status'] != 'damaged'))
    return options


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
    availabilityChanged = Signal()

    def __init__(self, resolver, parent=None, *, paths=None, engine_factories=None):
        super().__init__(parent)
        self.resolver = resolver
        self.paths = paths
        self._engine_factories = engine_factories
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.model = PackageRows()
        self._activation_model = PackageRows()
        self._rows, self._jobs = [], {}
        self._display_models = {}
        self._token, self._busy, self._closed = 0, False, False
        self._kind, self._message, self._catalog_issue = '', '', ''
        self._cancel = threading.Event()
        self._lease_provider = None
        self._lease = ''
        self._startup_notice = ''
        self._available_contexts = ()
        self._inventory_loaded = False
        self._automatic_import = False
        self._activation_plan, self._activation_choices = None, {}
        self._activation_plan_id = ''
        self._removal_plan, self._removal_plan_id = None, ''

    packageModel = Property(QObject, lambda s: s.model, constant=True)
    packageRows = Property('QVariantList', lambda s: deepcopy(s._rows), notify=inventoryChanged)
    activationOptions = Property('QVariantList', lambda s: [
        dict(context=f'{season}:{mode}', season=season, mode=mode, **metadata)
        for (season, mode), metadata in _ACTIVATION_CONTEXTS.items()], constant=True)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    availableContexts = Property('QVariantList', lambda s: list(s._available_contexts), notify=availabilityChanged)
    inventoryLoaded = Property(bool, lambda s: s._inventory_loaded, notify=changed)
    pendingKind = Property(str, lambda s: '', notify=changed)
    operationStage = Property(str, lambda s: 'cancelling' if s._busy and s._cancel.is_set() else s._kind, notify=changed)
    operationGeneration = Property(int, lambda s: s._token, notify=changed)
    message = Property(str, lambda s: '\n'.join(filter(None, (s._startup_notice, s._message))), notify=changed)
    canCancel = Property(bool, lambda s: s._busy and s._kind != 'recover' and not s._cancel.is_set(), notify=changed)
    pendingWrites = Property(bool, lambda s: s._busy and s._kind in {'save', 'activate', 'remove', 'recover'}, notify=changed)
    recoveryRequired = Property(bool, lambda s: s._catalog_issue in {
        'resource-catalog-recovery-required', 'resource-removal-recovery-required'}, notify=changed)
    activationPlanModel = Property(QObject, lambda s: s._activation_model, constant=True)
    activationPlanId = Property(str, lambda s: s._activation_plan_id, notify=changed)
    activationPlanOpen = Property(bool, lambda s: s._activation_plan is not None, notify=changed)
    activationPlanTitle = Property(str, lambda s: s._plan_title(), notify=changed)
    activationPlanSummary = Property(str, lambda s: s._plan_summary(), notify=changed)
    canCommitActivationPlan = Property(bool, lambda s: s._plan_complete() and not s._busy, notify=changed)
    removalPlanOpen = Property(bool, lambda s: s._removal_plan is not None, notify=changed)
    removalPlanId = Property(str, lambda s: s._removal_plan_id, notify=changed)
    removalPlanSummary = Property(str, lambda s: s._removal_summary(), notify=changed)

    def setLeaseProvider(self, provider):
        if self._busy or self._lease or self._activation_plan is not None or self._removal_plan is not None:
            raise RuntimeError('Cannot replace an active resource lease provider')
        self._lease_provider = provider

    @Slot(str, result=QObject)
    def displayModelForPackage(self, key):
        model = self._display_models.get(key)
        if model is None:
            model = PackageRows()
            model.setParent(self)
            row = next((row for row in self._rows if row['key'] == key), None)
            model.reconcile(_display_options(row) if row is not None else [])
            self._display_models[key] = model
        return model

    def setStartupCompatibilityReport(self, report):
        self._startup_notice = ('旧版资源操作记录已保留；新版资源从独立目录导入。'
                                if report.get('legacyPending') else '')
        self.changed.emit()

    def _begin(self, kind, function):
        if (self._closed or self._busy or (self._activation_plan is not None and kind != 'activate')
                or (self._removal_plan is not None and kind != 'remove')):
            return False
        self._token += 1
        self._busy, self._kind = True, kind
        self._cancel = threading.Event()
        self._message = {'list': '正在读取已保存版本…', 'refresh': '正在校验已保存文件…',
                         'save': '正在校验并导入资源包…',
                         'activate': '正在校验并启用所选资源…',
                         'plan': '正在核对基础版本与已选作者的配套关系…',
                         'inspect-remove': '正在核对删除范围与受影响资源…',
                         'remove': '正在删除已确认的资源文件…',
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
        self._begin('refresh' if verify else 'list', lambda cancelled:
                    self._read_inventory(verify=verify, cancelled=cancelled))

    def _read_inventory(self, *, verify=False, cancelled=None):
        from imported_resource_inventory import list_saved_versions
        from client_imported_admission import available_base_contexts
        inventory = list_saved_versions(self.resolver, verify=verify, cancelled=cancelled)
        available = ()
        if not inventory.catalog_issue:
            from imported_resource_catalog import ResourceCatalogError
            snapshot = self.resolver.snapshot()
            if snapshot.generation != inventory.generation:
                raise ResourceCatalogError('resource-inventory-superseded')
            available = available_base_contexts(snapshot, cancelled=cancelled)
        return dict(inventory=inventory, availableContexts=available)

    @Slot(str, str, result=bool)
    def contextAvailable(self, season, mode):
        return f'{season}:{mode}' in self._available_contexts

    def _set_available_contexts(self, contexts):
        contexts = tuple(contexts)
        if contexts != self._available_contexts:
            self._available_contexts = contexts
            self.availabilityChanged.emit()

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
            from client_imported_admission import admit_import
            from imported_resource_catalog import ResourceCatalogError
            from imported_resource_removal import assert_removal_ready
            assert_removal_ready(self.resolver.root)
            sha = None
            if expected is not None:
                if (set(expected) != {'packageId', 'bytes', 'sha256'}
                        or type(expected['bytes']) is not int or expected['bytes'] <= 0
                        or not isinstance(expected['sha256'], str)
                        or not re.fullmatch('[0-9a-f]{64}', expected['sha256'])):
                    raise ResourceCatalogError('resource-download-binding-invalid')
                sha = expected['sha256']
                inspected = inspect_archive(path, expected_archive_sha256=sha, cancelled=cancelled)
                if inspected.package_id != expected['packageId'] or inspected.archive_bytes != expected['bytes']:
                    raise ResourceCatalogError('resource-download-binding-invalid')
            admission = {}
            def validate_components(views):
                assert_removal_ready(self.resolver.root)
                from resource_package_types import infer_package_suffix, validate_package_filename
                components = [view.component for view in views]
                # ZIP remains an accepted transport name, but imports still
                # have one base/route role and one automatic activation path.
                infer_package_suffix(components)
                validate_package_filename(path, components)
                admission['contexts'] = admit_import(views, self.resolver, cancelled=cancelled)
                admission['displayRefs'] = tuple(view.component_ref for view in views
                                                 if view.component['type'] == 'display')
            summary = save_archive(path, self.resolver.root, expected_archive_sha256=sha, cancelled=cancelled,
                                   validate_components=validate_components)
            # A completed save is reported even if a cancellation arrives during
            # the following read. The immutable version may already be present.
            try:
                inventory = self._read_inventory()
            except (OSError, ValueError, RuntimeError):
                inventory = dict(inventory=None, availableContexts=())
            return dict(summary=summary, baseContexts=admission['contexts'],
                        displayRefs=admission['displayRefs'], **inventory)
        return self._begin('save', save)

    @Slot(str, str, str, result=bool)
    def activatePackage(self, key, season, mode):
        if self._closed or self._busy or self._activation_plan is not None:
            return False
        row = next((row for row in self._rows if row['key'] == key), None)
        refs = _activation_refs(row, season, mode) if row is not None else None
        if refs is None:
            self._problem('resource-activation-unavailable')
            return False
        if self.paths is None or self._lease_provider is None:
            self._problem('resource-activation-unavailable')
            return False
        def prepare(cancelled):
            from client_imported_activation_plan import plan_context_activation
            return plan_context_activation(self.resolver, *refs, season=season, mode=mode,
                                           cancelled=cancelled)
        return self._begin('plan', prepare)

    def _activate_imported_base(self, contexts):
        if self.paths is None or self._lease_provider is None:
            self._message = '资源版本已保存，但当前资源服务未配置启用入口。'
            self._problem('resource-activation-unavailable')
            self._message = '资源版本已保存，但未启用。' + self._message
            self.importCompleted.emit('基础资源未启用', self._message)
            self.changed.emit()
            return
        self._automatic_import = 'base'
        def prepare(cancelled):
            from client_imported_activation_plan import plan_context_activations
            return plan_context_activations(self.resolver, contexts, cancelled=cancelled)
        if not self._begin('plan', prepare):
            self._finish_automatic_import(False)

    def _finish_automatic_import(self, success):
        if self._automatic_import:
            name = '作者路线' if self._automatic_import == 'display' else '基础资源'
            self._automatic_import = False
            if not success:
                self._message = '新导入版本已保存，但未启用。' + self._message
            self.importCompleted.emit(name + ('已启用' if success else '未启用'), self._message)
            self.changed.emit()

    def _activate_imported_displays(self, refs):
        self._automatic_import = 'display'
        def activate(cancelled):
            from client_imported_activation import activate_displays
            return activate_displays(self.resolver, self.paths, refs, cancelled=cancelled)
        if not self._activate(activate, 'display'):
            self._finish_automatic_import(False)

    def _plan_title(self):
        plan = self._activation_plan
        if plan is not None and hasattr(plan, 'contexts'):
            return '基础资源配套更新'
        return (f'{plan.season.upper()} ' + ('困难' if plan.mode == 'difficult' else '噩梦')
                + '资源配套更新') if plan is not None else ''

    def _plan_complete(self):
        plan = self._activation_plan
        return plan is not None and all(row.key in self._activation_choices for row in plan.rows)

    def _plan_summary(self):
        plan = self._activation_plan
        if plan is None:
            return ''
        contexts = getattr(plan, 'contexts', (plan,))
        lines = [f'{item.season.upper()} ' + ('困难' if item.mode == 'difficult' else '噩梦')
                 + f'：基础结构 {item.base_ref.resource_version} · 楼层控件 {item.controls_ref.resource_version}'
                 for item in contexts]
        for row in plan.rows:
            selected = self._activation_choices.get(row.key)
            choice = next((item for item in row.choices if item.key == selected), None)
            lines.append(f'{row.label}：{choice.label if choice is not None else "请选择处理方式"}')
        return '\n'.join(lines)

    def _show_activation_plan(self, plan):
        self._activation_plan = plan
        self._activation_plan_id = str(self._token)
        self._activation_choices = {}
        self._activation_model.reconcile([dict(key=row.key, label=row.label, scope=row.scope,
            choices=[dict(key=choice.key, label=choice.label) for choice in row.choices], choice='')
            for row in plan.rows])
        self._message = '请为受影响作者选择配套版本，或明确停用该组件；当前资源尚未改变。'
        self.changed.emit()

    def _clear_activation_plan(self):
        self._activation_plan, self._activation_plan_id = None, ''
        self._activation_choices = {}
        self._activation_model.reconcile([])

    @Slot(str, str, str, result=bool)
    def chooseActivationAuthor(self, plan_id, row_id, choice_id):
        if self._closed or self._busy or plan_id != self._activation_plan_id or self._activation_plan is None:
            return False
        row = next((row for row in self._activation_plan.rows if row.key == row_id), None)
        if row is None or not any(choice.key == choice_id for choice in row.choices):
            return False
        if self._activation_choices.get(row_id) != choice_id:
            self._activation_choices[row_id] = choice_id
            rows = [dict(item['modelData'], choice=self._activation_choices.get(item['key'], ''))
                    for item in self._activation_model.rows]
            self._activation_model.reconcile(rows)
            self.changed.emit()
        return True

    @Slot(str, result=bool)
    def commitActivationPlan(self, plan_id):
        if (self._closed or self._busy or plan_id != self._activation_plan_id
                or not self._plan_complete()):
            return False
        return self._submit_activation_plan(self._activation_plan, dict(self._activation_choices))

    def _submit_activation_plan(self, plan, choices):
        updates, removed = [], []
        for row in plan.rows:
            choice = next(item for item in row.choices if item.key == choices[row.key])
            (removed if choice.display_ref is None else updates).append(
                row.old_ref if choice.display_ref is None else choice.display_ref)
        updates, removed = tuple(updates), tuple(removed)
        def activate(cancelled):
            if hasattr(plan, 'contexts'):
                from client_imported_activation import activate_contexts
                contexts = tuple((item.season, item.mode, item.base_ref, item.controls_ref) for item in plan.contexts)
                return activate_contexts(self.resolver, self.paths, contexts,
                    display_refs=updates, remove_display_refs=removed,
                    expected_catalog_stamp=plan.catalog_stamp, cancelled=cancelled)
            from client_imported_activation import activate_context
            outcome = activate_context(self.resolver, self.paths, plan.base_ref, plan.controls_ref,
                season=plan.season, mode=plan.mode, display_refs=updates, remove_display_refs=removed,
                expected_catalog_stamp=plan.catalog_stamp, cancelled=cancelled)
            return dict(outcome, context=f'{plan.season}:{plan.mode}')
        accepted = self._activate(activate, 'base')
        if accepted:
            self._clear_activation_plan()
            self.changed.emit()
        elif not self._activation_plan:
            self._finish_automatic_import(False)
        return accepted

    @Slot(str, result=bool)
    def cancelActivationPlan(self, plan_id):
        if self._activation_plan is None or plan_id != self._activation_plan_id:
            return False
        self._clear_activation_plan()
        self._message = '已取消配套更新，当前资源选择未改变。'
        self._finish_automatic_import(False)
        self.changed.emit()
        return True

    @Slot()
    def dismissActivationPlan(self):
        if self._activation_plan is not None:
            self.cancelActivationPlan(self._activation_plan_id)
        elif self._busy and self._kind == 'plan':
            self.cancel()

    @Slot(str, str, result=bool)
    def activateDisplayComponent(self, key, component_id):
        from imported_resource_packages import ComponentRef
        if self._closed or self._busy or self._activation_plan is not None:
            return False
        row = next((row for row in self._rows if row['key'] == key), None)
        option = next((option for option in _display_options(row)
                       if option['key'] == component_id and option['available']), None) if row else None
        if option is None:
            self._problem('resource-activation-unavailable')
            return False
        ref = ComponentRef(row['packageId'], row['resourceVersion'], component_id, row['manifestSha256'])
        def activate(cancelled):
            from client_imported_activation import activate_display
            return activate_display(self.resolver, self.paths, ref, cancelled=cancelled)
        return self._activate(activate, 'display')

    def _activate(self, operation, role):
        return self._mutate(operation, 'activate', role)

    def _mutate(self, operation, kind, role=''):
        if self._closed or self._busy:
            return False
        if self.paths is None or self._lease_provider is None:
            self._problem('resource-activation-unavailable')
            return False
        self._lease = self._lease_provider.acquireResourceLease()
        if not self._lease:
            self._message = '请先结束本局，并等待后台任务完成，再修改资源。'
            self.changed.emit()
            return False

        def activate(cancelled):
            outcome, failure, retry = None, None, False
            try:
                from imported_resource_removal import assert_removal_ready
                assert_removal_ready(self.resolver.root)
                outcome = operation(cancelled)
                if kind == 'activate' and outcome['status'] == 'already-active' and self._engine_factories is not None:
                    keys = outcome.get('contexts') or [outcome['context']]
                    retry = any(self._engine_factories.needs_display_revalidation(key.split(':')[1],
                        season=key.split(':')[0]) for key in keys)
            except Exception as error:
                failure = _failure(error)
            # Observe the actual final state after success, cancellation or a
            # failed rollback. A committed change remains success if this read
            # fails; a retained pending operation must expose recovery.
            try:
                inventory = self._read_inventory()
            except (OSError, ValueError, RuntimeError):
                inventory = dict(inventory=None, availableContexts=())
            return dict(activated=outcome, activationRole=role, retryPreparation=retry,
                        operationError=failure, **inventory)

        if self._begin(kind, activate):
            return True
        self._release_lease()
        return False

    @Slot(str, result=bool)
    def requestRemoval(self, key):
        if self._closed or self._busy or self._activation_plan is not None or self._removal_plan is not None:
            return False
        row = next((row for row in self._rows if row['key'] == key), None)
        if row is None or not row.get('canInspectRemoval') or self.recoveryRequired:
            self._problem('resource-removal-unavailable')
            return False
        def inspect(cancelled):
            from imported_resource_removal import inspect_saved_version_removal
            return inspect_saved_version_removal(self.resolver, row['packageId'],
                row['resourceVersion'], row['manifestSha256'], cancelled=cancelled)
        return self._begin('inspect-remove', inspect)

    def _removal_summary(self):
        plan = self._removal_plan
        if plan is None:
            return ''
        lines = [f"{plan['packageId']} · 版本 {plan['resourceVersion']}",
                 f"将删除 {plan['fileCount']} 个文件，合计 {plan['bytes'] / 1048576:.2f} MiB。"]
        if plan['affectedContexts']:
            names = [key.split(':')[0].upper() + (' 困难' if key.endswith(':difficult') else ' 噩梦')
                     for key in plan['affectedContexts']]
            lines.append('将停用对应模式：' + '、'.join(names) + '；重新导入基础结构后可恢复使用。')
        if plan['removedDisplays']:
            lines.append(f"将停用 {len(plan['removedDisplays'])} 个作者显示组件。其他作者包文件会保留。")
        if plan['preserved']:
            lines.append(f"将保留 {len(plan['preserved'])} 个未知或已改动的项目，不会删除源资源包。")
        return '\n'.join(lines)

    @Slot(str, result=bool)
    def confirmRemoval(self, plan_id):
        if (self._closed or self._busy or self._removal_plan is None
                or plan_id != self._removal_plan_id):
            return False
        plan = self._removal_plan
        def remove(cancelled):
            from imported_resource_removal import remove_saved_version
            from client_imported_activation import validate_selected_resources
            return remove_saved_version(self.resolver, plan, cancelled=cancelled,
                validate_selection=lambda document: validate_selected_resources(
                    self.resolver, self.paths, document, cancelled=cancelled))
        if not self._mutate(remove, 'remove'):
            return False
        self._clear_removal_plan()
        self.changed.emit()
        return True

    def _clear_removal_plan(self):
        self._removal_plan, self._removal_plan_id = None, ''

    @Slot(str, result=bool)
    def cancelRemoval(self, plan_id):
        if self._removal_plan is None or plan_id != self._removal_plan_id:
            return False
        self._clear_removal_plan()
        self._message = '已取消删除，资源文件和启用状态未改变。'
        self.changed.emit()
        return True

    @Slot()
    def requestRecovery(self):
        if (self._closed or self._busy or self._activation_plan is not None or self._removal_plan is not None
                or not self.recoveryRequired or self._lease_provider is None):
            return
        self._lease = self._lease_provider.acquireResourceLease()
        if not self._lease:
            self._message = '请先结束本局，并等待后台任务完成，再处理资源切换。'
            self.changed.emit()
            return
        def recover(cancelled):
            from resource_transaction import resource_write_lock
            from imported_resource_activation import recover_catalog_locked
            from imported_resource_removal import recover_saved_version_removal_locked, removal_recovery_required
            with resource_write_lock(self.resolver.root):
                outcome = (recover_saved_version_removal_locked(self.resolver.root)
                           if removal_recovery_required(self.resolver.root)
                           else recover_catalog_locked(self.resolver.root))
            return dict(recovered=outcome, **self._read_inventory())
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
        if (kind in {'list', 'refresh'} and error is not None
                and error['code'] in {'resource-inventory-cancelled', 'import-cancelled'}):
            self._message = '检查已取消，保留上次已核对的资源状态。'
            self.changed.emit()
            return
        if kind == 'plan':
            if self._cancel.is_set():
                self._message = '已取消配套核对，当前资源选择未改变。'
                self._finish_automatic_import(False)
                self.changed.emit()
            elif error:
                self._problem(error['code'])
                self._finish_automatic_import(False)
            elif value.rows:
                self._show_activation_plan(value)
            else:
                self._submit_activation_plan(value, {})
            return
        if kind == 'inspect-remove':
            if self._cancel.is_set():
                self._message = '已取消删除核对，资源文件未改变。'
            elif error:
                self._problem(error['code'])
            else:
                self._removal_plan, self._removal_plan_id = value, str(token)
                self._message = '请核对删除范围；确认前不会改变资源。'
            self.changed.emit()
            return
        if value is not None:
            inventory = value['inventory']
            self._inventory_loaded = inventory is not None
            self._catalog_issue = inventory.catalog_issue or '' if inventory is not None else 'resource-inventory-unavailable'
            self._set_available_contexts(value.get('availableContexts', ()))
            self._set_rows(inventory.rows if inventory is not None else [])
            error = error or value.get('operationError')
        if error:
            if kind in {'list', 'refresh'}:
                self._inventory_loaded = False
                self._set_rows([])
                self._set_available_contexts(())
            self._problem(error['code'])
        else:
            self._message = ('资源切换尚未完成，请处理后再使用。' if self.recoveryRequired else
                             '资源选择记录无法读取；已保存的版本仍予保留。' if self._catalog_issue else
                             '已读取保存版本。' if kind == 'list' else '文件完整性校验已完成。')
            summary = value.get('summary')
            if summary is not None:
                contexts = value.get('baseContexts', ())
                display_refs = value.get('displayRefs', ())
                if contexts or display_refs:
                    if self._cancel.is_set():
                        self._message = '资源版本已保存；已取消启用，当前资源选择未改变。'
                        self.importCompleted.emit('启用已取消', self._message)
                        self.changed.emit()
                    elif contexts:
                        self._activate_imported_base(contexts)
                    else:
                        self._activate_imported_displays(display_refs)
                    return
                duplicate = summary.status == 'already-saved'
                self._message = ('该版本已经保存，启用状态未改变。' if duplicate else
                                 '资源版本已保存，尚未启用。')
                if inventory is None:
                    self._message += ' 列表暂时无法读取，请重新核对。'
                self.importCompleted.emit('版本已保存', self._message)
            if kind == 'recover':
                self._message = '未完成的资源切换已处理，请重新核对可用资源。'
                self.resourcesChanged.emit()
            if kind == 'remove':
                outcome = value['activated']
                self._message = ('资源包已部分删除；未知或改动过的文件已保留。'
                                 if outcome['status'] == 'version-partially-removed'
                                 else '资源包已删除。')
                if inventory is None:
                    self._message += ' 列表暂时无法读取，请重新核对。'
                self.resourcesChanged.emit()
            if kind == 'activate':
                outcome = value['activated']
                duplicate = outcome['status'] == 'already-active'
                contexts = tuple(tuple(key.split(':')) for key in (outcome.get('contexts') or [outcome['context']]))
                context = contexts[0]
                display = value['activationRole'] == 'display'
                name = f'{context[0].upper()} ' + ('困难' if context[1] == 'difficult' else '噩梦')
                self._message = (f'{name}：此版本已经启用，资源选择未改变。' if duplicate
                                 else f'{name}作者显示组件已启用。' if display
                                 else _ACTIVATION_CONTEXTS[context]['activatedMessage'])
                if len(contexts) > 1 and not display:
                    self._message = '基础结构与楼层控件已一并启用：' + '、'.join(
                        item[0].upper() + (' 困难' if item[1] == 'difficult' else ' 噩梦') for item in contexts)
                if value['retryPreparation']:
                    self._message = '此版本已重新校验，正在重新准备显示资源。'
                if display:
                    self._message += ' 配准由作者提供，尚未复核。'
                elif any(item[0] == 's1' for item in contexts):
                    self._message += ' 自动楼层识别尚未验证，暂不可用。'
                if not display and ('s0', 'difficult') in contexts:
                    self._message += ' ' + _ACTIVATION_CONTEXTS[('s0', 'difficult')]['notice']
                if inventory is None:
                    self._message += ' 列表暂时无法读取，请重新核对。'
                elif inventory.generation != outcome['generation']:
                    self._message = '资源选择随后发生了变化，请重新核对当前版本。'
                if not duplicate or value['retryPreparation']:
                    self.resourcesChanged.emit()
                self._finish_automatic_import(True)
        if error and kind == 'activate':
            self._finish_automatic_import(False)
        if error and kind in {'activate', 'remove', 'recover'} and self.recoveryRequired:
            self.resourcesChanged.emit()
        self.changed.emit()

    def _set_rows(self, rows):
        decorated = []
        for row in rows:
            contexts = [f'{season}:{mode}' for season, mode in _ACTIVATION_CONTEXTS
                       if _activation_refs(row, season, mode) is not None]
            decorated.append(dict(row, canActivate=bool(contexts), activatableContexts=contexts))
        rows = decorated
        current_keys = {row['key'] for row in rows}
        for key in tuple(self._display_models):
            if key not in current_keys:
                self._display_models.pop(key).deleteLater()
        for row in rows:
            if row['key'] in self._display_models:
                self._display_models[row['key']].reconcile(_display_options(row))
        if rows != self._rows:
            self._rows = deepcopy(rows)
            self.model.reconcile(rows)
            self.inventoryChanged.emit()

    def _problem(self, code):
        self._message = ('旧图包格式已停用，请获取新版资源包；已有文件已保留。' if code == 'legacy-format-unsupported' else
                         '请选择星图结构包（.xingtu-base）或作者路线包（.xingtu-route）。' if code == 'resource-package-extension-unsupported' else
                         '文件后缀与包内类型不一致，请核对结构包和作者路线包。' if code == 'resource-package-extension-mismatch' else
                         '此文件的资源类型不能作为结构包或作者路线包导入。' if code == 'resource-package-kind-unsupported' else
                         '请先导入并启用对应模式的基础结构包，再导入作者图。' if code == 'resource-display-base-context-not-active' else
                         '此结构包适用于旧整合算法，请导入适配原算法的新结构包；现有文件和作者包已保留。' if code == 'original-qt-base-required' else
                         '此作者图依赖的基础结构版本尚未启用，请先导入对应基础包。' if code == 'resource-display-base-version-mismatch' else
                         '基础结构包须包含同一模式的楼层控件，请获取完整基础包。' if code == 'resource-import-base-controls-pair-required' else
                         '作者显示依赖其他基础版本，请先核对配套资源；当前选择已保留。' if code == 'resource-dependency-not-selected' else
                         '审阅期间资源选择已改变，请重新核对配套版本；当前选择已保留。' if code == 'resource-catalog-superseded' else
                         '原作者版本的声明无法读取，暂不能确认替换范围；当前选择已保留。' if code == 'resource-previous-display-metadata-unavailable' else
                         '当前模式已有同名作者显示，暂不能同时启用；当前选择已保留。' if code == 'resource-display-profile-conflict' else
                         '此资源尚不能在当前版本启用，请获取对应的基础结构与楼层控件包。' if code == 'resource-activation-unavailable' else
                         '此版本暂无可核对的删除记录，请检查资源清单。' if code == 'resource-removal-unavailable' else
                         '资源删除尚未完成，请先处理未完成的操作。' if code == 'resource-removal-recovery-required' else
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
        self._clear_activation_plan()
        self._clear_removal_plan()
        self.cancel()
