"""The runtime worker owns both the Qt-free gallery and recorded session."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "hybrid/python/runtime_worker.py"
for directory in (ROOT / "app", ROOT / "experiments/qt_quick", ROOT / "hybrid/python"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from client_live_runtime import _AlignmentHost
from runtime_session import RecordedRuntimeSession
from runtime_worker import browse_terminal
from worker import create_browse_worker
from s0_test_fixtures import synthetic_read
from test_client_live_runtime import fixture_engine


RUN_WORKER = r"""
import runpy
import sys
from importlib.abc import MetaPathFinder

root, tests, hybrid, worker_path = sys.argv[1:5]
for path in (root, tests, hybrid):
    if path not in sys.path:
        sys.path.insert(0, path)

class BlockQt(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise RuntimeError("unified runtime worker attempted to import PySide6")
        return None

sys.meta_path.insert(0, BlockQt())
sys.argv = [worker_path, *sys.argv[5:]]
runpy.run_path(worker_path, run_name="__main__")
"""


class UnifiedRuntimeProcess:
    def __init__(self, root, *, explicit_installation_root=False):
        self.root = Path(root)
        self.installation = self.root / "install"
        self.resources = self.installation / "_internal"
        self.frames = self.root / "frames"
        self.data = self.root / "data"
        for directory in (self.installation, self.resources, self.frames, self.data):
            directory.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-B", "-u", "-c", RUN_WORKER,
            str(ROOT), str(ROOT / "tests"), str(ROOT / "hybrid/python"), str(WORKER),
            "--resource-root", str(self.resources), "--data-root", str(self.data),
            "--frame-root", str(self.frames), "--grid-backend", "python"]
        if explicit_installation_root:
            command += ["--installation-root", str(self.installation)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.messages = queue.Queue()
        self.errors = []

        def read_stdout():
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
            self.messages.put({"event": "transport-closed"})

        def read_stderr():
            for chunk in self.process.stderr:
                self.errors.append(chunk)

        self.reader = threading.Thread(target=read_stdout, daemon=True)
        self.error_reader = threading.Thread(target=read_stderr, daemon=True)
        self.reader.start()
        self.error_reader.start()
        self.ready = self.messages.get(timeout=15)
        if self.ready.get("ready") is not True:
            self.close()
            raise AssertionError((self.ready, "".join(self.errors)))

    def send(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, request_id, operation, **values):
        self.send(dict(session="unified-test", id=request_id, operation=operation,
                       timeoutMs=10000, **values))
        while True:
            message = self.messages.get(timeout=20)
            if message.get("event") == "result" and message.get("id") == request_id:
                return message
            if message.get("event") == "transport-closed":
                raise AssertionError(("worker exited before response", request_id, "".join(self.errors)))

    def close(self):
        if self.process.poll() is None and self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        if self.process.poll() is None:
            self.process.wait(timeout=15)
        self.reader.join(timeout=2)
        self.error_reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream and not stream.closed:
                stream.close()
        return self.process.returncode


def write_gallery_fixture(resource_root):
    references = resource_root / "references"
    images = resource_root / "images"
    references.mkdir(parents=True, exist_ok=True)
    images.mkdir(parents=True, exist_ok=True)
    (references / "nightmare-map-pack.json").write_text(json.dumps({
        "packId": "fixture-nightmare", "displayName": "噩梦 fixture",
        "defaultEntryFloor": "1", "floors": ["1", "2"], "maps": [{
            "id": "nightmare-map", "name": "噩梦地图", "resourceBase": "resource-root",
            "routeProfileId": "suju",
            "layers": {"1": "images/nightmare-1.png", "2": "images/nightmare-2.png"},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (references / "difficult-runtime.json").write_text(json.dumps({
        "mode": "difficult", "packId": "fixture-difficult", "displayName": "困难 fixture",
        "defaultEntryFloor": "1", "floors": ["1", "2"], "maps": [{
            "id": "difficult-map", "name": "困难地图", "routeProfileId": "zhan10-speed",
            "layers": {"1": "images/difficult-1.png", "2": "images/difficult-2.png"},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    for name, color in (("nightmare-1.png", "red"), ("nightmare-2.png", "green"),
                        ("difficult-1.png", "blue"), ("difficult-2.png", "yellow")):
        Image.new("RGB", (24, 16), color).save(images / name, format="PNG")


class HybridUnifiedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def start_process(self, *, explicit_installation_root=False):
        process = UnifiedRuntimeProcess(self.temp.name,
            explicit_installation_root=explicit_installation_root)
        self.addCleanup(process.close)
        return process

    def test_qt_blocked_process_serves_hello_catalog_and_png_preview(self):
        process = self.start_process()
        write_gallery_fixture(process.resources)
        hello = process.request(1, "hello")
        self.assertTrue(hello["ok"], hello)
        self.assertTrue({"contexts", "python", "operations"}.issubset(hello["result"]))
        self.assertEqual(hello["result"]["operations"], [
            "hello", "catalog", "preview", "prepare", "set_frame", "dispatch", "snapshot"])

        catalog = process.request(2, "catalog", season="s0", mode="nightmare")
        self.assertTrue(catalog["ok"], catalog)
        row = catalog["result"]["rows"][0]
        preview = process.request(3, "preview", catalogId=catalog["result"]["catalogId"], key=row["key"])
        self.assertTrue(preview["ok"], preview)
        result = preview["result"]
        self.assertEqual((result["width"], result["height"]), (24, 16))
        with Image.open(BytesIO(base64.b64decode(result["pngBase64"]))) as decoded:
            decoded.load()
            self.assertEqual(decoded.size, (24, 16))
        self.assertIsNone(preview["render"])
        self.assertNotIn("render", result)

    def test_catalog_reuses_runtime_lease_and_does_not_change_prepared_context(self):
        process = self.start_process(explicit_installation_root=True)
        write_gallery_fixture(process.resources)
        prepared = process.request(1, "prepare", season="s0", mode="nightmare")
        self.assertTrue(prepared["ok"], prepared)
        before = process.request(2, "snapshot")["result"]["snapshot"]
        catalog = process.request(3, "catalog", season="s0", mode="difficult")
        self.assertTrue(catalog["ok"], catalog)
        after = process.request(4, "snapshot")["result"]["snapshot"]
        self.assertEqual((before["season"], before["mode"]), ("s0", "nightmare"))
        self.assertEqual(after, before)
        self.assertTrue((process.installation / "work/map-packages/import.lock").is_file())

    def test_browse_failure_preserves_production_runtime_round_and_render(self):
        root = Path(self.temp.name)
        installation = root / "install"
        resources = installation / "_internal"
        frames = root / "frames"
        data = root / "data"
        resources.mkdir(parents=True)
        frames.mkdir()
        write_gallery_fixture(resources)
        frame_path = frames / "saved.png"
        Image.new("RGB", (1280, 720), "black").save(frame_path, format="PNG")

        runtime = RecordedRuntimeSession(resources, data, frames)
        self.addCleanup(runtime.close)
        browse = create_browse_worker(resources, installation)
        fit = dict(success=True,
            matrixSourceToClient=[[.5, 0, 40], [0, .5, 60], [0, 0, 1]],
            selectedRouteCurrentFrameResidual=dict(verified=True,
                poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
                coordinatePackId="fixture-pack", validationBasis="shared-canonical-geometry-current-frame"))

        def build(mode, *, season):
            self.assertEqual(season, "s0")
            return fixture_engine(mode)

        with patch.object(runtime._factories, "_build", side_effect=build), \
                patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)), \
                patch("floor_control_region.locate_vertical_floor_controls",
                      side_effect=lambda frame, mode, **kw: synthetic_read(mode)), \
                patch.object(_AlignmentHost, "fit", side_effect=lambda *args, **kwargs: dict(fit)):
            prepared = runtime.prepare("s0", "nightmare")
            self.assertTrue(prepared["available"], prepared)
            self.assertIsNotNone(runtime._lease)
            self.assertTrue(runtime.set_frame("saved.png")["success"])
            started = runtime.dispatch("start_round")
            self.assertTrue(started["receipt"]["success"], started)
            captured = runtime.dispatch("capture_now")
            self.assertTrue(captured["receipt"]["success"], captured)
            before = runtime.snapshot()
            before_render = runtime.render()
            self.assertTrue(before["roundActive"])
            self.assertIsNotNone(before_render)

            failed = browse_terminal(browse, dict(session="same-process", id=17,
                operation="catalog", season="unknown", mode="nightmare"))
            after = runtime.snapshot()
            after_render = runtime.render()

        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"]["code"], "invalid-context")
        self.assertIsNone(failed["render"])
        for key in ("roundActive", "season", "mode", "mapId", "floor", "profileId",
                    "displayAuthorized", "structureMatrixSourceToClient"):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(after_render.keys(), before_render.keys())
        self.assertTrue((after_render["pixels"] == before_render["pixels"]).all())


if __name__ == "__main__":
    unittest.main()
