"""Real process-held instance lifetime, without opening an installer or client."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid


@unittest.skipUnless(os.name == "nt", "Windows parent-held mapping")
class SetupInstanceTests(unittest.TestCase):
    def test_reads_qtifw_style_pid_while_native_byte_range_is_locked(self):
        import ctypes
        from ctypes import wintypes as W
        from installer_resource_lease import _kernel
        from qtifw_setup_instance import _cache_owner
        api = _kernel()
        api.WriteFile.argtypes = [W.HANDLE, W.LPCVOID, W.DWORD, ctypes.POINTER(W.DWORD), W.LPVOID]
        api.LockFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'cache.lock'
            handle = api.CreateFileW(str(path), 0xC0000000, 1, None, 1, 0x04000000, None)
            self.assertNotIn(handle, (None, ctypes.c_void_p(-1).value))
            try:
                data = str(os.getpid()).encode('ascii')
                written = W.DWORD()
                self.assertTrue(api.WriteFile(handle, data, len(data), ctypes.byref(written), None))
                self.assertTrue(api.LockFile(handle, 0, 0, len(data), 0))
                self.assertEqual(_cache_owner(api, path), os.getpid())
            finally:
                api.CloseHandle(handle)

    def test_copies_share_one_instance_and_parent_exit_releases_it(self):
        instance = uuid.uuid4().hex
        child = ("import sys,json;from qtifw_setup_instance import hold;"
                 "print(json.dumps(hold({'instance':sys.argv[1]},sys.executable)),flush=True)")
        parent = ("import sys,subprocess;"
                  "p=subprocess.run([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]],check=True);"
                  "sys.stdin.readline()")
        # The venv redirector adds a short-lived launcher parent. Production
        # uses a one-folder helper directly, so exercise that parent topology.
        python = sys._base_executable
        with subprocess.Popen([python, '-B', '-c', parent, child, instance],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as primary:
            try:
                self.assertTrue(json.loads(primary.stdout.readline())["primary"])
                # A different live parent is refused, regardless of Setup path/name.
                with subprocess.Popen([python, '-B', '-c', parent, child, instance],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as duplicate:
                    self.assertFalse(json.loads(duplicate.stdout.readline())["primary"])
                    duplicate.communicate('\n', timeout=10)
                    self.assertEqual(duplicate.returncode, 0)
            finally:
                primary.communicate('\n', timeout=10)
        self.assertEqual(primary.returncode, 0)
        with subprocess.Popen([python, '-B', '-c', parent, child, instance],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as reopened:
            self.assertTrue(json.loads(reopened.stdout.readline())["primary"])
            reopened.communicate('\n', timeout=10)

    def test_wrong_parent_image_cannot_claim_instance(self):
        from qtifw_setup_instance import hold
        with self.assertRaisesRegex(ValueError, 'parent-mismatch'):
            hold({'instance':uuid.uuid4().hex}, Path(sys.executable).with_name('foreign-Setup.exe'))


if __name__ == '__main__':
    unittest.main()
