"""Pre-native legacy migration preparation for the current Setup helper.

No old executable is run, no registration is written and no source is deleted.
The prepared copy is deliberately not an installed product: historical QtIFW
retirement and the native handoff must be accepted before it can be published.
"""
from contextlib import contextmanager
from pathlib import Path
import argparse
import base64
import configparser
import hashlib
import json
import os
import re

import legacy_migration
from packaged_product_identity import PRODUCTION_APP_ID, PRODUCTION_PRODUCT
from resource_transaction import _safe

UNINSTALL = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
INNO_KEY = '{' + PRODUCTION_APP_ID + '}_is1'


class LegacyBridgeError(ValueError):
    pass


def _path(value):
    if (not isinstance(value, (str, os.PathLike)) or not Path(value).is_absolute()
            or any(c in os.fspath(value) for c in ('"', '\r', '\n', '\0'))):
        raise LegacyBridgeError('absolute-local-path-required')
    path = _safe(Path(os.path.abspath(value)))
    if path == Path(path.anchor):
        raise LegacyBridgeError('independent-install-root-required')
    return path


def _same(left, right):
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode('ascii')).hexdigest()


def _registry_rows(source):
    """Read complete matching current-user records, including value types.

    Do not collect or log unrelated applications' values. Machine registrations
    are observed only to reject ambiguity, never claimed for this user.
    """
    import winreg
    rows = []
    for hive_name, hive in (('HKCU', winreg.HKEY_CURRENT_USER), ('HKLM', winreg.HKEY_LOCAL_MACHINE)):
        # HKCU Software is shared between WOW64 views. Enumerating it twice
        # would turn one real installation into an apparent identity conflict.
        views = ((0, 0),) if hive_name == 'HKCU' else (
            (64, winreg.KEY_WOW64_64KEY), (32, winreg.KEY_WOW64_32KEY))
        for view, flag in views:
            try:
                parent = winreg.OpenKey(hive, UNINSTALL, 0, winreg.KEY_READ | flag)
            except FileNotFoundError:
                continue
            with parent:
                count = winreg.QueryInfoKey(parent)[0]
                if count > 20000:
                    raise LegacyBridgeError('registration-count-exceeded')
                for index in range(count):
                    name = winreg.EnumKey(parent, index)
                    with winreg.OpenKey(parent, name, 0, winreg.KEY_READ | flag) as key:
                        try:
                            location, kind = winreg.QueryValueEx(key, 'InstallLocation')
                        except FileNotFoundError:
                            continue
                        if kind != winreg.REG_SZ or not isinstance(location, str) or not _same(location, source):
                            continue
                        subkeys, values, _ = winreg.QueryInfoKey(key)
                        if subkeys or values > 256:
                            raise LegacyBridgeError('registration-shape-unsupported')
                        record = {}
                        for i in range(values):
                            field, value, value_type = winreg.EnumValue(key, i)
                            if isinstance(value, bytes):
                                value = {'base64': base64.b64encode(value).decode('ascii')}
                            record[field] = [value_type, value]
                        rows.append(dict(hive=hive_name, view=view, key=name, values=record))
    return rows


def _string(row, name):
    item = row['values'].get(name)
    if not isinstance(item, list) or len(item) != 2 or item[0] != 1 or not isinstance(item[1], str):
        raise LegacyBridgeError('registration-incomplete')
    return item[1]


