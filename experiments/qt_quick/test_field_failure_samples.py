"""Opt-in producer/export/drain contracts with synthetic captures and temp files."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PySide6.QtCore import QCoreApplication
import test_client_live_runtime as fixtures
from client_live_runtime import _AlignmentHost
from client_problems import ProblemsService
from client_session import SessionService
from diagnostic_export import export_safe_diagnostic_bundle
from nightmare_candidate import NightmareMemoryStore


class FailureSamplingContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.f = fixtures.LiveRuntimeContracts()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.engine.pack.by_id["fixture-map"].name = "Fixture"
        self.store = NightmareMemoryStore(self.f.engine.pack, self.root / "memory",
            portable_seed_path=self.root / "absent-seed.json")
        self.f.engine.memory_store = self.store
        self.problems = ProblemsService(SimpleNamespace(work_root=self.root),
            build_fingerprint="AAAAAAAA", source_commit="a"*40)
        self.addCleanup(self.problems.shutdown)
        self.f.runtime.configure_failure_sampling(event_path=self.problems.log_path,
            build_fingerprint="AAAAAAAA", source_commit="a"*40)
        self.f.runtime.set_event_callback(lambda event:self.problems.recordTrace(
            dict(role="runtime", stage=event["event"], frame=event.get("frameId"))))
        self.f.fit = dict(success=False, reason="initial-structure-current-geometry-failed",
            diagnostics=dict(queryChains=3, sourceChains=5, seedCount=2,
                candidates=[dict(regions=dict(regionalEdgeVerified=False), residual=None)]))

    def capture_failure(self):
        if not self.f.runtime.snapshot()["roundActive"]:
            self.f.dispatch("start_round")
        return self.f.dispatch("capture_now")

    def test_default_off_then_same_frame_opt_in_export_and_cooldown(self):
        self.assertFalse(self.capture_failure()["success"])
        self.assertFalse(self.store.alignment_failure_path.exists())
        self.f.runtime.set_failure_sampling_enabled(True)
        before = self.f.capture.count
        result = self.capture_failure()
        self.assertEqual(result["reason"], self.f.fit["reason"])
        self.assertEqual(self.f.capture.count, before+1)
        rows = [json.loads(s) for s in self.store.alignment_failure_path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["diagnostic"]["fit"]["diagnostics"]["seedCount"], 2)
        self.assertEqual(row["diagnostic"]["clientBounds"], [100,200,1380,920])
        self.assertEqual(row["diagnostic"]["frameId"], self.f.runtime.snapshot()["frameId"])
        import cv2
        pixels = cv2.imdecode(np.frombuffer((self.store.root/row["samplePath"]).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(pixels, self.f.capture.frame)
        with patch.object(_AlignmentHost, "_shared_structure_reference", side_effect=AssertionError("cooldown must skip preparation")) as prepare:
            self.capture_failure()
        prepare.assert_not_called()
        self.assertEqual(len(self.store.alignment_failure_path.read_text().splitlines()), 1)
        self.assertIsNone(self.store._failure_writer)
        deadline = time.monotonic() + 3
        while self.problems._job is not None or self.problems._queue:
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("accepted Problems rows did not drain")
            time.sleep(.001)
        self.problems.shutdown()
        receipt = export_safe_diagnostic_bundle(self.root, self.root/"nightmare-live",
            destination=self.root/"evidence.zip", app_version="fixture", build_fingerprint="AAAAAAAA",
            failure_roots=(self.store.root,))
        with zipfile.ZipFile(receipt["path"]) as bundle:
            manifest = json.loads(bundle.read("diagnostic-manifest.json"))
        self.assertEqual(manifest["failureEvidence"]["imageCount"], 1)

    def test_saved_frame_survives_actual_problems_log_rotation(self):
        self.f.runtime.set_failure_sampling_enabled(True)
        self.capture_failure()
        deadline = time.monotonic() + 3
        while self.problems._job is not None or self.problems._queue:
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("accepted Problems rows did not drain")
            time.sleep(.001)
        self.problems.shutdown()
        from client_problems import _write_batch
        from datetime import datetime, timezone
        with patch("client_problems.LOG_MAX_BYTES", self.problems.log_path.stat().st_size + 1):
            _write_batch(self.problems.log_path, [dict(event="fixture-padding",
                timestampUTC=datetime.now(timezone.utc).isoformat(), padding="x"*100)])
        previous = self.problems.log_path.with_name(self.problems.log_path.stem+"-previous.jsonl")
        self.assertTrue(previous.exists())
        receipt = export_safe_diagnostic_bundle(self.root, self.root/"nightmare-live",
            destination=self.root/"rotated-evidence.zip", app_version="fixture", build_fingerprint="AAAAAAAA",
            failure_roots=(self.store.root,))
        with zipfile.ZipFile(receipt["path"]) as bundle:
            manifest = json.loads(bundle.read("diagnostic-manifest.json"))
        evidence = manifest["failureEvidence"]
        self.assertEqual(evidence["imageCount"], 1)
        self.assertEqual(evidence["cases"][0]["logFile"], previous.name)

    def test_cancelled_or_changed_capture_does_not_save_and_sampling_error_does_not_change_reason(self):
        self.f.runtime.set_failure_sampling_enabled(True)
        def invalidated(*args):
            self.f.runtime.invalidate("map-closed")
            return self.f.fit
        with patch.object(_AlignmentHost, "fit", side_effect=invalidated):
            result = self.capture_failure()
        self.assertTrue(result["stale"])
        self.assertFalse(self.store.alignment_failure_path.exists())
        with patch.object(self.store, "record_alignment_failure", side_effect=OSError("fixture-disk-failure")):
            result = self.capture_failure()
        self.assertEqual(result["reason"], self.f.fit["reason"])
        self.assertEqual(self.f.runtime._failure_samples.last_error, "OSError")
        self.assertFalse(self.f.runtime.snapshot()["displayAuthorized"])

    def test_preparation_error_preserves_geometry_rejection(self):
        self.f.runtime.set_failure_sampling_enabled(True)
        with patch.object(_AlignmentHost, "_shared_structure_reference", side_effect=ValueError("fixture-metadata-error")):
            result = self.capture_failure()
        self.assertEqual(result["reason"], self.f.fit["reason"])
        self.assertEqual(self.f.runtime._failure_samples.last_error, "ValueError")
        self.assertFalse(self.store.alignment_failure_path.exists())

    def test_difficult_uses_same_capture_failure_recorder(self):
        engine = fixtures.fixture_engine("difficult")
        engine.pack.by_id["fixture-map"].name = "Difficult fixture"
        engine.memory_store = NightmareMemoryStore(engine.pack, self.root/"difficult-memory",
            portable_seed_path=self.root/"absent-seed.json")
        self.f.runtime._factories["difficult"] = lambda:engine
        self.f.dispatch("set_game_mode", mode="difficult")
        self.f.runtime.set_failure_sampling_enabled(True)
        result = self.capture_failure()
        self.assertEqual(result["reason"], self.f.fit["reason"])
        row = json.loads(engine.memory_store.alignment_failure_path.read_text().splitlines()[0])
        self.assertEqual(row["diagnostic"]["mode"], "difficult")
        self.assertEqual(row["diagnostic"]["evidenceKind"], "synthetic")

    def test_accepted_sample_is_in_session_worker_drain_without_daemon(self):
        self.f.runtime.set_event_callback(None)
        self.f.runtime.set_failure_sampling_enabled(True)
        self.f.dispatch("start_round")
        session = SessionService(self.f.runtime)
        entered, release = threading.Event(), threading.Event()
        original = self.store.record_alignment_failure
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("sample writer not released")
            return original(*args, **kwargs)
        try:
            with patch.object(self.store, "record_alignment_failure", side_effect=delayed):
                session.dispatch("capture_now")
                self.assertTrue(entered.wait(3))
                session.stop()
                self.assertGreater(session._pool.activeThreadCount(), 0)
                release.set()
                self.assertTrue(session._pool.waitForDone(3000))
            self.assertTrue(self.store.alignment_failure_path.exists())
            self.assertIsNone(self.store._failure_writer)
            self.app.processEvents()
        finally:
            release.set()
            session._pool.waitForDone(3000)
            session.shutdown()


if __name__ == "__main__":
    unittest.main()
