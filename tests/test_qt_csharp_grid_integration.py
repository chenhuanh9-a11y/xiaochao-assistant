"""Actual Qt Session/LiveRuntime/Host/solver, with synthetic capture and identity.

Only the resource identity, map/floor sensor and captured pixels are fixtures.
The grid, subsequent geometry validation, Qt worker and runtime result remain real.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from pathlib import Path
import json
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in ("app", "tests", "experiments/qt_quick")]
from PySide6.QtGui import QGuiApplication
from client_live_runtime import LiveRuntime
from client_session import SessionService
from client_startup_cleanup import shutdown_client
from client_region_capture import ClientCaptureContext, RegionCapture
from csharp_grid_runtime import CSharpGridRuntime
import fast_structure_alignment as fast
from shared_structure_reference import SharedStructureReference
from test_client_live_runtime import fixture_engine
from test_hybrid_grid_backend import ambiguous_scene
from s0_test_fixtures import synthetic_read

EXE = ROOT / "hybrid/StarMap.Grid/bin/Release/net10.0/StarMap.Grid.exe"


class Capture:
    def __init__(self, frame):
        self.frame, self.enabled = frame, False
    @property
    def snapshot(self):
        return dict(enabled=self.enabled, hwnd="synthetic")
    def enable(self):
        self.enabled = True
        return True
    def disable(self):
        self.enabled = False
    def capture(self, cancelled=None):
        height, width = self.frame.shape[:2]
        bounds = (0, 0, width, height)
        return RegionCapture(self.frame.copy(), bounds, bounds,
            ClientCaptureContext((width, height), bounds, 96), "synthetic")


class QtGridIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def wait(self, session):
        deadline = time.monotonic() + 20
        while session.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertFalse(session.busy, "actual Qt worker failed to finish")
        self.assertTrue(session._pool.waitForDone(1000))
        self.app.processEvents()

    def test_both_modes_use_native_grid_through_actual_qt_worker_and_keep_rejection(self):
        source, frame = ambiguous_scene()
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                owner = CSharpGridRuntime(EXE, temporary)
                engine = fixture_engine(mode)
                reference = SharedStructureReference(source, source,
                    dict(binding=dict(profile="shared-structure", mapId="fixture-map", floor="1"),
                        canvas=dict(shapeHW=list(source.shape), coordinateSpace="fixture-pixels"),
                        structureVersionKey="synthetic-grid-scene"),
                    Path(temporary) / "resources/references/shared/fixture.npz", resource_root=Path(temporary) / "resources")
                prepared = fast.prepare(source)
                prepared_path = fast.prepared_path_for(reference)
                prepared_path.parent.mkdir(parents=True)
                np.savez(prepared_path, sourceMetadata=json.dumps(reference.metadata), distance=prepared.distance,
                    chains=prepared.chains, rays=prepared.rays, pitch=prepared.pitch, shape=prepared.shape)
                engine.qt_shared_provider = lambda *args: reference
                runtime = LiveRuntime(engine_factories={mode: lambda: engine}, capture_provider=Capture(frame),
                    evidence_kind="synthetic", grid_runtime=owner)
                runtime.dispatch("set_context", dict(mode=mode, season="s0", profileId="shared-structure"))
                session = SessionService(runtime)
                try:
                    with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)), \
                            patch("floor_control_region.locate_vertical_floor_controls", side_effect=lambda *a, **k: synthetic_read(mode)):
                        session.dispatch("start_round")
                        self.wait(session)
                        session.dispatch("capture_now")
                        self.wait(session)
                    state = runtime.snapshot()
                    self.assertTrue(owner.calls, ("Qt worker never reached native grid", session.snapshot.get("message"),
                        state.get("message"), state.get("poseAttempt")))
                    self.assertTrue(owner.process_id)
                    self.assertFalse(state["displayAuthorized"])
                    self.assertFalse(state["render"])
                    self.assertFalse(state["liveAuthorization"])
                    process = owner._client._process
                    session.dispatch("capture_now")
                    self.wait(session)
                    self.assertEqual(owner.process_id, process.pid)
                    self.assertFalse(runtime.snapshot()["displayAuthorized"])
                    session.dispatch("end_round")
                    self.wait(session)
                    self.assertFalse(runtime.snapshot()["roundActive"])
                finally:
                    client = SimpleNamespace(session=session, runtime=runtime, grid_runtime=owner)
                    shutdown_client(client)
                    self.assertTrue(client._shutdown_complete)
                self.assertIsNone(owner.process_id)
                self.assertEqual(process.poll(), 0)