def probe_source(source, *, registry_reader=None):
    source = _path(source)
    from installer_uninstall import _profile_identity, _read_json
    # Repository QtIFW puts the payload under TargetDir/program, with its
    # maintenance tool beside program. Flat Inno installs use the root itself.
    candidates = [root for root in (source, _safe(source / 'program'))
                  if _safe(root / '_internal/installation-profile.json').is_file()]
    if len(candidates) != 1:
        raise LegacyBridgeError('unique-program-layout-required')
    program = candidates[0]
    profile = _read_json(program / '_internal/installation-profile.json', 128 * 1024)
    name, _ = _profile_identity(profile)
    if name != 'production':
        raise LegacyBridgeError('production-source-required')
    rows = (registry_reader or _registry_rows)(source)
    if len(rows) != 1 or rows[0]['hive'] != 'HKCU':
        raise LegacyBridgeError('unique-current-user-registration-required')
    row = rows[0]
    label = _string(row, 'DisplayName')
    version = _string(row, 'DisplayVersion')
    if not re.fullmatch(r'[0-9A-Za-z.+_-]{1,100}', version) or label not in {
            PRODUCTION_PRODUCT, PRODUCTION_PRODUCT + ' ' + version}:
        raise LegacyBridgeError('registered-product-mismatch')
    if not _same(_string(row, 'InstallLocation'), source):
        raise LegacyBridgeError('registered-root-mismatch')
    command = _string(row, 'UninstallString')
    # Exact executable only. Never parse/execute an arbitrary command line.
    match = re.fullmatch(r'"([^"\r\n]+)"', command)
    executable = _path(match[1] if match else command)
    if not _same(executable.parent, source):
        raise LegacyBridgeError('maintenance-tool-outside-source')
    if row['key'] == INNO_KEY and re.fullmatch(r'unins[0-9]{3}\.exe', executable.name, re.I):
        if program != source:
            raise LegacyBridgeError('inno-program-layout-unverified')
        source_kind = 'inno'
    elif (row['key'] != INNO_KEY and executable.name.casefold() in {
            'maintenancetool.exe', 'trial-maintenance.exe'} and
            _safe(executable.with_suffix('.dat')).is_file() and
            _safe(source / 'components.xml').is_file()):
        source_kind = 'qtifw'
    else:
        raise LegacyBridgeError('maintenance-format-unverified')
    tool = legacy_migration._file(executable)
    identity = dict(sourceKind=source_kind, product=PRODUCTION_PRODUCT,
                    appId=PRODUCTION_APP_ID, installRoot=str(program),
                    dataRoot=str(program / 'work'), uninstallRecordSha256=_digest(row),
                    maintenanceToolSha256=tool['sha256'])
    return identity, executable


@contextmanager
def _closed_source(source, tool, program, asset_roots=None):
    # An open write-capable handle conflicts with an executing image. While
    # held it also prevents a new image section or replacement of that file.
    # The handle is used only for exclusion; no WriteFile/Delete API is called.
    from installer_uninstall import _kernel, _open_handle, _pin_directory
    api, held = _kernel(), []
    try:
        _pin_directory(api, source, held)
        if program != source:
            _pin_directory(api, program, held)
        external = (asset_roots or {}).get('external')
        if external is not None:
            local = external.parents[1]
            _safe(local)
            local_handle = _pin_directory(api, local, held)
            external_handle = _pin_directory(api, external, held)
            from installer_uninstall import _owner_sid
            if _owner_sid(local_handle) != _owner_sid(external_handle):
                raise LegacyBridgeError('external-resource-owner-mismatch')
        for label, root in (asset_roots or {}).items():
            if label != 'external':
                _pin_directory(api, root, held)
        for path in (tool, program / '星图.exe', program / '第五人格小抄助手.exe',
                     program / 'XingtuGameWatcher.exe'):
            if _safe(path).exists():
                handle, _ = _open_handle(api, path, directory=False, write=True, share=1)
                held.append(handle)
        yield
    finally:
        for handle in reversed(held):
            api.CloseHandle(handle)


def _asset_roots(program, external_root, *, require_locks=True):
    """Return only product-bound legacy resource roots; never scan a user tree."""
    candidates = {
        'external': external_root,
        'outer-custom-maps': program / 'assets/custom-maps',
        'internal-custom-maps': program / '_internal/assets/custom-maps',
        'editor-base': program / '_internal/qt-editor-base',
        'imported-resources': program / 'work/imported-resources',
    }
    roots = {}
    for label, value in candidates.items():
        path = _safe(Path(value))
        if path.exists():
            if not path.is_dir():
                raise LegacyBridgeError('asset-root-not-directory')
            if (require_locks and label == 'external'
                    and not (path / 'work/map-packages/import.lock').is_file()):
                raise LegacyBridgeError('external-resource-lock-missing')
            roots[label] = path
    return roots


