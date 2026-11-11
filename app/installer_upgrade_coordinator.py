"""Setup orchestration: pre-native restoration or verified forward completion only."""
from __future__ import annotations

import base64
from contextlib import nullcontext
import hashlib
import json
import ntpath
from pathlib import Path

import installer_native_observation as native
from installer_resource_lease import installer_coordinator_scope
from installer_upgrade_journal import InstallerUpgradeJournal, read_status
from resource_transaction import _safe


def native_context(args, target):
    path = _safe(Path(args.program_inventory))
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('installer-program-inventory-too-large')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != args.program_inventory_sha256:
        raise ValueError('installer-program-inventory-changed')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('installer-program-inventory-duplicate-key')
            result[key] = value
        return result
    inventory = json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique)
    if (type(inventory) is not dict or set(inventory) != {
            'schema', 'sourceCommit', 'ownershipSha256', 'ownershipBase64', 'files'}
            or inventory['schema'] != 'xingtu-installer-program-inventory-v1'
            or inventory['sourceCommit'] != target['sourceCommit']):
        raise ValueError('installer-program-inventory-invalid')
    ownership = base64.b64decode(inventory['ownershipBase64'], validate=True)
    if hashlib.sha256(ownership).hexdigest() != inventory['ownershipSha256']:
        raise ValueError('installer-ownership-changed')
    executable = ntpath.join(str(args.install_root), '星图.exe')
    uninstall_exe = args.uninstall_exe or None
    return dict(target_identity=target, target_report_sha256=args.target_report_sha256,
        ownership_bytes=ownership, ownership_sha256=inventory['ownershipSha256'],
        program_inventory=inventory['files'], uninstall_exe=uninstall_exe,
        shortcut_targets=[dict(path=args.group_shortcut, target=executable, enabled=args.group_enabled),
            dict(path=args.uninstall_shortcut, target=uninstall_exe, enabled=args.group_enabled),
            dict(path=args.desktop_shortcut, target=executable, enabled=args.desktop_enabled)])


def receipt(state, resource=None):
    """Small public status only. Never serialize the native observation into INI."""
    status = state['status']
    finalized = status == 'completed' and state.get('pending') is False
    result = dict(status='committed' if finalized else status, installerStatus=status,
                  installerFinalized=finalized, resourceCommitted=False)
    for source, dest in [('sessionId', 'sessionId'), ('planSha256', 'planSha256'),
                         ('resourceTransactionId', 'transactionId'), ('restoreWatcher', 'restoreWatcher'),
                         ('resourceAction', 'resourceAction')]:
        if state.get(source) is not None:
            result[dest] = state[source]
    if resource:
        result.update(resourceStatus=resource['status'], resourceCommitted=resource['status'] == 'committed')
    return result


