"""Recorded production session + optional persistent C# grid, with async cancel.

This development entry never captures a window or sends input. Recorded pixels
retain their evidence kind through the original final Python/Core consumers.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import nullcontext, redirect_stdout
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import cv2
import PIL
from PIL import Image

from grid_backend import GridBackend
from output_pipe import OutputPipe
from runtime_session import RecordedRuntimeSession
from fast_structure_alignment import using_grid_backend, _grid as python_grid


def native(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


class Request:
    def __init__(self, value, output):
        self.session, self.id = value["session"], value["id"]
        self.value = value
        self.deadline = time.monotonic() + value.get("timeoutMs", 10000) / 1000
        self.stop = threading.Event()
        self.output = output
        self.reason = None

    def cancel(self, reason):
        self.reason = self.reason or reason
        self.stop.set()

    def cancelled(self):
        if time.monotonic() >= self.deadline:
            self.cancel("timeout")
        if self.output.closed.is_set():
            self.cancel("output-closed")
        return self.stop.is_set()

    def check(self):
        if self.cancelled():
            raise InterruptedError(self.reason)


def render_result(runtime):
    render = runtime.render()
    if render is None:
        return None
    metadata = {key: value for key, value in render.items() if key != "pixels"}
    pixels = np.asarray(render["pixels"])
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError("invalid-runtime-render-pixels")
    output = BytesIO()
    Image.fromarray(pixels).save(output, format="PNG")
    metadata.update(width=int(pixels.shape[1]), height=int(pixels.shape[0]),
                    pngBase64=base64.b64encode(output.getvalue()).decode("ascii"))
    return metadata


def run(args):
    state_lock = threading.Lock()
    active = None
    computation = None
    with redirect_stdout(sys.stderr):
        runtime = RecordedRuntimeSession(args.resource_root, args.data_root, args.frame_root)
    backend = None
    try:
        backend = GridBackend(args.grid_executable, args.data_root / "grid-cache",
            event_sink=lambda value: send(dict(value, event="grid-" + value["event"]))) if args.grid_backend == "csharp" else None
        # All failure-prone construction precedes the non-daemon output pump.
        output = OutputPipe(sys.stdout.fileno())
    except Exception:
        try:
            runtime.close()
        finally:
            if backend is not None:
                backend.close()
        raise

    def send(value):
        output.send(json.dumps(value, default=native, ensure_ascii=False, allow_nan=False,
                               separators=(",", ":")).encode("utf-8") + b"\n")

    def compute(context):
        nonlocal active
        started = time.monotonic()
        request = context.value
        python_calls = []

        def observed_python_grid(points, prepared, scale, prior, cancelled):
            began = time.monotonic()
            send(dict(event="grid-started", session=context.session, id=context.id, processId=os.getpid()))
            rows = python_grid(points, prepared, scale, prior, cancelled)
            python_calls.append(dict(candidateCount=len(rows), kernelMs=(time.monotonic()-began)*1000))
            return rows

        try:
            send(dict(event="started", session=context.session, id=context.id))
            scope = backend.request(context.session, context.id, max(.001, context.deadline-time.monotonic()), context.cancelled) if backend else nullcontext()
            grid_scope = using_grid_backend(backend if backend else observed_python_grid)
            with scope, grid_scope, redirect_stdout(sys.stderr):
                context.check()
                operation = request["operation"]
                if operation == "prepare":
                    result = dict(capability=runtime.prepare(request["season"], request["mode"]), snapshot=runtime.snapshot())
                elif operation == "set_frame":
                    result = dict(frame=runtime.set_frame(request["path"], capture_context=request.get("captureContext")), snapshot=runtime.snapshot())
                elif operation == "dispatch":
                    result = runtime.dispatch(request["action"], request.get("payload", {}), context.cancelled)
                elif operation == "snapshot":
                    result = dict(snapshot=runtime.snapshot())
                else:
                    raise ValueError("unknown-runtime-operation")
                context.check()
            result["render"] = render_result(runtime)
            context.check()
            terminal = dict(event="result", session=context.session, id=context.id, ok=True,
                result=result, gridBackend=args.grid_backend, gridCalls=list(backend.calls) if backend else python_calls,
                gridProcessId=backend.process_id if backend else None,
                computeMs=(time.monotonic()-started)*1000)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            runtime.invalidate(context.reason or "runtime-request-failed")
            terminal = dict(event="result", session=context.session, id=context.id, ok=False,
                error=dict(code=context.reason or type(error).__name__, message=str(error)),
                snapshot=runtime.snapshot(), render=None, gridBackend=args.grid_backend,
                gridCalls=list(backend.calls) if backend else python_calls)
        with state_lock:
            try:
                context.check()
            except InterruptedError:
                runtime.invalidate(context.reason or "cancelled")
                terminal = dict(event="result", session=context.session, id=context.id, ok=False,
                    error=dict(code=context.reason, message=context.reason), snapshot=runtime.snapshot(), render=None)
            try:
                send(terminal)
            except BrokenPipeError:
                pass
            finally:
                if active is context:
                    active = None

    try:
        send(dict(ready=True, evidenceKind="recorded", gridBackend=args.grid_backend, pid=os.getpid(),
            dependencies=dict(python=sys.version.split()[0], opencv=cv2.__version__, numpy=np.__version__, pillow=PIL.__version__),
            sourceRuntimeModule=sys.modules[RecordedRuntimeSession.__module__].__file__))
        while True:
            line = sys.stdin.buffer.readline(65537)
            if not line:
                break
            request = None
            try:
                if len(line) > 65536:
                    while line and not line.endswith(b"\n"):
                        line = sys.stdin.buffer.readline(65537)
                    raise ValueError("runtime-request-too-large")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("runtime-request-object-required")
                if request.get("operation") == "shutdown":
                    break
                session, rid = request.get("session"), request.get("id")
                if not isinstance(session, str) or not session or type(rid) is not int:
                    raise ValueError("runtime-session-and-integer-id-required")
                timeout = request.get("timeoutMs", 10000)
                if type(timeout) is not int or not 1 <= timeout <= 60000:
                    raise ValueError("runtime-timeout-invalid")
                with state_lock:
                    if request.get("operation") == "cancel":
                        matched = active is not None and (active.session, active.id) == (session, rid)
                        if matched:
                            active.cancel("cancelled")
                            runtime.invalidate("cancelled")
                        send(dict(event="cancel-ack", session=session, id=rid, matched=matched))
                    elif active is not None:
                        active.cancel("superseded")
                        runtime.invalidate("superseded")
                        send(dict(event="result", session=session, id=rid, ok=False,
                            error=dict(code="busy-retry", message="Wait for the current cancelled request before retrying."), render=None))
                    else:
                        active = Request(request, output)
                        computation = threading.Thread(target=compute, args=(active,), name="hybrid-runtime-computation")
                        computation.start()
            except Exception as error:
                send(dict(event="protocol-error", id=request.get("id") if isinstance(request, dict) else None,
                    ok=False, error=dict(code=type(error).__name__, message=str(error))))
    finally:
        with state_lock:
            if active is not None:
                active.cancel("closed")
                runtime.invalidate("closed")
        if computation is not None:
            computation.join(timeout=10)
        try:
            if computation is not None and computation.is_alive():
                raise RuntimeError("runtime-computation-shutdown-incomplete")
            try:
                runtime.close()
            finally:
                if backend is not None:
                    backend.close()
        finally:
            output.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--frame-root", type=Path, required=True)
    parser.add_argument("--grid-backend", choices=("python", "csharp"), default="csharp")
    parser.add_argument("--grid-executable", type=Path)
    args = parser.parse_args()
    for name in ("resource_root", "data_root", "frame_root"):
        path = getattr(args, name)
        if not path.is_absolute():
            parser.error("explicit absolute roots are required")
        setattr(args, name, path.resolve())
    if not args.resource_root.is_dir() or not args.frame_root.is_dir():
        parser.error("resource and frame roots must exist")
    if args.data_root.is_relative_to(args.resource_root) or args.data_root.is_relative_to(args.frame_root):
        parser.error("data root must be outside resource/frame roots")
    args.data_root.mkdir(parents=True, exist_ok=True)
    if args.grid_executable is None:
        args.grid_executable = Path(__file__).resolve().parents[1] / "StarMap.Grid/bin/Release/net10.0/StarMap.Grid.exe"
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
