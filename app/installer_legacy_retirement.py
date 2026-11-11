"""Bound, forward-only QtIFW retirement; never executes a historical tool.

The pre-native plan is the authority for *objects*, not their current names.
An immutable intent is published before the first removal. Missing objects are
then recoverable; replacement objects and unlisted content are never adopted.
All Windows removals use the same verified handle that was opened and hashed.
"""
from contextlib import ExitStack, contextmanager
import base64
import copy
import ctypes
from ctypes import wintypes as wt
import hashlib
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import installer_legacy_bridge as bridge
import installer_uninstall as uninstall
import legacy_migration as migration
from installer_upgrade_journal import _bounded, _parse, _read, _write_new
from resource_transaction import _safe


def fail(reason):
    raise bridge.LegacyBridgeError('legacy-retirement-' + reason)


class WindowsObjects:
    """Pinned complete trees, byte locks and image exclusion in one lifetime."""
    def __init__(self, owner, deadline, *, deleting=False):
        self.api = uninstall._kernel()
        self.owner, self.deadline, self.deleting = owner, deadline, deleting
        self.nodes, self.parents, self.parent_paths, self.read_nodes = {}, [], set(), {}

    def checkpoint(self):
        if time.monotonic() >= self.deadline:
            fail('deadline-exceeded')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for node in reversed(list(self.nodes.values())):
            self.close_node(node)
        for handle in reversed(self.parents):
            self.api.CloseHandle(handle)

    def close_node(self, node):
        if node.get('fd') is not None:
            os.close(node.pop('fd'))
            node['handle'] = None
        elif node.get('handle'):
            self.api.CloseHandle(node['handle'])
            node['handle'] = None

    def _hash(self, handle):
        digest = hashlib.sha256()
        buffer, count = ctypes.create_string_buffer(1024 * 1024), wt.DWORD()
        while True:
            self.checkpoint()
            if not self.api.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                fail('read-failed')
            if not count.value:
                return digest.hexdigest()
            digest.update(buffer.raw[:count.value])

    def add(self, path, *, directory=False, lock=False):
        self.checkpoint()
        path = _safe(Path(path))
        key = str(path)
        if key in self.nodes:
            return self.nodes[key]['stamp']
        # Hold every ancestor against directory replacement. These pins never
        # include the tree itself, whose own handle may carry DELETE access.
        for parent in reversed(path.parents):
            if (str(parent) in self.nodes or parent in self.parent_paths
                    or parent == Path(parent.anchor)):
                continue
            handle, _ = uninstall._open_handle(self.api, parent, directory=True)
            if uninstall._handle_path(self.api, handle) != uninstall._path_key(parent):
                self.api.CloseHandle(handle)
                fail('parent-path-changed')
            self.parents.append(handle)
            self.parent_paths.add(parent)
        image = not directory and path.suffix.casefold() == '.exe'
        handle, info = uninstall._open_handle(self.api, path, directory=directory,
            read=not directory, write=image or lock, read_control=True,
            delete=self.deleting, share=0 if lock else 1)
        node = dict(handle=handle, path=path, directory=directory)
        self.nodes[key] = node
        self.read_nodes[uninstall._path_key(path)] = node
        if (uninstall._handle_path(self.api, handle) != uninstall._path_key(path)
                or uninstall._owner_sid(handle) != self.owner):
            fail('object-owner-or-path-changed')
        stamp = dict(directory=directory, identity=list(uninstall._file_id(info)),
            created=[int(info.created.dwHighDateTime), int(info.created.dwLowDateTime)])
        if not directory:
            stamp.update(bytes=uninstall._size(info), sha256=self._hash(handle))
        if uninstall._file_id(uninstall._handle_info(self.api, handle)) != tuple(stamp['identity']):
            fail('object-changed-during-read')
        node['stamp'] = stamp
        if lock:
            import msvcrt
            fd = msvcrt.open_osfhandle(int(handle), os.O_RDWR | os.O_BINARY)
            node['fd'] = fd
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return stamp

    def tree(self, root, *, keep_lock=False):
        root = _safe(Path(root))
        if not root.exists():
            return {}
        result = {str(root): self.add(root, directory=True)}
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=migration._scan_error):
            self.checkpoint()
            for name in sorted(dirs):
                path = _safe(Path(directory) / name)
                result[str(path)] = self.add(path, directory=True)
            for name in sorted(files):
                path = _safe(Path(directory) / name)
                result[str(path)] = self.add(path, lock=(path.parts[-3:] == ('work', 'map-packages', 'import.lock')))
        return result

    def remove(self, path, stamp):
        self.checkpoint()
        node = self.nodes[str(path)]
        if node['stamp'] != stamp:
            fail('object-replaced')
        if node['directory'] and any(node['path'].iterdir()):
            fail('new-directory-content')
        uninstall._mark_delete(self.api, node['handle'], node['path'].name)
        self.close_node(node)
        if _safe(node['path']).exists():
            fail('removal-not-confirmed')

    def permits_read(self, path):
        node = self.read_nodes.get(uninstall._path_key(path))
        return bool(node and not node['directory'] and node.get('handle')
            and list(uninstall._file_id(uninstall._handle_info(self.api, node['handle']))) == node['stamp']['identity']
            and uninstall._handle_path(self.api, node['handle']) == uninstall._path_key(node['path']))


