"""Compare the two current runtime-worker routes on explicitly selected new frames.

Input metadata supplies season/mode/capture coordinates, never map or pose truth.
Private frames, full responses and rendered pixels stay under the local output.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from contextlib import nullcontext
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))


SNAPSHOT_FIELDS = ("season", "mode", "mapId", "floor", "preferredProfileId", "effectiveProfileId",
    "displayFallback", "fallbackReason", "roundActive", "roundPreparing", "mapOpen", "liveAuthorization",
    "evidenceKind", "durableMapId", "displayReferenceMapId", "identityState", "identitySource",
    "candidateRows", "automaticCandidateRows", "selectedCandidateIndex", "poseAttempt", "poseResult",
    "floorSource", "observedFloor", "floorRequired", "floorPolicyReason")
VARIABLE_FIELDS = {"token", "frameId", "roundId", "cycleId", "epoch", "generation", "requestId",
                   "attemptId", "renderToken", "createdAt", "startedAt", "completedAt", "deadlineAt", "elapsedSeconds"}


def semantics(value):
    if isinstance(value, dict):
        return {key: semantics(item) for key, item in value.items()
                if key not in VARIABLE_FIELDS and not key.endswith("Ms")}
    if isinstance(value, list):
        return [semantics(item) for item in value]
    return value


class Worker:
    def __init__(self, args, backend, data_root):
        command = [sys.executable, "-B", "-u", str(Path(__file__).with_name("runtime_worker.py")),
            "--resource-root", str(args.resource_root), "--frame-root", str(args.frame_root),
            "--data-root", str(data_root), "--grid-backend", backend]
        if args.grid_executable:
            command += ["--grid-executable", str(args.grid_executable)]
        self.stderr = (data_root.parent / (backend + "-stderr.log")).open("w", encoding="utf-8")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.messages = queue.Queue(maxsize=32)
        self.request_id = 0
        self.thread = threading.Thread(target=self._read)
        self.thread.start()
        try:
            self.ready = self.messages.get(timeout=15)
            if self.ready.get("ready") is not True:
                raise RuntimeError("runtime worker did not start: " + repr(self.ready))
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        finally:
            self.messages.put(dict(event="transport-closed"))

    def call(self, operation, **values):
        return self.wait_result(self.start(operation, **values))

    def start(self, operation, **values):
        self.request_id += 1
        rid = self.request_id
        request = dict(session="current-replay", id=rid, operation=operation, timeoutMs=15000)
        request.update(values)
        self.send(request)
        return rid

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def wait(self, predicate):
        while True:
            value = self.messages.get(timeout=20)
            if value.get("event") == "transport-closed":
                raise RuntimeError("runtime worker closed before result")
            if predicate(value):
                return value

    def wait_result(self, rid, *, allow_error=False):
        value = self.wait(lambda value: value.get("event") == "result" and value.get("id") == rid)
        if not value.get("ok") and not allow_error:
            raise RuntimeError(value.get("error"))
        return value

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # This verifier owns the root process. A forced cleanup is a
            # failed check and is never recorded as a normal runtime exit.
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
            self.process.wait(timeout=5)
            raise
        self.thread.join(timeout=2)
        self.process.stdout.close()
        self.stderr.close()
        if self.process.returncode != 0:
            raise RuntimeError(f"runtime worker exited {self.process.returncode}")


def check_controls(args, row):
    results = []
    for backend in ("python", "csharp"):
        directory = args.output / ("controls-" + backend)
        directory.mkdir()
        worker = Worker(args, backend, directory / "data")
        closed = False
        try:
            worker.call("prepare", season=row["season"], mode=row["mode"])
            worker.call("set_frame", path=row["image"], captureContext=row["physicalClient"])
            worker.call("dispatch", action="start_round")
            rid = worker.start("dispatch", action="capture_now")
            event_name = "grid-progress" if backend == "csharp" else "grid-started"
            worker.wait(lambda value: value.get("event") == event_name and value.get("id") == rid)
            worker.send(dict(operation="cancel", session="foreign", id=rid))
            foreign = worker.wait(lambda value: value.get("event") == "cancel-ack" and value.get("session") == "foreign")
            if foreign["matched"]:
                raise AssertionError("Foreign cancellation acquired current request authority.")
            worker.send(dict(operation="cancel", session="current-replay", id=rid))
            matched = worker.wait(lambda value: value.get("event") == "cancel-ack" and value.get("session") == "current-replay")
            cancelled = worker.wait_result(rid, allow_error=True)
            if not matched["matched"] or cancelled["ok"] or cancelled.get("render") is not None:
                raise AssertionError("Active grid cancellation did not revoke the current result.")
            worker.call("set_frame", path=row["image"], captureContext=row["physicalClient"])
            expired = worker.wait_result(worker.start("dispatch", action="capture_now", timeoutMs=1), allow_error=True)
            if expired["ok"] or expired["error"]["code"] != "timeout":
                raise AssertionError("Runtime deadline was not enforced.")
            worker.call("set_frame", path=row["image"], captureContext=row["physicalClient"])
            recovered = worker.call("dispatch", action="capture_now")
            if not recovered["gridCalls"]:
                raise AssertionError("Recovery did not execute the actual grid path.")
            save(directory / "process-tree.json", process_tree_snapshot(worker.process.pid))
            worker.call("set_frame", path=row["image"], captureContext=row["physicalClient"])
            rid = worker.start("dispatch", action="capture_now")
            running = worker.wait(lambda value: value.get("event") == event_name and value.get("id") == rid)
            handle = None
            kernel = None
            if backend == "csharp":
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                handle = kernel.OpenProcess(0x100000 | 0x1000, False, running["processId"])
                if not handle:
                    raise OSError("Cannot retain the owned grid process for EOF verification.")
            try:
                worker.close()
                closed = True
                if handle and kernel.WaitForSingleObject(handle, 0) != 0:
                    raise AssertionError("Owned C# grid process survived runtime EOF.")
            finally:
                if handle:
                    kernel.CloseHandle(handle)
            results.append(dict(backend=backend, case=row["id"], foreignCancelRejected=True,
                activeGridCancelled=True, timeoutRejected=True, actualGridRecovered=True, eofExited=True))
        finally:
            if not closed:
                worker.close()
    save(args.output / "controls.json", results)
    return results


def process_tree_snapshot(root_pid):
    # Measurement helper only; includes every descendant returned by the OS,
    # rather than assuming Python and grid are the only possible processes.
    script = r"""
