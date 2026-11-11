"""Pure Python official-format fixtures; no historical installer acceptance.

The encoders here follow QtIFW's writeMaintenanceToolBinaryData/fileio and Qt's
QDataStream operators. They do not invoke the parser's own encoding helpers.
The opaque QResource fixture is intentionally not executed or interpreted.
"""
from pathlib import Path
import hashlib
import os
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET

import installer_qtifw_inventory as inventory


PROGRAM = 'org.xingtu.qtifw.realtrial'
DATA = 'org.xingtu.qtifw.trialdata'
ROOT = '@RELOCATABLE_PATH@'


def operation(name, args, component, **values):
    root = ET.Element('operation')
    arguments = ET.SubElement(root, 'arguments')
    for arg in args:
        ET.SubElement(arguments, 'argument').text = arg
    entries = ET.SubElement(root, 'values')
    values = {'component': ('QString', component), **values}
    for key, (kind, text) in values.items():
        ET.SubElement(entries, 'value', name=key, type=kind).text = text
    return name, ET.tostring(root, encoding='unicode')


def dat_file(operations):
    # Real on-disk schema: native Windows qint64 and length-prefixed UTF8;
    # the two operation counts sandwich records. Metadata bytes are opaque.
    resource = b'qres' + bytes(20)
    encoded = bytearray(struct.pack('<q', len(operations)))
    for name, xml in operations:
        for text in (name, xml):
            raw = text.encode('utf-8')
            encoded += struct.pack('<q', len(raw)) + raw
    encoded += struct.pack('<q', len(operations))
    result = bytearray(resource) + encoded
    result += struct.pack('<q', 0)  # no archive collection data
    collection_start = len(result)
    result += struct.pack('<qq', 0, 0)  # empty collection index
    result += struct.pack('<qq', collection_start, 16)
    result += struct.pack('<qq', 0, len(resource))  # one metadata segment
    result += struct.pack('<qq', len(resource), len(encoded))
    result += struct.pack('<q', 1)
    result += struct.pack('<q', len(result) + 24)
    result += struct.pack('<qQ', 0x12023234, 0xc2630a1c99d668f9)
    return bytes(result)


def qstring_list(paths):
    result = bytearray(struct.pack('>I', len(paths)))
    for path in paths:
        data = path.encode('utf-16-be')
        result += struct.pack('>I', len(data)) + data
    return bytes(result)


class NativeQtIfwInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'installed'
        self.root.mkdir()
        self.dat = self.root / 'trial-maintenance.dat'
        self.index = self.root / 'installerResources' / PROGRAM / '1.0.0data.txt'
        self.index.parent.mkdir(parents=True)
        for relative in ('program/星图.exe', 'program/_internal/中文.dll',
                         '_installer-support/qtifw-trial-helper.exe',
                         '_installer-support/_internal/support.dll',
                         'program/_internal/alias.dll'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode('utf-8'))
        self.index.write_bytes(qstring_list([ROOT + '/program', ROOT + '/program/星图.exe',
                                            ROOT + '/program/_internal/中文.dll']))
        self.outside = Path(self.temp.name) / 'external'
        self.operations = [
            operation('Extract', ['installer://' + PROGRAM + '/1.0.0data.7z', ROOT], PROGRAM,
                      files=('QString', ROOT + '/installerResources/' + PROGRAM + '/1.0.0data.txt')),
            operation('Mkdir', [ROOT + '/_installer-support'], DATA),
            operation('Copy', [str(self.outside / 'qtifw-trial-helper.exe'),
                               ROOT + '/_installer-support/qtifw-trial-helper.exe'], DATA),
            operation('Copy', [str(self.outside / 'support.dll'),
                               ROOT + '/_installer-support/_internal/support.dll'], DATA),
            operation('Copy', [ROOT + '/_installer-support/_internal/support.dll',
                               ROOT + '/program/_internal/alias.dll'], PROGRAM),
            operation('CreateShortcut', [ROOT + '/program/星图.exe',
                                         str(self.outside / '星图.lnk')], PROGRAM),
            operation('Execute', ['{0}', str(self.outside / 'qtifw-trial-helper.exe'), 'noop', '@TargetDir@',
                                  'UNDOEXECUTE', '@TargetDir@/_installer-support/qtifw-trial-helper.exe',
                                  'undo-data', '@TargetDir@'], DATA),
            operation('Execute', ['{0}', str(self.outside / 'qtifw-trial-helper.exe'),
                                  'verify-program', '@TargetDir@'], PROGRAM),
            operation('MinimumProgress', [], PROGRAM),
        ]
        self.save()

    def save(self):
        self.dat.write_bytes(dat_file(self.operations))

    def read(self):
        return inventory.read_operations(self.dat, self.root)

    def test_native_records_supply_exact_files_and_external_shortcut_pair_read_only(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.read()
        expected = {'program/星图.exe', 'program/_internal/中文.dll',
                    'program/_internal/alias.dll', '_installer-support/qtifw-trial-helper.exe',
                    '_installer-support/_internal/support.dll', 'trial-maintenance.dat',
                    'installerResources/' + PROGRAM + '/1.0.0data.txt'}
        self.assertEqual({p.relative_to(self.root).as_posix() for p in result.files}, expected)
        self.assertEqual(result.components, {PROGRAM, DATA})
        self.assertEqual(result.shortcuts[0].source, self.root / 'program/星图.exe')
        self.assertEqual(result.shortcuts[0].target, self.outside / '星图.lnk')
        self.assertFalse(self.outside.exists())
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertEqual(result.metadata_digests[self.dat],
                         (len(before[self.dat]), hashlib.sha256(before[self.dat]).hexdigest()))

    def test_support_files_are_never_inferred_by_scanning_directory(self):
        unknown = self.root / '_installer-support/unrecorded.dll'
        unknown.write_bytes(b'unknown')
        self.assertNotIn(unknown, self.read().files)

    def test_support_helper_requires_an_actual_copy_record(self):
        self.operations.pop(2)
        self.save()
        with self.assertRaisesRegex(inventory.QtIfwInventoryError, 'incomplete-current-layout'):
            self.read()

    def test_unknown_dangerous_operation_and_fault_execute_are_rejected(self):
        for record in (operation('Delete', [ROOT + '/program/星图.exe'], PROGRAM),
                       operation('Execute', ['{0}', str(self.outside / 'qtifw-trial-helper.exe'),
                                             'fail', ROOT], PROGRAM)):
            with self.subTest(record=record[0]):
                original = list(self.operations)
                self.operations.append(record)
                self.save()
                with self.assertRaises(inventory.QtIfwInventoryError):
                    self.read()
                self.operations = original

    def test_copy_cannot_claim_an_external_file_or_directory_target(self):
        for target in (str(self.outside / 'claim.dll'), ROOT + '/program'):
            with self.subTest(target=target):
                self.operations.append(operation('Copy', [ROOT + '/program/星图.exe', target], PROGRAM))
                self.save()
                with self.assertRaises(inventory.QtIfwInventoryError):
                    self.read()
                self.operations.pop()

    def test_extract_index_cannot_point_outside_the_component_directory(self):
        self.operations[0] = operation('Extract', ['installer://' + PROGRAM + '/1.0.0data.7z', ROOT],
                                       PROGRAM, files=('QString', str(self.outside / 'files.txt')))
        self.save()
        with self.assertRaises(inventory.QtIfwInventoryError):
            self.read()

    def test_extract_list_rejects_traversal_external_support_and_ads(self):
        for path in (ROOT + '/../outside.txt', str(self.outside / 'file.txt'),
                     ROOT + '/_installer-support/qtifw-trial-helper.exe',
                     ROOT + '/program/星图.exe:stream'):
            with self.subTest(path=path):
                self.index.write_bytes(qstring_list([path]))
                with self.assertRaises(inventory.QtIfwInventoryError):
                    self.read()

    def test_copy_source_inside_root_requires_an_independent_native_record(self):
        source = self.root / 'program/_internal/not-recorded.dll'
        source.write_bytes(b'unrecorded')
        self.operations[4] = operation('Copy', [str(source), ROOT + '/program/_internal/alias.dll'], PROGRAM)
        self.save()
        with self.assertRaisesRegex(inventory.QtIfwInventoryError, 'unowned-copy-source'):
            self.read()

    def test_old_inline_variant_list_is_explicitly_unsupported(self):
        self.operations[0] = operation('Extract', ['installer://' + PROGRAM + '/1.0.0data.7z', ROOT],
                                       PROGRAM, files=('QStringList', 'AAAA'))
        self.save()
        with self.assertRaisesRegex(inventory.QtIfwInventoryError, 'unsupported-extract-file-list'):
            self.read()

    def test_copy_cycle_cannot_manufacture_native_provenance(self):
        other = self.root / 'program/_internal/cycle.dll'
        other.write_bytes(b'cycle')
        self.operations[4] = operation('Copy', [str(other), ROOT + '/program/_internal/alias.dll'], PROGRAM)
        self.operations.append(operation('Copy', [ROOT + '/program/_internal/alias.dll', str(other)], PROGRAM))
        self.save()
        with self.assertRaisesRegex(inventory.QtIfwInventoryError, 'cyclic-copy-source'):
            self.read()

    def test_missing_and_corrupt_extract_lists_fail_closed(self):
        for data in (b'', b'\xff\xff\xff\xfe', qstring_list([ROOT + '/program/星图.exe']) + b'extra',
                     struct.pack('>II', 1, 3) + b'abc'):
            with self.subTest(data=data):
                self.index.write_bytes(data)
                with self.assertRaises(inventory.QtIfwInventoryError):
                    self.read()
        self.index.unlink()
        with self.assertRaises(inventory.QtIfwInventoryError):
            self.read()

    def test_footer_ranges_counts_and_trailing_bytes_are_checked(self):
        original = self.dat.read_bytes()
        variants = [original + b'x', original[:-1], b'not a dat']
        for offset, value in ((-48, -1), (-40, 2**40), (-32, 1000), (-24, 0), (-16, 0)):
            altered = bytearray(original)
            struct.pack_into('<q', altered, len(altered) + offset, value)
            variants.append(bytes(altered))
        altered = bytearray(original)
        op_offset = struct.unpack_from('<q', altered, len(altered) - 48)[0]
        struct.pack_into('<q', altered, op_offset, -1)
        variants.append(bytes(altered))
        for data in variants:
            with self.subTest(size=len(data)):
                self.dat.write_bytes(data)
                with self.assertRaises(inventory.QtIfwInventoryError):
                    self.read()

    def test_non_current_package_and_xml_entities_are_rejected(self):
        for record in (operation('MinimumProgress', [], 'other.package'),
                       ('MinimumProgress', '<!DOCTYPE operation [<!ENTITY x "x">]>'
                        '<operation><arguments/></operation>')):
            self.operations.append(record)
            self.save()
            with self.assertRaises(inventory.QtIfwInventoryError):
                self.read()
            self.operations.pop()

    def test_linked_file_is_not_adopted(self):
        path = self.root / 'program/_internal/中文.dll'
        path.unlink()
        try:
            os.link(self.root / 'program/星图.exe', path)
        except OSError as error:
            self.skipTest('hard links unavailable: ' + str(error))
        with self.assertRaisesRegex(inventory.QtIfwInventoryError, 'multiply-linked'):
            self.read()


if __name__ == '__main__':
    unittest.main()
