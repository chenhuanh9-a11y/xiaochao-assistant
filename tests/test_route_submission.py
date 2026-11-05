"""Real localhost HTTP/PUT, synthetic navigation; no production account or art."""
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import zipfile

from account_service import AccountError, AccountService
from custom_maps import CustomMapLibrary
import route_submission as submission
from test_custom_maps import write_package
from test_route_plaza import stamp

VERSION, WORK, SESSION = (str(uuid.UUID(int=i)) for i in (1, 2, 3))
REVISION = str(uuid.UUID(int=4))


def rle(values):
    out = []
    for value in values:
        if out and out[-2] == value:
            out[-1] += 1
        else:
            out.extend((value, 1))
    return out


def navigation():
    cols, rows = 60, 45
    edges = [(1 if x < cols - 1 else 0) | (2 if y < rows - 1 else 0)
             | (4 if x else 0) | (8 if y else 0) for y in range(rows) for x in range(cols)]
    labels = [1 if x <= 29 and y <= 29 else 0 for y in range(rows) for x in range(cols)]
    return dict(version=1, width=120, height=90, step=2, cols=cols, rows=rows,
                edges=rle(edges), labels=rle(labels), rooms=[dict(id=1, cell=22 * 60 + 22)])


def write_submission_resource(root):
    write_package(root)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    row = manifest["maps"][0]
    row["templateId"] = "nightmare-layout-01"
    contract = dict(templateId=row["templateId"], navigationSignature="a" * 64,
        floors={"1": navigation(), "2": navigation()}, stairs=[dict(kind="stairs", verified=True,
            bidirectional=True, cost=100., **{"from": dict(floor="1", cell=7 * 60 + 37), "to": dict(floor="2", cell=22 * 60 + 22)})])
    raw = json.dumps(contract).encode()
    (root / "submission.json").write_bytes(raw)
    row["routeSubmission"] = dict(path="submission.json", sha256=hashlib.sha256(raw).hexdigest())
    path.write_text(json.dumps(manifest))
    return CustomMapLibrary(root)


def gallery():
    return dict(id="personal", name="个人路线", readonly=False, maps=dict(m=dict(points=[
        dict(floor="1", x=90, y=90, kind="room", color="#ff6b81"),
        dict(floor="1", x=150, y=30, kind="via", color="#63CAFF"),
        dict(floor="2", x=90, y=90, kind="room", color="#FFD54F"),
        dict(floor="1", x=30, y=30, kind="via", color="#63CAFF", start=True)])))


