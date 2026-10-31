"""Owned offscreen subprocesses only: real mutex/pipe, no client/game/listeners."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


def child(directory, tag, once=False, crash=False, activation='normal'):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication, QWindow
    from client_single_instance import SingleInstanceGuard, SingleInstanceError, instance_namespace, restore_main_window
    root = Path(directory)
    app = QGuiApplication([])
    guard = SingleInstanceGuard(instance_namespace(production=False,frozen=False,sandbox=root), app)
    try:
        try:
            primary = guard.start()
        except SingleInstanceError as error:
            (root / (tag + '.role')).write_text(json.dumps(dict(primary=False,error=str(error))),encoding='utf-8')
            return 2
        result = dict(primary=primary,pid=os.getpid())
        (root / (tag + '.role')).write_text(json.dumps(result),encoding='utf-8')
        if not primary:
            return 0
        # A hidden OWNED window stands in for main; no product object created.
        window = QWindow(); window.setTitle('Owned single-instance test')
        def activate():
            if activation != 'normal':
                return activation
            restore_main_window(window)
            (root / 'activated.json').write_text(json.dumps(dict(visible=window.isVisible(),
                minimized=window.visibility()==QWindow.Minimized)),encoding='utf-8')
            return 'restored' if window.isVisible() and window.visibility()!=QWindow.Minimized else 'unavailable'
        guard.setActivationHandler(activate)
        if crash:
            os._exit(23)  # OS must release this owned process's mutex/pipe.
        if once:
            return 0
        timer = QTimer(); timer.setInterval(20)
        timer.timeout.connect(lambda: app.quit() if (root / 'stop').exists() else None)
        timer.start(); QTimer.singleShot(12000,app.quit)
        return app.exec()
    finally:
        guard.close()


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xingtu-instance-owned-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.processes = []
        self.addCleanup(self.cleanup_children)

    def spawn(self, tag, *extra):
        env = dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software')
        process = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'child',str(self.root),tag,*extra],
            env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.processes.append(process)
        return process

    def cleanup_children(self):
        (self.root/'stop').touch()
        for process in self.processes:
            try:
                process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate(timeout=2)  # only retained owned child

    def wait_json(self, name):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            path = self.root/name
            if path.exists():
                try: return json.loads(path.read_text('utf-8'))
                except json.JSONDecodeError: pass
            time.sleep(.02)
        self.fail('Owned child did not produce '+name)

    def test_duplicate_restores_hidden_main_then_release_allows_restart(self):
        first = self.spawn('first'); self.assertTrue(self.wait_json('first.role')['primary'])
        second = self.spawn('second'); self.assertFalse(self.wait_json('second.role')['primary'])
        self.assertEqual(second.wait(timeout=4),0)
        self.assertEqual(self.wait_json('activated.json'),dict(visible=True,minimized=False))
        (self.root/'stop').touch(); self.assertEqual(first.wait(timeout=4),0)
        restarted = self.spawn('restarted','once')
        self.assertTrue(self.wait_json('restarted.role')['primary']); self.assertEqual(restarted.wait(timeout=4),0)

    def test_simultaneous_owned_starts_elect_exactly_one_primary(self):
        self.spawn('race-a'); self.spawn('race-b')
        roles = [self.wait_json(name+'.role')['primary'] for name in ('race-a','race-b')]
        self.assertEqual(sorted(roles),[False,True])

    def test_closing_primary_gives_feedback_instead_of_silent_success(self):
        self.spawn('closing','closing');self.assertTrue(self.wait_json('closing.role')['primary'])
        second=self.spawn('blocked');role=self.wait_json('blocked.role')
        self.assertFalse(role['primary']);self.assertIn('正在退出',role['error'])
        self.assertEqual(second.wait(timeout=4),2)
        self.assertFalse((self.root/'activated.json').exists())

    def test_unavailable_primary_and_unproven_legacy_ack_are_not_success(self):
        self.spawn('unavailable','unavailable');self.assertTrue(self.wait_json('unavailable.role')['primary'])
        second=self.spawn('unconfirmed');role=self.wait_json('unconfirmed.role')
        self.assertFalse(role['primary']);self.assertIn('未能恢复可见窗口',role['error'])
        self.assertEqual(second.wait(timeout=4),2)
        from client_single_instance import SingleInstanceGuard,SingleInstanceError,RESULT_V2,ACK
        with self.assertRaisesRegex(SingleInstanceError,'正在启动'):
            SingleInstanceGuard._activation_result(RESULT_V2+b'starting\n')
        with self.assertRaisesRegex(SingleInstanceError,'旧版已收到恢复请求'):
            SingleInstanceGuard._activation_result(ACK)

    def test_crashed_owned_primary_can_be_started_again(self):
        crashed = self.spawn('crashed','crash')
        self.assertTrue(self.wait_json('crashed.role')['primary']); self.assertEqual(crashed.wait(timeout=4),23)
        restarted = self.spawn('after-crash','once')
        self.assertTrue(self.wait_json('after-crash.role')['primary']); self.assertEqual(restarted.wait(timeout=4),0)

    def test_probe_source_and_production_namespaces_are_disjoint(self):
        from client_single_instance import instance_namespace
        production = instance_namespace(production=True,frozen=True,sandbox=self.root/'v1')
        self.assertEqual(production,instance_namespace(production=True,frozen=True,sandbox=self.root/'v2'))
        source = instance_namespace(production=False,frozen=False,sandbox=self.root)
        isolated = instance_namespace(production=False,frozen=True,sandbox=self.root)
        probe = instance_namespace(production=True,frozen=True,sandbox=self.root,probe_output=self.root/'probe')
        self.assertEqual(len({production,source,isolated,probe}),4)

    def test_permission_failure_never_creates_a_second_primary(self):
        from PySide6.QtCore import QCoreApplication
        from client_single_instance import SingleInstanceGuard, SingleInstanceError, instance_namespace
        app = QCoreApplication.instance() or QCoreApplication([])
        guard = SingleInstanceGuard(instance_namespace(production=False,frozen=False,sandbox=self.root),app)
        with patch('client_single_instance._Ownership',side_effect=PermissionError('access denied')):
            with patch.object(guard,'_notify_existing',return_value=False):
                with self.assertRaisesRegex(SingleInstanceError,'权限'):
                    guard.start(1)
        self.assertIsNone(guard._server)
        guard.close()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='child':
        activation=next((value for value in sys.argv[4:] if value in {'closing','unavailable'}),'normal')
        raise SystemExit(child(sys.argv[2],sys.argv[3],once='once' in sys.argv[4:],crash='crash' in sys.argv[4:],activation=activation))
    unittest.main()
