"""Synthetic Win32/Inno format regressions; no user paths or tracker IDs."""
import ctypes
from pathlib import Path
import struct
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import installer_native_observation as observe


class RegistryClassTests(unittest.TestCase):
    def backend(self, status=0, text='', returned=None):
        backend = observe.WindowsReadOnlyBackend.__new__(observe.WindowsReadOnlyBackend)
        def query(key, buf, size, reserved, children, max_child, max_class,
                  values, max_name, max_data, security, stamp):
            if size._obj.value > 256:
                size._obj.value = 0
                return 122  # observed Windows RegQueryInfoKeyW large-buffer failure
            buf.value = text
            size._obj.value = len(text) if returned is None else returned
            values._obj.value = 21
            return status
        backend.advapi = SimpleNamespace(RegQueryInfoKeyW=query)
        return backend

    def test_empty_class_with_supported_capacity(self):
        self.assertEqual('', self.backend()._key_info(1)['keyClass'])
        self.assertEqual(21, self.backend()._key_info(1)['valueCount'])

    def test_class_status_and_length_fail_closed(self):
        for status, text, returned in ((122, '', 0), (234, '', 255), (5, '', 0),
                                       (0, 'abc', 2), (0, '', 256)):
            with self.subTest(status=status, returned=returned), self.assertRaises(observe.NativeObservationError):
                self.backend(status, text, returned)._key_info(1)

    def test_maximum_supported_class(self):
        self.assertEqual('x' * 255, self.backend(text='x' * 255)._key_info(1)['keyClass'])


def ansi_link(target, *, extra=b'', pidl=b''):
    import uuid
    header = bytearray(76)
    struct.pack_into('<I', header, 0, 76)
    header[4:20] = uuid.UUID('00021401-0000-0000-c000-000000000046').bytes_le
    struct.pack_into('<I', header, 20, 0x82 | bool(pidl))
    volume = struct.pack('<4I', 17, 3, 123, 16) + b'\0'
    encoded = target.encode('cp936') + b'\0'
    info = struct.pack('<7I', 28 + len(volume) + len(encoded) + 1, 28, 1,
                       28, 28 + len(volume), 0, 28 + len(volume) + len(encoded))
    return (bytes(header) + (struct.pack('<H', len(pidl)) + pidl if pidl else b'')
            + info + volume + encoded + b'\0' + extra + b'\0' * 4)


class AnsiFormatTests(unittest.TestCase):
    def backend(self):
        backend = observe.WindowsReadOnlyBackend.__new__(observe.WindowsReadOnlyBackend)
        def convert(cp, flags, raw, size, output, capacity):
            self.assertEqual((936, 8, len(raw)), (cp, flags, size))
            try:
                result = raw.decode('cp936', errors='strict')
            except UnicodeError:
                return 0
            if output is not None:
                for index, char in enumerate(result):
                    output[index] = char
            return len(result)
        backend.kernel = SimpleNamespace(GetACP=lambda: 936, MultiByteToWideChar=convert)
        return backend

    def test_real_shape_ansi_linkinfo_uses_authoritative_acp(self):
        target = r'C:\Synthetic\星图.exe'
        backend = self.backend()
        self.assertEqual(target, backend.shortcut_target(ansi_link(target)))
        self.assertEqual(936, backend.shortcut_context['ansiCodePage'])

    def test_unknown_acp_no_conversion_or_nonwindows_fallback(self):
        backend = self.backend()
        backend.kernel.GetACP = lambda: 0
        with self.assertRaisesRegex(observe.NativeObservationError, 'codepage-changed-or-unknown'):
            backend.shortcut_target(ansi_link(r'C:\Synthetic\星图.exe'))
        with self.assertRaisesRegex(observe.NativeObservationError, 'ansi-codepage-unknown'):
            observe._shortcut_target(ansi_link(r'C:\Synthetic\星图.exe'))

    def test_codepage_change_and_invalid_bytes_fail_closed(self):
        backend = self.backend()
        values = iter((936, 936, 1252))
        backend.kernel.GetACP = lambda: next(values)
        with self.assertRaisesRegex(observe.NativeObservationError, 'codepage-changed-or-unknown'):
            backend.shortcut_target(ansi_link(r'C:\Synthetic\星图.exe'))
        raw = ansi_link(r'C:\Synthetic\星图.exe').replace('星图'.encode('cp936'), b'\xff\xff\xff\xff')
        with self.assertRaisesRegex(observe.NativeObservationError, 'ansi-invalid'):
            self.backend().shortcut_target(raw)

    def test_tracker_shape_and_unknown_blocks(self):
        target = r'C:\Synthetic\星图.exe'
        tracker = struct.pack('<4I', 96, 0xa0000003, 88, 0) + b'\0' * 80
        self.assertEqual(target, self.backend().shortcut_target(ansi_link(target, extra=tracker)))
        for extra in (tracker + tracker, struct.pack('<II', 8, 0xa000000c),
                      struct.pack('<II', 8, 0xa0000003)):
            with self.assertRaises(observe.NativeObservationError):
                self.backend().shortcut_target(ansi_link(target, extra=extra))

    def test_intermediate_linkinfo_header_rejected(self):
        for size in range(29, 36):
            raw = bytearray(ansi_link(r'C:\Synthetic\app.exe'))
            struct.pack_into('<I', raw, 80, size)
            with self.assertRaisesRegex(observe.NativeObservationError, 'unsupported-linkinfo'):
                self.backend().shortcut_target(bytes(raw))


