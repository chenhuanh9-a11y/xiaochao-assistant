"""Native-uninstall continuation, rooted in a separate Windows registration.

The registration is the authority, just like the existing native installation
registration. No caller-supplied JSON, missing-registration inference, or
installed Python runtime grants deletion rights. A matching Setup supplies its
own verified helper. This does not claim to resist arbitrary same-user code
that can rewrite both Windows registrations and executable files.
"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

import installer_uninstall as u

RECOVERY_KEY = r"Software\Xingtu\UninstallRecovery"
MAX_STATE = 4 * 1024 * 1024
SCHEMA = "xingtu-native-uninstall-recovery-v1"


@contextmanager
def hold_maintenance(profile):
    """Keep the parent's existing named object alive through worker exit.

    Inno uses atomic object existence, not mutex ownership. Opening (never
    creating) that object here keeps other Setup/uninstall parents excluded
    even if our exact parent crashes or cannot confirm termination.
    """
    _name, expected = u._profile_identity(profile)
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenMutexW.argtypes, api.OpenMutexW.restype = [wt.DWORD, wt.BOOL, wt.LPCWSTR], wt.HANDLE
    api.CloseHandle.argtypes, api.CloseHandle.restype = [wt.HANDLE], wt.BOOL
    handle = api.OpenMutexW(0x100000, False, 'Global\\XingtuMaintenance-{' + expected['appId'] + '}')
    if not handle:
        raise u.UninstallError('recovery-maintenance-parent-missing')
    try:
        yield
    finally:
        api.CloseHandle(handle)


def _encode(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_STATE:
        raise u.UninstallError("recovery-registration-too-large")
    return raw


class RecoveryStore:
    """Narrow native registry adapter; fixtures replace this adapter only.

    State is one atomic REG_SZ value, including its phase and complete snapshot.
    There is no capsule file or persistent executable to strand at finalization.
    All paths, hives and views are derived from source-owned product identities.
    Writes are ordinary Windows writes: access denied stops before data removal.
    """
    @staticmethod
    def _address(identity):
        import winreg
        expected = u._IDENTITIES[identity]
        hive = winreg.HKEY_CURRENT_USER if expected["hive"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        return hive, RECOVERY_KEY + "\\{" + expected["appId"] + "}"

    def read(self, identity):
        import winreg
        hive, path = self._address(identity)
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                if winreg.QueryInfoKey(key)[:2] != (0, 1):
                    raise u.UninstallError("recovery-registration-shape-changed")
                value, kind = winreg.QueryValueEx(key, "State")
                if kind != winreg.REG_SZ or not isinstance(value, str):
                    raise u.UninstallError("recovery-registration-invalid")
                if len(value.encode("utf-8")) > MAX_STATE:
                    raise u.UninstallError("recovery-registration-too-large")
                return value
        except FileNotFoundError:
            return None

    def replace(self, identity, expected, replacement):
        import winreg
        if self.read(identity) != expected:
            raise u.UninstallError("recovery-registration-changed")
        hive, path = self._address(identity)
        if replacement is None:
            # Delete the one channel key atomically. A crash cannot leave an
            # empty credential with no way to finish removing it.
            winreg.DeleteKeyEx(hive, path, winreg.KEY_WOW64_64KEY)
            try:
                with winreg.OpenKey(hive, RECOVERY_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as parent:
                    empty = winreg.QueryInfoKey(parent)[:2] == (0, 0)
                if empty:
                    winreg.DeleteKeyEx(hive, RECOVERY_KEY, winreg.KEY_WOW64_64KEY)
            except FileNotFoundError:
                pass
            except OSError as error:
                # The shared parent is not ours if another channel uses it.
                if getattr(error, "winerror", None) != 145:
                    raise u.UninstallError("recovery-registration-parent-not-cleared") from error
        else:
            with winreg.CreateKeyEx(hive, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE |
                                    winreg.KEY_WOW64_64KEY) as key:
                winreg.SetValueEx(key, "State", 0, winreg.REG_SZ, replacement)
                winreg.FlushKey(key)
        if self.read(identity) != replacement:
            raise u.UninstallError("recovery-registration-readback-failed")

    @staticmethod
    def _native_address(row):
        import winreg
        expected = u._IDENTITIES[row["identity"]]
        if row["app_id"] != expected["appId"] or row["hive"] != expected["hive"]:
            raise u.UninstallError("recovery-native-identity-invalid")
        hive = winreg.HKEY_CURRENT_USER if row["hive"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        view = winreg.KEY_WOW64_64KEY if row["view"] == 64 else winreg.KEY_WOW64_32KEY
        return hive, u._UNINSTALL_KEY + "\\{" + expected["appId"] + "}_is1", view

    def snapshot_native(self, row):
        import winreg
        hive, path, view = self._native_address(row)
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
                subkeys, count, _ = winreg.QueryInfoKey(key)
                if subkeys or count > 128:
                    raise u.UninstallError("recovery-native-registration-shape")
                values = []
                for i in range(count):
                    name, value, kind = winreg.EnumValue(key, i)
                    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ, winreg.REG_DWORD,
                                    winreg.REG_QWORD, winreg.REG_MULTI_SZ):
                        raise u.UninstallError("recovery-native-registration-type")
                    values.append([name, kind, value])
                return sorted(values)
        except FileNotFoundError:
            return None

    def delete_native(self, row, snapshot):
        import winreg
        actual = self.snapshot_native(row)
        if actual is None:
            return
        if actual != snapshot:
            raise u.UninstallError("recovery-native-registration-changed")
        hive, path, view = self._native_address(row)
        winreg.DeleteKeyEx(hive, path, view)
        if self.snapshot_native(row) is not None:
            raise u.UninstallError("recovery-native-registration-remains")


def _state(raw, identity, sid, digest, root=None):
    if not isinstance(raw, str):
        raise u.UninstallError("recovery-registration-missing")
    record = u._read_json_bytes(raw.encode("utf-8"), MAX_STATE, "recovery-registration-invalid")
    if (record.get("schema") != SCHEMA or record.get("identity") != identity
            or record.get("sid") != sid or record.get("helperDigest") != digest
            or record.get("phase") not in {"prepared", "native-ready"}
            or not re.fullmatch(r"[0-9a-f]{32}", str(record.get("transaction", "")))):
        raise u.UninstallError("recovery-registration-identity-mismatch")
    actual = u._check_install_root(record.get("root", ""))
    if root is not None and u._path_key(root) != u._path_key(actual):
        raise u.UninstallError("recovery-root-mismatch")
    if not isinstance(record.get("nodes"), dict) or not isinstance(record.get("native"), list):
        raise u.UninstallError("recovery-registration-incomplete")
    return record


def _digest_handle(api, handle, maximum=512 * 1024 * 1024):
    seek = api.SetFilePointerEx
    seek.argtypes, seek.restype = [wt.HANDLE, ctypes.c_longlong, wt.LPVOID, wt.DWORD], wt.BOOL
    if not seek(handle, 0, None, 0):
        raise u.UninstallError("recovery-file-seek-failed")
    digest, total = hashlib.sha256(), 0
    while True:
        buffer, count = ctypes.create_string_buffer(65536), wt.DWORD()
        if not api.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
            raise u.UninstallError("recovery-file-read-failed")
        if not count.value:
            return digest.hexdigest()
        total += count.value
        if total > maximum:
            raise u.UninstallError("recovery-file-too-large")
        digest.update(buffer.raw[:count.value])


def _observe(api, path, *, directory=False, native_log=False, resource_lock=False):
    u._safe_ancestors(path)
    opaque = native_log or resource_lock
    handle, info = u._open_handle(api, path, directory=directory, read=not directory and not opaque,
                                 read_control=True,
                                 share=(u._FILE_SHARE_READ | u._FILE_SHARE_WRITE | 4) if opaque or directory else u._FILE_SHARE_READ)
    try:
        if u._handle_path(api, handle) != u._path_key(path):
            raise u.UninstallError("recovery-physical-path-mismatch")
        row = dict(identity=list(u._file_id(info)), directory=directory, owner=u._owner_sid(handle))
        if resource_lock:
            row['resourceLock'] = True
        elif native_log:
            # Inno holds its .dat exclusively and truncates that SAME object
            # before attempting to delete it. Its bytes are not a credential.
            row['nativeLog'] = True
        elif not directory:
            row["sha256"] = _digest_handle(api, handle)
        return row
    finally:
        api.CloseHandle(handle)


def _helper_inventory(api, root, digest):
    raw = u._read_secure(api, Path(root) / "_uninstall-helper-files.txt", u._MAX_OWNERSHIP)
    if not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or hashlib.sha256(raw).hexdigest() != digest:
        raise u.UninstallError("recovery-helper-inventory-mismatch")
    rows = {}
    for line in raw.decode("utf-8-sig").splitlines():
        if not re.fullmatch(r"[0-9a-f]{64} .+", line):
            raise u.UninstallError("recovery-helper-inventory-invalid")
        name = u._relative(line[65:])
        if (name != "star-map-uninstall-helper.exe" and not name.startswith("_star_map_uninstall_runtime/")
                or name.casefold() in {x.casefold() for x in rows}):
            raise u.UninstallError("recovery-helper-inventory-invalid")
        rows[name] = line[:64]
    if ("star-map-uninstall-helper.exe" not in rows
            or "_star_map_uninstall_runtime/installation-profile.json" not in rows):
        raise u.UninstallError("recovery-helper-inventory-incomplete")
    return rows


def prepare(args, *, identity, sid, registry, store, app_plan, data_roots, native_executable):
    """Called under the live original parent lease, before the first delete."""
    api, root = app_plan.api, app_plan.root
    old = store.read(identity)
    previous = _state(old, identity, sid, args.helper_inventory_sha256, root) if old is not None else None
    if previous and previous['phase'] == 'native-ready':
        # Native-ready never goes backwards or adopts newly created data.
        # The matching Setup can also safely finish an original lock tail.
        raise u.UninstallError('recovery-data-already-cleared-use-setup')
    inventory = _helper_inventory(api, root, args.helper_inventory_sha256)
    nodes = {}
    for node in app_plan.nodes:
        if node['action'] == 'preserve-native' and node['path'].stem.casefold() != Path(native_executable).stem.casefold():
            raise u.UninstallError('recovery-unregistered-native-file', remaining=(node['relative'],))
        if node["relative"] and node["action"] not in {"preserve-program", "preserve-native", "preserve-dir", "preserve-lock"}:
            continue
        relative = node["relative"]
        row = _observe(api, node["path"], directory=node["directory"],
                       native_log=node['action'] == 'preserve-native' and node['path'].suffix.casefold() == '.dat',
                       resource_lock=node['action'] == 'preserve-lock')
        if node.get("identity") is not None and row["identity"] != list(node["identity"]):
            raise u.UninstallError("recovery-preflight-object-changed")
        if relative in inventory and row.get("sha256") != inventory[relative]:
            raise u.UninstallError("recovery-helper-file-changed", remaining=(relative,))
        nodes[relative] = row
    if set(inventory) - set(nodes):
        raise u.UninstallError("recovery-helper-files-missing")
    native_relative = Path(native_executable).relative_to(root).as_posix()
    if nodes.get(native_relative, {}).get('sha256') != args.parent_sha256:
        # Inno 6.7.3 byte-copies this registered executable to _unins.tmp.
        # The borrowed lease separately verifies the live direct parent's image
        # against this digest. An arbitrary caller with a claimed image/hash
        # therefore cannot create recovery authority or supply shortcut paths.
        raise u.UninstallError('recovery-native-parent-mismatch')
    shortcuts = {}
    for value in getattr(args, "shortcut", ()):
        path = Path(u._win_path(value))
        if path.suffix.casefold() != ".lnk":
            raise u.UninstallError("recovery-shortcut-kind-invalid")
        u._safe_ancestors(path)
        if path.exists():
            shortcuts[str(path)] = _observe(api, path)
    groups = {}
    for value in getattr(args, "shortcut_group", ()):
        path = Path(u._win_path(value))
        u._safe_ancestors(path)
        if path.exists():
            # A shared group is not an owned directory. Only capture an empty
            # group or one containing exclusively the verified shortcut files.
            if all(str(child) in shortcuts for child in path.iterdir()):
                groups[str(path)] = _observe(api, path, directory=True)
    native = []
    for row in registry.registrations():
        if row["identity"] == identity:
            native.append(dict(row=row, values=store.snapshot_native(row)))
    if not native or any(item["values"] is None for item in native):
        raise u.UninstallError("recovery-native-registration-missing")
    data_identities = {}
    for path in (path for path in data_roots if path is not None):
        u._safe_ancestors(path)
        data_identities[str(path)] = _observe(api, path, directory=True) if path.exists() else None
    if previous:
        # A failed data-stage retry consumes the original authorization. It
        # never refreshes snapshots around replacements or newly appeared roots.
        if _observe(api, root.parent, directory=True) != previous['parent']:
            raise u.UninstallError('recovery-parent-identity-changed')
        for current, original in ((nodes, previous['nodes']),
                                  (shortcuts, previous['shortcuts']), (groups, previous['groups'])):
            if any(original.get(name) != row for name, row in current.items()):
                raise u.UninstallError('recovery-prepared-object-changed')
        for name, row in data_identities.items():
            if row is not None and previous.get('dataIdentities', {}).get(name) != row:
                raise u.UninstallError('recovery-prepared-data-root-changed')
        if native != previous['native'] or store.read(identity) != old:
            raise u.UninstallError('recovery-registration-changed')
        return old
    record = dict(schema=SCHEMA, transaction=uuid.uuid4().hex,
                  identity=identity, sid=sid, root=str(root), phase="prepared",
                  helperDigest=args.helper_inventory_sha256,
                  parent=_observe(api, root.parent, directory=True), nodes=nodes,
                  native=native, shortcuts=shortcuts, groups=groups,
                  dataRoots=[str(path) for path in data_roots if path is not None],
                  dataIdentities=data_identities,
                  nativeParent=dict(pid=args.parent_pid, created=args.parent_created,
                                    executable=args.parent_executable, sha256=args.parent_sha256))
    raw = _encode(record)
    store.replace(identity, old, raw)
    if store.read(identity) != raw:
        raise u.UninstallError("recovery-registration-readback-failed")
    return raw


def ready(store, identity, raw):
    record = json.loads(raw)
    record["phase"] = "native-ready"
    replacement = _encode(record)
    store.replace(identity, raw, replacement)
    if store.read(identity) != replacement:
        raise u.UninstallError("recovery-registration-readback-failed")


class _SnapshotPlan(u._TreePlan):
    def __init__(self, api, root, snapshot, checkpoint):
        super().__init__(api, root, mode="recovery", checkpoint=checkpoint)
        self.snapshot = snapshot

    def _classify(self, relative, is_dir):
        row = self.snapshot.get(relative)
        if row is None or row.get("directory") != is_dir:
            raise u.UninstallError("recovery-unknown-or-replaced-entry", remaining=(relative,))
        return "delete"

    def scan(self, **kwargs):
        super().scan(**kwargs)
        for node in self.nodes:
            row = self.snapshot.get(node["relative"])
            if (row is None or row.get("identity") != list(node["identity"])
                    or row.get("directory") != node["directory"]
                    or row.get("owner") != u._owner_sid(node["handle"])):
                raise u.UninstallError("recovery-object-identity-changed", remaining=(node["relative"] or ".",))
            native_log = row.get('nativeLog') is True and u._native_file_name(node['relative']) and node['path'].suffix.casefold() == '.dat'
            resource_lock = row.get('resourceLock') is True and node['relative'] == 'work/map-packages/import.lock'
            if not node["directory"] and not native_log and not resource_lock and _digest_handle(self.api, node["handle"]) != row.get("sha256"):
                raise u.UninstallError("recovery-file-bytes-changed", remaining=(node["relative"],))
        return self


def _native_check(record, registry, store):
    original = [item["row"] for item in record["native"]]
    root_key = u._path_key(record["root"])
    for row in registry.registrations():
        location = u._path_key(row["install_location"])
        if (row["identity"] == record["identity"] or location == root_key
                or location.startswith(root_key + "\\") or root_key.startswith(location + "\\")):
            if row not in original:
                raise u.UninstallError("recovery-native-registration-conflict")
    for item in record["native"]:
        actual = store.snapshot_native(item["row"])
        if actual is not None and actual != item["values"]:
            raise u.UninstallError("recovery-native-registration-changed")


def recover(args, *, registry_provider=None, owner_sid_provider=None, profile_provider=None, store=None):
    """Actual CLI business path. Only native identity adapters are replaceable."""
    plans, pinned, shortcut_nodes = [], [], []
    mutation = False
    try:
        result_dir = u._validate_result_directory(Path(args.result_directory))
    except Exception:
        return 2
    deadline = time.monotonic() + args.timeout_seconds

    def checkpoint():
        if time.monotonic() >= deadline:
            raise u.UninstallError("cleanup-time-limit")

    def began():
        nonlocal mutation
        checkpoint()
        mutation = True

    try:
        profile = profile_provider() if profile_provider else u._baked_profile()
        identity, expected = u._profile_identity(profile)
        sid = owner_sid_provider() if owner_sid_provider else u._current_user_sid()
        store = store or RecoveryStore()
        raw = store.read(identity)
        record = _state(raw, identity, sid, args.helper_inventory_sha256,
                        None if args.install_root == "@registered" else args.install_root)
        if record["phase"] != "native-ready":
            raise u.UninstallError("recovery-data-not-cleared-use-original-uninstaller")
        root = Path(record["root"])
        if expected['production'] and record["nodes"].get("", {}).get("owner") != sid:
            raise u.UninstallError("recovery-root-owner-mismatch")
        result_dir = u._validate_result_directory(result_dir, root)
        api = u._kernel()
        if _observe(api, root.parent, directory=True) != record["parent"]:
            raise u.UninstallError("recovery-parent-identity-changed")
        u._pin_directory(api, root.parent, pinned)
        registry = registry_provider or u._RegistryProvider(sid)
        _native_check(record, registry, store)
        # No new data is adopted after the data phase. Missing old resources
        # are expected; a new tree, even with the same name, is not ours.
        for value in record["dataRoots"]:
            path = Path(value)
            u._safe_ancestors(path)
            if path.exists():
                raise u.UninstallError("recovery-data-reappeared", remaining=(str(path),))
        u._safe_ancestors(root)
        if root.exists():
            plan = _SnapshotPlan(api, root, record["nodes"], checkpoint)
            plans.append(plan)
            plan.scan(exe_name=expected["executable"])
        run_state = u._validate_run_values(registry, root) if expected["production"] else None
        if run_state and (run_state[1] is not None or run_state[2]):
            raise u.UninstallError("recovery-startup-data-reappeared")
        for value, row in record["shortcuts"].items():
            path = Path(value)
            u._safe_ancestors(path)
            if path.exists():
                handle, info = u._open_handle(api, path, directory=False, delete=True, read=True,
                                             read_control=True, share=u._FILE_SHARE_READ)
                shortcut_nodes.append((handle, path))
                if (list(u._file_id(info)) != row["identity"] or u._handle_path(api, handle) != u._path_key(path)
                        or u._owner_sid(handle) != row["owner"] or _digest_handle(api, handle) != row["sha256"]):
                    raise u.UninstallError("recovery-shortcut-changed", remaining=(str(path),))
        # Group handles pin and later remove only an originally exclusive group.
        groups = []
        for value, row in record["groups"].items():
            path = Path(value)
            u._safe_ancestors(path)
            if path.exists():
                handle, info = u._open_handle(api, path, directory=True, delete=True, read_control=True)
                pinned.append(handle)
                if (list(u._file_id(info)) != row["identity"] or u._handle_path(api, handle) != u._path_key(path)
                        or u._owner_sid(handle) != row['owner']
                        or any(str(child) not in record["shortcuts"] for child in path.iterdir())):
                    raise u.UninstallError("recovery-shortcut-group-changed")
                groups.append((handle, path))
        for plan in plans:
            plan.assert_unchanged()
        checkpoint()
        if store.read(identity) != raw:
            raise u.UninstallError("recovery-registration-changed")
        _native_check(record, registry, store)
        for plan in plans:
            plan.delete_files(began)
            plan.delete_directories(began, include_root=True)
            plan.close()
        for handle, path in list(shortcut_nodes):
            began()
            u._mark_delete(api, handle, str(path))
            api.CloseHandle(handle)
            shortcut_nodes.remove((handle, path))
        for handle, path in groups:
            began()
            u._mark_delete(api, handle, str(path))
            api.CloseHandle(handle)
            pinned.remove(handle)
        for item in record["native"]:
            began()
            store.delete_native(item["row"], item["values"])
        # Independently enumerate state after every handle has been closed.
        for path in [root, *(Path(x) for x in record["dataRoots"]),
                     *(Path(x) for x in record["shortcuts"]), *(Path(x) for x in record["groups"])]:
            u._safe_ancestors(path)
            if path.exists():
                raise u.UninstallError("recovery-remaining-entry", remaining=(str(path),))
        for item in record["native"]:
            if store.snapshot_native(item["row"]) is not None:
                raise u.UninstallError("recovery-native-registration-remains")
        _native_check(record, registry, store)
        began()
        store.replace(identity, raw, None)
        if store.read(identity) is not None:
            raise u.UninstallError("recovery-registration-remains")
        u._write_receipt(result_dir, status="cleared", mutation_started=mutation)
        return 0
    except Exception as error:
        try:
            u._write_receipt(result_dir, status="error", mutation_started=mutation,
                             error=error, remaining=getattr(error, "remaining", ()) or ("recovery-not-confirmed",))
        except Exception:
            pass
        return 2
    finally:
        for plan in plans:
            plan.close()
        for handle, _ in shortcut_nodes:
            api.CloseHandle(handle)
        for handle in reversed(pinned):
            api.CloseHandle(handle)
