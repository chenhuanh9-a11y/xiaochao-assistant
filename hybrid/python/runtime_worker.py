"""Production session with recorded PNGs or an explicit C# native capture lane.

The Python process never acquires desktop pixels or sends input itself. Native
callbacks belong to the C# owner; recorded evidence never gains live authority.
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
from worker import create_browse_worker


BROWSE_OPERATIONS = frozenset(("hello", "catalog", "preview"))
READ_ONLY_OPERATIONS = BROWSE_OPERATIONS | {"input_preferences"}
RUNTIME_OPERATIONS = ("hello", "catalog", "preview", "prepare", "set_frame", "dispatch", "snapshot", "input_preferences")


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


def finalize_runtime_result(runtime, result):
    # Rendering rechecks the native capture and may invalidate the epoch.
    # Phase evidence must survive that same final check, just like pixels.
    result["render"] = render_result(runtime)
    result["snapshot"] = runtime.snapshot()
    if not runtime.input_observation_is_current(result.get("mapInputObservation")):
        result.pop("mapInputObservation", None)


def browse_terminal(browse, request, *, cancelled=None):
    """Adapt the existing gallery response to the runtime worker's async RPC."""
    response, _ = browse.handle(request, cancelled=cancelled)
    terminal = dict(event="result", session=request["session"], id=request["id"],
                    ok=response.get("ok") is True, render=None)
    if terminal["ok"]:
        result = response["result"]
        if request.get("operation") == "hello":
            result = dict(result, operations=list(RUNTIME_OPERATIONS))
        terminal["result"] = result
    else:
        terminal["error"] = response.get("error") or dict(
            code="browse-error", message="gallery request failed")
    return terminal


