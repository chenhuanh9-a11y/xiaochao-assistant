"""Manifest contract only; no executable, UAC prompt, GUI, input or game is run."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_admin_manifest', ROOT / 'tools/verify_admin_manifest.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AdministratorManifestTests(unittest.TestCase):
    def test_source_manifest_contract(self):
        module.verify_manifest_xml((ROOT / 'tools/identityv-assistant.manifest').read_bytes())

    def test_rejects_non_administrator_or_uiaccess(self):
        for attributes in ('level="asInvoker" uiAccess="false"',
                           'level="highestAvailable" uiAccess="false"',
                           'level="requireAdministrator" uiAccess="true"',
                           'level="requireAdministrator"'):
            with self.subTest(attributes=attributes), self.assertRaises(ValueError):
                module.verify_manifest_xml(
                    f'<assembly xmlns="urn:schemas-microsoft-com:asm.v3"><requestedExecutionLevel {attributes}/></assembly>'.encode())

    def test_rejects_missing_or_ambiguous_level(self):
        for xml in (b'<assembly/>', b'<assembly xmlns="urn:schemas-microsoft-com:asm.v3">'
                    b'<requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>'
                    b'<requestedExecutionLevel level="asInvoker" uiAccess="false"/></assembly>'):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                module.verify_manifest_xml(xml)

    def test_rejects_malformed_xml(self):
        with self.assertRaises(ET.ParseError):
            module.verify_manifest_xml(b'<assembly')


if __name__ == '__main__':
    unittest.main()
