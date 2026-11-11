"""Production migration adapter. Old resource bytes stay in re-import quarantine.

One synchronous helper owns all writes; Setup bounds and confirms its death.
Python-only native read seams serve temporary-directory tests, never the CLI.
"""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import os

import installer_legacy_bridge as bridge
import legacy_migration as migration
from installer_upgrade_journal import _read, _parse, _bounded, _write_new
from resource_transaction import _safe, rename_no_replace


def _external_root():
    from installer_native_observation import WindowsReadOnlyBackend, _LOCAL_APP_DATA
    return Path(WindowsReadOnlyBackend()._known_folder_path(_LOCAL_APP_DATA)) / 'Xingtu/resources'


def _production_roots():
    """Do not claim a per-user shared root while another product can own it."""
    import winreg
    result = set()
    for hive, views in ((winreg.HKEY_CURRENT_USER, (0,)),
                        (winreg.HKEY_LOCAL_MACHINE, (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY))):
        for view in views:
            try:
                parent = winreg.OpenKey(hive, bridge.UNINSTALL, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with parent:
                count = winreg.QueryInfoKey(parent)[0]
                if count > 20000:
                    raise bridge.LegacyBridgeError('registration-count-exceeded')
                for index in range(count):
                    with winreg.OpenKey(parent, winreg.EnumKey(parent, index), 0, winreg.KEY_READ | view) as key:
                        try:
                            label, kind = winreg.QueryValueEx(key, 'DisplayName')
                        except FileNotFoundError:
                            continue
                        if kind != winreg.REG_SZ or not isinstance(label, str):
                            continue
                        if not (label == '星图' or label.startswith('星图 ') or label.startswith('第五人格小抄助手')):
                            continue
                        location, kind = winreg.QueryValueEx(key, 'InstallLocation')
                        if kind != winreg.REG_SZ:
                            raise bridge.LegacyBridgeError('external-resource-registration-unverified')
                        # Machine-wide ownership can never be this user's data.
                        if hive != winreg.HKEY_CURRENT_USER:
                            raise bridge.LegacyBridgeError('external-resource-root-shared')
                        result.add(str(bridge._path(location)))
    return result


def _stamp(path):
    info = _safe(path).stat()
    return dict(identity=[info.st_dev, info.st_ino], **migration._file(path))


class CompleteAssetTransfer:
    owns_source_guard = True

    def __init__(self, *, external_root=None, registry_reader=None, source_guard=None,
                 production_roots=None):
        self.external = external_root
        self.registry_reader = registry_reader
        self.guard = source_guard or bridge._closed_source
        self.registrations = production_roots or _production_roots

    @contextmanager
    def _source(self, source, prepared, bindings):
        token = migration.operation_deadline.set(bindings['deadline'])
        try:
            migration._checkpoint()
            intent = _parse(_read(prepared.parent / '.legacy-upgrade-intent.json')[0])
            expected = dict(ownerSid=bindings['owner_sid'], sessionId=bindings['session_id'],
                            targetIdentity=bindings['target_identity'], targetRoot=str(prepared.parent),
                            targetRootIdentity=[prepared.parent.stat().st_dev, prepared.parent.stat().st_ino])
            if any(intent.get(k) != v for k, v in expected.items()):
                raise bridge.LegacyBridgeError('legacy-asset-binding-mismatch')
            identity, tool = bridge.probe_source(intent['sourceRoot'], registry_reader=self.registry_reader)
            if identity != intent['sourceIdentity'] or Path(identity['installRoot']) != source:
                raise bridge.LegacyBridgeError('source-identity-changed')
            external = _safe(Path(self.external)) if self.external is not None else _external_root()
            roots = bridge._asset_roots(source, external)
            def registered():
                return {os.path.normcase(str(bridge._path(p))) for p in self.registrations()}
            if 'external' in roots:
                registrations = registered()
                source_key = os.path.normcase(intent['sourceRoot'])
                target_key = os.path.normcase(str(prepared.parent))
                # Only the source and this transaction's new target are allowed.
                # Native completion is independently verified before commit.
                if not registrations or not registrations <= {source_key, target_key}:
                    raise bridge.LegacyBridgeError('external-resource-root-shared')
                if source_key not in registrations:
                    raise bridge.LegacyBridgeError('external-resource-registration-unverified')
            with self.guard(Path(intent['sourceRoot']), tool, source, roots), ExitStack() as stack:
                locks = migration._SourceImportLocks([
                    stack.enter_context(migration._SourceImportLock(root))
                    for root in dict.fromkeys((source, *roots.values()))])
                def inventory():
                    migration._checkpoint()
                    locks.verify()
                    if bridge.probe_source(intent['sourceRoot'], registry_reader=self.registry_reader)[0] != identity:
                        raise bridge.LegacyBridgeError('source-identity-changed')
                    if bridge._asset_roots(source, external, require_locks=False) != roots:
                        raise bridge.LegacyBridgeError('asset-root-set-changed')
                    if 'external' in roots and registered() != registrations:
                        raise bridge.LegacyBridgeError('external-resource-registration-changed')
                    migration._check_source_transactions(source, locks)
                    return migration._inventory(source, import_lock=locks, extra_roots=roots)
                files = inventory()
                yield identity, roots, locks, files, intent
                if inventory() != files:
                    raise bridge.LegacyBridgeError('source-changed')
        finally:
            migration.operation_deadline.reset(token)

    @staticmethod
    def _manifest(files, roots, intent, objects):
        # Same verified target layouts used by RuntimePaths: work/nightmare-
        # memory, work/seasons, user-routes, etc. Resource metadata and complete
        # payloads retain their namespaced quarantine, never active receipts.
        return dict(schema='xingtu-legacy-assets-v1', sessionId=intent['sessionId'],
            ownerSid=intent['ownerSid'], sourceIdentity=intent['sourceIdentity'],
            targetIdentity=intent['targetIdentity'], reportSha256=intent['reportSha256'],
            programInventorySha256=intent['programInventorySha256'],
            assetRoots=sorted(roots), inventorySha256=bridge._digest(files), files=files,
            preparedObjects=objects,
            resourceStatus='reimport-required' if any(name.startswith('map-packages/') for name in files)
                           else 'no-map-resources',
            resourcesActivated=False)

    def prepare(self, source, prepared, **bindings):
        with self._source(source, prepared, bindings) as (identity, roots, locks, files, intent):
            # Parent's exact resource lease serializes target writes. The PR151
            # engine retains its source locks and byte/inventory rechecks.
            migration._migrate(source, prepared, identity, import_lock=locks, extra_roots=roots)
            return self._manifest(files, roots, intent,
                                  {name: _stamp(prepared / 'work' / name) for name in files})

    def _validate(self, manifest, source, prepared, bindings, *, committed=False):
        with self._source(source, prepared, bindings) as (_, roots, _, files, intent):
            expected = self._manifest(files, roots, intent, manifest.get('preparedObjects'))
            if manifest != expected or type(manifest.get('preparedObjects')) is not dict or set(manifest['preparedObjects']) != set(files):
                raise bridge.LegacyBridgeError('legacy-asset-manifest-changed')
            complete = True
            for name, row in files.items():
                before, after = prepared / 'work' / name, prepared.parent / 'work' / name
                stamp = manifest['preparedObjects'][name]
                if set(stamp) != {'identity', 'bytes', 'sha256'} or {k: stamp[k] for k in row} != row:
                    raise bridge.LegacyBridgeError('legacy-asset-manifest-changed')
                if after.exists():
                    if _stamp(after) != stamp or before.exists():
                        raise bridge.LegacyBridgeError('legacy-destination-conflict')
                else:
                    if _stamp(before) != stamp:
                        raise bridge.LegacyBridgeError('legacy-prepared-bytes-changed')
                    complete = False
            # No unknown data can be smuggled into the prepared work tree.
            for path in (prepared / 'work').rglob('*'):
                _safe(path)
                if path.is_file() and path.relative_to(prepared / 'work').as_posix() not in files:
                    raise bridge.LegacyBridgeError('legacy-unlisted-prepared-file')
            permitted = {'map-packages/import.lock', 'pending-installer-upgrade.json'}
            history = 'installer-upgrades/' + bindings['session_id'] + '/'
            for path in (prepared.parent / 'work').rglob('*'):
                _safe(path)
                relative = path.relative_to(prepared.parent / 'work').as_posix()
                if (path.is_file() and relative not in files and relative not in permitted
                        and not relative.startswith(history)
                        and not relative.startswith('installer-upgrades/.initializing-')):
                    raise bridge.LegacyBridgeError('legacy-unlisted-target-file')
            return complete if committed else True

    def verify_prepared(self, manifest, source, prepared, **bindings):
        return self._validate(manifest, source, prepared, bindings)

    def verify_committed(self, manifest, root, **bindings):
        source = Path(manifest['sourceIdentity']['installRoot'])
        return self._validate(manifest, source, root / '.legacy-upgrade-prepared', bindings, committed=True)

    def commit(self, manifest, prepared, root, **bindings):
        source = Path(manifest['sourceIdentity']['installRoot'])
        self._validate(manifest, source, prepared, bindings)
        with self._source(source, prepared, bindings) as (_, roots, _, files, _):
            if files != manifest['files'] or sorted(roots) != manifest['assetRoots']:
                raise bridge.LegacyBridgeError('legacy-asset-manifest-changed')
            # Intent (plan) already pins each source object before its no-replace
            # rename. A lost receipt is resolved from SAME inode + current bytes.
            for name in manifest['files']:
                migration._checkpoint()
                before, after = _safe(prepared / 'work' / name), _safe(root / 'work' / name)
                if after.exists():
                    if _stamp(after) != manifest['preparedObjects'][name] or before.exists():
                        raise bridge.LegacyBridgeError('legacy-destination-conflict')
                    continue
                if _stamp(before) != manifest['preparedObjects'][name]:
                    raise bridge.LegacyBridgeError('legacy-prepared-bytes-changed')
                after.parent.mkdir(parents=True, exist_ok=True)
                rename_no_replace(before, after)
        if not self._validate(manifest, source, prepared, bindings, committed=True):
            raise bridge.LegacyBridgeError('legacy-asset-commit-unverified')
