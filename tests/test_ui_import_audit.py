"""Owned import audit boundary tests; these are not actual frozen UI evidence."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

spec = importlib.util.spec_from_file_location('ui_import_audit', Path(__file__).resolve().parents[1] / 'app/ui_import_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ImportAuditTests(unittest.TestCase):
    def test_scenario_uses_current_page_widgets_and_never_mutates_runtime_mode(self):
        tree = ast.parse(Path(audit.__file__).read_text(encoding='utf-8'))
        scenario = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'run_import_scenario')
        attributes = {node.attr for node in ast.walk(scenario) if isinstance(node, ast.Attribute)}
        self.assertFalse(attributes & {'source_box', '_cards', '_profile_keys', '_detail_path', '_select_package'})
        self.assertTrue({'map_box', 'floor_box', 'detail_image', '_row_data', '_card_records',
                         '_browse_context', 'browse_mode', 'manage_button', 'managed_confirm'} <= attributes)
        for node in ast.walk(scenario):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                self.assertNotIn('assistant.game_mode', [ast.unparse(target) for target in targets])

    def fixture(self, parent):
        root = parent / ('xingtu-import-audit-' + uuid.uuid4().hex)
        root.mkdir()
        installation = root / 'installation'
        installation.mkdir()
        (installation / '_internal').mkdir()
        exe = installation / '第五人格小抄助手.exe'
        exe.write_bytes(b'fixture PE identity only; not executable')
        nonce = uuid.uuid4().hex
        (root / 'owner.marker').write_text(nonce, encoding='ascii')
        owner = dict(schema=audit.SCHEMA, nonce=nonce, runRoot=str(root), installation=str(installation),
                     executableSha256=audit.sha256(exe), buildFingerprint='64710EF6', createdAtUTC='2026-10-29T12:00:00+00:00')
        (root / 'ownership.json').write_text(json.dumps(owner), encoding='utf-8')
        (installation / 'release-report.json').write_text(json.dumps(dict(buildFingerprint='64710EF6',
            executableSha256=audit.sha256(exe), sourceDirty=False)), encoding='utf-8')
        (root / 'inputs').mkdir()
        packages, rows = {}, []
        for package in audit.PACKAGES:
            path = root / 'inputs' / (package + '.zip')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('map-package.json', json.dumps(dict(packageId=package)))
            size, digest = path.stat().st_size, audit.sha256(path)
            packages[package] = size, digest
            rows.append(dict(packageId=package, path='inputs/' + package + '.zip', bytes=size, sha256=digest))
        (root / 'inputs.json').write_text(json.dumps(rows), encoding='utf-8')
        return root, nonce, exe, packages

    def test_host_and_extra_arguments_rejected_before_any_claim(self):
        with patch.object(sys, 'frozen', False, create=True):
            with self.assertRaises(SystemExit):
                audit.prepare_import_audit(['exe', '--ui-import-audit', 'root', 'nonce'])
        with self.assertRaises(SystemExit):
            audit.prepare_import_audit(['exe', '--ui-import-audit', 'root', 'nonce', '--script', 'x.py'])

    def test_owned_copy_identity_and_exclusive_one_shot_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, nonce, exe, packages = self.fixture(Path(temporary).resolve())
            with patch.object(audit, 'PACKAGES', packages), patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, '_MEIPASS', str(exe.parent / '_internal'), create=True), patch.object(sys, 'executable', str(exe)):
                data = audit.prepare_import_audit([str(exe), '--ui-import-audit', str(root), nonce])
                self.assertEqual(data['installation'], exe.parent)
                self.assertEqual(len(data['inputs']), 5)
                self.assertTrue((root / 'import-started.json').exists())
                with self.assertRaises(SystemExit):
                    audit.prepare_import_audit([str(exe), '--ui-import-audit', str(root), nonce])

    def test_forged_executable_nonce_and_modified_package_fail_without_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, nonce, exe, packages = self.fixture(Path(temporary).resolve())
            with patch.object(audit, 'PACKAGES', packages):
                with self.assertRaisesRegex(ValueError, 'ownership-mismatch'):
                    audit.validate_owned_run(root, '0' * 32, exe)
                forged = root / exe.name
                forged.write_bytes(exe.read_bytes())
                with self.assertRaisesRegex(ValueError, 'not-owned-copy'):
                    audit.validate_owned_run(root, nonce, forged)
                (root / 'inputs' / 'nightmare-suju.zip').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'input-integrity'):
                    audit.validate_owned_run(root, nonce, exe)
                self.assertFalse((root / 'import-started.json').exists())

    def test_traversal_duplicate_missing_and_unexpected_inputs_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, nonce, exe, packages = self.fixture(Path(temporary).resolve())
            rows = json.loads((root / 'inputs.json').read_text())
            cases = []
            for value in ('../inputs/difficult-zhan10.zip', 'inputs/sub/difficult-zhan10.zip', 'C:/other.zip'):
                changed = [dict(row) for row in rows]
                changed[0]['path'] = value
                cases.append(changed)
            cases.extend([rows[:-1], rows[:-1] + [rows[0]], rows + [rows[0]]])
            with patch.object(audit, 'PACKAGES', packages):
                for case in cases:
                    (root / 'inputs.json').write_text(json.dumps(case), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        audit.validate_owned_run(root, nonce, exe)
            self.assertFalse((root / 'import-started.json').exists())

    def test_reparse_component_rejected_without_resolving_it(self):
        from types import SimpleNamespace
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0, st_file_attributes=0x400)):
            with self.assertRaisesRegex(ValueError, 'reparse-point'):
                audit.no_links(Path('C:/owned/link'))

    def test_bounded_link_check_includes_root_and_rejects_nonancestor_before_io(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            boundary = Path(temporary).resolve()
            path = boundary / 'custom' / 'marker.bin'
            seen = []
            def info(part):
                seen.append(part)
                return SimpleNamespace(st_mode=0, st_file_attributes=0)
            with patch.object(Path, 'lstat', info):
                audit.no_links(path, stop_at=boundary)
                self.assertEqual(seen, [path, path.parent, boundary])
                seen.clear()
                for invalid in (boundary / 'sibling', boundary.parent / (boundary.name + '-other'),
                                boundary / '..'):
                    with self.assertRaisesRegex(ValueError, 'invalid-link-boundary'):
                        audit.no_links(path, stop_at=invalid)
                    self.assertEqual(seen, [])

    def test_custom_inventory_under_real_guard_never_reads_above_owned_root(self):
        # Guard audit hooks cannot be removed; keep the real filesystem check in
        # a child, with both readable/writable roots restricted to the owned run.
        source = Path(audit.__file__).parent
        script = '''
import importlib.util,json,os,pathlib,sys,tempfile,uuid
sys.dont_write_bytecode=True
def load(name):
    spec=importlib.util.spec_from_file_location(name,pathlib.Path({source!r})/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
m=load('ui_import_audit');g=load('ui_packaged_audit')
parent=pathlib.Path(tempfile.mkdtemp());root=parent/('xingtu-import-audit-'+uuid.uuid4().hex)
root.mkdir();custom=root/'custom';custom.mkdir();nested=custom/'nested';nested.mkdir()
marker=nested/'marker.bin';marker.write_bytes(b'preserve owned custom fixture')
expected={{str(marker.relative_to(custom)):m.sha256(marker)}}
guard=g.PathGuard(root,[root]);guard.install()
before=m._inventory(custom,root)
assert before==expected
assert m._inventory(custom,root)==before
assert m._inventory(root/'missing-library',root)=={{}}
assert not guard.attempts,guard.attempts
print(json.dumps(dict(inventory=before,attempts=guard.attempts)),flush=True)
os._exit(0)
'''.format(source=str(source))
        result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['attempts'], [])

    def test_wrong_meipass_and_tampered_release_identity_refuse_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, nonce, exe, packages = self.fixture(Path(temporary).resolve())
            with patch.object(audit, 'PACKAGES', packages), patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, '_MEIPASS', str(exe.parent), create=True), patch.object(sys, 'executable', str(exe)):
                with self.assertRaisesRegex(SystemExit, 'meipass-not-owned-copy'):
                    audit.prepare_import_audit([str(exe), '--ui-import-audit', str(root), nonce])
            release_path = exe.parent / 'release-report.json'
            release = json.loads(release_path.read_text())
            release['buildFingerprint'] = 'DEADBEEF'
            release_path.write_text(json.dumps(release))
            with patch.object(audit, 'PACKAGES', packages):
                with self.assertRaisesRegex(ValueError, 'release-identity-mismatch'):
                    audit.validate_owned_run(root, nonce, exe)
            self.assertFalse((root / 'import-started.json').exists())

    def test_package_manifest_identity_is_checked_even_after_hash_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, nonce, exe, packages = self.fixture(Path(temporary).resolve())
            path = root / 'inputs/difficult-zhan10.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('map-package.json', json.dumps(dict(packageId='nightmare-suju')))
            digest, size = audit.sha256(path), path.stat().st_size
            packages['difficult-zhan10'] = size, digest
            manifest = json.loads((root / 'inputs.json').read_text())
            manifest[0].update(bytes=size, sha256=digest)
            (root / 'inputs.json').write_text(json.dumps(manifest))
            with patch.object(audit, 'PACKAGES', packages):
                with self.assertRaisesRegex(ValueError, 'input-package-id'):
                    audit.validate_owned_run(root, nonce, exe)


if __name__ == '__main__':
    unittest.main()
