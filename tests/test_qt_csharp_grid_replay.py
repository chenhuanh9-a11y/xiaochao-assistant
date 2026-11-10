"""Opt-in comparison through the original Qt worker using private saved frames.

Only pixel acquisition/window facts are supplied. Identity, floor observation,
rank, fit, and render consumers use the production implementation and resources.
No native input is sent and recorded evidence never becomes live authorization.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / part) for part in ("app", "experiments/qt_quick")]
import cv2
import numpy as np

SAMPLES = ("batch02-event-000097", "batch01-event-000003",
           "batch02-event-000255", "batch02-event-000313")
FIELDS = ("season", "mode", "mapId", "floor", "preferredProfileId", "effectiveProfileId",
          "displayFallback", "fallbackReason", "roundActive", "mapOpen", "liveAuthorization",
          "evidenceKind", "durableMapId", "displayReferenceMapId", "identityState", "identitySource",
          "candidateRows", "automaticCandidateRows", "selectedCandidateIndex", "poseAttempt", "poseResult",
          "floorSource", "observedFloor", "floorRequired", "floorPolicyReason", "displayAuthorized", "render")
# These are request identities/timings, not recognition or geometry results.
VARIABLES = {"token", "frameId", "roundId", "cycleId", "epoch", "generation", "requestId",
             "attemptId", "renderToken", "authorizationId", "createdAt", "startedAt", "completedAt",
             "deadlineAt", "elapsedSeconds", "elapsedMs", "identityElapsedMs", "engineIdentity"}


def semantics(value, resource_root):
    if isinstance(value, dict):
        result = {key: semantics(item, resource_root) for key, item in value.items() if key not in VARIABLES}
        # The same inventoried resource is copied into independent learning roots.
        if isinstance(result.get("path"), str) and Path(result["path"]).is_relative_to(resource_root):
            result["path"] = "<resources>/" + Path(result["path"]).relative_to(resource_root).as_posix()
        return result
    if isinstance(value, (list, tuple)):
        return [semantics(item, resource_root) for item in value]
    return value


class RecordedCapture:
    evidence_kind = "recorded"

    def __init__(self, sample):
        from client_region_capture import ClientCaptureContext
        self.frame = cv2.imdecode(np.fromfile(sample["image"], dtype=np.uint8), cv2.IMREAD_COLOR)
        facts = sample["physicalClient"]
        width, height = facts["Width"], facts["Height"]
        assert self.frame is not None and self.frame.shape[:2] == (height, width)
        self.frame.setflags(write=False)
        self.context = ClientCaptureContext((width, height),
            (facts["Left"], facts["Top"], facts["Left"] + width, facts["Top"] + height), facts["Dpi"])
        self.enabled = False
        self.generation = 1

    @property
    def snapshot(self):
        return dict(enabled=self.enabled, generation=self.generation, evidenceKind="recorded")

    def enable(self):
        self.enabled = True
        return True

    def disable(self):
        self.enabled = False

    def shutdown(self):
        self.disable()
        self.generation += 1

    def is_current(self, context, *, generation=None):
        return context == self.context and (generation is None or generation == self.generation)

    def capture(self, cancelled=None):
        from client_region_capture import RegionCapture
        if not self.enabled or (cancelled is not None and cancelled()):
            raise InterruptedError("recorded-capture-unavailable")
        width, height = self.context.full_client_size
        bounds = (0, 0, width, height)
        return RegionCapture(self.frame, bounds, bounds, self.context, "recorded-png")


def run_case(config):
    from PySide6.QtGui import QGuiApplication
    from client_live_runtime import LiveRuntime
    from client_providers import EngineFactories
    from client_session import SessionService
    from client_startup_cleanup import shutdown_client
    from csharp_grid_backend import GridBackend
    from csharp_grid_runtime import CSharpGridRuntime
    import fast_structure_alignment as fast

    output = Path(config["output"])
    sample = config["sample"]
    assert datetime.fromisoformat(sample["capturedAt"]).date().isoformat() >= "2026-11-05"
    resources = output / "resources"
    inventory = json.loads(Path(config["inventory"]).read_bytes())
    for name, row in inventory["files"].items():
        source = Path(config["resources"]) / name
        target = resources / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        assert target.stat().st_size == row["bytes"], name
        with target.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"], name
    app = QGuiApplication([])
    factories = EngineFactories(resources)
    started = time.perf_counter()
    capabilities = factories.prepare_context(sample["mode"], season=sample["season"])
    prepare_ms = (time.perf_counter() - started) * 1000
    assert capabilities[sample["mode"]]["available"], capabilities
    owner = (CSharpGridRuntime(ROOT / "hybrid/StarMap.Grid/bin/Release/net10.0/StarMap.Grid.exe",
                              output / "grid-cache") if config["backend"] == "csharp" else None)
    runtime = LiveRuntime(engine_factories=factories, capture_provider=RecordedCapture(sample),
                          allowed_read_root=output, evidence_kind="recorded", grid_runtime=owner)
    session = SessionService(runtime)
    python_calls = []
    native_durations = []
    original_grid = fast._grid
    original_native_grid = GridBackend.__call__

    def observed_grid(*args, **kwargs):
        assert owner is None, "C# route unexpectedly called the Python grid"
        began = time.perf_counter()
        result = original_grid(*args, **kwargs)
        python_calls.append(dict(roundTripMs=(time.perf_counter() - began) * 1000, rows=len(result)))
        return result

    def observed_native_grid(backend, *args, **kwargs):
        began = time.perf_counter()
        result = original_native_grid(backend, *args, **kwargs)
        native_durations.append((time.perf_counter() - began) * 1000)
        return result

    def wait():
        deadline = time.monotonic() + 90
        while session.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.001)
        assert not session.busy, "Qt worker did not finish"
        assert session._pool.waitForDone(1000)
        app.processEvents()

    result = dict(backend=config["backend"], sample=sample["id"], prepareMs=prepare_ms, captures=[])
    try:
        with (patch.object(fast, "_grid", observed_grid),
              patch.object(GridBackend, "__call__", observed_native_grid)):
            assert session.reconfigure(sample["mode"], "shared-structure", sample["season"])
            wait()
            session.dispatch("start_round")
            wait()
            assert session.snapshot["lastReceipt"].get("success"), session.snapshot
            for repeat in range(3):
                python_calls.clear()
                native_durations.clear()
                began = time.perf_counter()
                session.dispatch("capture_now")
                wait()
                elapsed_ms = (time.perf_counter() - began) * 1000
                state = runtime.snapshot()
                assert not state["liveAuthorization"], "Recorded input acquired live authority"
                render = runtime.render_payload((state.get("render") or {}).get("token"))
                if render is not None:
                    np.save(output / f"pixels-{repeat}.npy", render["pixels"])
                grid = ([dict(call, wholeGridMs=duration) for call, duration in zip(owner.calls, native_durations)]
                        if owner is not None else [dict(call, wholeGridMs=call["roundTripMs"]) for call in python_calls])
                if owner is not None:
                    assert len(owner.calls) == len(native_durations), "Missing native timing endpoint"
                result["captures"].append(dict(sessionResultMs=elapsed_ms,
                    grid=grid,
                    processId=owner.process_id if owner is not None else None,
                    state=semantics({key: state.get(key) for key in FIELDS}, resources),
                    receipt=semantics(session.snapshot["lastReceipt"], resources),
                    message=session.snapshot["message"], rendered=render is not None))
            session.dispatch("end_round")
            wait()
            assert not runtime.snapshot()["roundActive"]
            if owner is not None:
                pids = {capture["processId"] for capture in result["captures"] if capture["grid"]}
                assert len(pids) <= 1 and None not in pids, "Grid process was not reused"
    finally:
        process = owner._client._process if owner is not None and owner._client is not None else None
        shutdown_client(SimpleNamespace(session=session, runtime=runtime, grid_runtime=owner))
        runtime.shutdown()
        factories.invalidate()
        if process is not None:
            assert process.poll() == 0, "Grid did not exit normally"
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


@unittest.skipUnless(os.environ.get("STARMAP_QT_REPLAY_CONFIG"), "Private saved-frame replay is opt-in")
class QtSavedFrameGridReplay(unittest.TestCase):
    def test_original_qt_worker_routes_match_on_current_layout_frames(self):
        config = json.loads(Path(os.environ["STARMAP_QT_REPLAY_CONFIG"]).read_bytes())
        output = Path(config["output"])
        output.mkdir(parents=True, exist_ok=False)
        samples = {row["id"]: row for row in json.loads(Path(config["manifest"]).read_bytes())["samples"]}
        native_searches = rendered_pairs = 0
        for index, sample_id in enumerate(SAMPLES):
            routes = {}
            # Alternate route order; each process and resource/learning directory starts fresh.
            for backend in (("python", "csharp") if index % 2 == 0 else ("csharp", "python")):
                case = dict(config, sample=samples[sample_id], backend=backend,
                            output=str(output / (sample_id + "-" + backend)))
                Path(case["output"]).mkdir()
                path = Path(case["output"]) / "case.json"
                path.write_text(json.dumps(case), encoding="utf-8")
                with (path.parent / "worker.log").open("w", encoding="utf-8") as log:
                    subprocess.run([sys.executable, "-B", __file__, "--case", str(path)],
                                   stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240)
                routes[backend] = json.loads((path.parent / "result.json").read_bytes())
            for repeat, (python, csharp) in enumerate(zip(routes["python"]["captures"], routes["csharp"]["captures"])):
                with self.subTest(sample=sample_id, repeat=repeat):
                    self.assertEqual(python["state"], csharp["state"])
                    self.assertEqual(python["receipt"], csharp["receipt"])
                    self.assertEqual(python["rendered"], csharp["rendered"])
                    self.assertEqual(len(python["grid"]), len(csharp["grid"]))
                    native_searches += len(csharp["grid"])
                    if python["rendered"]:
                        rendered_pairs += 1
                        np.testing.assert_array_equal(
                            np.load(output / (sample_id + "-python") / f"pixels-{repeat}.npy"),
                            np.load(output / (sample_id + "-csharp") / f"pixels-{repeat}.npy"))
        self.assertGreater(native_searches, 0, "Selected frames no longer exercise the native grid")
        self.assertGreater(rendered_pairs, 0, "Selected frames no longer exercise the render consumer")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--case":
        run_case(json.loads(Path(sys.argv[2]).read_bytes()))
    else:
        unittest.main()