def run(args):
    state_lock = threading.Lock()
    active = None
    computation = None
    native_capture = None
    with redirect_stdout(sys.stderr):
        if getattr(args, "native_pipe", None):
            from native_capture import NativeCaptureProxy
            from runtime_session import LiveRuntimeSession
            native_capture = NativeCaptureProxy(args.native_pipe)
            runtime = LiveRuntimeSession(args.resource_root, args.data_root, native_capture)
        else:
            runtime = RecordedRuntimeSession(args.resource_root, args.data_root, args.frame_root)
        browse = create_browse_worker(args.resource_root, args.installation_root)
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
            if request.get("operation") == "input_preferences":
                from input_preferences import read_input_preferences
                context.check()
                terminal = dict(event="result", session=context.session, id=context.id, ok=True,
                    result=read_input_preferences(args.data_root))
                context.check()
            elif request.get("operation") in BROWSE_OPERATIONS:
                context.check()
                terminal = browse_terminal(browse, request, cancelled=context.cancelled)
                if native_capture and request.get("operation") == "hello" and terminal.get("ok"):
                    terminal["result"]["operations"] = [operation for operation in RUNTIME_OPERATIONS if operation != "set_frame"]
                context.check()
            else:
                scope = backend.request(context.session, context.id, max(.001, context.deadline-time.monotonic()), context.cancelled) if backend else nullcontext()
                grid_scope = using_grid_backend(backend if backend else observed_python_grid)
                capture_scope = native_capture.request(context.session, context.id, context.cancelled, context.deadline) if native_capture else nullcontext()
                with capture_scope, scope, grid_scope, redirect_stdout(sys.stderr):
                    context.check()
                    operation = request["operation"]
                    if operation == "prepare":
                        result = dict(capability=runtime.prepare(request["season"], request["mode"]), snapshot=runtime.snapshot())
                    elif operation == "set_frame":
                        if native_capture:
                            raise ValueError("recorded-frames-not-accepted-by-native-session")
                        result = dict(frame=runtime.set_frame(request["path"], capture_context=request.get("captureContext")), snapshot=runtime.snapshot())
                    elif operation == "dispatch":
                        result = runtime.dispatch(request["action"], request.get("payload", {}), context.cancelled)
                    elif operation == "snapshot":
                        result = dict(snapshot=runtime.snapshot())
                    else:
                        raise ValueError("unknown-runtime-operation")
                    context.check()
                    finalize_runtime_result(runtime, result)
                    context.check()
                terminal = dict(event="result", session=context.session, id=context.id, ok=True,
                    result=result, gridBackend=args.grid_backend, gridCalls=list(backend.calls) if backend else python_calls,
                    gridProcessId=backend.process_id if backend else None,
                    computeMs=(time.monotonic()-started)*1000)
            terminal["computeMs"] = (time.monotonic()-started)*1000
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            is_browse = request.get("operation") in READ_ONLY_OPERATIONS
            if not is_browse:
                runtime.invalidate(context.reason or "runtime-request-failed")
            terminal = dict(event="result", session=context.session, id=context.id, ok=False,
                error=dict(code=context.reason or type(error).__name__, message=str(error)),
                render=None, computeMs=(time.monotonic()-started)*1000)
            if not is_browse:
                terminal.update(snapshot=runtime.snapshot(), gridBackend=args.grid_backend,
                    gridCalls=list(backend.calls) if backend else python_calls)
        with state_lock:
            try:
                context.check()
            except InterruptedError:
                if request.get("operation") not in READ_ONLY_OPERATIONS:
                    runtime.invalidate(context.reason or "cancelled")
                terminal = dict(event="result", session=context.session, id=context.id, ok=False,
                    error=dict(code=context.reason, message=context.reason), render=None)
                if request.get("operation") not in READ_ONLY_OPERATIONS:
                    terminal["snapshot"] = runtime.snapshot()
            try:
                send(terminal)
            except BrokenPipeError:
                pass
            finally:
                if active is context:
                    active = None

    try:
        send(dict(ready=True, evidenceKind="live-client-capture" if native_capture else "recorded", gridBackend=args.grid_backend, pid=os.getpid(),
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
                            if active.value.get("operation") not in READ_ONLY_OPERATIONS:
                                runtime.invalidate("cancelled")
                        send(dict(event="cancel-ack", session=session, id=rid, matched=matched))
                    elif active is not None:
                        if request.get("operation") in READ_ONLY_OPERATIONS:
                            code, message = "busy-retry", "Wait for the current request before retrying."
                        else:
                            active.cancel("superseded")
                            if active.value.get("operation") not in READ_ONLY_OPERATIONS:
                                runtime.invalidate("superseded")
                            code, message = "busy-retry", "Wait for the current cancelled request before retrying."
                        send(dict(event="result", session=session, id=rid, ok=False,
                            error=dict(code=code, message=message), render=None))
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
                if active.value.get("operation") not in READ_ONLY_OPERATIONS:
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
    parser.add_argument("--installation-root", type=Path,
                        help="installation root owning the resource transaction lock")
    parser.add_argument("--native-pipe", help="explicit C#-owned native capture callback pipe; recorded frames remain separate")
    parser.add_argument("--grid-backend", choices=("python", "csharp"), default="csharp")
    parser.add_argument("--grid-executable", type=Path)
    args = parser.parse_args()
    for name in ("resource_root", "data_root", "frame_root"):
        path = getattr(args, name)
        if not path.is_absolute():
            parser.error("explicit absolute roots are required")
        setattr(args, name, path.resolve())
    if args.installation_root is None:
        args.installation_root = (args.resource_root.parent if args.resource_root.name.casefold() == "_internal"
                                  else args.resource_root)
    elif not args.installation_root.is_absolute():
        parser.error("installation root must be absolute")
    try:
        args.installation_root = args.installation_root.resolve(strict=True)
    except OSError as error:
        parser.error(f"installation root must exist: {type(error).__name__}")
    if not args.installation_root.is_dir():
        parser.error("installation root must be a directory")
    if args.resource_root not in (args.installation_root, args.installation_root / "_internal"):
        parser.error("resource root must be the installation root or its _internal directory")
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
