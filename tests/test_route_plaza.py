"""Account sign-in and route plaza downloads against a local fake of the account service."""
from datetime import datetime, timedelta, timezone
import hashlib
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
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import account_service  # noqa: E402
from account_service import AccountError, AccountService, SessionVault  # noqa: E402
from custom_maps import CustomMapLibrary, GalleryStore  # noqa: E402
import route_plaza  # noqa: E402
from test_custom_maps import write_package  # noqa: E402

# Fixture art is 120x90 image pixels (60x45 cells), doubled into native pixels.
ROOM_CELL = 22 * 60 + 22  # Image (45, 45) -> native (90, 90): room "big" on floor 1.
VIA_CELL = 7 * 60 + 37    # Image (75, 15) -> native (150, 30): corridor tile on floor 1.


def stamp(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def package_bytes(**overrides):
    manifest = dict(schemaVersion=3, workId="work-1", packageId="pkg-1", revision=2, mode="nightmare",
                    title="噩梦速通", author="alice", minClientVersion="1", imageCount=0,
                    contentKind="nightmare-routes", maps=[
                        dict(mapEntryId="a", displayName="测试图", defaultFloor="1", floors=[], routePlan=dict(
                            version=2, navigationVersion=1, navigationSignature="0" * 64,
                            templateId="nightmare-layout-01", lineColor="#FFD54F", points=[
                                dict(floor="1", cell=ROOM_CELL, kind="room", roomId=3),
                                dict(floor="1", cell=VIA_CELL, kind="via", roomId=0, color="#ff6b81"),
                                dict(floor="2", cell=0, kind="via", roomId=0, start=True),
                                dict(floor="1", cell=10 ** 6, kind="via", roomId=0)], segments=[])),
                        dict(mapEntryId="b", displayName="没有的图", defaultFloor="1", floors=[],
                             routePlan=dict(templateId="nightmare-layout-17", points=[]))])
    manifest.update(overrides)
    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("map-gallery.json", raw)
    return buffer.getvalue(), len(raw)


class FakeServer:
    """The subset of api.xingchenmap.cn the PC client calls."""

    def __init__(self):
        self.archive, self.manifest_bytes = package_bytes()
        self.served = None  # Bytes the object store returns instead of the recorded package.
        self.calls = []
        self.access = {"a1"}
        self.refresh = {"r1"}
        self.access_ttl = 900
        self.fail_refresh = False
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _reply(self, status, body=None, raw=None, content="application/json"):
                data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode("utf-8"))
                self.send_response(status)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _tokens(self, n):
                server.access.add(f"a{n}")
                server.refresh.add(f"r{n}")
                return dict(access_token=f"a{n}", refresh_token=f"r{n}", token_type="Bearer",
                            access_expires_at=stamp(server.access_ttl), refresh_expires_at=stamp(86400))

            def _authorized(self):
                header = self.headers.get("Authorization", "")
                return header.startswith("Bearer ") and header[7:] in server.access

            def do_GET(self):
                server.calls.append(("GET", self.path, self.headers.get("Authorization")))
                if self.path.startswith("/v1/community/map-galleries?"):
                    self._reply(200, [dict(id="ver-2", work_id="work-1", revision=2, title="噩梦速通", description="五张图",
                                           owner_username="alice", content_kind="nightmare-routes", status="approved",
                                           modes=["nightmare"], nightmare_map_count=2, download_count=7,
                                           updated_at="2026-10-28T08:00:00+00:00"),
                                      dict(id="img", work_id="w", content_kind="images", status="approved")])
                elif self.path == "/objects/pkg-1.zip":
                    self._reply(200, raw=server.served or server.archive, content="application/zip")
                else:
                    self._reply(404, dict(detail="not found"))

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                server.calls.append(("POST", self.path, body))
                if self.path == "/v1/auth/login":
                    if body["password"] != "secret1":
                        return self._reply(401, dict(detail="用户名、邮箱或密码错误"))
                    return self._reply(200, self._tokens(1))
                if self.path == "/v1/auth/register":
                    return self._reply(201, dict(self._tokens(1), recovery_code="ABCD-EFGH-JKMP-QRST"))
                if self.path == "/v1/auth/refresh":
                    if server.fail_refresh or body["refresh_token"] not in server.refresh:
                        return self._reply(401, dict(detail="登录已失效"))
                    server.refresh.discard(body["refresh_token"])
                    return self._reply(200, self._tokens(len(server.calls)))
                if self.path == "/v1/auth/logout":
                    return self._reply(204)
                if self.path == "/v1/auth/password-recovery":
                    return self._reply(200, dict(message="密码已重置"))
                if self.path == "/v1/community/map-galleries/ver-2/packages/nightmare/download":
                    if not self._authorized():
                        return self._reply(401, dict(detail="请先登录"))
                    host = f"http://127.0.0.1:{self.server.server_address[1]}"
                    return self._reply(200, dict(
                        version_id="ver-2", work_id="work-1", content_kind="nightmare-routes",
                        package=dict(package_id="pkg-1", revision=2, mode="nightmare", map_count=2, image_count=0,
                                     manifest_bytes=server.manifest_bytes, object_bytes=len(server.archive),
                                     sha256=hashlib.sha256(server.archive).hexdigest()),
                        download_url=f"{host}/objects/pkg-1.zip", expires_in_seconds=600))
                self._reply(404, dict(detail="not found"))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class MemoryVault(SessionVault):
    def __init__(self, path):
        super().__init__(path, protect=lambda data: data[::-1], unprotect=lambda data: data[::-1])


class RoutePlazaTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.env = mock.patch.dict(os.environ, {"XINGTU_ACCOUNT_API_URL": self.server.url})
        self.env.start()
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        write_package(self.root / "maps")
        manifest = json.loads((self.root / "maps/manifest.json").read_text("utf-8"))
        manifest["maps"][0]["templateId"] = "nightmare-layout-01"
        (self.root / "maps/manifest.json").write_text(json.dumps(manifest), "utf-8")
        self.library = CustomMapLibrary(self.root / "maps")
        self.store = GalleryStore(self.root / "settings/custom-galleries.json", self.root / "none.json")
        self.account = AccountService(self.root / "settings", vault=MemoryVault(self.root / "settings/session.bin"))

    def tearDown(self):
        self.env.stop()
        self.server.close()
        self.folder.cleanup()

    def test_only_local_test_servers_override_production(self):
        with mock.patch.dict(os.environ, {"XINGTU_ACCOUNT_API_URL": "https://evil.example"}):
            with self.assertRaises(AccountError):
                account_service.api_root()
        with mock.patch.dict(os.environ, {"XINGTU_ACCOUNT_API_URL": ""}):
            self.assertEqual(account_service.api_root(), "https://api.xingchenmap.cn")
            self.assertFalse(route_plaza._download_url_allowed("https://evil.example/x.zip"))
            self.assertFalse(route_plaza._download_url_allowed("http://bucket.oss-cn-hangzhou.aliyuncs.com/x"))
            self.assertTrue(route_plaza._download_url_allowed("https://bucket.oss-cn-hangzhou.aliyuncs.com/x"))

    def test_login_errors_and_session_survives_restart(self):
        with self.assertRaisesRegex(AccountError, "密码错误"):
            self.account.login("alice", "wrong-1")
        with self.assertRaisesRegex(AccountError, "用户名"):
            self.account.login("Bad Name!", "secret1")
        self.assertEqual(self.account.login(" Alice ", "secret1"), "alice")
        login = next(call for call in self.server.calls if call[1] == "/v1/auth/login")[2]
        self.assertEqual(login["installation_id"], self.account.installation_id)
        stored = (self.root / "settings/session.bin").read_bytes()
        self.assertNotIn(b"secret1", stored[::-1], "the password is never kept")
        again = AccountService(self.root / "settings", vault=MemoryVault(self.root / "settings/session.bin"))
        self.assertEqual((again.username, again.installation_id), ("alice", self.account.installation_id))

    def test_list_keeps_only_approved_routes(self):
        routes = route_plaza.list_routes("速通")
        self.assertEqual([(r.version_id, r.author, r.map_count, r.updated_date) for r in routes],
                         [("ver-2", "alice", 2, "2026-10-28")])
        path = next(call[1] for call in self.server.calls if call[0] == "GET")
        self.assertIn("content_kind=nightmare-routes", path)
        self.assertIsNone(next(call[2] for call in self.server.calls if call[0] == "GET"), "listing sends no token")

    def test_download_converts_cells_to_native_targets(self):
        with self.assertRaisesRegex(AccountError, "请先登录"):
            route_plaza.install(self.account, self.library, self.store, route_plaza.list_routes()[0])
        self.account.login("alice", "secret1")
        route = route_plaza.list_routes()[0]
        gallery, report = route_plaza.install(self.account, self.library, self.store, route)
        self.assertEqual(report, dict(maps=1, missingMaps=1, droppedPoints=1))
        points = self.store.route(gallery, "m")
        self.assertEqual([(p["floor"], p["kind"], p["room"], p["x"], p["y"], p["color"], p.get("start", False))
                          for p in points],
                         [("1", "room", "big", 30.0, 30.0, "#FFD54F", False),
                          ("1", "via", None, 150.0, 30.0, "#FF6B81", False),
                          ("2", "via", None, 30.0, 30.0, "#FFD54F", True)])
        self.assertTrue(self.store.readonly(gallery))
        self.assertIn((gallery, "噩梦速通", True), self.store.galleries())
        self.assertEqual(self.store.plaza_versions(), {"work-1": "ver-2"})
        self.assertEqual(self.store.source(gallery)["author"], "alice")
        grant_get = [call for call in self.server.calls if call[1] == "/objects/pkg-1.zip"]
        self.assertEqual(grant_get[0][2], None, "the signed object link gets no token")
        # A second download replaces the same gallery instead of adding another.
        self.assertEqual(route_plaza.install(self.account, self.library, self.store, route)[0], gallery)
        self.assertEqual(len(self.store.galleries()), 2)
        # Copies and exports are the player's own, editable data.
        copy = self.store.create("我的", copy_from=gallery)
        self.assertFalse(self.store.readonly(copy))
        self.assertNotIn("source", self.store.export(gallery))
        reloaded = GalleryStore(self.root / "settings/custom-galleries.json", self.root / "none.json")
        self.assertTrue(reloaded.readonly(gallery))
        self.assertTrue(reloaded.delete(gallery))

    def test_tampered_or_mismatched_packages_are_rejected(self):
        self.account.login("alice", "secret1")
        route = route_plaza.list_routes()[0]
        self.server.served = self.server.archive[:-1] + b"x"
        with self.assertRaisesRegex(AccountError, "校验失败"):
            route_plaza.download(self.account, route)
        self.server.served = None
        self.server.archive, self.server.manifest_bytes = package_bytes(workId="other")
        with self.assertRaisesRegex(AccountError, "不一致"):
            route_plaza.download(self.account, route)
        self.assertEqual(self.store.plaza_versions(), {})

    def test_expired_access_refreshes_once_then_signs_out(self):
        self.server.access_ttl = 10  # Inside the 30 s margin: refresh before the call.
        self.account.login("alice", "secret1")
        route_plaza.download(self.account, route_plaza.list_routes()[0])
        self.assertIn("/v1/auth/refresh", [call[1] for call in self.server.calls])
        self.server.fail_refresh = True
        with self.assertRaisesRegex(AccountError, "重新登录"):
            route_plaza.download(self.account, route_plaza.list_routes()[0])
        self.assertIsNone(self.account.username)
        self.assertFalse((self.root / "settings/session.bin").exists())

    def test_register_returns_recovery_code_and_recovery_signs_out(self):
        self.assertEqual(self.account.register("bob_1", "secret1", "Bob@Example.com"), "ABCD-EFGH-JKMP-QRST")
        self.assertEqual(self.account.username, "bob_1")
        register = next(call[2] for call in self.server.calls if call[1] == "/v1/auth/register")
        self.assertEqual(register["email"], "bob@example.com")
        with self.assertRaisesRegex(AccountError, "恢复码"):
            self.account.recover("bob_1", "nope", "secret2")
        self.assertEqual(self.account.recover("bob_1", "abcd-efgh-jkmp-qrst", "secret2"), "密码已重置")
        self.assertIsNone(self.account.username)

    def test_without_dpapi_nothing_is_written(self):
        vault = SessionVault(self.root / "plain.bin", protect=None, unprotect=None)
        if sys.platform == "win32":
            self.skipTest("Windows always has DPAPI")
        account = AccountService(self.root / "settings", vault=vault)
        account.login("alice", "secret1")
        self.assertFalse(vault.persistent)
        self.assertFalse((self.root / "plain.bin").exists())


if __name__ == "__main__":
    unittest.main()