$sampleRootPid = ROOT_PID
$sampleProcesses = @(Get-CimInstance Win32_Process)
$sampleIds = [System.Collections.Generic.HashSet[int]]::new()
[void]$sampleIds.Add($sampleRootPid)
do {
  $sampleChanged = $false
  foreach ($sampleRow in $sampleProcesses) {
    if ($sampleIds.Contains([int]$sampleRow.ParentProcessId) -and $sampleIds.Add([int]$sampleRow.ProcessId)) { $sampleChanged = $true }
  }
} while ($sampleChanged)
$sampleResults = foreach ($sampleRow in $sampleProcesses) {
  if ($sampleIds.Contains([int]$sampleRow.ProcessId)) {
    $sampleProcess = Get-Process -Id $sampleRow.ProcessId -ErrorAction Stop
    [pscustomobject]@{pid=$sampleRow.ProcessId; parentPid=$sampleRow.ParentProcessId; name=$sampleRow.Name; workingSetBytes=$sampleProcess.WorkingSet64; privateBytes=$sampleProcess.PrivateMemorySize64; handles=$sampleProcess.HandleCount; cpuMs=$sampleProcess.TotalProcessorTime.TotalMilliseconds}
  }
}
@($sampleResults) | ConvertTo-Json -Compress
""".replace("ROOT_PID", str(int(root_pid)))
    measured = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=15, check=True)
    rows = json.loads(measured.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    return dict(processes=rows, scope="one post-recovery snapshot, not peak or long-duration stability",
        workingSetSumBytes=sum(row["workingSetBytes"] for row in rows),
        privateSumBytes=sum(row["privateBytes"] for row in rows),
        workingSetCaveat="Shared pages can be counted in multiple processes.")


def check_synthetic_fit(args):
    """Known target/structure, no identity claim; generator pose never enters fit."""
    import cv2
    from runtime_session import RecordedRuntimeSession
    from client_live_runtime import _AlignmentHost
    import fast_structure_alignment as fast
    from grid_backend import GridBackend
    from single_map_structure import contour_graph

    output = args.output / "synthetic-host-fit"
    output.mkdir()
    session = RecordedRuntimeSession(args.resource_root, output / "data", args.frame_root)
    backend = GridBackend(args.grid_executable or Path(__file__).resolve().parents[1] / "StarMap.Grid/bin/Release/net10.0/StarMap.Grid.exe", output / "cache")
    results = []
    try:
        session.prepare("s0", "nightmare")
        engine = session._factories.get_engine("nightmare")
        reference = engine.qt_shared_provider("shared-structure", "nightmare-03", "2")
        mask = reference.clean_mask
        nodes = contour_graph(mask)
        for index, scale in enumerate((.4, .5, .6)):
            directory = output / f"case-{index}"
            directory.mkdir()
            tx, ty = round(1280-mask.shape[1]*scale/2), round(760-mask.shape[0]*scale/2)
            generator = np.array([[scale, 0, tx], [0, scale, ty]], np.float64)
            projected = cv2.warpAffine(mask, generator, (2560, 1417), flags=cv2.INTER_NEAREST)
            image = (25 + projected*105).astype(np.uint8)
            blurred = cv2.GaussianBlur(image, (0, 0), 1.2)
            regions = np.zeros_like(image)
            for node in nodes:
                x, y = np.rint(node["point"]*scale + [tx, ty]).astype(int)
                cv2.circle(regions, (int(x), int(y)), 4, 1, -1)
            frame = cv2.cvtColor(np.where(regions > 0, blurred, image).astype(np.uint8), cv2.COLOR_GRAY2BGR)
            Image.fromarray(frame[:, :, ::-1]).save(directory / "synthetic.png")
            outputs = {}
            for route in ("python", "csharp"):
                host = _AlignmentHost(engine, collect_fit_diagnostics=True)
                refined = []
                python_calls = []
                original_refine = fast.refine

                def observe_refine(*values, **options):
                    result = original_refine(*values, **options)
                    refined.append(result)
                    return result

                def observe_grid(*values):
                    result = fast._grid(*values)
                    python_calls.append(result)
                    return result

                scope = backend.request("synthetic-host", index, 15) if route == "csharp" else nullcontext()
                with scope, fast.using_grid_backend(backend if route == "csharp" else observe_grid), patch.object(fast, "refine", observe_refine):
                    fit = host.fit(frame, "nightmare-03", "2", lambda: False, observation_bounds=(0, 0, 2560, 1417))
                plain = json.loads(json.dumps(dict(fit=fit, events=host.events, refined=refined),
                    default=lambda value: value.tolist() if isinstance(value, np.ndarray) else value.item()))
                save(directory / (route + ".json"), plain)
                outputs[route] = semantics(plain)
                outputs[route + "Calls"] = len(backend.calls) if route == "csharp" else len(python_calls)
            result = dict(synthetic=True, scale=scale, semanticsEqual=outputs["python"] == outputs["csharp"],
                gridCalls=outputs["csharpCalls"], pythonGridCalls=outputs["pythonCalls"], refineCalls=len(refined),
                accepted=bool(fit.get("success")), fastAccepted=any(e.get("event") == "experimental-fast-alignment" and e.get("accepted") for e in host.events),
                scope="generated known-target Host fit; not automatic identity, Runtime render or native display acceptance")
            results.append(result)
        if not all(row["semanticsEqual"] and row["accepted"] and row["fastAccepted"] and row["gridCalls"] and row["refineCalls"] for row in results):
            raise AssertionError(results)
    finally:
        session.close()
        backend.close()
    save(output / "summary.json", results)
    return results


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def decode_render(response, path):
    render = response["result"].get("render")
    if render is None:
        return None
    png = base64.b64decode(render["pngBase64"])
    path.write_bytes(png)
    with Image.open(BytesIO(png)) as image:
        return np.asarray(image.convert("RGBA"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--frame-root", type=Path, required=True)
    parser.add_argument("--sample-index", type=Path, required=True)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--grid-executable", type=Path)
    parser.add_argument("--check-controls", action="store_true",
                        help="Check cancellation, deadline, recovery and EOF on the last case; it must naturally invoke grid.")
    parser.add_argument("--check-synthetic-fit", action="store_true",
                        help="Separately compare generated known-target Host fits; does not supply map/pose to automatic recognition.")
    args = parser.parse_args()
    for name in ("resource_root", "frame_root", "sample_index", "output"):
        setattr(args, name, getattr(args, name).resolve())
    index = {row["id"]: row for row in json.loads(args.sample_index.read_text("utf-8"))["samples"]}
    rows = [index[case] for case in args.case]
    for row in rows:
        captured = datetime.fromisoformat(row["capturedAt"]).astimezone(timezone.utc)
        if captured < datetime(2026, 11, 5, tzinfo=timezone.utc):
            raise ValueError("Current-layout verification requires capture provenance from November 5 or later.")
        if not Path(row["image"]).resolve().is_relative_to(args.frame_root):
            raise ValueError("Sample frame is outside the explicit frame root.")
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for row in rows:
        directory = args.output / row["id"]
        directory.mkdir()
        outputs, pixels = {}, {}
        execution = {}
        for backend in ("python", "csharp"):
            worker = Worker(args, backend, directory / (backend + "-data"))
            try:
                prepared = worker.call("prepare", season=row["season"], mode=row["mode"])
                selected = worker.call("set_frame", path=row["image"], captureContext=row["physicalClient"])
                if not selected["result"]["frame"]["success"]:
                    raise RuntimeError(selected)
                started = worker.call("dispatch", action="start_round")
                captured = worker.call("dispatch", action="capture_now")
                save(directory / (backend + ".json"), dict(ready=worker.ready, prepare=prepared,
                    setFrame=selected, start=started, capture=captured))
                state = captured["result"]["snapshot"]
                outputs[backend] = semantics({key: state.get(key) for key in SNAPSHOT_FIELDS})
                outputs[backend]["receipt"] = semantics(captured["result"]["receipt"])
                pixels[backend] = decode_render(captured, directory / (backend + "-render.png"))
                outputs[backend + "GridCalls"] = len(captured["gridCalls"])
                execution[backend] = dict(prepareAvailable=prepared["result"]["capability"].get("available"),
                    startSuccess=started["result"]["receipt"].get("success"),
                    captureSuccess=captured["result"]["receipt"].get("success"),
                    captureReason=captured["result"]["receipt"].get("reason"),
                    dependencies=worker.ready.get("dependencies"))
            finally:
                worker.close()
        same = outputs["python"] == outputs["csharp"]
        left, right = pixels["python"], pixels["csharp"]
        pixel_equal = (left is None and right is None) or (left is not None and right is not None and np.array_equal(left, right))
        result = dict(id=row["id"], season=row["season"], mode=row["mode"], capturedAt=row["capturedAt"],
            semanticsEqual=same, rgbaEqual=bool(pixel_equal), rendered=right is not None,
            pythonGridCalls=outputs["pythonGridCalls"], csharpGridCalls=outputs["csharpGridCalls"],
            liveAuthorization=outputs["csharp"]["liveAuthorization"],
            execution=execution,
            scope="recorded-current-runtime-worker-not-native-capture-or-game-acceptance")
        save(directory / "semantics.json", outputs)
        results.append(result)
        print(json.dumps(result), flush=True)
    save(args.output / "summary.json", dict(results=results))
    if args.check_controls:
        print(json.dumps(dict(controls=check_controls(args, rows[-1]))), flush=True)
    if args.check_synthetic_fit:
        print(json.dumps(dict(syntheticFits=check_synthetic_fit(args))), flush=True)
    return 0 if all(row["semanticsEqual"] and row["rgbaEqual"] and not row["liveAuthorization"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
