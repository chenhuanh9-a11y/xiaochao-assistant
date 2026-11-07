"""Offline owned fixtures and mocked Win32 calls, NOT native Setup validation."""
import base64
from contextlib import contextmanager
import copy
import ctypes
import hashlib
import json
import ntpath
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import installer_native_observation as observe


ROOT = r'C:\Users\fixture\App\Xingtu'
PROGRAMS = r'C:\Users\fixture\Start Menu\Programs'
DESKTOP = r'C:\Users\fixture\Desktop'
GROUP = PROGRAMS + r'\Final Group'
SID = 'S-1-5-21-100-200-300-1001'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sz(name, value, kind=1):
    return dict(name=name, type=kind, rawBase64=base64.b64encode((value + '\0').encode('utf-16-le')).decode())


def link_bytes(target, *, arguments=''):
    """Small synthetic MS-SHLLINK, no Windows creation/execution implied."""
    header = bytearray(76)
    struct.pack_into('<I', header, 0, 76)
    header[4:20] = uuid.UUID('00021401-0000-0000-c000-000000000046').bytes_le
    struct.pack_into('<I', header, 20, 2 | 128 | (32 if arguments else 0))
    volume = struct.pack('<4I', 16, 3, 123, 16)
    ansi = b'C:\\placeholder.exe\0'
    suffix = b'\0'
    local_u = (target + '\0').encode('utf-16-le')
    suffix_u = b'\0\0'
    offsets = [36, 36 + len(volume), 36 + len(volume) + len(ansi)]
    u_offset = offsets[2] + len(suffix)
    size = u_offset + len(local_u) + len(suffix_u)
    info = struct.pack('<9I', size, 36, 1, offsets[0], offsets[1], 0, offsets[2], u_offset, u_offset + len(local_u))
    data = bytes(header) + info + volume + ansi + suffix + local_u + suffix_u
    if arguments:
        encoded = arguments.encode('utf-16-le')
        data += struct.pack('<H', len(encoded) // 2) + encoded
    return data + b'\0\0\0\0'


class OwnedFixtureBackend:
    """Real files only under TemporaryDirectory; registry and users are mocks."""
    def __init__(self, directory):
        self.directory_path = Path(directory)
        self.sid = SID
        self.shell_sid = SID
        self.root_nonce = 1
        self.pass_count = 0
        self.on_pass = None
        self.calls = []
        self.registry_value = dict(path='\\REGISTRY\\USER\\' + SID + '\\' + observe.UNINSTALL_KEY,
            lastWrite=123456789, keyClass='', valueCount=3, subkeyCount=0,
            values=[sz('InstallLocation', ROOT + '\\'), sz('DisplayVersion', '1.2.0'),
                    sz('UninstallString', '"' + ROOT + '\\unins017.exe"')], subkeys=[])

    def local(self, path):
        observe._path(path)
        return self.directory_path.joinpath(*path[3:].split('\\'))

    def put(self, path, data):
        target = self.local(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    @contextmanager
    def read_session(self):
        self.pass_count += 1
        if self.on_pass:
            self.on_pass(self.pass_count)
        yield self

    def users(self):
        return dict(tokenSid=self.sid, shellSid=self.shell_sid)

    def directory(self, path):
        stat = self.local(path).stat()
        return dict(state='directory', identity=[stat.st_dev, stat.st_ino], nonce=self.root_nonce)

    def file(self, path, *, required, raw=False, limit=observe.MAX_FILE):
        self.calls.append(('file', path))
        local = self.local(path)
        if any(p.is_symlink() for p in (local, *local.parents)):
            raise observe.NativeObservationError('fixture-linked')
        if not local.exists():
            if required:
                raise observe.NativeObservationError('fixture-required-missing')
            return dict(state='absent')
        data = local.read_bytes()
        if len(data) > limit:
            raise observe.NativeObservationError('fixture-limit')
        stat = local.stat()
        result = dict(state='file', identity=[stat.st_dev, stat.st_ino],
                      bytes=len(data), sha256=digest(data), written=stat.st_mtime_ns)
        if raw:
            result['rawBase64'] = base64.b64encode(data).decode()
        return result

    def registry(self, sid):
        self.calls.append(('registry', observe.UNINSTALL_KEY))
        return copy.deepcopy(self.registry_value)

    def absent(self, path):
        self.calls.append(('absent', path))
        return dict(state='present' if self.local(path).exists() else 'absent')

    def shortcut_locations(self):
        return dict(programs=PROGRAMS, desktop=DESKTOP)

    def shortcut_target(self, data):
        return observe._shortcut_target(data)


class ObservationFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = OwnedFixtureBackend(self.temp.name)
        self.target = dict(sourceCommit='a' * 40, displayVersion='1.2.0', numericVersion='1.0.0.24',
                           executableSha256=digest(b'current main exe'), resourceInventorySha256='b' * 64)
        self.report = json.dumps(dict(self.target, sourceDirty=False)).encode()
        self.ownership = ('\ufeff星图.exe\nrelease-report.json\n_installer-owned-files.txt\n'
                          '_internal\\code.pyd\n_internal\\managed-base-resources\\payload\\_internal\\assets\\map.png\n'
                          '_internal\\qt-editor-base\\factory.png\n_internal\\assets\\custom-maps\\user.png\n'
                          '_internal\\references\\build-fingerprint.txt\n_internal\\assets\\active.png\n').encode()
        payload = {'星图.exe': b'current main exe', 'release-report.json': self.report,
                   '_installer-owned-files.txt': self.ownership, '_internal/code.pyd': b'full program runtime',
                   '_internal/managed-base-resources/payload/_internal/assets/map.png': b'candidate immutable image',
                   '_internal/qt-editor-base/factory.png': b'factory baseline',
                   '_internal/references/build-fingerprint.txt': b'identity'}
        self.inventory = {name: dict(bytes=len(data), sha256=digest(data)) for name, data in payload.items()}
        for name, data in payload.items():
            self.backend.put(ROOT + '\\' + name.replace('/', '\\'), data)
        self.backend.put(ROOT + r'\unins017.exe', b'current uninstall executable')
        self.backend.put(ROOT + r'\unins017.dat', b'current native uninstall log')
        self.shortcuts = [dict(path=GROUP + r'\星图.lnk', target=ROOT + r'\星图.exe', enabled=True),
                          dict(path=GROUP + r'\卸载星图.lnk', target=ROOT + r'\unins017.exe', enabled=True),
                          dict(path=DESKTOP + r'\星图.lnk', target=ROOT + r'\星图.exe', enabled=False)]
        for row in self.shortcuts:
            if row['enabled']:
                self.backend.put(row['path'], link_bytes(row['target']))
        self.kw = dict(target_identity=self.target, target_report_sha256=digest(self.report),
                       ownership_bytes=self.ownership, ownership_sha256=digest(self.ownership),
                       program_inventory=self.inventory, uninstall_exe=ROOT + r'\unins017.exe',
                       shortcut_targets=self.shortcuts, _backend=self.backend)

    def capture(self):
        return observe.capture_native_completion(ROOT, **self.kw)

    def verify(self, evidence):
        return observe.verify_native_completion(evidence, ROOT, **self.kw)

    def test_capture_and_replay_fixture_label_not_native(self):
        result = self.capture()
        self.assertEqual('fixture-only', result['source'])
        self.assertEqual(2, self.backend.pass_count)
        self.assertEqual(result, self.verify(result))
        self.assertEqual('absent', result['observation']['uninstall']['.msg']['state'])
        self.assertEqual('absent', result['observation']['shortcuts'][0]['file']['state'])
        self.assertEqual(set(self.inventory), set(result['observation']['program']))

    def test_known_fixture_capture_fits_conservative_preflight(self):
        from test_native_evidence_budget import node_count
        evidence = self.capture()
        kw = {k: v for k, v in self.kw.items() if k != '_backend'}
        budget = observe.preflight_native_evidence(ROOT, **kw)
        known = copy.deepcopy(evidence)
        known['observation'].update(users={}, uninstall={}, registry={}, shortcuts=[])
        self.assertLessEqual(len(observe._encoded(known)), budget['knownBytes'])
        self.assertLessEqual(node_count(known), budget['knownNodes'])

    def test_fixture_cannot_be_replayed_as_native(self):
        result = self.capture()
        args = dict(self.kw)
        args.pop('_backend')
        with self.assertRaisesRegex(observe.NativeObservationError, 'backend-mismatch'):
            observe.verify_native_completion(result, ROOT, **args)

    @unittest.skipIf(os.name == 'nt', 'Linux-specific refusal, not a Windows success test')
    def test_default_backend_refuses_non_windows(self):
        args = dict(self.kw)
        args.pop('_backend')
        with self.assertRaisesRegex(observe.NativeObservationError, 'windows-required'):
            observe.capture_native_completion(ROOT, **args)
        with self.assertRaisesRegex(observe.NativeObservationError, 'windows-required'):
            observe.current_user_sid()

    def test_full_inventory_filters_only_non_program_active_planes(self):
        paths = observe.program_paths_from_ownership(self.ownership)
        self.assertEqual(set(self.inventory), set(paths))
        self.assertIn('_internal/qt-editor-base/factory.png', paths)
        self.assertIn('_internal/references/build-fingerprint.txt', paths)
        self.assertFalse(any('active.png' in name or 'user.png' in name for name in paths))
        self.capture()
        self.assertFalse(any('active.png' in path or 'user.png' in path for _, path in self.backend.calls))

    def test_inventory_omission_or_extra_rejected(self):
        for change in ('omit', 'extra'):
            with self.subTest(change=change):
                inv = copy.deepcopy(self.inventory)
                if change == 'omit':
                    inv.pop('_internal/code.pyd')
                else:
                    inv['work/user.py'] = dict(bytes=1, sha256='c' * 64)
                with self.assertRaisesRegex(observe.NativeObservationError, 'incomplete-program-inventory'):
                    observe.capture_native_completion(ROOT, **dict(self.kw, program_inventory=inv))

    def test_target_program_hash_must_match_for_every_file(self):
        self.backend.put(ROOT + r'\_internal\code.pyd', b'old runtime')
        with self.assertRaisesRegex(observe.NativeObservationError, 'program-target-mismatch'):
            self.capture()

    def test_same_bytes_replacement_file_identity_blocks_replay(self):
        result = self.capture()
        old = self.backend.local(ROOT + r'\_internal\code.pyd')
        data = old.read_bytes()
        renamed = old.with_suffix('.retained')
        old.rename(renamed)
        old.write_bytes(data)
        self.assertNotEqual(old.stat().st_ino, renamed.stat().st_ino)
        with self.assertRaisesRegex(observe.NativeObservationError, 'evidence-no-longer-matches'):
            self.verify(result)

    def test_changed_program_between_reads_fails(self):
        def change(number):
            if number == 2:
                self.backend.put(ROOT + r'\_internal\code.pyd', b'other bytes')
        self.backend.on_pass = change
        with self.assertRaises(observe.NativeObservationError):
            self.capture()

    def test_changed_registry_between_reads_fails(self):
        def change(number):
            if number == 2:
                self.backend.registry_value['lastWrite'] += 1
        self.backend.on_pass = change
        with self.assertRaisesRegex(observe.NativeObservationError, 'changed-during-capture'):
            self.capture()

    def test_same_account_as_shell_is_required(self):
        for bad in ('S-1-5-21-100-200-300-999', '', None):
            self.backend.shell_sid = bad
            with self.subTest(sid=bad), self.assertRaisesRegex(observe.NativeObservationError, 'interactive-user'):
                self.capture()

    def test_new_token_sid_cannot_replay_even_when_shell_matches(self):
        result = self.capture()
        self.backend.sid = self.backend.shell_sid = 'S-1-5-21-100-200-300-999'
        self.backend.calls.clear()
        with self.assertRaisesRegex(observe.NativeObservationError, 'recorded-owner-mismatch'):
            self.verify(result)

    def test_different_physical_root_identity_cannot_replay(self):
        result = self.capture()
        self.backend.root_nonce += 1
        self.backend.calls.clear()
        with self.assertRaisesRegex(observe.NativeObservationError, 'recorded-physical-root-mismatch'):
            self.verify(result)

    def test_uninstall_number_is_not_assumed_zero(self):
        self.assertIn('unins017.exe', self.capture()['contract']['uninstallExe'])
        self.assertFalse(any('unins000' in path for _, path in self.backend.calls))

    def test_uninstall_escape_or_non_standard_executable_rejected(self):
        for name in (r'C:\Elsewhere\unins017.exe', ROOT + r'\not-uninstaller.exe', ROOT + r'\unins1000.exe',
                     ROOT + r'\sub\unins017.exe', ROOT + r'\unins017.exe:ads'):
            with self.subTest(path=name), self.assertRaises(observe.NativeObservationError):
                observe.capture_native_completion(ROOT, **dict(self.kw, uninstall_exe=name))

    def test_uninstall_exe_and_dat_required(self):
        for suffix in ('exe', 'dat'):
            file = self.backend.local(ROOT + '\\unins017.' + suffix)
            data = file.read_bytes()
            file.unlink()
            with self.subTest(suffix=suffix), self.assertRaises(observe.NativeObservationError):
                self.capture()
            file.write_bytes(data)

    def test_msg_appearance_or_uninstall_change_blocks(self):
        result = self.capture()
        self.backend.put(ROOT + r'\unins017.msg', b'later msg')
        with self.assertRaisesRegex(observe.NativeObservationError, 'evidence-no-longer-matches'):
            self.verify(result)

    def test_registry_identity_fields_types_and_commands_required(self):
        original = copy.deepcopy(self.backend.registry_value)
        cases = [('InstallLocation', r'C:\Elsewhere'), ('DisplayVersion', '1.1.0'),
                 ('UninstallString', '"' + ROOT + r'\unins017.exe" /unexpected')]
        for name, text in cases:
            self.backend.registry_value = copy.deepcopy(original)
            self.backend.registry_value['values'] = [sz(name, text) if r['name'] == name else r
                                                     for r in original['values']]
            with self.subTest(name=name), self.assertRaises(observe.NativeObservationError):
                self.capture()
        self.backend.registry_value = copy.deepcopy(original)
        self.backend.registry_value['values'][0]['type'] = 2
        with self.assertRaises(observe.NativeObservationError):
            self.capture()

    def test_registry_any_extra_value_subkey_or_timestamp_change_blocks(self):
        result = self.capture()
        original = copy.deepcopy(self.backend.registry_value)
        mutations = [lambda r: r['values'].append(sz('NewValue', 'new')),
                     lambda r: r['subkeys'].append(dict(name='Later', evidence={})),
                     lambda r: r.update(lastWrite=r['lastWrite'] + 1)]
        for mutation in mutations:
            self.backend.registry_value = copy.deepcopy(original)
            mutation(self.backend.registry_value)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(observe.NativeObservationError, 'evidence-no-longer-matches'):
                self.verify(result)

    def test_enabled_link_must_exist_and_point_to_target(self):
        self.backend.put(self.shortcuts[0]['path'], link_bytes(r'C:\Other\app.exe'))
        with self.assertRaisesRegex(observe.NativeObservationError, 'shortcut-target-mismatch'):
            self.capture()

    def test_enabled_link_with_arguments_or_unknown_format_rejected(self):
        for data in (link_bytes(ROOT + r'\星图.exe', arguments='/other-mode'), b'not a shell link'):
            self.backend.put(self.shortcuts[0]['path'], data)
            with self.subTest(data=data[:10]), self.assertRaises(observe.NativeObservationError):
                self.capture()

    def test_disabled_user_link_is_observed_without_target_authority(self):
        self.backend.put(self.shortcuts[2]['path'], b'user-owned opaque bytes')
        result = self.capture()
        self.assertEqual(result, self.verify(result))
        self.backend.put(self.shortcuts[2]['path'], b'changed by user later')
        with self.assertRaisesRegex(observe.NativeObservationError, 'evidence-no-longer-matches'):
            self.verify(result)

    def test_disabled_shortcut_absence_then_creation_blocks(self):
        result = self.capture()
        self.backend.put(self.shortcuts[2]['path'], b'later link')
        with self.assertRaisesRegex(observe.NativeObservationError, 'evidence-no-longer-matches'):
            self.verify(result)

    def test_same_name_pif_url_or_folder_collision_rejected(self):
        for suffix in ('.pif', '.url', ''):
            local = self.backend.local(self.shortcuts[0]['path'][:-4] + suffix)
            if suffix:
                local.write_bytes(b'collision')
            else:
                local.mkdir()
            with self.subTest(suffix=suffix), self.assertRaisesRegex(observe.NativeObservationError, 'shortcut-collision'):
                self.capture()
            local.unlink() if suffix else local.rmdir()

    def test_other_users_shortcut_path_rejected_without_reading_it(self):
        self.shortcuts[2]['path'] = r'C:\Users\someone-else\Desktop\星图.lnk'
        with self.assertRaises(observe.NativeObservationError):
            self.capture()
        self.assertFalse(any('someone-else' in path for _, path in self.backend.calls))

    def test_group_links_need_one_final_group_under_current_programs(self):
        self.shortcuts[1]['path'] = PROGRAMS + r'\Other group\卸载星图.lnk'
        with self.assertRaisesRegex(observe.NativeObservationError, 'shortcut-group-outside'):
            self.capture()

    def test_owner_bytes_modified_and_unsafe_paths_rejected(self):
        for name in ('../escape', 'work/../evil', 'C:/escape', 'a:ads', 'CON.txt', 'space. /file', 'a//b'):
            with self.subTest(name=name), self.assertRaises(observe.NativeObservationError):
                observe.program_paths_from_ownership(self.ownership + name.encode() + b'\n')
        with self.assertRaisesRegex(observe.NativeObservationError, 'ownership-hash-mismatch'):
            observe.capture_native_completion(ROOT, **dict(self.kw, ownership_bytes=self.ownership + b'evil.exe\n'))

    def test_duplicate_case_inventory_paths_fail(self):
        with self.assertRaisesRegex(observe.NativeObservationError, 'duplicate-ownership-path'):
            observe.program_paths_from_ownership(self.ownership + b'RELEASE-report.json\n')

    def test_broken_or_tampered_evidence_blocks_replay(self):
        good = self.capture()
        for corrupt in (None, {}, dict(good, sha256='c' * 64), dict(good, extra=1)):
            with self.subTest(value=corrupt is None), self.assertRaises(observe.NativeObservationError):
                self.verify(corrupt)
        changed = copy.deepcopy(good)
        changed['observation']['registry']['lastWrite'] += 1
        unsigned = {k: v for k, v in changed.items() if k != 'sha256'}
        changed['sha256'] = digest(observe._encoded(unsigned))
        with self.assertRaisesRegex(observe.NativeObservationError, 'evidence-no-longer-matches'):
            self.verify(changed)

    def test_resume_context_reconstructs_old_target_without_new_setup(self):
        result = self.capture()
        context = observe.resume_context_from_evidence(result, ROOT, _backend=self.backend)
        self.assertEqual(self.target, context['target_identity'])
        self.assertEqual(self.ownership, context['ownership_bytes'])
        self.assertEqual(self.inventory, context['program_inventory'])
        self.assertEqual(result, observe.verify_native_completion(result, ROOT, **context, _backend=self.backend))

    def test_resume_context_checks_owner_and_physical_root_before_any_file(self):
        result = self.capture()
        self.backend.calls.clear()
        self.backend.sid = self.backend.shell_sid = 'S-1-5-21-100-200-300-999'
        with self.assertRaisesRegex(observe.NativeObservationError, 'recorded-owner-mismatch'):
            observe.resume_context_from_evidence(result, ROOT, _backend=self.backend)
        self.assertEqual([], self.backend.calls)
        self.backend.sid = self.backend.shell_sid = SID
        self.backend.root_nonce += 1
        with self.assertRaisesRegex(observe.NativeObservationError, 'recorded-physical-root-mismatch'):
            observe.resume_context_from_evidence(result, ROOT, _backend=self.backend)
        self.assertEqual([], self.backend.calls)

    def test_resume_rejects_replaced_or_modified_ownership(self):
        result = self.capture()
        self.backend.put(ROOT + '\\' + observe.OWNERSHIP_NAME, self.ownership + b'bad.exe\n')
        with self.assertRaisesRegex(observe.NativeObservationError, 'ownership-hash-mismatch'):
            observe.resume_context_from_evidence(result, ROOT, _backend=self.backend)

    def test_stdlib_import_has_no_gui_or_image_modules(self):
        code = ('import sys; sys.path.insert(0, "app"); import installer_native_observation; '
                'assert not any(x.split(".")[0] in {"numpy", "PIL", "cv2", "PySide6", "PyQt6"} for x in sys.modules)')
        subprocess.run([sys.executable, '-I', '-c', code], cwd=Path(__file__).resolve().parents[1], check=True)


class MockWin32Kernel:
    """Minimal handle mock. Exercises the real backend, not a native OS claim."""
    def __init__(self):
        self.opens = []
        self.closed = []
        self.attributes = 0
        self.links = 1
        self.identity = 15
        self.data = b'content'
        self.read = False
        self.path = r'\\?\Volume{00000000-1111-2222-3333-444444444444}\App\file.bin'

    def CreateFileW(self, path, access, share, security, disposition, flags, template):
        self.opens.append((path, access, share, disposition, flags))
        return 17

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1

    def GetFileType(self, handle):
        return 1

    def GetFileInformationByHandle(self, handle, pointer):
        info = pointer._obj
        info.attributes = self.attributes
        info.volume = 100
        info.links = self.links
        info.indexLow = self.identity
        info.sizeLow = len(self.data)
        info.created.dwLowDateTime = 100
        info.written.dwLowDateTime = 200
        return 1

    def GetFileInformationByHandleEx(self, handle, kind, pointer, size):
        info = pointer._obj
        info.volumeSerial = 100
        info.fileId[:] = self.identity.to_bytes(16, 'little')
        return 1

    def GetFinalPathNameByHandleW(self, handle, buffer, length, flags):
        buffer.value = self.path
        return len(self.path)

    def ReadFile(self, handle, buffer, length, count, overlapped):
        data = b'' if self.read else self.data
        self.read = True
        ctypes.memmove(buffer, data, len(data))
        count._obj.value = len(data)
        return 1


class MockRegistry:
    """Explicit tiny product subtree; no Windows registry access."""
    def __init__(self):
        self.paths = {1: '\\REGISTRY\\USER\\' + SID}
        self.opens = []
        self.closed = []
        self.enumerated = []
        self.link = None
        self.unknown_type = False
        self.stamp = 987654321
        self.product = self.paths[1] + '\\' + observe.UNINSTALL_KEY
        self.rows = {
            self.product: [('InstallLocation', 1, (ROOT + '\\\0').encode('utf-16-le')),
                           ('DisplayVersion', 1, '1.2.0\0'.encode('utf-16-le')),
                           ('Binary', 3, b'\0\xff\0'), ('Number', 4, b'\x12\0\0\0')],
            self.product + '\\Settings': [('Entries', 7, 'one\0two\0\0'.encode('utf-16-le'))],
        }

    @staticmethod
    def value(key):
        return key.value if hasattr(key, 'value') else key

    def RegOpenCurrentUser(self, access, result):
        self.opens.append(('HKCU', 0, access))
        result._obj.value = 1
        return 0

    def RegOpenKeyExW(self, key, name, options, access, result):
        self.opens.append((name, options, access))
        index = len(self.paths) + 1
        self.paths[index] = self.paths[self.value(key)] + '\\' + name
        result._obj.value = index
        return 0

    def RegCloseKey(self, key):
        self.closed.append(self.value(key))
        return 0

    def NtQueryKey(self, key, kind, buffer, length, needed):
        data = self.paths[self.value(key)].encode('utf-16-le')
        raw = struct.pack('<I', len(data)) + data
        needed._obj.value = len(raw)
        if buffer is None:
            return -1
        ctypes.memmove(buffer, raw, len(raw))
        return 0

    def RegQueryValueExW(self, key, name, reserved, kind, data, size):
        return 0 if self.paths[self.value(key)] == self.link else 2

    def RegQueryInfoKeyW(self, key, klass, size, reserved, children, max_child, max_class,
                         values, max_name, max_data, security, stamp):
        path = self.paths[self.value(key)]
        rows = self.rows[path]
        klass.value = 'mock-class'
        size._obj.value = len(klass.value)
        children._obj.value = 1 if path == self.product else 0
        values._obj.value = len(rows)
        max_data._obj.value = max(len(row[2]) for row in rows)
        stamp._obj.dwLowDateTime = self.stamp
        return 0

    def RegEnumValueW(self, key, index, name, size, reserved, kind, data, length):
        path = self.paths[self.value(key)]
        self.enumerated.append(('values', path))
        rows = self.rows[path]
        if index >= len(rows):
            return 259
        text, datatype, raw = rows[index]
        name.value = text
        size._obj.value = len(text)
        kind._obj.value = 999 if self.unknown_type else datatype
        ctypes.memmove(data, raw, len(raw))
        length._obj.value = len(raw)
        return 0

    def RegEnumKeyExW(self, key, index, name, size, reserved, klass, klass_size, stamp):
        path = self.paths[self.value(key)]
        self.enumerated.append(('children', path))
        if path != self.product or index > 0:
            return 259
        name.value = 'Settings'
        size._obj.value = len(name.value)
        stamp._obj.dwLowDateTime = self.stamp
        return 0


class MockWin32Tests(unittest.TestCase):
    def backend(self):
        backend = observe.WindowsReadOnlyBackend.__new__(observe.WindowsReadOnlyBackend)
        backend.kernel = MockWin32Kernel()
        backend._directories = {}
        return backend

    def test_real_file_backend_read_only_flags_and_raw_hash(self):
        backend = self.backend()
        parent = dict(state='directory', physicalPath=backend.kernel.path.rsplit('\\', 1)[0].casefold())
        with patch.object(backend, '_parents', return_value=[parent]):
            result = backend.file(r'C:\App\file.bin', required=True, raw=True)
        self.assertEqual(digest(b'content'), result['sha256'])
        self.assertEqual(dict(volumeSerial=100, fileId=(15).to_bytes(16, 'little').hex()), result['identity'])
        self.assertEqual(b'content', base64.b64decode(result['rawBase64']))
        _, access, share, disposition, flags = backend.kernel.opens[0]
        self.assertEqual(0x80000000, access)
        self.assertEqual(1, share)
        self.assertEqual(3, disposition)
        self.assertTrue(flags & 0x200000)  # OPEN_REPARSE_POINT
        self.assertEqual([17], backend.kernel.closed)

    def test_real_backend_rejects_reparse_directory_file_and_hardlink(self):
        for attrs, links in ((0x400, 1), (0x10, 1), (0, 2)):
            backend = self.backend()
            backend.kernel.attributes, backend.kernel.links = attrs, links
            with self.subTest(attrs=attrs, links=links), self.assertRaisesRegex(observe.NativeObservationError, 'linked-or-wrong-kind'):
                backend._info(17, directory=False)

    def test_physical_file_path_alias_rejected(self):
        backend = self.backend()
        parent = dict(state='directory', physicalPath=r'\\?\Volume{00000000-1111-2222-3333-444444444444}\Elsewhere'.casefold())
        with patch.object(backend, '_parents', return_value=[parent]), self.assertRaisesRegex(observe.NativeObservationError, 'file-path-alias'):
            backend.file(r'C:\App\file.bin', required=True)
        self.assertEqual([17], backend.kernel.closed)

    def test_same_handle_changed_identity_during_hash_rejected(self):
        backend = self.backend()
        parent = dict(state='directory', physicalPath=backend.kernel.path.rsplit('\\', 1)[0].casefold())
        original = backend.kernel.ReadFile
        def change(*args):
            result = original(*args)
            backend.kernel.identity += 1
            return result
        backend.kernel.ReadFile = change
        with patch.object(backend, '_parents', return_value=[parent]), self.assertRaisesRegex(observe.NativeObservationError, 'file-changed-during-read'):
            backend.file(r'C:\App\file.bin', required=True)

    def test_ancestor_handles_deny_delete_and_reject_reparse(self):
        backend = self.backend()
        backend.kernel.attributes = 0x400 | 0x10
        with self.assertRaisesRegex(observe.NativeObservationError, 'linked-or-wrong-kind'):
            backend.directory(r'C:\App')
        _, access, share, disposition, flags = backend.kernel.opens[0]
        self.assertEqual(0x80, access)
        self.assertEqual(3, share)  # READ|WRITE, no DELETE sharing
        self.assertEqual(3, disposition)
        self.assertEqual([17], backend.kernel.closed)

    def test_registry_link_and_unknown_link_query_fail_closed(self):
        class Registry:
            def __init__(self, result):
                self.result = result
            def RegQueryValueExW(self, *args):
                return self.result
        for status in (0, 5, 234):
            backend = self.backend()
            backend.advapi = Registry(status)
            with patch.object(backend, '_key_name', return_value='correct'), self.subTest(status=status), self.assertRaisesRegex(observe.NativeObservationError, 'registry-link-or-unknown'):
                backend._check_key(7, 'correct')

    def test_registry_nt_name_redirect_refused(self):
        backend = self.backend()
        with patch.object(backend, '_key_name', return_value='wrong'), self.assertRaisesRegex(observe.NativeObservationError, 'registry-redirected'):
            backend._check_key(7, 'expected')

    def test_registry_raw_types_no_lossy_conversion(self):
        for kind, raw in ((0, b'raw'), (1, '内容\0'.encode('utf-16-le')), (2, '%PATH%\0'.encode('utf-16-le')),
                          (3, b'\0\1\xff'), (4, b'\0' * 4), (5, b'\0' * 4),
                          (7, 'one\0two\0\0'.encode('utf-16-le')), (11, b'\0' * 8)):
            with self.subTest(kind=kind):
                observe.WindowsReadOnlyBackend._check_value(kind, raw)
        for kind, raw in ((6, b'link'), (8, b'unsupported'), (10, b'unsupported'), (999, b'x'),
                          (4, b'\0'), (1, b'x\0'), (1, b'\x00\xd8\0\0'), (7, b'\0\0')):
            with self.subTest(kind=kind, raw=raw), self.assertRaises(observe.NativeObservationError):
                observe.WindowsReadOnlyBackend._check_value(kind, raw)

    def test_complete_native_registry_read_raw_values_subkeys_and_timestamps(self):
        backend = self.backend()
        backend.advapi = backend.nt = registry = MockRegistry()
        result = backend.registry(SID)
        self.assertEqual(registry.product, result['path'])
        self.assertEqual(registry.stamp, result['lastWrite'])
        self.assertEqual('mock-class', result['keyClass'])
        self.assertEqual(['Settings'], [row['name'] for row in result['subkeys']])
        binary = next(row for row in result['values'] if row['name'] == 'Binary')
        self.assertEqual(3, binary['type'])
        self.assertEqual(b'\0\xff\0', base64.b64decode(binary['rawBase64']))
        self.assertTrue(all(access == 0x20119 for _, _, access in registry.opens))
        self.assertTrue(all(option == 8 for _, option, _ in registry.opens[1:]))
        self.assertTrue(all(path.startswith(registry.product) for _, path in registry.enumerated))
        self.assertEqual(set(registry.paths), set(registry.closed))

    def test_registry_ancestor_link_refuses_without_enumerating_outside_product(self):
        backend = self.backend()
        backend.advapi = backend.nt = registry = MockRegistry()
        registry.link = registry.paths[1] + '\\Software'
        with self.assertRaisesRegex(observe.NativeObservationError, 'registry-link-or-unknown'):
            backend.registry(SID)
        self.assertEqual([], registry.enumerated)
        self.assertEqual(set(registry.paths), set(registry.closed))

    def test_registry_unsupported_type_during_real_enumeration_refuses(self):
        backend = self.backend()
        backend.advapi = backend.nt = registry = MockRegistry()
        registry.unknown_type = True
        with self.assertRaisesRegex(observe.NativeObservationError, 'registry-value-type-unsupported'):
            backend.registry(SID)
        self.assertEqual(set(registry.paths), set(registry.closed))

    def test_registry_key_timestamp_change_during_real_enumeration_refuses(self):
        backend = self.backend()
        backend.advapi = backend.nt = registry = MockRegistry()
        original = registry.RegEnumValueW
        def change(*args):
            status = original(*args)
            registry.stamp += 1
            return status
        registry.RegEnumValueW = change
        with self.assertRaisesRegex(observe.NativeObservationError, 'registry-changed-during-read'):
            backend.registry(SID)
        self.assertEqual(set(registry.paths), set(registry.closed))

    def test_full_128bit_file_id_required(self):
        backend = self.backend()
        backend.kernel.GetFileInformationByHandleEx = lambda *args: 0
        with self.assertRaisesRegex(observe.NativeObservationError, 'full-file-identity-unavailable'):
            backend._info(17, directory=False)

    def test_current_process_token_and_shell_token_read_without_elevation(self):
        class Kernel:
            def __init__(self):
                self.closed = []
                self.freed = []
            def GetCurrentThread(self):
                return 99
            def GetCurrentProcess(self):
                return 10
            def GetCurrentProcessId(self):
                return 44
            def ProcessIdToSessionId(self, pid, output):
                output._obj.value = 1
                return 1
            def OpenProcess(self, access, inherit, pid):
                self.open_args = (access, inherit, pid.value)
                return 20
            def CloseHandle(self, handle):
                self.closed.append(handle.value if hasattr(handle, 'value') else handle)
                return 1
            def LocalFree(self, value):
                self.freed.append(value.value)
        class User:
            def GetShellWindow(self):
                return 123
            def GetWindowThreadProcessId(self, hwnd, pid):
                pid._obj.value = 55
                return 1
        class Advapi:
            def OpenThreadToken(self, thread, access, as_self, token):
                return 0
            def OpenProcessToken(self, process, access, token):
                token._obj.value = process + 100
                return 1
            def GetTokenInformation(self, token, kind, data, size, needed):
                needed._obj.value = ctypes.sizeof(ctypes.c_void_p)
                if data is not None:
                    ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0] = token.value
                    return 1
                return 0
            def ConvertSidToStringSidW(self, sid, output):
                output._obj.value = SID
                return 1
        backend = self.backend()
        backend.kernel, backend.user, backend.advapi = Kernel(), User(), Advapi()
        with patch.object(ctypes, 'get_last_error', return_value=1008, create=True):
            self.assertEqual(dict(tokenSid=SID, shellSid=SID), backend.users())
        self.assertEqual((0x1000, False, 55), backend.kernel.open_args)
        self.assertEqual([110, 120, 20], backend.kernel.closed)
        self.assertEqual([SID, SID], backend.kernel.freed)
        with patch.object(ctypes, 'get_last_error', return_value=5, create=True), self.assertRaisesRegex(observe.NativeObservationError, 'thread-token-unknown'):
            backend.users()

    def test_known_folders_are_current_user_and_never_created(self):
        class Shell:
            def __init__(self):
                self.calls = []
            def SHGetKnownFolderPath(self, identifier, flags, token, output):
                self.calls.append((flags, token))
                guid = uuid.UUID(bytes_le=ctypes.string_at(identifier, 16))
                output._obj.value = PROGRAMS if str(guid).startswith('a77f5d77') else DESKTOP
                return 0
        class Ole:
            def __init__(self):
                self.freed = []
            def CoTaskMemFree(self, value):
                self.freed.append(value.value)
        backend = self.backend()
        backend.shell, backend.ole = Shell(), Ole()
        self.assertEqual(dict(programs=PROGRAMS, desktop=DESKTOP), backend.shortcut_locations())
        self.assertEqual([(0x4000, None), (0x4000, None)], backend.shell.calls)
        self.assertEqual([PROGRAMS, DESKTOP], backend.ole.freed)

    def test_shortcut_idlist_must_agree_with_linkinfo_no_execute_or_resolve(self):
        target = ROOT + r'\星图.exe'
        root_item = struct.pack('<HBB', 20, 0x1f, 0x50) + uuid.UUID('20d04fe0-3aea-1069-a2d8-08002b30309d').bytes_le
        drive_item = struct.pack('<HB', 25, 0x2f) + b'C:\\\0' + b'\0' * 18
        file_item = struct.pack('<HB', 14, 0x32) + b'\0' * 11
        pidl = root_item + drive_item + file_item + b'\0\0'
        raw = bytearray(link_bytes(target))
        flags = struct.unpack_from('<I', raw, 20)[0]
        struct.pack_into('<I', raw, 20, flags | 1)
        raw = raw[:76] + struct.pack('<H', len(pidl)) + pidl + raw[76:]
        class Shell:
            def __init__(self):
                self.path = target
                self.calls = 0
            def SHGetPathFromIDListEx(self, data, output, size, flags):
                self.calls += 1
                output.value = self.path
                return 1
        backend = self.backend()
        backend.shell = Shell()
        self.assertEqual(target, backend.shortcut_target(bytes(raw)))
        self.assertEqual(1, backend.shell.calls)
        backend.shell.path = r'C:\Other\target.exe'
        with self.assertRaisesRegex(observe.NativeObservationError, 'shortcut-idlist-target-conflict'):
            backend.shortcut_target(bytes(raw))
        raw[78 + 4:78 + 20] = uuid.UUID('00000000-0000-0000-0000-000000000000').bytes_le
        before = backend.shell.calls
        with self.assertRaisesRegex(observe.NativeObservationError, 'shortcut-idlist-unsupported'):
            backend.shortcut_target(bytes(raw))
        self.assertEqual(before, backend.shell.calls)

    def test_shortcut_parser_rejects_network_and_unknown_extra(self):
        raw = bytearray(link_bytes(ROOT + r'\星图.exe'))
        struct.pack_into('<I', raw, 76 + 8, 2)
        with self.assertRaises(observe.NativeObservationError):
            observe._shortcut_target(raw)
        raw = link_bytes(ROOT + r'\星图.exe')[:-4] + struct.pack('<II', 8, 0xa0000001) + b'\0' * 4
        with self.assertRaisesRegex(observe.NativeObservationError, 'shortcut-extra-unsupported'):
            observe._shortcut_target(raw)

    def test_no_mutating_win32_registry_or_process_execution_apis(self):
        source = Path(observe.__file__).read_text()
        for api in ('RegSetValue', 'RegCreateKey', 'RegDeleteKey', 'RegDeleteValue', 'WriteFile',
                    'DeleteFileW', 'ShellExecuteW(', 'CreateProcessW', 'subprocess.'):
            self.assertNotIn(api, source)


if __name__ == '__main__':
    unittest.main()