class Coordinator:
    def __init__(self, args, api, target, lease, owner_sid, *, observer=native,
                 scope=installer_coordinator_scope):
        self.args, self.api, self.target = args, api, target
        self.root, self.lease, self.owner = Path(args.install_root), lease, owner_sid
        self.observer, self.scope = observer, scope
        self.journal = None
        self.context = None

    def _journal(self, state=None):
        a = self.args
        if state is None:
            state = dict(sessionId=a.session_id, targetIdentity=self.target,
                         reportSha256=a.target_report_sha256, planSha256=a.plan_sha256,
                         restoreWatcher=a.restore_watcher, resourceAction=getattr(a, 'resource_action', 'reset'))
        return InstallerUpgradeJournal(self.root, owner_sid=self.owner,
            session_id=state['sessionId'], target_identity=state['targetIdentity'],
            report_sha256=state['reportSha256'], plan_sha256=state['planSha256'],
            restore_watcher=state['restoreWatcher'], resource_action=state.get('resourceAction', 'reset'))

    def _native_context(self):
        context = native_context(self.args, self.target)
        if (self.journal.resource_action == 'preserve'
                and self.journal.bindings['planSha256'] != self.args.program_inventory_sha256):
            raise ValueError('installer-program-plan-mismatch')
        return context

    def _preserved(self, state):
        with self._scope() if state.get('pending') else nullcontext():
            self.api.assert_resource_upgrade_ready(self.root)
        return True

    def _scope(self):
        return self.scope(self.root, self.lease, self.journal.bindings['sessionId'], validate=self.journal.status)

    def _resource(self, state, allowed, *, verify_projection=False):
        tx = state['resourceTransactionId']
        if not tx:
            return False
        if verify_projection:
            result = self.api.resource_upgrade_status(self.root, tx, verify_projection=True)
        else:
            result = self.api.resource_upgrade_status(self.root, tx)
        return (result.get('transactionId') == tx and result.get('planSha256') == state['planSha256']
                and result.get('targetIdentity') == state['targetIdentity'] and result['status'] in allowed)

    def _prepared(self, state):
        if self.journal.resource_action == 'preserve':
            return self._preserved(state)
        pending = self.api.resource_upgrade_status(self.root)
        return (pending.get('transactionId') == state['resourceTransactionId']
                and self._resource(state, {'prepared-resources'}))

    def _native(self, state, evidence):
        if (self.context['target_identity'] != state['targetIdentity']
                or self.context['target_report_sha256'] != state['reportSha256']):
            raise ValueError('installer-native-journal-binding-mismatch')
        self.observer.verify_native_completion(evidence, self.root, **self.context)
        return True

    def _restored(self, state):
        if self.journal.resource_action == 'preserve':
            return self._preserved(state)
        if state['resourceTransactionId'] and not self._resource(state, {'rolled-back'}):
            return False
        if self.api.resource_upgrade_status(self.root)['status'] != 'not-needed':
            return False
        with self._scope() if (self.root / 'work/pending-installer-upgrade.json').exists() else nullcontext():
            plan = self.api.inspect_resource_upgrade(self.root,
                candidate_directory=self.args.candidate_directory, target_identity=state['targetIdentity'])
        return plan['planSha256'] == state['planSha256']

    def _committed(self, state):
        if self.journal.resource_action == 'preserve':
            return self._preserved(state) if state['pending'] else True
        # The last pre-finalization callback needs a fresh bound permit and a
        # full resource witness, including resume after a lost commit receipt.
        # Historical completed sessions must not freeze later legitimate imports.
        with self._scope() if state['pending'] else nullcontext():
            return (self._resource(state, {'committed'}, verify_projection=state['pending'])
                    and self.api.resource_upgrade_status(self.root)['status'] == 'not-needed')

    def _finish(self):
        state = self.journal.assert_can_forward(verify_native=self._native)
        if state['pending'] and self.journal.resource_action != 'preserve':
            with self._scope():
                self.api.finish_resource_upgrade(self.root, state['resourceTransactionId'], setup_succeeded=True)
        state = self.journal.finish_complete(verify_native=self._native, verify_resource=self._committed)
        return receipt(state, None if self.journal.resource_action == 'preserve' else
                       self.api.resource_upgrade_status(self.root, state['resourceTransactionId']))

    def execute(self):
        a = self.args
        pending = read_status(self.root, owner_sid=self.owner)
        if pending.get('resourceAction') == 'legacy' and a.action != 'installer-pending':
            # A native-complete migration still needs its own asset commit and
            # historical retirement. Generic preserve/reset must never clear it.
            raise ValueError('legacy-handoff-recovery-required')
        if a.action == 'installer-pending':
            resource = None
            if pending.get('resourceTransactionId'):
                resource = self.api.resource_upgrade_status(self.root, pending['resourceTransactionId'])
            return receipt(pending, resource)
        if a.action == 'resume-native-complete':
            if not a.confirmed or pending['status'] not in {'native-complete', 'completed'}:
                raise ValueError('installer-only-confirmed-native-complete-resume')
            if a.session_id != pending['sessionId'] or a.plan_sha256 != pending['planSha256']:
                raise ValueError('installer-resume-binding-mismatch')
            if a.transaction_id and a.transaction_id != pending['resourceTransactionId']:
                raise ValueError('installer-resource-transaction-mismatch')
            self.journal = self._journal(pending)
            self.context = self.observer.resume_context_from_evidence(pending['nativeEvidence'], self.root)
            return self._finish()
        self.journal = self._journal()
        if a.action == 'prepare':
            if not a.confirmed:
                raise ValueError('installer-reviewed-confirmation-required')
            self.context = self._native_context()
            self.observer.preflight_native_evidence(self.root, **self.context)
            if self.journal.resource_action == 'preserve':
                if pending['status'] == 'not-needed':
                    self.api.assert_resource_upgrade_ready(self.root)
                else:
                    self._preserved(self.journal.status())
                state = self.journal.begin()
                self._preserved(state)
                return receipt(state)
            self.journal.begin()
            with self._scope():
                result = self.api.prepare_resource_upgrade(self.root, expected_plan_sha256=a.plan_sha256,
                    confirmed=True, candidate_directory=a.candidate_directory, target_identity=self.target)
            state = self.journal.bind_resource(result['transactionId'], verify_resource=self._prepared)
            return receipt(state, self.api.resource_upgrade_status(self.root, state['resourceTransactionId']))
        if a.action == 'status' and pending['status'] == 'not-needed':
            # An explicit history may be terminal after its marker was cleared.
            history = self.root / 'work' / 'installer-upgrades' / a.session_id
            if not history.exists():
                return receipt(pending)
        if a.action == 'rollback' and pending['status'] == 'not-needed':
            history = self.root / 'work' / 'installer-upgrades' / a.session_id
            if not history.exists():
                unbegun = dict(self.journal.bindings, resourceTransactionId=None)
                if not self._restored(unbegun):
                    raise ValueError('installer-unbegun-resource-restoration-unverified')
                return receipt(dict(unbegun, status='aborted', pending=False))
        state = self.journal.status()
        if a.transaction_id and a.transaction_id != state['resourceTransactionId']:
            raise ValueError('installer-resource-transaction-mismatch')
        if a.action == 'status':
            resource = self.api.resource_upgrade_status(self.root, state['resourceTransactionId']) if state['resourceTransactionId'] else None
            return receipt(state, resource)
        if a.action == 'start-native':
            self.context = self._native_context()
            self.observer.preflight_native_evidence(self.root, **self.context)
            return receipt(self.journal.start_native(verify_resource=self._prepared))
        if a.action in {'native-complete', 'commit'}:
            self.context = self._native_context()
            if a.action == 'native-complete':
                evidence = self.observer.capture_native_completion(self.root, **self.context)
                return receipt(self.journal.record_native_complete(evidence, verify_native=self._native))
            return self._finish()
        if a.action == 'rollback':
            if self.journal.resource_action == 'preserve':
                return receipt(self.journal.abort_pre_native(verify_resource=self._restored))
            if state['status'] == 'aborted':
                return receipt(self.journal.abort_pre_native(verify_resource=self._restored))
            if state['status'] != 'prepared':
                raise ValueError('installer-rollback-after-native-forbidden')
            if not state['resourceTransactionId']:
                resource = self.api.resource_upgrade_status(self.root)
                if resource['status'] != 'not-needed':
                    def same_plan(proposed):
                        return self._resource(proposed, {'preparing', 'moving', 'prepared-resources', 'rolled-back'})
                    state = self.journal.bind_resource(resource['transactionId'], verify_resource=same_plan)
            if state['resourceTransactionId']:
                with self._scope():
                    self.api.finish_resource_upgrade(self.root, state['resourceTransactionId'], setup_succeeded=False)
            return receipt(self.journal.abort_pre_native(verify_resource=self._restored))
        raise ValueError('installer-invalid-action')
