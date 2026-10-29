"""Diagnostic boundary contracts; these tests are NOT packaged-PE evidence."""
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'app/ui_packaged_audit.py'
spec = importlib.util.spec_from_file_location('ui_packaged_audit', MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class PackagedAuditTests(unittest.TestCase):
    def test_host_python_rejected_without_creating_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'not-created'
            with patch.object(sys, 'frozen', False, create=True):
                with self.assertRaises(SystemExit):
                    audit.prepare(['app.exe', '--ui-audit', str(output)])
            self.assertFalse(output.exists())

    def test_external_script_and_extra_flags_rejected(self):
        with self.assertRaises(SystemExit):
            audit.prepare(['app.exe', '--ui-audit', 'somewhere', '--script', 'code.py'])

    def test_guard_component_boundary_and_write_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            package = Path(temporary) / 'package'
            output.mkdir()
            package.mkdir()
            guard = audit.PathGuard(output, [output, package])
            guard.check(package / 'resource', api='read')
            guard.check(output / 'settings', True)
            with self.assertRaises(PermissionError):
                guard.check(package / 'resource', True)
            with self.assertRaises(PermissionError):
                guard.check(Path(str(output) + '-sibling') / 'resource')
            self.assertEqual(len(guard.attempts), 2)

    def test_installed_guard_rejects_caught_reads_writes_and_network(self):
        # Audit hooks are intentionally permanent, so exercise them in a child.
        script = '''
import importlib.util, pathlib, tempfile, os, socket
spec=importlib.util.spec_from_file_location('guard', {module!r})
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as t:
    root=pathlib.Path(t); output=root/'out'; output.mkdir()
    forbidden=root/'private.txt'; forbidden.write_text('private')
    guard=m.PathGuard(output,[output]); guard.install()
    for action in (lambda: forbidden.read_text(), lambda: forbidden.stat(),
                   lambda: forbidden.unlink(), lambda: socket.socket().connect(('127.0.0.1',9))):
        try: action()
        except PermissionError: pass
        else: raise AssertionError('guard did not block')
    assert len(guard.attempts)==4
    (output/'result').write_text('ok')
    # Cleanup outside output is also deliberately denied; process owns temp.
    os._exit(0)
'''.format(module=str(MODULE))
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_early_dispatch_and_no_executable_identity_assignment(self):
        source = (ROOT / 'app/identityv_daily_assistant.py').read_text(encoding='utf-8')
        self.assertLess(source.index('_packaged_audit.prepare'), source.index('from PIL import'))
        main = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        self.assertIsInstance(main.body[0], ast.If)
        tree = ast.parse(MODULE.read_text(encoding='utf-8'))
        assigned = [ast.unparse(target) for n in ast.walk(tree) if isinstance(n, ast.Assign) for target in n.targets]
        self.assertNotIn('sys.executable', assigned)
        self.assertNotIn('sys._MEIPASS', assigned)

    def test_timeout_exits_even_when_report_write_fails(self):
        tree = ast.parse(MODULE.read_text(encoding='utf-8'))
        timeout = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'timed_out')
        def bad_save(state):
            raise OSError('diagnostic volume unavailable')
        def exit_owned(code):
            raise SystemExit(code)
        from types import SimpleNamespace
        namespace = dict(report={}, state={}, save=bad_save, os=SimpleNamespace(_exit=exit_owned))
        exec(compile(ast.Module(body=[timeout], type_ignores=[]), str(MODULE), 'exec'), namespace)
        with self.assertRaises(SystemExit) as raised:
            namespace['timed_out']()
        self.assertEqual(raised.exception.code, 124)


if __name__ == '__main__':
    unittest.main()
