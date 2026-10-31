"""Offline route-plaza boundaries; no credentials, network or user galleries."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from urllib.request import Request
import zipfile

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import route_plaza as plaza
from account_service import AccountError
from custom_maps import FloorGraph, MapGraph, RouteError


ROUTE = plaza.PlazaRoute("version-2", "work-1", 2, "路线", "描述", "alice", 1, 3, "2026-10-31")
URL = "https://bucket.oss-cn-hangzhou.aliyuncs.com/package.zip?signature=fake"


def manifest_fixture():
    return dict(schemaVersion=3, contentKind="nightmare-routes", mode="nightmare",
        packageId="pkg-1", workId="work-1", revision=2, title="测试路线", imageCount=0,
        maps=[dict(routePlan=dict(templateId="nightmare-layout-01", lineColor="#FFD54F", points=[
            dict(floor="1", cell=7 * 60 + 37, kind="via", color="#ff6b81"),
            dict(floor="2", cell=7 * 60 + 7, kind="via", start=True)]))])


def archive_fixture(manifest=None, *, extra=None):
    raw = json.dumps(manifest or manifest_fixture()).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(plaza.MANIFEST_NAME, raw)
        if extra:
            bundle.writestr(extra, b"not allowed")
    archive = buffer.getvalue()
    grant = dict(version_id="version-2", work_id="work-1", content_kind=plaza.CONTENT_KIND,
        download_url=URL, package=dict(package_id="pkg-1", mode="nightmare", revision=2,
        map_count=1, image_count=0, object_bytes=len(archive), manifest_bytes=len(raw),
        sha256=hashlib.sha256(archive).hexdigest()))
    return archive, grant


class FakeAccount:
    def __init__(self, grant):
        self.grant, self.calls = grant, []

    def authorized(self, method, path, body):
        self.calls.append((method, path, body))
        return self.grant


class FakeOpener:
    def __init__(self, archive, url=URL):
        self.archive, self.url, self.requests = archive, url, []

    def open(self, req, timeout):
        self.requests.append((req, timeout))
        response = io.BytesIO(self.archive)
        response.geturl = lambda: self.url
        return response


def library_fixture(*, registration=True):
    # The Android art is half native size, while the current display is quarter.
    asset = SimpleNamespace(image_size=(60, 45), to_native=np.diag([4., 4., 1.]),
        to_image=np.diag([.25, .25, 1.]), native_shape=(180, 240),
        template_size=(120, 90) if registration else None,
        template_to_native=np.diag([2., 2., 1.]) if registration else None)
    graph = MapGraph({f: FloorGraph([[30, 30], [90, 30], [150, 30]], [None, None, None],
        [[0, 1, "road"], [1, 2, "road"]]) for f in ("1", "2")}, [])
    return SimpleNamespace(available=True, error="", templates=lambda: {"map-1": "nightmare-layout-01"},
        floor=lambda *_: asset, graph=lambda *_: graph)


class PlazaTransportTests(unittest.TestCase):
    def test_public_listing_filters_approved_routes_and_encodes_query(self):
        calls = []
        row = dict(id="version-2", work_id="work-1", revision=2, content_kind=plaza.CONTENT_KIND,
                   status="approved", modes=["nightmare"], owner_username="alice")
        def api(*args):
            calls.append(args)
            return [row, row, dict(row, status="pending"), dict(row, content_kind="images")]
        routes = plaza.list_routes("a&b", api=api)
        self.assertEqual(len(routes), 1)
        self.assertEqual(calls[0][0], "GET")
        self.assertIn("query=a%26b", calls[0][1])
        self.assertEqual(len(calls[0]), 2, "public listing has no account/token argument")

    def test_complete_fake_download_is_verified_and_oss_get_has_no_token(self):
        archive, grant = archive_fixture()
        account, opener = FakeAccount(grant), FakeOpener(archive)
        manifest, _ = plaza.download(account, ROUTE,
            fetcher=lambda *args: plaza.fetch_archive(*args, opener=opener))
        self.assertEqual(manifest, manifest_fixture())
        self.assertEqual(account.calls, [("POST", "/v1/community/map-galleries/version-2/packages/nightmare/download", {"confirmed": True})])
        self.assertEqual(set(k.lower() for k in opener.requests[0][0].headers), {"user-agent"})

    def test_oss_url_and_redirect_allowlist_is_enforced_before_following(self):
        for url in ("http://bucket.aliyuncs.com/a", "https://evil.test/a", "https://aliyuncs.com/a",
                    "https://bucket.aliyuncs.com.evil.test/a", "https://user:pass@bucket.aliyuncs.com/a",
                    "https://bucket.aliyuncs.com:9443/a", "https://bucket.aliyuncs.com/a#secret",
                    "https://bucket.aliyuncs.com\\@evil.test/a", " https://bucket.aliyuncs.com/a"):
            with self.subTest(url=url):
                self.assertFalse(plaza._download_url_allowed(url))
        self.assertTrue(plaza._download_url_allowed(URL))
        with self.assertRaisesRegex(AccountError, "其他站点"):
            plaza._ObjectRedirect().redirect_request(Request(URL), None, 302, "found", {}, "https://evil.test/a")

    def test_size_hash_and_record_mismatch_rejected(self):
        archive, grant = archive_fixture()
        for data, size, digest in ((archive + b"x", len(archive), grant["package"]["sha256"]),
                                  (archive, len(archive) + 1, grant["package"]["sha256"]),
                                  (archive, len(archive), "0" * 64)):
            with self.assertRaisesRegex(AccountError, "校验失败"):
                plaza.fetch_archive(URL, size, digest, opener=FakeOpener(data))
        with self.assertRaisesRegex(AccountError, "上限"):
            plaza.fetch_archive(URL, plaza.MAX_ARCHIVE_BYTES + 1, "0" * 64)
        bad = deepcopy(grant)
        bad["version_id"] = "another-version"
        with self.assertRaisesRegex(AccountError, "不一致"):
            plaza.download(FakeAccount(bad), ROUTE, fetcher=lambda *_: self.fail("must not fetch"))

    def test_schema_identity_size_and_non_manifest_members_rejected(self):
        for change in ({"schemaVersion": 2}, {"workId": "other"}, {"revision": 3}, {"mode": "difficult"}, {"imageCount": 1}):
            archive, grant = archive_fixture(dict(manifest_fixture(), **change))
            with self.assertRaises(AccountError):
                plaza.read_manifest(archive, grant["package"], grant)
        for extra in ("../outside.txt", "image.png", "folder/", "map-gallery.json"):
            archive, grant = archive_fixture(extra=extra)
            with self.assertRaisesRegex(AccountError, "格式"):
                plaza.read_manifest(archive, grant["package"], grant)
        archive, grant = archive_fixture()
        grant["package"]["manifest_bytes"] += 1
        with self.assertRaisesRegex(AccountError, "大小"):
            plaza.read_manifest(archive, grant["package"], grant)


class PlazaConversionTests(unittest.TestCase):
    def test_template_cells_to_native_then_current_display_coordinates(self):
        library = library_fixture()
        maps, report = plaza.convert_manifest(library, manifest_fixture())
        points = maps["map-1"]["points"]
        self.assertEqual([(p["floor"], p["x"], p["y"]) for p in points], [("1", 150., 30.), ("2", 30., 30.)])
        self.assertEqual(points[0]["color"], "#FF6B81")
        self.assertTrue(points[1]["start"])
        np.testing.assert_array_equal(library.floor().to_image @ [points[0]["x"], points[0]["y"], 1], [37.5, 7.5, 1])
        self.assertEqual(report, dict(maps=1, missingMaps=0, droppedPoints=0))

    def test_missing_template_registration_never_uses_display_matrix(self):
        with self.assertRaisesRegex(RouteError, "缺少安卓模板坐标注册"):
            plaza.convert_manifest(library_fixture(registration=False), manifest_fixture())

    def test_dropped_new_start_still_separates_next_surviving_target(self):
        manifest = manifest_fixture()
        points = manifest["maps"][0]["routePlan"]["points"]
        points.insert(1, dict(floor="1", cell=999999, kind="via", start=True))
        points[-1].pop("start")
        maps, report = plaza.convert_manifest(library_fixture(), manifest)
        self.assertTrue(maps["map-1"]["points"][-1]["start"])
        self.assertEqual(report["droppedPoints"], 1)

    def test_missing_maps_invalid_points_and_duplicate_map_reporting(self):
        manifest = manifest_fixture()
        manifest["maps"].append(dict(routePlan=dict(templateId="unregistered", points=[])))
        manifest["maps"][0]["routePlan"]["points"].extend([
            dict(floor="1", cell=True, kind="via"), dict(floor="1", cell=999999, kind="via"),
            dict(floor="B1", cell=1, kind="via")])
        maps, report = plaza.convert_manifest(library_fixture(), manifest)
        self.assertEqual(report, dict(maps=1, missingMaps=1, droppedPoints=3))
        manifest["maps"].append(deepcopy(manifest["maps"][0]))
        with self.assertRaisesRegex(RouteError, "重复地图"):
            plaza.convert_manifest(library_fixture(), manifest)


if __name__ == "__main__":
    unittest.main()