class SubmissionServer:
    """State transitions mirror account-sync-server/app/map_galleries.py."""
    def __init__(self):
        self.calls, self.uploads, self.records = [], [], {}
        self.session = None
        self.status_override = None
        self.fail = ""
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.gate = ""
        self.conflict = False
        self.session_mismatch = False
        self.put_redirect = False
        self.required_revision = None
        server = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def reply(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            def handle_request(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
                server.calls.append((self.command, self.path, body, dict(self.headers)))
                if self.path == server.gate:
                    server.entered.set(); server.release.wait(6)
                if self.path == "/v1/auth/login":
                    return self.reply(200, dict(access_token="test-access", refresh_token="test-refresh",
                        access_expires_at=stamp(900), refresh_expires_at=stamp(86400)))
                if self.path == "/v1/auth/logout":
                    return self.reply(200, {})
                if self.path.startswith("/v1/") and self.headers.get("Authorization") != "Bearer test-access":
                    return self.reply(401, dict(detail="请先登录"))
                if server.fail and self.path.endswith(server.fail):
                    return self.reply(503, dict(detail="测试网络暂不可用"))
                if self.path == "/v1/community/my/map-galleries":
                    return self.reply(200, list(server.records.values()))
                if self.path.startswith(submission.BASE + "?"):
                    return self.reply(200, [])
                if self.path == submission.BASE and self.command == "POST":
                    row = dict(id=VERSION, work_id=WORK, revision=1, content_kind="nightmare-routes",
                        status="draft", official_source_id=None, packages=[], owner_username="alice",
                        title=body["title"], description=body["description"], rejection_reason=None,
                        updated_at="2026-11-03T00:00:00Z")
                    server.records[VERSION] = row
                    return self.reply(201, row)
                key = self.path.removeprefix(submission.BASE + "/").split("/")[0]
                row = server.records.get(key)
                if row is None:
                    return self.reply(404, dict(detail="不存在"))
                if self.command == "GET":
                    return self.reply(200, row)
                if self.command == "PUT":
                    if row["status"] == "pending":
                        return self.reply(409, dict(detail="审核中不能编辑"))
                    row = deepcopy(row)
                    if row["status"] in ("approved", "withdrawn", "taken_down"):
                        row.update(id=REVISION, revision=row["revision"] + 1)
                    row.update(body, status="draft")
                    server.records[row["id"]] = row
                    return self.reply(200, row)
                if server.conflict:
                    return self.reply(409, dict(detail="当前状态不能提交审核"))
                if self.path.endswith("/upload-sessions"):
                    old = server.session
                    if server.required_revision and body["package_revision"] != server.required_revision:
                        return self.reply(409, dict(detail=dict(message="图库包身份不是当前可上传修订",
                            package_id=body["package_id"], package_revision=server.required_revision)))
                    session = dict(body, id=SESSION, gallery_version_id=key,
                        status="completed" if old and old["status"] == "completed" and old["sha256"] == body["sha256"] else "created",
                        upload=dict(method="PUT", url=server.url + "/object", headers={"Content-Type": submission.CONTENT_TYPE,
                            "Authorization": "forbidden", "Cookie": "forbidden"}))
                    server.session = session
                    if server.session_mismatch:
                        session = dict(session, sha256="b" * 64)
                    return self.reply(201, session)
                if self.path.endswith("/complete"):
                    server.session["status"] = "completed"
                    row["packages"] = [dict(package_id=server.session["package_id"], revision=server.session["package_revision"],
                        mode="nightmare", map_count=server.session["map_count"], image_count=0)]
                    return self.reply(200, server.session)
                if self.path.endswith("/submit"):
                    row["status"] = "pending"
                    return self.reply(200, row)
                if self.path.endswith("/withdraw"):
                    row["status"] = "withdrawn"
                    return self.reply(200, row)
                return self.reply(404, {})
            do_GET = do_POST = handle_request
            def do_PUT(self):
                if self.path != "/object":
                    return self.handle_request()
                data = self.rfile.read(int(self.headers["Content-Length"]))
                server.uploads.append((data, dict(self.headers)))
                if server.gate == "/object":
                    server.entered.set(); server.release.wait(6)
                self.send_response(307 if server.put_redirect else 200)
                self.send_header("ETag", '"test-etag"')
                self.send_header("Location", server.url + "/must-not-follow")
                self.send_header("Content-Length", "0"); self.end_headers()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.release.set(); self.httpd.shutdown(); self.httpd.server_close()


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = write_submission_resource(self.root / "maps")
        self.server = SubmissionServer()
        self.env = patch.dict(os.environ, XINGTU_ACCOUNT_API_URL=self.server.url)
        self.env.start()
        self.account = AccountService(self.root / "settings")
        self.account.login("alice", "secret1")
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.account.shutdown(); self.server.close(); self.env.stop(); self.temp.cleanup()

    def attempt(self, **kwargs):
        maps = submission.prepare_maps(self.library, gallery())
        return submission.Submission("personal", maps, "我的路线", "这是说明", **kwargs)

    def test_conversion_package_and_actual_put_no_token(self):
        progress = []
        result = self.attempt().run(self.account, progress=lambda *args: progress.append(args))
        self.assertEqual(result["status"], "pending")
        data, headers = self.server.uploads[0]
        self.assertNotIn("Authorization", headers); self.assertNotIn("Cookie", headers)
        self.assertEqual(headers["Content-Type"], submission.CONTENT_TYPE)
        self.assertEqual(len(data), self.server.session["object_bytes"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.server.session["sha256"])
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            self.assertEqual(bundle.namelist(), ["map-gallery.json"])
            raw = bundle.read("map-gallery.json")
        self.assertEqual(len(raw), self.server.session["manifest_bytes"])
        manifest = json.loads(raw)
        self.assertEqual(manifest["schemaVersion"], 3)
        self.assertNotIn("official", manifest)
        plan = manifest["maps"][0]["routePlan"]
        self.assertEqual(plan["points"][0], dict(floor="1", cell=1342, kind="room", roomId=1, color="#FF6B81"))
        self.assertEqual(plan["points"][1]["cell"], 457)
        self.assertTrue(plan["points"][3]["start"])
        self.assertEqual(len(plan["segments"]), 2)
        self.assertEqual([p["floor"] for p in plan["segments"][1]["parts"]], ["1", "2"])
        # Independently replay cardinal runs and check both ordered endpoints.
        for leg in plan["segments"]:
            first, last = plan["points"][leg["from"]], plan["points"][leg["to"]]
            self.assertEqual((leg["parts"][0]["floor"], leg["parts"][0]["cell"]), (first["floor"], first["cell"]))
            part = leg["parts"][-1]; cell = part["cell"]
            for direction, count in zip(part["runs"][::2], part["runs"][1::2]):
                cell += {1: 1, 2: 60, 4: -1, 8: -60}[direction] * count
            self.assertEqual((part["floor"], cell), (last["floor"], last["cell"]))
        self.assertTrue(any(len(p) == 3 and p[1] == p[2] and p[2] > 0 for p in progress))
        self.assertEqual(submission.list_mine(self.account)[0]["id"], VERSION)
        self.assertEqual(submission.withdraw(self.account, VERSION)["status"], "withdrawn")

    def test_approved_update_adopts_returned_version_and_package_revision(self):
        self.attempt().run(self.account)
        self.server.records[VERSION]["status"] = "approved"
        previous = deepcopy(self.server.records[VERSION]["packages"][0])
        result = self.attempt(version=VERSION).run(self.account)
        self.assertEqual(result["id"], REVISION)
        self.assertEqual(self.server.session["gallery_version_id"], REVISION)
        self.assertEqual(self.server.session["package_id"], previous["package_id"])
        self.assertEqual(self.server.session["package_revision"], previous["revision"] + 1)
        self.assertEqual(self.server.records[VERSION]["status"], "approved")

    def test_failure_retry_reuses_exact_upload_and_submit_response_loss(self):
        attempt = self.attempt()
        self.server.fail = "/complete"
        with self.assertRaises(AccountError): attempt.run(self.account)
        original = deepcopy(self.server.session)
        self.server.fail = ""
        attempt.run(self.account)
        self.assertEqual(self.server.session["idempotency_key"], original["idempotency_key"])
        self.assertEqual(self.server.uploads[0][0], self.server.uploads[1][0])
        count = len(self.server.calls)
        attempt.run(self.account)
        self.assertEqual(len(self.server.calls), count + 1)  # only GET after known pending
        self.assertEqual(sum(method == "POST" and path == submission.BASE for method, path, *_ in self.server.calls), 1)

    def test_cancel_after_put_resumes_completed_session_and_never_submits_early(self):
        attempt, cancelled = self.attempt(), threading.Event()
        def progress(stage, *args):
            if stage == "正在校验并绑定路线包": cancelled.set()
        with self.assertRaisesRegex(AccountError, "已停止投稿"):
            attempt.run(self.account, cancelled=cancelled.is_set, progress=progress)
        self.assertEqual(self.server.records[VERSION]["status"], "draft")
        cancelled.clear()
        attempt.run(self.account, cancelled=cancelled.is_set)
        self.assertEqual(self.server.records[VERSION]["status"], "pending")

    def test_mismatch_conflict_and_redirect_fail_closed(self):
        self.server.session_mismatch = True
        with self.assertRaisesRegex(AccountError, "会话.*不一致"): self.attempt().run(self.account)
        self.assertEqual(self.server.uploads, [])
        self.server.session_mismatch = False
        self.server.put_redirect = True
        with self.assertRaisesRegex(AccountError, "307"): self.attempt().run(self.account)
        self.assertFalse(any("must-not-follow" in call[1] for call in self.server.calls))
        self.server.conflict = True
        with self.assertRaises(AccountError) as caught: self.attempt().run(self.account)
        self.assertEqual(caught.exception.status, 409)

    def test_no_fake_navigation_or_silent_point_loss(self):
        row = gallery()
        for updates in (dict(readonly=True), dict(source={"workId": WORK}), dict(id="default")):
            with self.assertRaises(AccountError): submission.prepare_maps(self.library, dict(row, **updates))
        missing_root = self.root / "missing"
        write_package(missing_root)
        path = missing_root / "manifest.json"
        data = json.loads(path.read_text()); data["maps"][0]["templateId"] = "nightmare-layout-01"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "缺少投稿导航资源"):
            submission.prepare_maps(CustomMapLibrary(missing_root), row)
        raw = self.root / "maps/submission.json"
        raw.write_text("{}")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            submission.prepare_maps(CustomMapLibrary(self.root / "maps"), row)
        self.library = write_submission_resource(self.root / "other")
        bad = gallery(); bad["maps"]["m"]["points"][0].update(x=999999)
        with self.assertRaisesRegex(AccountError, "无法对应"): submission.prepare_maps(self.library, bad)
        with self.assertRaisesRegex(AccountError, "2–48"): submission.metadata("一", "")
        with self.assertRaises(AccountError): submission.metadata("标题", "字" * 501)
        self.assertEqual(submission.validate_version(dict(self.attempt().run(self.account), official_source_id=None))["status"], "pending")

    def test_unknown_draft_creation_does_not_automatically_duplicate(self):
        attempt = self.attempt()
        original = self.account.authorized
        def lost(method, path, body=None):
            result = original(method, path, body)
            if method == "POST" and path == submission.BASE:
                raise AccountError("网络断开")
            return result
        with patch.object(self.account, "authorized", side_effect=lost):
            with self.assertRaises(AccountError): attempt.run(self.account)
        with self.assertRaisesRegex(AccountError, "结果未确认"): attempt.run(self.account)
        self.assertEqual(len(self.server.records), 1)

    def test_server_supplied_revision_recovers_abandoned_upload(self):
        self.server.required_revision = 3
        self.attempt().run(self.account)
        self.assertEqual(self.server.session["package_revision"], 3)
        sessions = [call for call in self.server.calls if call[1].endswith("/upload-sessions")]
        self.assertEqual([call[2]["package_revision"] for call in sessions], [1, 3])
        self.assertEqual(sessions[0][2]["package_id"], sessions[1][2]["package_id"])


if __name__ == "__main__":
    unittest.main()