def _registry_record(key, expected):
    import winreg
    count, values, modified = winreg.QueryInfoKey(key)
    if count or values > 256:
        fail('registration-shape-changed')
    result = dict(hive=expected['hive'], view=expected['view'], key=expected['key'],
                  modified=modified, values={})
    for index in range(values):
        name, value, kind = winreg.EnumValue(key, index)
        if isinstance(value, bytes):
            value = {'base64': base64.b64encode(value).decode('ascii')}
        result['values'][name] = [kind, value]
    return result


class WindowsRegistration:
    @contextmanager
    def opened(self, expected, *, deleting=False):
        import winreg
        if expected['hive'] != 'HKCU' or expected['view'] != 0 or expected['key'] == bridge.INNO_KEY:
            fail('registration-scope-unsupported')
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, bridge.UNINSTALL + '\\' + expected['key'],
                                 0, winreg.KEY_READ | (0x10000 if deleting else 0))
        except FileNotFoundError:
            yield None
            return
        with key:
            if _registry_record(key, expected) != expected:
                fail('registration-replaced')
            yield key

    def verify(self, expected, *, missing=False):
        with self.opened(expected) as key:
            if key is None and not missing:
                fail('registration-missing-before-intent')
            return key is not None

    def remove(self, expected):
        with self.opened(expected, deleting=True) as key:
            if key is None:
                return
            # Delete the checked object, not a possibly replaced pathname.
            # https://learn.microsoft.com/windows-hardware/drivers/ddi/wdm/nf-wdm-zwdeletekey
            delete = ctypes.WinDLL('ntdll').NtDeleteKey
            delete.argtypes, delete.restype = [wt.HANDLE], wt.LONG
            if delete(int(key)) != 0:
                fail('registration-remove-denied')


