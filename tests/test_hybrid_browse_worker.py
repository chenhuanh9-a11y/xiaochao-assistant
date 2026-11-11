"""Subprocess contract tests for the Qt-free first hybrid gallery worker."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from PIL import Image
from tests.test_season_offline_resources import fixture as make_s1_offline_fixture


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "hybrid" / "python" / "worker.py"
BLOCK_QT_AND_RUN = textwrap.dedent("""
    import runpy
    import sys
    from importlib.abc import MetaPathFinder

    class BlockQt(MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "PySide6" or fullname.startswith("PySide6."):
                raise RuntimeError("hybrid browse worker attempted to import PySide6")
            return None

    sys.meta_path.insert(0, BlockQt())
    worker_path = sys.argv[1]
    sys.argv = [worker_path, *sys.argv[2:]]
    runpy.run_path(worker_path, run_name="__main__")
""")


class WorkerProcess:
    def __init__(self, root):
        self.process = subprocess.Popen(
            [sys.executable, "-c", BLOCK_QT_AND_RUN, str(WORKER), "--resource-root", str(root)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

    def request(self, request):
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read()
            raise AssertionError(f"worker exited before replying ({self.process.poll()}): {stderr}")
        return json.loads(line)

    def raw(self, line):
        self.process.stdin.write(line)
        self.process.stdin.flush()
        response = self.process.stdout.readline()
        if not response:
            stderr = self.process.stderr.read()
            raise AssertionError(f"worker exited before replying ({self.process.poll()}): {stderr}")
        return json.loads(response)

    def close_input(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        return self.process.wait(timeout=10)

    def terminate(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream and not stream.closed:
                stream.close()


class HybridBrowseWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.worker = None

    def tearDown(self):
        if self.worker is not None:
            self.worker.terminate()

    def write_json(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def write_png(self, relative, size=(24, 16), color="red"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, color).save(path, format="PNG")
        return path

    def make_s0_resources(self):
        self.write_json("references/nightmare-map-pack.json", {
            "packId": "fixture-nightmare",
            "displayName": "噩梦 fixture",
            "defaultEntryFloor": "1",
            "floors": ["1", "2"],
            "maps": [{
                "id": "nightmare-map",
                "name": "噩梦地图",
                "resourceBase": "resource-root",
                "routeProfileId": "suju",
                "layers": {"1": "images/nightmare-1.png", "2": "images/nightmare-2.png"},
            }],
        })
        self.write_json("references/difficult-runtime.json", {
            "mode": "difficult",
            "packId": "fixture-difficult",
            "displayName": "困难 fixture",
            "defaultEntryFloor": "1",
            "floors": ["1", "2"],
            "maps": [{
                "id": "difficult-map",
                "name": "困难地图",
                "routeProfileId": "zhan10-speed",
                "layers": {"1": "images/difficult-1.png", "2": "images/difficult-2.png"},
            }],
        })
        for name in ("nightmare-1.png", "nightmare-2.png", "difficult-1.png", "difficult-2.png"):
            self.write_png(Path("images") / name)

    def start_worker(self):
        self.worker = WorkerProcess(self.root)
        self.addCleanup(self.worker.terminate)
        return self.worker

    def test_selecting_internal_obeys_parent_installation_recovery_marker(self):
        installation = self.root
        self.root = installation / "_internal"
        self.root.mkdir()
        self.make_s0_resources()
        marker = installation / "work/map-packages/pending-resource-reset.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}", encoding="utf-8")
        worker = self.start_worker()
        response = worker.request({"id": 1, "operation": "catalog", "season": "s0", "mode": "nightmare"})
        self.assertFalse(response["ok"], response)
        self.assertIn("resource-reset-recovery-required", response["error"]["message"])
        self.assertTrue((installation / "work/map-packages/import.lock").exists())
        self.assertFalse((self.root / "work/map-packages/import.lock").exists())

    def catalog(self, worker, request_id, season, mode):
        response = worker.request({"id": request_id, "operation": "catalog",
                                   "season": season, "mode": mode})
        self.assertTrue(response["ok"], response)
        return response["result"]

    def test_hello_and_catalog_cover_s0_nightmare_and_difficult_rows(self):
        self.make_s0_resources()
        worker = self.start_worker()
        hello = worker.request({"id": 1, "operation": "hello"})
        self.assertTrue(hello["ok"], hello)
        self.assertEqual(hello["result"]["python"], f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        self.assertIn({"season": "s0", "mode": "nightmare", "label": "S0 · 厄运之女 噩梦"},
                      hello["result"]["contexts"])
        self.assertIn({"season": "s0", "mode": "difficult", "label": "S0 · 厄运之女 困难"},
                      hello["result"]["contexts"])

        nightmare = self.catalog(worker, 2, "s0", "nightmare")
        difficult = self.catalog(worker, 3, "s0", "difficult")
        self.assertEqual((nightmare["catalogId"], difficult["catalogId"]), (1, 2))
        self.assertEqual(nightmare["profiles"], [{"id": "suju", "name": "噩梦 fixture"}])
        self.assertEqual([row["key"] for row in nightmare["rows"]], [
            "s0/nightmare/suju/nightmare-map/pack/1",
            "s0/nightmare/suju/nightmare-map/pack/2",
        ])
        self.assertEqual([row["floorLabel"] for row in nightmare["rows"]], ["1 楼", "2 楼"])
        self.assertTrue(all(row["available"] for row in nightmare["rows"]))
        self.assertEqual([row["key"] for row in difficult["rows"]], [
            "s0/difficult/zhan10-speed/difficult-map/pack/1",
            "s0/difficult/zhan10-speed/difficult-map/pack/2",
        ])
        self.assertEqual(difficult["rows"][0]["profileId"], "zhan10-speed")

    def test_preview_is_an_actual_bounded_rgba_png(self):
        self.make_s0_resources()
        worker = self.start_worker()
        catalog = self.catalog(worker, 1, "s0", "nightmare")
        key = catalog["rows"][0]["key"]
        response = worker.request({"id": 2, "operation": "preview",
                                   "catalogId": catalog["catalogId"], "key": key})
        self.assertTrue(response["ok"], response)
        result = response["result"]
        self.assertEqual((result["catalogId"], result["key"]), (catalog["catalogId"], key))
        png = base64.b64decode(result["pngBase64"], validate=True)
        self.assertLessEqual(len(png), 12 * 1024 * 1024)
        with Image.open(__import__("io").BytesIO(png)) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.mode, "RGBA")
            self.assertEqual(decoded.size, (result["width"], result["height"]))
            self.assertLessEqual(max(decoded.size), 1600)

    def test_missing_resource_rows_and_failed_catalog_clear_prior_snapshot(self):
        self.make_s0_resources()
        (self.root / "images/nightmare-2.png").unlink()
        worker = self.start_worker()
        catalog = self.catalog(worker, 1, "s0", "nightmare")
        missing = catalog["rows"][1]
        self.assertFalse(missing["available"])
        unavailable = worker.request({"id": 2, "operation": "preview",
                                      "catalogId": catalog["catalogId"], "key": missing["key"]})
        self.assertEqual(unavailable["error"]["code"], "preview-unavailable")

        failed = worker.request({"id": 3, "operation": "catalog", "season": "s1", "mode": "difficult"})
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"]["code"], "resource-error")
        stale = worker.request({"id": 4, "operation": "preview",
                                "catalogId": catalog["catalogId"], "key": catalog["rows"][0]["key"]})
        self.assertEqual(stale["error"]["code"], "no-catalog")

    def test_switching_catalog_rejects_old_catalog_id_and_foreign_key(self):
        self.make_s0_resources()
        worker = self.start_worker()
        old = self.catalog(worker, 1, "s0", "nightmare")
        current = self.catalog(worker, 2, "s0", "difficult")
        old_response = worker.request({"id": 3, "operation": "preview",
                                       "catalogId": old["catalogId"], "key": old["rows"][0]["key"]})
        self.assertEqual(old_response["error"]["code"], "stale-catalog")
        foreign = worker.request({"id": 4, "operation": "preview",
                                  "catalogId": current["catalogId"], "key": old["rows"][0]["key"]})
        self.assertEqual(foreign["error"]["code"], "invalid-key")

    def test_preview_rejects_a_file_changed_after_catalog_load(self):
        self.make_s0_resources()
        worker = self.start_worker()
        catalog = self.catalog(worker, 1, "s0", "nightmare")
        changed_path = self.root / "images/nightmare-1.png"
        self.write_png("images/nightmare-1.png", size=(35, 21), color="blue")
        response = worker.request({"id": 2, "operation": "preview",
                                   "catalogId": catalog["catalogId"], "key": catalog["rows"][0]["key"]})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "resource-changed")
        self.assertNotEqual(changed_path.stat().st_size, 0)

    def test_s1_catalog_preview_uses_source_height_labels_and_hash_stamps(self):
        source_index = make_s1_offline_fixture(self.root)
        worker = self.start_worker()

        for request_id, mode in enumerate(("nightmare", "difficult"), start=1):
            catalog = self.catalog(worker, request_id, "s1", mode)
            row = next(row for row in catalog["rows"]
                       if row["profileId"] == "s1-offline-rendered"
                       and row["floor"] == "source-height-4")
            self.assertEqual(row["floorLabel"], "-1层")
            preview = worker.request({"id": request_id + 10, "operation": "preview",
                                      "catalogId": catalog["catalogId"], "key": row["key"]})
            self.assertTrue(preview["ok"], preview)
            result = preview["result"]
            png = base64.b64decode(result["pngBase64"], validate=True)
            with Image.open(io.BytesIO(png)) as decoded:
                self.assertEqual(decoded.format, "PNG")
                self.assertEqual(decoded.mode, "RGBA")
                self.assertEqual(decoded.size, (result["width"], result["height"]))

            map_record = next(item for item in source_index["maps"]
                              if item["mapId"] == row["mapId"])
            floor_record = next(floor for floor in map_record["floors"]
                                if floor["sourceHeight"] == 4)
            image_path = self.root / floor_record["rendered"]["path"]
            original = image_path.read_bytes()
            original_stat = image_path.stat()
            changed_stream = io.BytesIO()
            Image.new("RGBA", (6, 8), (80, 160, 211, 255)).save(changed_stream, "PNG")
            changed = changed_stream.getvalue()
            self.assertNotEqual(changed, original)
            self.assertEqual(len(changed), len(original))
            image_path.write_bytes(changed)
            os.utime(image_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            changed_stat = image_path.stat()
            self.assertEqual((changed_stat.st_mtime_ns, changed_stat.st_size),
                             (original_stat.st_mtime_ns, original_stat.st_size))
            stale = worker.request({"id": request_id + 20, "operation": "preview",
                                    "catalogId": catalog["catalogId"], "key": row["key"]})
            self.assertFalse(stale["ok"])
            self.assertEqual(stale["error"]["code"], "resource-changed")
            image_path.write_bytes(original)
            os.utime(image_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        stopped = worker.request({"id": 99, "operation": "shutdown"})
        self.assertTrue(stopped["ok"], stopped)
        self.assertEqual(worker.process.wait(timeout=10), 0)

    def test_bad_json_unknown_operation_and_oversized_line_recover(self):
        worker = self.start_worker()
        bad_json = worker.raw("{not-json}\n")
        self.assertEqual(bad_json["error"]["code"], "invalid-json")
        unknown = worker.request({"id": 7, "operation": "write-settings"})
        self.assertEqual(unknown["error"]["code"], "unknown-operation")
        oversized = worker.raw("x" * (64 * 1024 + 1) + "\n")
        self.assertEqual(oversized["error"]["code"], "request-too-large")
        recovered = worker.request({"id": 8, "operation": "hello"})
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(worker.close_input(), 0)
        self.assertEqual(worker.process.stdout.read(), "")

    def test_shutdown_replies_then_exits_normally(self):
        worker = self.start_worker()
        response = worker.request({"id": 10, "operation": "shutdown"})
        self.assertEqual(response, {"id": 10, "ok": True, "result": {}})
        self.assertEqual(worker.process.wait(timeout=10), 0)
        self.assertEqual(worker.process.stdout.read(), "")


if __name__ == "__main__":
    unittest.main()
