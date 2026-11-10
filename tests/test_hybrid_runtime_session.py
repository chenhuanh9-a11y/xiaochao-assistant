"""Recorded-frame adapter tests: production orchestration, synthetic core seams."""
from pathlib import Path
from contextlib import ExitStack
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "app", ROOT / "experiments" / "qt_quick", ROOT / "hybrid" / "python"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from client_live_runtime import _AlignmentHost
from client_providers import EngineFactories
from s0_test_fixtures import synthetic_read
import test_client_live_runtime as runtime_fixtures
from runtime_session import MAX_FRAME_PIXELS, RecordedRuntimeSession


class HybridRuntimeSessionTests(unittest.TestCase):
    def test_data_root_cannot_write_into_resource_or_frame_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resources, frames = root / "resources", root / "frames"
            resources.mkdir(); frames.mkdir()
            for data in (resources, resources / "new-memory", frames / "new-memory"):
                with self.assertRaisesRegex(ValueError, "outside resource and frame roots"):
                    RecordedRuntimeSession(resources, data, frames)
            self.assertFalse((resources / "new-memory").exists())
            self.assertFalse((frames / "new-memory").exists())

    def test_dispatch_cannot_read_while_another_process_holds_the_resource_lease(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
            root = Path(folder)
            resources, frames = root / "resources", root / "frames"
            resources.mkdir(); frames.mkdir()
            code = ("import sys; sys.path.insert(0,sys.argv[1]); "
                    "from resource_transaction import resource_runtime_lease; "
                    "lease=resource_runtime_lease(sys.argv[2]); print('locked',flush=True); "
                    "sys.stdin.read(); lease.close()")
            holder = subprocess.Popen([sys.executable, "-B", "-c", code, str(ROOT / "app"), str(resources)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            try:
                self.assertEqual(holder.stdout.readline().strip(), "locked")
                session = RecordedRuntimeSession(resources, root / "data", frames)
                cleanup.callback(session.close)
                result = session.dispatch("start_round")
                self.assertFalse(result["receipt"]["success"])
                self.assertIn("resource-busy", result["receipt"]["reason"])
                self.assertIsNone(session._lease)
            finally:
                holder.stdin.close()
                holder.wait(timeout=5)
                holder.stdout.close(); holder.stderr.close()
            self.assertEqual(holder.returncode, 0)

    def test_dispatch_without_or_after_failed_prepare_cannot_bypass_recovery(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
            root = Path(folder)
            resources, data, frames = root / "resources", root / "data", root / "frames"
            resources.mkdir(); frames.mkdir()
            marker = resources / "work/map-packages/pending-resource-reset.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}", encoding="utf-8")
            session = RecordedRuntimeSession(resources, data, frames)
            cleanup.callback(session.close)
            for mode in ("nightmare", "difficult"):
                session.dispatch("set_context", dict(mode=mode, season="s0", profileId="shared-structure"))
                result = session.dispatch("start_round")
                self.assertFalse(result["receipt"]["success"])
                self.assertIn("resource-reset-recovery-required", result["receipt"]["reason"])
                self.assertNotIn("missing-resource", result["receipt"]["reason"])
                self.assertIn("resource-reset-recovery-required", session.prepare("s0", mode)["reason"])
                repeated = session.dispatch("start_round")
                self.assertIn("resource-reset-recovery-required", repeated["receipt"]["reason"])

    def write_png(self, path, *, width=1280, height=720):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[40:80, 60:120] = (12, 80, 190)
        ok, encoded = cv2.imencode(".png", frame)
        self.assertTrue(ok)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded.tobytes())

    def test_construction_is_lazy_and_png_provenance_defaults_to_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resources, data, frames = root / "resources", root / "data", root / "frames"
            resources.mkdir(); frames.mkdir()
            frame_path = frames / "saved.png"
            self.write_png(frame_path, width=96, height=64)

            session = RecordedRuntimeSession(resources, data, frames)
            self.addCleanup(session.close)
            self.assertFalse((resources / "work").exists())
            self.assertFalse(data.exists())
            self.assertIsNone(session.render())

            result = session.set_frame("saved.png")
            self.assertTrue(result["success"], result)
            self.assertEqual(result["evidenceKind"], "recorded")
            self.assertFalse(result["liveAuthorization"])
            self.assertEqual(result["captureContext"]["coordinateOrigin"], "unknown")
            self.assertFalse(result["captureContext"]["coordinateOriginKnown"])
            self.assertFalse(result["captureContext"]["globalPlacementAvailable"])
            self.assertFalse(session._capture._frame.flags.writeable)
            self.assertNotIn("PySide6", sys.modules)

    def test_real_runtime_start_and_capture_produces_recorded_nonlive_render_in_both_modes(self):
        fit = dict(success=True,
            matrixSourceToClient=np.array([[.5, 0, 40], [0, .5, 60], [0, 0, 1.]]),
            selectedRouteCurrentFrameResidual=dict(verified=True,
                poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
                coordinatePackId="fixture-pack", validationBasis="shared-canonical-geometry-current-frame"))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resources = root / "install" / "_internal"
            resources.mkdir(parents=True)
            data, frames = root / "data", root / "frames"
            frames.mkdir()
            frame_path = frames / "map.png"
            self.write_png(frame_path)
            physical = dict(Width=1280, Height=720, Left=410, Top=125, Dpi=144)

            session = RecordedRuntimeSession(resources, data, frames)
            self.addCleanup(session.close)

            def build(mode, *, season):
                self.assertEqual(season, "s0")
                return runtime_fixtures.fixture_engine(mode)

            with patch.object(session._factories, "_build", side_effect=build), \
                    patch("map_interface_observation.detect_map_interface",
                          return_value=dict(detected=True)), \
                    patch("floor_control_region.locate_vertical_floor_controls",
                          side_effect=lambda frame, mode, **kw: synthetic_read(mode)), \
                    patch.object(_AlignmentHost, "fit", side_effect=lambda *args, **kwargs: fit.copy()):
                for mode in ("nightmare", "difficult"):
                    with self.subTest(mode=mode):
                        prepared = session.prepare("s0", mode)
                        self.assertTrue(prepared["available"], prepared)
                        frame = session.set_frame("map.png", capture_context={"physicalClient": physical})
                        self.assertTrue(frame["success"], frame)
                        self.assertEqual(frame["captureContext"]["clientScreenBounds"], [410, 125, 1690, 845])
                        self.assertEqual(frame["captureContext"]["dpi"], 144)
                        self.assertFalse(frame["captureContext"]["globalPlacementAvailable"])

                        started = session.dispatch("start_round")
                        self.assertTrue(started["receipt"]["success"], started["receipt"])
                        captured = session.dispatch("capture_now")
                        self.assertTrue(captured["receipt"]["success"], captured["receipt"])
                        self.assertEqual(captured["snapshot"]["evidenceKind"], "recorded")
                        self.assertFalse(captured["receipt"].get("liveAuthorization", False))
                        self.assertFalse(captured["snapshot"]["liveAuthorization"])
                        render = session.render()
                        self.assertIsNotNone(render)
                        self.assertIn("pixels", render)
                        self.assertEqual(render["pixels"].ndim, 3)
                        self.assertFalse(render.get("liveAuthorization", False))
                        bad_replacement = session.set_frame("map.png", capture_context={
                            "physicalClient": {"Width": 1279, "Height": 720,
                                "Left": 410, "Top": 125, "Dpi": 144}})
                        self.assertFalse(bad_replacement["success"])
                        self.assertFalse(session._capture.snapshot["frameAvailable"])
                        self.assertIsNone(session.render())
                        invalidated = session.invalidate("test-request-cancelled")
                        self.assertIsNone(session.render())
                        self.assertFalse(invalidated["liveAuthorization"])
                        self.assertTrue(invalidated["roundActive"])
                        session.dispatch("end_round")

            self.assertTrue((root / "install" / "work" / "map-packages" / "import.lock").is_file())
            session.close()
            # The lease remains held across prepare/dispatch and is released by close.
            from resource_transaction import resource_runtime_lease
            lease = resource_runtime_lease(root / "install")
            lease.close()

    def test_missing_production_resources_remain_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = RecordedRuntimeSession(root / "resources", root / "data", root / "frames")
            self.addCleanup(session.close)
            (root / "resources").mkdir()
            report = session.prepare("s0", "nightmare")
            self.assertFalse(report["available"])
            self.assertTrue(report["prepared"])
            self.assertIn("missing-resource", report["reason"])
            self.assertFalse(session._factories._engines)
            session.close()

    def test_explicit_memory_root_is_per_context_and_legacy_paths_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = EngineFactories(root / "resources")
            self.assertEqual(legacy._memory_store_root("nightmare", "s0"),
                (root / "resources" / ".qt-saved-frame-memory" / "nightmare"))
            self.assertEqual(legacy._memory_store_root("nightmare", "s1"),
                (root / "resources" / ".qt-saved-frame-memory" / "s1" / "nightmare"))

            injected = EngineFactories(root / "resources", memory_root=root / "data" / "memory")
            expected = root / "data" / "memory" / "s1" / "difficult-memory"
            self.assertEqual(injected._memory_store_root("difficult", "s1"), expected)
            self.assertTrue(expected.is_relative_to(root / "data" / "memory"))
            self.assertFalse((root / "resources" / ".qt-saved-frame-memory").exists())

            production_paths = type("Paths", (), {
                "memory_root": lambda self, mode, season: root / "production-work" / season / (mode + "-memory")})()
            with patch("client_providers.PRODUCTION", True), \
                    patch("client_providers.runtime_paths", return_value=production_paths):
                self.assertEqual(legacy._memory_store_root("nightmare", "s0"),
                    root / "production-work" / "s0" / "nightmare-memory")

    def test_invalid_prepare_context_does_not_acquire_a_resource_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resources = root / "resources"
            resources.mkdir()
            session = RecordedRuntimeSession(resources, root / "data", root / "frames")
            self.addCleanup(session.close)
            invalid_season = session.prepare("s999", "nightmare")
            invalid_mode = session.prepare("s0", "unknown-mode")
            self.assertFalse(invalid_season["available"])
            self.assertFalse(invalid_mode["available"])
            self.assertIsNone(session._lease)
            self.assertFalse((resources / "work").exists())
            session.close()

    def test_oversized_png_header_is_rejected_before_decode_and_retires_prior_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resources, data, frames = root / "resources", root / "data", root / "frames"
            resources.mkdir(); frames.mkdir()
            good = frames / "saved.png"
            self.write_png(good, width=96, height=64)
            oversized = frames / "oversized.png"
            oversized.write_bytes(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                + (MAX_FRAME_PIXELS + 1).to_bytes(4, "big") + (1).to_bytes(4, "big"))
            session = RecordedRuntimeSession(resources, data, frames)
            self.addCleanup(session.close)
            self.assertTrue(session.set_frame("saved.png")["success"])
            with patch("cv2.imdecode", side_effect=AssertionError("must reject from IHDR")) as decode:
                result = session.set_frame("oversized.png")
            self.assertFalse(result["success"])
            self.assertIn("recorded-frame-size-invalid", result["reason"])
            decode.assert_not_called()
            self.assertFalse(session._capture.snapshot["frameAvailable"])
            self.assertIsNone(session.render())

    def test_frame_paths_outside_root_and_mismatched_client_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resources, data, frames = root / "resources", root / "data", root / "frames"
            resources.mkdir(); frames.mkdir()
            inside = frames / "saved.png"
            self.write_png(inside, width=96, height=64)
            outside = root / "outside.png"
            self.write_png(outside, width=96, height=64)
            session = RecordedRuntimeSession(resources, data, frames)
            self.addCleanup(session.close)

            self.assertFalse(session.set_frame(outside)["success"])
            good = session.set_frame("saved.png")
            self.assertTrue(good["success"], good)
            mismatch = session.set_frame("saved.png", capture_context={"physicalClient": {
                "Width": 95, "Height": 64, "Left": 10, "Top": 20, "Dpi": 96}})
            self.assertFalse(mismatch["success"])
            self.assertFalse(session._capture.snapshot["frameAvailable"])
            self.assertIsNone(session.render())


if __name__ == "__main__":
    unittest.main()