class MetadataBindingTests(unittest.TestCase):
    def test_capture_binds_native_shortcut_interpretation(self):
        from test_installer_native_observation import ObservationFixtureTests, OwnedFixtureBackend
        case = ObservationFixtureTests('test_capture_and_replay_fixture_label_not_native')
        case.setUp()
        self.addCleanup(case.doCleanups)
        class NativeFixture(OwnedFixtureBackend, observe.WindowsReadOnlyBackend):
            cp = 936
            def shortcut_target(self, data):
                self.shortcut_context = dict(ansiCodePage=self.cp, knownFolders=[])
                return observe._shortcut_target(data)
        case.backend.__class__ = NativeFixture
        result = case.capture()
        interpretations = [row['file'].get('interpretation') for row in result['observation']['shortcuts']]
        self.assertIn(dict(ansiCodePage=936, knownFolders=[]), interpretations)
        case.backend.cp = 1252
        with self.assertRaisesRegex(observe.NativeObservationError, 'evidence-no-longer-matches'):
            case.verify(result)


USERS_FILES = 'f3ce0f7c-4901-4acc-8648-d5d44b04ef8f'
PROFILE = '5e6c858f-0e22-4760-9afe-ea3317b67173'
LOCAL_APP_DATA = 'f1b32785-6fba-4fcf-9d55-7b8e7f157091'