class LegacyRetirement:
    def __init__(self, *, registration=None, locations=None, inventory=None):
        self.registration = registration or WindowsRegistration()
        self.locations = locations
        self.inventory = inventory

    def capture(self, source, identity, manifest, *, owner_sid, deadline, registry_reader=None,
                external_root=None):
        """Capture only supported native ownership, BEFORE starting native IO."""
        rows = (registry_reader or bridge._registry_rows)(source)
        if len(rows) != 1 or bridge._digest(rows[0]) != identity['uninstallRecordSha256']:
            fail('registration-changed')
        program = Path(identity['installRoot'])
        # Historical formats without a provable per-file inventory may prepare
        # data, but cannot gain destructive authority merely by having a name.
        if self.inventory is None:
            inventory = native_ownership
        else:
            inventory = self.inventory
        allowed = inventory(Path(source), program, identity)
        if not allowed['supported']:
            return allowed
        if external_root is None:
            from installer_legacy_assets import _external_root
            external_root = _external_root()
        roots = bridge._asset_roots(program, external_root)
        for name, row in manifest['files'].items():
            prefix = 'map-packages/legacy-unverified/asset-roots/'
            if name.startswith(prefix):
                label, relative = name[len(prefix):].split('/', 1)
                path = _safe(roots[label] / relative)
            else:
                path = migration._source_file(program, name)
            if migration._file(path) != row:
                fail('migration-source-changed')
            allowed['files'][str(path)] = row
        allowed_dirs = {Path(p) for p in allowed.get('directories', ())}
        for path in allowed['files']:
            allowed_dirs.update(Path(path).parents)
        lock_paths = {r / 'work/map-packages/import.lock' for r in (Path(source), program, *roots.values())}
        for path in lock_paths:
            if path.exists():
                allowed_dirs.update(path.parents)
        locations = self.locations
        if locations is None:
            from installer_native_observation import WindowsReadOnlyBackend
            locations = WindowsReadOnlyBackend().shortcut_locations()
        candidates = {}
        command = bridge._string(rows[0], 'UninstallString')
        tool = Path(command[1:-1] if command.startswith('"') and command.endswith('"') else command)
        for label, folder, title, target in (
            ('desktop', locations['desktop'], '星图.lnk', program / '星图.exe'),
            ('menu', locations['programs'], '星图/星图.lnk', program / '星图.exe'),
            ('uninstall', locations['programs'], '星图/卸载星图.lnk', tool)):
            path = _safe(Path(folder) / title)
            candidates[str(path)] = str(target)
        shortcuts = allowed.get('shortcuts', {})
        for path, target in shortcuts.items():
            if path not in candidates or not bridge._same(candidates[path], target):
                fail('historical-shortcut-location-unsupported')
        if any(Path(path).exists() and path not in shortcuts for path in candidates):
            fail('shortcut-not-in-native-inventory')
        with WindowsObjects(owner_sid, deadline) as objects:
            trees = {str(source): objects.tree(source)}
            if 'external' in roots:
                trees[str(roots['external'])] = objects.tree(roots['external'])
            for tree in trees.values():
                for path, stamp in tree.items():
                    if stamp['directory']:
                        if Path(path) not in allowed_dirs:
                            fail('unowned-directory:' + Path(path).name)
                        continue
                    if Path(path) in lock_paths:
                        continue
                    if {k: stamp[k] for k in ('bytes', 'sha256')} != allowed['files'].get(path):
                        fail('unmigrated-or-unowned-content:' + Path(path).name)
            links = {}
            for path, target in shortcuts.items():
                links[path] = objects.add(path) if Path(path).exists() else None
                if links[path] is not None:
                    # The identity/byte handle is already live and denies both
                    # writers and replacement while interpreting this target.
                    from installer_native_observation import _shortcut_target
                    if not bridge._same(_shortcut_target(Path(path).read_bytes()), target):
                        fail('shortcut-owned-by-another-installation')
            return dict(supported=True, format=allowed['format'], registration=rows[0],
                sourceRoot=str(source), programRoot=str(program), trees=trees, shortcuts=links,
                externalRoot=str(roots['external']) if 'external' in roots else None)

    def execute(self, plan, root, history, *, owner_sid, deadline, verify_current, registrations,
                native_shortcuts, protected=()):
        """Persist intent, verify all remaining objects, then remove by handle.

        verify_current re-reads the new native installation AND every committed
        migration object under the caller's live borrowed resource lease.
        """
        if not plan.get('supported'):
            fail('unsupported-history:' + plan.get('reason', 'missing-owned-inventory'))
        history = _safe(Path(history))
        intent_path = history / 'retirement-intent.json'
        expected = dict(plan=plan, ownerSid=owner_sid, targetRoot=str(root))
        started = intent_path.exists()
        if started and _parse(_read(intent_path)[0]) != expected:
            fail('intent-changed')
        verify_current()
        source, external = Path(plan['sourceRoot']), plan['externalRoot']
        def shared():
            if external:
                actual = {os.path.normcase(str(bridge._path(p))) for p in registrations()}
                if not actual or not actual <= {os.path.normcase(str(source)), os.path.normcase(str(root))}:
                    fail('external-root-shared')
        shared()
        present = self.registration.verify(plan['registration'], missing=started)
        inherited = {os.path.normcase(s['path']) for s in native_shortcuts if s['enabled']}
        with WindowsObjects(owner_sid, deadline) as target_objects, \
                WindowsObjects(owner_sid, deadline, deleting=True) as objects, ExitStack() as reads:
            for path in dict.fromkeys(protected):
                target_objects.add(path)
            from installer_native_observation import pinned_read_sharing
            reads.enter_context(pinned_read_sharing(target_objects.permits_read))
            current = {}
            for name, tree in plan['trees'].items():
                actual = objects.tree(Path(name))
                if any(path not in tree or tree[path] != stamp for path, stamp in actual.items()):
                    fail('source-replaced-or-new-content')
                if not started and actual != tree:
                    fail('source-missing-before-intent')
                # Missing directories cannot hide a newly substituted tree:
                # every surviving ancestor in a supported tree has its old ID.
                current.update(actual)
            for path, stamp in plan['shortcuts'].items():
                if os.path.normcase(path) in inherited:
                    continue  # new native verifier owns this exact selected link
                if Path(path).exists():
                    if stamp is None or objects.add(path) != stamp:
                        fail('shortcut-replaced')
                    current[path] = stamp
                elif stamp is not None and not started:
                    fail('shortcut-missing-before-intent')
            temporary = plan.get('temporary', {})
            prepared = _safe(Path(root) / '.legacy-upgrade-prepared')
            actual_temp = objects.tree(prepared)
            intent = _safe(Path(root) / '.legacy-upgrade-intent.json')
            if intent.exists():
                actual_temp[str(intent)] = objects.add(intent)
            if (any(temporary.get(path) != stamp for path, stamp in actual_temp.items())
                    or not started and actual_temp != temporary):
                fail('preparation-replaced-or-new-content')
            current.update(actual_temp)
            verify_current()
            shared()
            if not started:
                _write_new(intent_path, _bounded(expected))
            # The full intent is durable before ANY deletion. Each item then
            # has its own durable intent and completion receipt. A lost receipt
            # is recovered from absence, never by adopting another object.
            actions = [(path, stamp) for path, stamp in current.items()
                       if not external or not (path == external or path in {
                           str(Path(external) / 'work'), str(Path(external) / 'work/map-packages'),
                           str(Path(external) / 'work/map-packages/import.lock')})]
            actions.sort(key=lambda item: (item[1]['directory'],
                Path(item[0]).parts[-3:] == ('work', 'map-packages', 'import.lock'),
                -len(Path(item[0]).parts)))
            originals = {path for tree in plan['trees'].values() for path in tree}
            originals.update(plan['shortcuts'])
            originals.update(plan.get('temporary', {}))
            indices = {path: index for index, path in enumerate(sorted(originals))}
            for path, stamp in actions:
                objects.checkpoint()
                shared()
                key = str(indices[path]).zfill(6)
                before, after = history / ('retire-' + key + '-intent.json'), history / ('retire-' + key + '-done.json')
                row = dict(path=path)
                if before.exists():
                    if _parse(_read(before)[0]) != row:
                        fail('item-intent-changed')
                else:
                    _write_new(before, _bounded(row))
                objects.remove(path, stamp)
                if not after.exists():
                    _write_new(after, _bounded(row))
            verify_current()
            shared()
            if present:
                self.registration.remove(plan['registration'])
        if self.registration.verify(plan['registration'], missing=True):
            fail('registration-remains')
        verify_current()
        if source.exists():
            fail('source-remains')
        self.verify_finished(plan, root, owner_sid=owner_sid, deadline=deadline,
                             native_shortcuts=native_shortcuts)
        return True

    def verify_finished(self, plan, root, *, owner_sid, deadline, native_shortcuts):
        if not plan.get('supported'):
            fail('unsupported-history')
        if self.registration.verify(plan['registration'], missing=True):
            fail('registration-remains')
        if _safe(Path(plan['sourceRoot'])).exists():
            fail('source-remains-or-replaced')
        for path in plan.get('temporary', {}):
            if _safe(Path(path)).exists():
                fail('preparation-remains-or-replaced')
        inherited = {os.path.normcase(s['path']) for s in native_shortcuts if s['enabled']}
        for path in plan['shortcuts']:
            if os.path.normcase(path) not in inherited and _safe(Path(path)).exists():
                fail('old-shortcut-remains-or-replaced')
        external = plan['externalRoot']
        if external:
            retain = {external, str(Path(external) / 'work'), str(Path(external) / 'work/map-packages'),
                      str(Path(external) / 'work/map-packages/import.lock')}
            expected = {p: row for p, row in plan['trees'][external].items() if p in retain}
            with WindowsObjects(owner_sid, deadline) as objects:
                if objects.tree(Path(external)) != expected:
                    fail('external-content-remains-or-replaced')
        return True


