"""Windows notice publisher against a local stand-in for the update server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import publish_windows_notice as publisher  # noqa: E402

TOKEN = "t" * 40
NOTES = """更新公告：
PC端 v1.0.2
更新内容：
1. 新增自定义地图。
夸克网盘链接：https://pan.quark.cn/s/4cec4ca814f7
UC网盘链接：https://drive.uc.cn/s/df8af96910bb4?public=1
"""


class FakeServer(BaseHTTPRequestHandler):
    """Mirrors the update server's rules that matter to the publisher."""
    manifest = {}
    posts = []

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/updates/windows.json":
            self._send(200, type(self).manifest)
        else:
            self._send(404, {"message": "接口不存在"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        type(self).posts.append(body)
        if self.headers.get("X-Admin-Token") != TOKEN:
            return self._send(401, {"ok": False, "message": "管理员口令不正确"})
        if self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}":
            return self._send(403, {"ok": False, "message": "拒绝跨站管理请求"})
        if body["versionCode"] <= type(self).manifest.get("versionCode", 0):
            return self._send(409, {"ok": False, "message": "versionCode 必须更大"})
        if body["artifactFileName"] != f"IdentityV-Map-pc-{body['version']}.exe":
            return self._send(400, {"ok": False, "message": "文件名不对"})
        type(self).manifest = {**body, "schemaVersion": 1}
        self._send(201, {"ok": True, "release": body})


class PublisherTests(unittest.TestCase):
    def setUp(self):
        FakeServer.manifest = {"version": "1.7.10", "versionCode": 1710, "releaseNotes": "旧版"}
        FakeServer.posts = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.installer = root / "星图-1.0.2-Setup.exe"
        self.installer.write_bytes(b"installer-bytes")
        self.notes = root / "notes.txt"
        self.notes.write_text(NOTES, encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {
            "XINGTU_PUBLISH_SERVER": f"http://127.0.0.1:{self.server.server_port}", "UPDATE_ADMIN_TOKEN": TOKEN})
        self.env.start()
        self.version = mock.patch.object(publisher, "app_version", lambda: "1.0.2")
        self.version.start()

    def tearDown(self):
        self.version.stop()
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.folder.cleanup()

    def run_main(self, *extra, typed=None, tty=True):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), \
                mock.patch("sys.stdin.isatty", lambda: tty), \
                mock.patch("builtins.input", lambda _prompt: typed or ""):
            code = publisher.main(["--installer", str(self.installer), "--notes", str(self.notes), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_preview_sends_nothing(self):
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("内部版本号 1711", out)
        self.assertIn("IdentityV-Map-pc-1.0.2.exe", out)
        self.assertEqual(FakeServer.posts, [])

    def test_publish_after_typed_confirmation(self):
        code, out, err = self.run_main("--publish", typed="1.0.2")
        self.assertEqual(code, 0, err)
        self.assertIn("已发布并核对一致", out)
        self.assertEqual(FakeServer.manifest["versionCode"], 1711)
        self.assertEqual(FakeServer.manifest["size"], len(b"installer-bytes"))
        self.assertNotIn(TOKEN, out + err)
        # Running again finds it already live and does not post twice.
        code, out, _ = self.run_main("--publish", typed="1.0.2")
        self.assertIn("无需重复发布", out)
        self.assertEqual(len(FakeServer.posts), 1)

    def test_publish_refuses_without_person(self):
        code, _, err = self.run_main("--publish", tty=False)
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        code, out, _ = self.run_main("--publish", typed="1.0.1")
        self.assertEqual(code, 1)
        self.assertEqual(FakeServer.posts, [])

    def test_token_prompt_when_not_in_environment(self):
        with mock.patch.dict(os.environ, {"UPDATE_ADMIN_TOKEN": ""}), \
                mock.patch.object(publisher.getpass, "getpass", lambda _prompt: TOKEN):
            code, out, err = self.run_main("--publish", typed="1.0.2")
        self.assertEqual(code, 0, err)
        self.assertEqual(FakeServer.manifest["versionCode"], 1711)

    def test_unattended_publish(self):
        code, out, err = self.run_main("--publish", "--yes", tty=False)
        self.assertEqual(code, 0, err)
        self.assertIn("已发布并核对一致", out)
        self.assertEqual(FakeServer.manifest["versionCode"], 1711)
        self.assertNotIn(TOKEN, out + err)

    def test_unattended_publish_needs_token(self):
        with mock.patch.dict(os.environ, {"UPDATE_ADMIN_TOKEN": ""}):
            code, _, err = self.run_main("--publish", "--yes", tty=False)
        self.assertEqual(code, 2)
        self.assertIn("UPDATE_ADMIN_TOKEN", err)
        self.assertEqual(FakeServer.posts, [])

    def test_wrong_token_stops(self):
        with mock.patch.dict(os.environ, {"UPDATE_ADMIN_TOKEN": "x" * 40}):
            code, _, err = self.run_main("--publish", typed="1.0.2")
        self.assertEqual(code, 2)
        self.assertIn("口令不正确", err)
        self.assertNotIn("x" * 40, err)

    def test_rejects_bad_notes_and_names(self):
        self.notes.write_text(NOTES.replace("PC端 v1.0.2", "PC端 v1.0.1"), encoding="utf-8")
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("第二行", err)
        self.notes.write_text(NOTES, encoding="utf-8")
        self.installer = self.installer.rename(self.installer.with_name("setup.exe"))
        code, _, err = self.run_main()
        self.assertIn("安装包文件名", err)

    def test_version_must_move_forward(self):
        FakeServer.manifest = {"version": "1.0.3", "versionCode": 1712}
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("不比线上", err)

    def test_notes_rules(self):
        self.assertEqual(publisher.check_notes(NOTES, "1.0.2"), [])
        long = NOTES.replace("1. 新增自定义地图。", "1. " + "很长" * 400)
        self.assertTrue(any("超过" in problem for problem in publisher.check_notes(long, "1.0.2")))
        self.assertTrue(publisher.check_notes(NOTES + "作者：星辰", "1.0.2"))
        self.assertEqual(publisher.server_version("1.0"), "1.0.0")

    def test_production_only_unless_local(self):
        with mock.patch.dict(os.environ, {"XINGTU_PUBLISH_SERVER": "https://evil.example"}):
            with self.assertRaises(publisher.PublishError):
                publisher.server_url()


if __name__ == "__main__":
    unittest.main(verbosity=2)
