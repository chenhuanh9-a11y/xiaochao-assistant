"""Parent-leased legacy/native transaction for the current Setup helper.

Uses the existing journal, native observation and real multi-root adapter,
not an installer's claim of success. There is no CLI fixture switch.
Native install, asset publication and old-instance retirement are separate
bound stages. Only verified retirement may finish the legacy journal.
"""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import copy
import time
import uuid

import installer_legacy_bridge as bridge
import installer_native_observation as native
from installer_resource_lease import installer_coordinator_scope
from installer_upgrade_coordinator import native_context
from installer_upgrade_journal import (
    InstallerUpgradeJournal, _bounded, _parse, _read, _write_new, read_status,
)
from resource_transaction import _safe
from installer_legacy_retirement import LegacyRetirement, WindowsObjects
from resource_transaction import rename_no_replace


def complete_asset_transfer():
    from installer_legacy_assets import CompleteAssetTransfer
    return CompleteAssetTransfer()


class LegacyHandoff:
    """All mutations require the caller's verified borrowed Setup lease.

    Asset adapter operations (each receives bindings and a monotonic deadline):
      prepare(source_program, prepared, **bindings) -> nonempty manifest dict
      verify_prepared(manifest, source_program, prepared, **bindings) -> True
      commit(manifest, prepared, install_root, **bindings) -> ignored result
      verify_committed(manifest, install_root, **bindings) -> True

    The adapter owns complete multi-root inventory/admission and durable,
    no-overwrite publication, including retry after a lost commit receipt.
    Verification must read current objects, not trust a result/status Boolean.
    The default factory supplies the production PR151 multi-root engine.
    """
    def __init__(self, args, target, lease, owner_sid, *, assets=None,
                 observer=native, registry_reader=None, source_guard=None,
                 scope=installer_coordinator_scope, retirement=None):
        self.args, self.target, self.lease, self.owner = args, target, lease, owner_sid
        self.root, self.source = bridge._path(args.install_root), bridge._path(args.legacy_source)
        if (self.root == self.source or self.root.is_relative_to(self.source)
                or self.source.is_relative_to(self.root)):
            raise bridge.LegacyBridgeError('overlapping-install-roots')
        self.prepared = _safe(self.root / '.legacy-upgrade-prepared')
        self.plan_path = _safe(self.prepared / 'handoff-plan.json')
        self.intent_path = _safe(self.root / '.legacy-upgrade-intent.json')
        self.assets, self.observer, self.scope = assets or complete_asset_transfer(), observer, scope
        self.retirement = retirement or LegacyRetirement()
        if self.args.session_id is None:
            if self.intent_path.exists():
                self.args.session_id = _parse(_read(self.intent_path)[0])['sessionId']
            else:
                prior = read_status(self.root, owner_sid=self.owner)
                complete = self.root / 'work/installer-upgrades/legacy-recovery.json'
                self.args.session_id = (prior['sessionId'] if prior['status'] != 'not-needed' else
                    _parse(_read(complete)[0])['sessionId'] if complete.exists() else str(uuid.uuid4()))
        self.registry_reader = registry_reader
        self.source_guard = source_guard or bridge._closed_source
        self.deadline = time.monotonic() + 120
        self.journal = self.plan = self.context = None

    @property
    def history(self):
        # The journal validates this UUID and its directory/root identities
        # before a path under it can ever grant cleanup authority.
        from installer_upgrade_journal import _uuid
        return _safe(self.root / 'work/installer-upgrades' / _uuid(self.args.session_id))

    @property
    def archive(self):
        return self.history / 'legacy-plan.json'

    def _deadline(self):
        if time.monotonic() >= self.deadline:
            raise bridge.LegacyBridgeError('legacy-operation-deadline-exceeded')

    def _asset(self, method, *values):
        self._deadline()
        result = getattr(self.assets, method)(*copy.deepcopy(values),
            session_id=self.args.session_id, owner_sid=self.owner,
            target_identity=copy.deepcopy(self.target), lease=self.lease,
            deadline=self.deadline)
        self._deadline()
        return result

    @contextmanager
    def _source(self, expected=None):
        identity, tool = bridge.probe_source(self.source, registry_reader=self.registry_reader)
        if identity['sourceKind'] != 'qtifw':
            raise bridge.LegacyBridgeError('inno-upgrade-must-use-registered-root')
        if expected is not None and identity != expected:
            raise bridge.LegacyBridgeError('source-identity-changed')
        program = Path(identity['installRoot'])
        guard = (nullcontext() if getattr(self.assets, 'owns_source_guard', False)
                 else self.source_guard(self.source, tool, program))
        with guard:
            if bridge.probe_source(self.source, registry_reader=self.registry_reader)[0] != identity:
                raise bridge.LegacyBridgeError('source-identity-changed')
            yield identity, program
            if bridge.probe_source(self.source, registry_reader=self.registry_reader)[0] != identity:
                raise bridge.LegacyBridgeError('source-identity-changed')

    def _bindings(self, identity):
        return dict(sourceRoot=str(self.source), sourceIdentity=identity,
                    targetRoot=str(self.root),
                    targetRootIdentity=[self.root.stat().st_dev, self.root.stat().st_ino],
                    ownerSid=self.owner, sessionId=self.args.session_id,
                    targetIdentity=self.target, reportSha256=self.args.target_report_sha256,
                    programInventorySha256=self.args.program_inventory_sha256,
                    nativeSelection={name: getattr(self.args, name) for name in (
                        'group_shortcut', 'uninstall_shortcut', 'desktop_shortcut',
                        'group_enabled', 'desktop_enabled', 'restore_watcher')})

    def _load_plan(self, identity):
        path = self.archive if self.archive.exists() else self.plan_path
        plan = _parse(_read(path)[0])
        if (type(plan) is not dict or set(plan) != set(self._bindings(identity)) | {'assets', 'retirement'}
                or any(plan.get(key) != value for key, value in self._bindings(identity).items())
                or type(plan['assets']) is not dict or not plan['assets']):
            raise bridge.LegacyBridgeError('legacy-plan-binding-mismatch')
        digest = bridge._digest(plan)
        if self.args.plan_sha256 is not None and self.args.plan_sha256 != digest:
            raise bridge.LegacyBridgeError('legacy-plan-changed')
        self.plan = plan
        self.journal = InstallerUpgradeJournal(self.root, owner_sid=self.owner,
            session_id=self.args.session_id, target_identity=self.target,
            report_sha256=self.args.target_report_sha256, plan_sha256=digest,
            restore_watcher=self.args.restore_watcher, resource_action='legacy')

    def _intent(self, identity):
        bindings = self._bindings(identity)
        if self.intent_path.exists():
            if _parse(_read(self.intent_path)[0]) != bindings:
                raise bridge.LegacyBridgeError('legacy-plan-binding-mismatch')
        else:
            self._fresh_target()
            _write_new(self.intent_path, _bounded(bindings))

    def _scope(self):
        return self.scope(self.root, self.lease, self.args.session_id, validate=self.journal.status)

    def _prepared(self, state):
        with self._scope() if state['pending'] else nullcontext():
            return self._asset('verify_prepared', self.plan['assets'],
                               Path(self.plan['sourceIdentity']['installRoot']), self.prepared) is True

    def _native(self, state, evidence):
        if (self.context['target_identity'] != state['targetIdentity']
                or self.context['target_report_sha256'] != state['reportSha256']):
            raise bridge.LegacyBridgeError('legacy-native-binding-mismatch')
        if self.context.get('retiring_shortcuts', []) != self._retiring_shortcuts():
            raise bridge.LegacyBridgeError('legacy-shortcut-retirement-binding-mismatch')
        self.observer.verify_native_completion(evidence, self.root, **self.context)
        return True

    def _retiring_shortcuts(self):
        if self.plan is None or not self.plan['retirement'].get('supported'):
            return []
        original = {str(Path(path)).casefold(): stamp
                    for path, stamp in self.plan['retirement']['shortcuts'].items()}
        selection, result = self.plan['nativeSelection'], []
        for name, enabled in (('group_shortcut', 'group_enabled'),
                              ('uninstall_shortcut', 'group_enabled'),
                              ('desktop_shortcut', 'desktop_enabled')):
            path = native._path(selection[name])
            stamp = original.get(path.casefold())
            if not selection[enabled] and stamp is not None:
                result.append(dict(path=path, file=stamp, planSha256=bridge._digest(self.plan)))
        return sorted(result, key=lambda row: row['path'].casefold())

    def _native_context(self):
        result = native_context(self.args, self.target)
        deferred = self._retiring_shortcuts()
        if deferred:
            result['retiring_shortcuts'] = deferred
        return result

    def _committed(self):
        return self._asset('verify_committed', self.plan['assets'], self.root) is True

    def _receipt(self, state, *, committed=False, retired=False):
        self._deadline()
        # Installer journal completion is intentionally withheld until a
        # separately validated historical retirement implementation exists.
        complete = retired and state['status'] == 'completed' and not state['pending']
        status = ('committed' if complete else
                  'new-install-verified-old-entry-pending' if committed else state['status'])
        return dict(status=status, installerStatus=state['status'],
            sessionId=state['sessionId'], planSha256=state['planSha256'],
            nativeInstalled=state['status'] in {'native-complete', 'completed'},
            migrationCommitted=committed, oldEntryRetired=retired, sourceDeleted=retired,
            installerFinalized=complete, upgradeComplete=complete,
            resourceStatus=self.plan['assets'].get('resourceStatus', 'unverified'),
            resourcesActivated=False,
            remaining='' if complete else self.plan['retirement'].get('reason', 'old-instance-retirement-pending'))

    def _temporary_plan(self, manifest):
        """Pin our private preparation before native start, excluding payloads
        that will be moved to their original, separately verified destinations.
        The small handoff plan itself is moved to permanent history, not deleted.
        """
        import legacy_migration as migration
        journal = self.prepared / 'legacy-migration.json'
        state = migration._load_journal(journal)
        if (state['status'] != 'complete' or state.get('partials')
                or state['files'] != migration._private_files(manifest['files'])):
            raise bridge.LegacyBridgeError('legacy-preparation-journal-unverified')
        payloads = {str(self.prepared / 'work' / name) for name in manifest.get('files', {})}
        directories = {self.prepared, self.prepared / 'work'}
        for path in payloads:
            directories.update(p for p in Path(path).parents if p == self.prepared or p.is_relative_to(self.prepared))
        with WindowsObjects(self.owner, self.deadline) as objects:
            tree = objects.tree(self.prepared)
            for path, stamp in tree.items():
                if (stamp['directory'] and Path(path) not in directories
                        or not stamp['directory'] and path not in payloads and Path(path) != journal):
                    raise bridge.LegacyBridgeError('legacy-unlisted-preparation-object')
            result = {path: stamp for path, stamp in tree.items() if path not in payloads}
            result[str(self.intent_path)] = objects.add(self.intent_path)
            return result

    def _published(self, *_):
        return self._asset('verify_published', self.plan['assets'], self.root) is True

    def _current_retirement_target(self):
        state = self.journal.assert_can_forward(verify_native=self._native)
        if not self._published():
            raise bridge.LegacyBridgeError('legacy-asset-commit-unverified')
        return state

    def _verify_retired(self, *_):
        return self.retirement.verify_finished(self.plan['retirement'], self.root,
            owner_sid=self.owner, deadline=self.deadline,
            native_shortcuts=self.context['shortcut_targets'])

    def _retire(self):
        state = self.journal.status()
        self.context = self.observer.resume_context_from_evidence(state['nativeEvidence'], self.root)
        self._current_retirement_target()
        record = dict(planSha256=state['planSha256'], nativeEvidenceSha256=state['nativeEvidenceSha256'],
                      phase='old-entry-pending')
        if _parse(_read(self.history / 'legacy-commit.json')[0]) != record:
            raise bridge.LegacyBridgeError('legacy-commit-record-changed')
        pointer = _safe(self.root / 'work/installer-upgrades/legacy-recovery.json')
        value = dict(sessionId=self.args.session_id, sourceRoot=str(self.source), planSha256=state['planSha256'])
        if pointer.exists():
            if _parse(_read(pointer)[0]) != value:
                raise bridge.LegacyBridgeError('legacy-completion-pointer-changed')
        else:
            _write_new(pointer, _bounded(value))
        # Retain this SAME small plan object in permanent history before any
        # retirement. Once archived no old registration/tool is needed to load it.
        if not self.archive.exists():
            rename_no_replace(self.plan_path, self.archive)
        elif self.plan_path.exists():
            raise bridge.LegacyBridgeError('legacy-plan-replaced')
        protected = [self.root / name for name in self.context['program_inventory']]
        protected += [self.root / 'work' / name for name in self.plan['assets']['files']]
        protected += [Path(self.context['uninstall_exe']), Path(self.context['uninstall_exe']).with_suffix('.dat')]
        protected += [Path(s['path']) for s in self.context['shortcut_targets'] if s['enabled']]
        scope = self._scope() if state['pending'] else nullcontext()
        with scope:
            self.retirement.execute(self.plan['retirement'], self.root, self.history,
                owner_sid=self.owner, deadline=self.deadline,
                verify_current=self._current_retirement_target,
                registrations=getattr(self.assets, 'registrations', lambda: set()),
                native_shortcuts=self.context['shortcut_targets'], protected=protected)
            state = self.journal.finish_legacy(verify_native=self._native,
                verify_resource=self._published, verify_retirement=self._verify_retired)
        return self._receipt(state, committed=True, retired=True)

    def _pre_native_target(self):
        # Existing preparation is allowed only through the verified intent;
        # arbitrary nonempty targets remain rejected on every retry/start.
        allowed = {'.legacy-upgrade-intent.json', 'work', 'work/map-packages',
                   'work/map-packages/import.lock', 'work/installer-upgrades',
                   'work/pending-installer-upgrade.json'}
        for path in self.root.rglob('*'):
            _safe(path)
            name = path.relative_to(self.root).as_posix()
            if (name in allowed or name == '.legacy-upgrade-prepared'
                    or name.startswith('.legacy-upgrade-prepared/')
                    or name.startswith('work/installer-upgrades/' + self.args.session_id + '/')
                    or name == 'work/installer-upgrades/' + self.args.session_id
                    or name.startswith('work/installer-upgrades/.initializing-')):
                continue
            raise bridge.LegacyBridgeError('fresh-target-required')

    def _fresh_target(self):
        # Setup's already-held import lock is the only permitted pre-existing
        # content. Never turn this seam into admission of an unknown nonempty
        # installation, including an unbound old preparation directory.
        allowed = {'work', 'work/map-packages', 'work/map-packages/import.lock'}
        for path in self.root.rglob('*'):
            _safe(path)
            name = path.relative_to(self.root).as_posix()
            if name not in allowed or (name != 'work/map-packages/import.lock' and not path.is_dir()):
                raise bridge.LegacyBridgeError('fresh-target-required')

    def execute(self):
        action = self.args.action.removeprefix('legacy-')
        self._deadline()
        # Destructive forward recovery uses the original journal-bound evidence.
        # Calling probe_source here would require an object we intentionally
        # removed, or accidentally adopt a later same-named installation.
        if action == 'retire' or self.archive.exists() and action in {'resume', 'status'}:
            path = self.archive if self.archive.exists() else self.plan_path
            original = _parse(_read(path)[0])
            self._load_plan(original['sourceIdentity'])
            if action == 'status':
                state = self.journal.status()
                if state['status'] != 'completed':
                    return self._receipt(state)
                self.context = self.observer.resume_context_from_evidence(state['nativeEvidence'], self.root)
                self._current_retirement_target()
                self._verify_retired()
                return self._receipt(state, committed=True, retired=True)
            if action != 'status' and self.args.plan_sha256 != bridge._digest(self.plan):
                raise bridge.LegacyBridgeError('legacy-reviewed-plan-required')
            return self._retire()
        # The lease is verified by the existing helper entrypoint before any
        # constructor or adapter operation. No new resource/reset authority.
        with self._source() as (identity, program):
            if action == 'prepare':
                if not self.args.confirmed:
                    raise bridge.LegacyBridgeError('legacy-reviewed-confirmation-required')
                self.context = self._native_context()
                self.observer.preflight_native_evidence(self.root, **self.context)
                self._intent(identity)
                if self.plan_path.exists():
                    self._load_plan(identity)
                    pending = read_status(self.root, owner_sid=self.owner)
                    if pending['status'] != 'not-needed':
                        state = self.journal.status()
                        if state['status'] != 'prepared':
                            raise bridge.LegacyBridgeError('legacy-native-already-started')
                self._pre_native_target()
                if not self.plan_path.exists():
                    if read_status(self.root, owner_sid=self.owner)['status'] != 'not-needed':
                        raise bridge.LegacyBridgeError('legacy-other-upgrade-pending')
                    manifest = self._asset('prepare', program, self.prepared)
                    if type(manifest) is not dict or not manifest:
                        raise bridge.LegacyBridgeError('legacy-complete-assets-required')
                    if self._asset('verify_prepared', manifest, program, self.prepared) is not True:
                        raise bridge.LegacyBridgeError('legacy-preparation-unverified')
                    retirement = self.retirement.capture(self.source, identity, manifest,
                        owner_sid=self.owner, deadline=self.deadline, registry_reader=self.registry_reader,
                        external_root=getattr(self.assets, 'external', None))
                    if retirement.get('supported'):
                        retirement['temporary'] = self._temporary_plan(manifest)
                    plan = dict(self._bindings(identity), assets=manifest, retirement=retirement)
                    # Adapter must create only its own preparation, never adopt
                    # an unknown target. The plan is an exclusive publication.
                    _write_new(_safe(self.plan_path), _bounded(plan))
                self._load_plan(identity)
                state = self.journal.begin()
                if not self._prepared(state):
                    raise bridge.LegacyBridgeError('legacy-preparation-unverified')
                return self._receipt(state)

            self._intent(identity)
            self._load_plan(identity)
            state = self.journal.status()
            if action == 'status':
                # Read-only journal state never implies that bytes are still
                # committed. Only commit/resume performs full current verification.
                return self._receipt(state)
            if self.args.plan_sha256 != state['planSha256']:
                raise bridge.LegacyBridgeError('legacy-reviewed-plan-required')
            if action == 'start-native':
                self._pre_native_target()
                self.context = self._native_context()
                self.observer.preflight_native_evidence(self.root, **self.context)
                return self._receipt(self.journal.start_native(verify_resource=self._prepared))
            if action == 'native-complete':
                if state['status'] == 'native-in-progress':
                    self.context = self._native_context()
                    evidence = self.observer.capture_native_completion(self.root, **self.context)
                    state = self.journal.record_native_complete(evidence, verify_native=self._native)
                else:
                    self.context = self.observer.resume_context_from_evidence(state['nativeEvidence'], self.root)
                    state = self.journal.assert_can_forward(verify_native=self._native)
                return self._receipt(state)
            if action in {'commit', 'resume'}:
                if action == 'resume' and state['status'] == 'native-in-progress':
                    if self.args.uninstall_exe is None:
                        self.args.uninstall_exe = self.observer.registered_uninstaller(self.root)
                    self.context = self._native_context()
                    evidence = self.observer.capture_native_completion(self.root, **self.context)
                    state = self.journal.record_native_complete(evidence, verify_native=self._native)
                self.context = self.observer.resume_context_from_evidence(state['nativeEvidence'], self.root)
                state = self.journal.assert_can_forward(verify_native=self._native)
                with self._scope():
                    # The adapter must recover its own partial/lost publication;
                    # never restart the native installation to retry asset commit.
                    if not self._committed():
                        self._asset('commit', self.plan['assets'], self.prepared, self.root)
                    if not self._committed():
                        raise bridge.LegacyBridgeError('legacy-asset-commit-unverified')
                    self.journal.assert_can_forward(verify_native=self._native)
                    commit_path = _safe(self.root / 'work/installer-upgrades' / self.args.session_id / 'legacy-commit.json')
                    record = dict(planSha256=state['planSha256'], nativeEvidenceSha256=state['nativeEvidenceSha256'],
                                  phase='old-entry-pending')
                    if commit_path.exists():
                        if _parse(_read(commit_path)[0]) != record:
                            raise bridge.LegacyBridgeError('legacy-commit-record-changed')
                    else:
                        _write_new(commit_path, _bounded(record))
                return self._receipt(state, committed=True)
            if action == 'cancel':
                # Terminal cancellation before native start; copied data and
                # evidence survive. A new attempt uses a new empty target.
                state = self.journal.abort_pre_native(verify_resource=self._prepared)
                return self._receipt(state)
            raise bridge.LegacyBridgeError('legacy-invalid-action')