def native_ownership(source, program, identity):
    """Supported repository portable QtIFW format, not a tool-name heuristic.

    The native Extract/Copy operation inventory proves individual payload and
    support paths. The policy and root owner independently bind their product,
    build and location, and every inventoried program byte is checked.
    """
    policy_path = _safe(source / '_installer-support/_internal/qtifw-trial-policy.json')
    owner_path = _safe(source / '.qtifw-trial-owner.json')
    if program != source / 'program' or not policy_path.is_file() or not owner_path.is_file():
        return dict(supported=False, reason='portable-qtifw-owned-policy-required')
    policy, owner = _parse(_read(policy_path)[0]), _parse(_read(owner_path)[0])
    if (policy.get('portable') is not True or policy.get('faultsAllowed') is not False
            or policy.get('fullUninstall') is not True or policy.get('allowNewTargets') is not True
            or owner.get('phase') != 'complete' or owner.get('instance') != policy.get('instance')
            or owner.get('payloadSource') != policy.get('payloadSource')
            or not bridge._same(owner.get('target', ''), source)):
        fail('historical-owner-policy-mismatch')
    from installer_qtifw_inventory import read_operations
    tool = next((p for p in (source / 'trial-maintenance.exe', source / 'MaintenanceTool.exe')
                 if p.is_file() and migration._file(p)['sha256'] == identity['maintenanceToolSha256']), None)
    if tool is None:
        fail('maintenance-tool-changed')
    native = read_operations(tool.with_suffix('.dat'), source)
    if native.components != frozenset({'org.xingtu.qtifw.realtrial', 'org.xingtu.qtifw.trialdata'}):
        fail('historical-components-unsupported')
    files = {str(path): migration._file(path) for path in native.files}
    for path, (count, digest) in native.metadata_digests.items():
        if files.get(str(path)) != dict(bytes=count, sha256=digest):
            fail('native-inventory-changed-during-read')
    required = {'星图.exe', 'release-report.json', '_installer-owned-files.txt', '_internal/installation-profile.json'}
    if not required <= set(policy.get('files', {})):
        fail('historical-program-inventory-incomplete')
    for name, row in policy['files'].items():
        name = uninstall._relative(name)
        if name.casefold().startswith('work/'):
            fail('historical-program-inventory-invalid')
        path = _safe(program / name)
        if files.get(str(path)) != row:
            fail('historical-program-inventory-changed')
    from installer_uninstall import _read_ownership
    owned = _read_ownership(_read(program / '_installer-owned-files.txt')[0])
    if not set(policy['files']) <= owned:
        fail('historical-program-ownership-changed')
    # Native extraction can include initial resource/user files. Their native
    # origin is never permission to discard their later contents: they need a
    # corresponding verified migration destination, added by capture().
    for path in list(files):
        relative = Path(path).relative_to(program).as_posix() if Path(path).is_relative_to(program) else None
        if relative is not None and relative not in policy['files']:
            del files[path]
    report = _parse(_read(program / 'release-report.json')[0])
    profile = _parse(_read(program / '_internal/installation-profile.json')[0])
    if (report.get('sourceCommit') != policy['payloadSource']
            or profile.get('sourceCommit') != policy['payloadSource']
            or report.get('executableSha256') != migration._file(program / '星图.exe')['sha256']):
        fail('historical-program-report-changed')
    # QtIFW's own metadata is not program payload; bind only the documented
    # native names and parsed installerResources list files, never whole dirs.
    for path in (tool, tool.with_suffix('.dat'), tool.with_suffix('.ini'),
                 source / 'components.xml', source / 'network.xml', owner_path):
        if _safe(path).is_file():
            files[str(path)] = migration._file(path)
    packages = ET.fromstring(_read(source / 'components.xml')[0])
    names = {node.findtext('Name') for node in packages.findall('Package')}
    if names != native.components:
        fail('historical-components-changed')
    if any(node.findtext('Version') != '1.0.0' for node in packages.findall('Package')):
        fail('historical-component-version-unsupported')
    return dict(supported=True, format='repository-portable-qtifw-native-operations', files=files,
                directories=[str(path) for path in native.directories],
                shortcuts={str(s.target): str(s.source) for s in native.shortcuts})