class UsersFilesFormatTests(AnsiFormatTests):
    def fixture(self, basename='星图.exe'):
        import uuid
        profile = r'C:\Users\synthetic'
        local = profile + r'\AppData\Local'
        target = local + '\\TestApp\\' + basename
        root = (bytes.fromhex('3a001f49') + uuid.UUID('59031a47-3f72-44a7-89c5-5595fe6b30ee').bytes_le
                + struct.pack('<HHII', 38, 1, 0xbeef0026, 0x10)
                + b'\0' * 24 + struct.pack('<H', 20))
        delegate = bytes.fromhex(
            '820074001c004346534616003100000000000000000010004170704461746100'
            '0000741a595e96dfd3488d671733bcee28bac5cdfadf9f6756418947c5c76bc0'
            'b67f400009000400efbe00000000000000002e00000000000000000000000000'
            '0000000000000000000000000000000041007000700044006100740061000000'
            '4200'
        )
        delegate = delegate[:108] + b'\x01\x02\x03\x04' + delegate[112:]
        def item(size, kind):
            return struct.pack('<HB', size, kind) + b'\0' * (size - 3)
        pidl = root + delegate + item(80, 0x31) + item(80, 0x31) + item(68, 0x36) + b'\0\0'
        known = (struct.pack('<II', 28, 0xa000000b) + uuid.UUID(USERS_FILES).bytes_le
                 + struct.pack('<I', 58))
        backend = self.backend()
        paths = {60: profile, 190: profile + r'\AppData', 270: local, len(pidl): target}
        calls = []
        trusted = ctypes.create_string_buffer(root + b'\0\0')
        trusted_child = ctypes.create_string_buffer(root + delegate + b'\0\0')
        def get_idlist(identifier, flags, token, result):
            self.assertEqual((0x4000, None), (flags, token))
            result._obj.value = ctypes.addressof(trusted)
            return 0
        def parse(name, context, result, flags, attributes):
            self.assertEqual('::{59031a47-3f72-44a7-89c5-5595fe6b30ee}\\AppData', name)
            self.assertEqual((None, 0), (context, flags))
            result._obj.value = ctypes.addressof(trusted_child)
            return 0
        def convert(data, output, size, flags):
            raw = data.raw
            calls.append(len(raw) - 1)
            output.value = paths[len(raw) - 1]
            return 1
        def known_path(identifier, flags, token, output):
            guid = str(uuid.UUID(bytes_le=ctypes.string_at(identifier, 16)))
            self.assertIn(guid, (PROFILE, LOCAL_APP_DATA))
            output._obj.value = profile if guid == PROFILE else local
            return 0
        backend.shell = SimpleNamespace(SHGetKnownFolderIDList=get_idlist, ILGetSize=lambda p: 60 if p.value == ctypes.addressof(trusted) else 190,
            SHParseDisplayName=parse,
            SHGetPathFromIDListEx=convert, SHGetKnownFolderPath=known_path)
        backend.ole = SimpleNamespace(CoTaskMemFree=lambda p: None,
            CoInitializeEx=lambda reserved, mode: 0, CoUninitialize=lambda: None)
        tracker = struct.pack('<4I', 96, 0xa0000003, 88, 0) + b'\0' * 80
        # Property store remains opaque captured evidence, as in the original reader.
        property_store = struct.pack('<II', 12, 0xa0000009) + b'\0' * 4
        raw = bytearray(ansi_link(target, extra=known + tracker + property_store, pidl=pidl))
        struct.pack_into('<I', raw, 20, 0x9b)
        link_end = 78 + len(pidl)
        link_end += struct.unpack_from('<I', raw, link_end)[0]
        strings = b''
        for value in (r'.\星图.exe', target.rsplit('\\', 1)[0]):
            encoded = value.encode('utf-16-le')
            strings += struct.pack('<H', len(encoded) // 2) + encoded
        raw = bytes(raw[:link_end]) + strings + bytes(raw[link_end:])
        return backend, raw, target, paths, calls, trusted

    def test_users_files_ascii_target_still_validates_known_folder(self):
        backend, raw, target, paths, calls, trusted = self.fixture('app.exe')
        self.assertEqual(target, backend.shortcut_target(raw))

    def test_users_files_known_folder_and_delegate_are_verified(self):
        backend, raw, target, paths, calls, trusted = self.fixture()
        self.assertEqual(target, backend.shortcut_target(raw))
        self.assertEqual([len(raw[78:78 + struct.unpack_from('<H', raw, 76)[0]]), 60, 190, 270], calls)
        self.assertEqual(58, backend.shortcut_context['knownFolders'][0]['childOffset'])

    def test_known_folder_conflicts_before_shell_conversion(self):
        for offset, value in ((78 + 4, 0), (78 + 58 + 34, 0), (78 + 58 + 50, 0),
                              (78 + 58 + 112, 0), (-116, 57), (-132, 0)):
            backend, raw, target, paths, calls, trusted = self.fixture()
            changed = bytearray(raw)
            changed[offset] = value
            with self.subTest(offset=offset), self.assertRaises(observe.NativeObservationError):
                backend.shortcut_target(bytes(changed))
            self.assertEqual([], calls)

    def test_unknown_authoritative_root_refused_before_conversion(self):
        backend, raw, target, paths, calls, trusted = self.fixture()
        trusted[4] = b'\0'
        with self.assertRaisesRegex(observe.NativeObservationError, 'root-conflict'):
            backend.shortcut_target(raw)
        self.assertEqual([], calls)

    def test_root_extension_version_one_is_not_control_dword(self):
        backend, raw, target, paths, calls, trusted = self.fixture()
        self.assertEqual((38, 1, 0xbeef0026, 0x10), struct.unpack_from('<HHII', raw, 78 + 20))
        self.assertEqual(target, backend.shortcut_target(raw))

    def test_root_extension_version_sixteen_is_rejected_before_shell_conversion(self):
        backend, raw, target, paths, calls, trusted = self.fixture()
        changed = bytearray(raw)
        struct.pack_into('<H', changed, 78 + 22, 0x10)
        with self.assertRaisesRegex(observe.NativeObservationError, 'shortcut-idlist-unsupported'):
            backend.shortcut_target(bytes(changed))
        self.assertEqual([], calls)

    def test_root_extension_control_dword_is_not_masked(self):
        backend, raw, target, paths, calls, trusted = self.fixture()
        trusted[28] = b'\x01'
        with self.assertRaisesRegex(observe.NativeObservationError, 'root-conflict'):
            backend.shortcut_target(raw)
        self.assertEqual([], calls)

    def test_mutable_root_timestamps_do_not_change_namespace(self):
        backend, raw, target, paths, calls, trusted = self.fixture()
        for index in range(32, 56):
            trusted[index] = b'\xff'
        self.assertEqual(target, backend.shortcut_target(raw))

    def test_all_path_views_must_agree(self):
        for length in (60, 190, 270, 418):
            backend, raw, target, paths, calls, trusted = self.fixture()
            # Full target list is 418 bytes in this synthetic shape.
            paths[length] = r'C:\Elsewhere'
            with self.subTest(length=length), self.assertRaises(observe.NativeObservationError):
                backend.shortcut_target(raw)

    def test_delegate_controls_and_unknown_field_must_agree_with_authority(self):
        for offset in (94, 102, 104, 108):
            backend, raw, target, paths, calls, trusted = self.fixture()
            changed = bytearray(raw)
            changed[78 + 58 + offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(observe.NativeObservationError):
                backend.shortcut_target(bytes(changed))
            self.assertEqual([], calls)

    def test_trusted_delegate_api_failure_and_changed_shape_fail_closed(self):
        for status, size in ((1, 190), (0, 189)):
            backend, raw, target, paths, calls, trusted = self.fixture()
            original = backend.shell.SHParseDisplayName
            def parse(*args):
                original(*args)
                return status
            backend.shell.SHParseDisplayName = parse
            original_size = backend.shell.ILGetSize
            backend.shell.ILGetSize = lambda p: 60 if original_size(p) == 60 else size
            with self.subTest(status=status, size=size), self.assertRaises(observe.NativeObservationError):
                backend.shortcut_target(raw)
            self.assertEqual([], calls)

    def test_com_initialization_is_balanced_and_failure_is_closed(self):
        for status, expected_uninit in ((0, 2), (1, 2), (-2147417850, 0), (5, 0)):
            backend, raw, target, paths, calls, trusted = self.fixture()
            uninit = []
            backend.ole.CoInitializeEx = lambda reserved, mode: status
            backend.ole.CoUninitialize = lambda: uninit.append(True)
            if status == 5:
                with self.assertRaisesRegex(observe.NativeObservationError, 'shell-initialization'):
                    backend.shortcut_target(raw)
                self.assertEqual([], calls)
            else:
                self.assertEqual(target, backend.shortcut_target(raw))
            self.assertEqual(expected_uninit, len(uninit))

    def test_missing_or_duplicate_known_folder_rejected(self):
        backend, raw, target, paths, calls, trusted = self.fixture()
        known_start = raw.index(struct.pack('<II', 28, 0xa000000b))
        known = raw[known_start:known_start + 28]
        for changed in (raw[:known_start] + raw[known_start + 28:], raw[:-4] + known + b'\0' * 4):
            with self.assertRaises(observe.NativeObservationError):
                backend.shortcut_target(changed)