def prepare(source, target, *, registry_reader=None, source_guard=None, external_root=None):
    """Copy and verify a resumable preparation, never report an installed upgrade.

    Injectable read/guard surfaces are only for directory fixtures. The CLI has
    no fixture switches and always uses native Win32 registry and image locks.
    """
    source, target = _path(source), _path(target)
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise LegacyBridgeError('overlapping-install-roots')
    identity, tool = probe_source(source, registry_reader=registry_reader)
    program = Path(identity['installRoot'])
    # Inno same-root upgrades already use the preserve coordinator. Moving a
    # registered Inno instance is a different operation and is not guessed here.
    if identity['sourceKind'] != 'qtifw':
        raise LegacyBridgeError('inno-upgrade-must-use-registered-root')
    if external_root is None:
        from installer_native_observation import WindowsReadOnlyBackend, _LOCAL_APP_DATA
        external_root = Path(WindowsReadOnlyBackend()._known_folder_path(_LOCAL_APP_DATA)) / 'Xingtu/resources'
    external_root = _safe(Path(external_root))
    roots = _asset_roots(program, external_root)
    prepared = _safe(target / '.legacy-upgrade-prepared')
    if target.exists() and (not target.is_dir() or any(p != prepared for p in target.iterdir())):
        raise LegacyBridgeError('fresh-target-required')
    guard = source_guard or _closed_source
    import inspect
    try:
        inspect.signature(guard).bind(source, tool, program, roots)
    except (TypeError, ValueError):
        # Narrow compatibility for injected three-argument fixture guards.
        guard_context = guard(source, tool, program)
    else:
        guard_context = guard(source, tool, program, roots)
    with guard_context:
        locked_identity, _ = probe_source(source, registry_reader=registry_reader)
        if locked_identity != identity:
            raise LegacyBridgeError('source-identity-changed')
        if _asset_roots(program, external_root, require_locks=False) != roots:
            raise LegacyBridgeError('asset-root-set-changed')
        result = legacy_migration.migrate(program, prepared, identity=identity, extra_roots=roots)
        if _asset_roots(program, external_root, require_locks=False) != roots:
            raise LegacyBridgeError('asset-root-set-changed')
        if probe_source(source, registry_reader=registry_reader)[0] != identity:
            raise LegacyBridgeError('source-identity-changed')
    return dict(status='prepared-native-unverified', copiedFiles=result['files'],
                nativeInstalled=False, oldEntryRetired=False, sourceDeleted=False,
                assetRoots=sorted(roots), inventorySha256=result['inventorySha256'],
                evidence='native' if registry_reader is None and source_guard is None else 'fixture',
                remaining='historical-retirement-and-native-handoff-unverified')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--result-directory', required=True)
    args = parser.parse_args(argv)
    try:
        result_dir = _path(args.result_directory)
        if not result_dir.is_dir() or any(result_dir.iterdir()):
            return 2
        for root in (_path(args.source), _path(args.target)):
            if result_dir == root or result_dir.is_relative_to(root) or root.is_relative_to(result_dir):
                return 2
    except (OSError, ValueError):
        return 2
    try:
        # Only this build's production helper may serve Setup. Source invocation
        # is useful for tests through prepare(), not a production bypass.
        from installer_uninstall import _baked_profile, _profile_identity
        if _profile_identity(_baked_profile())[0] != 'production' or os.name != 'nt':
            raise LegacyBridgeError('production-windows-helper-required')
        result = prepare(args.source, args.target)
        code = 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Paths, registry values, user filenames and arbitrary exception text
        # are intentionally not exported into the installer receipt.
        result, code = dict(status='error', nativeInstalled=False, sourceDeleted=False,
                            errorCode=str(error) if isinstance(error, LegacyBridgeError)
                            else 'legacy-preparation-failed'), 2
    receipt = configparser.ConfigParser(interpolation=None)
    receipt.optionxform = str
    receipt['result'] = {key: str(value) for key, value in result.items()}
    with _safe(result_dir / 'result.ini').open('x', encoding='utf-16') as stream:
        receipt.write(stream)
    return code
