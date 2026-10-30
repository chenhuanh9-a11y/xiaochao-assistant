"""Standalone Qt lab. Run from source; no production entrypoint/config changes."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import platform
import time

from PySide6 import __version__ as qt_version
from PySide6.QtCore import QObject, QPoint, QTimer, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
import psutil
from backend import Backend, ROOT

HERE = Path(__file__).resolve().parent


def windows_overlay_style(window, click_through=False):
    """Same native flags as production _set_clickthrough; operates on our HWND only."""
    if os.name != "nt":
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = wintypes.LONG
    user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user.SetWindowLongW.restype = wintypes.LONG
    handle = int(window.winId())
    style = user.GetWindowLongW(handle, -20) | 0x08000080  # NOACTIVATE | TOOLWINDOW
    style = style | 0x20 if click_through else style & ~0x20
    ctypes.set_last_error(0)
    previous = user.SetWindowLongW(handle, -20, style)
    if not previous and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())


def distribution(values):
    values = sorted(values)
    return {"n": len(values), **{key: round(values[min(len(values)-1, int((len(values)-1)*p))], 3)
            if values else None for key, p in (("p50", .5), ("p95", .95), ("max", 1))}}


class Metrics(QObject):
    def __init__(self, window, overlay):
        super().__init__()
        self.frames = []
        self.overlay_frames = []
        self.delays = []
        self.rss = []
        self.active = False
        self.last = time.perf_counter()
        self.last_frame = None
        self.last_overlay = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        window.frameSwapped.connect(self.frame, Qt.QueuedConnection)
        overlay.frameSwapped.connect(self.overlay_frame, Qt.QueuedConnection)

    def tick(self):
        now = time.perf_counter()
        if self.active:
            self.delays.append(max(0, (now - self.last)*1000 - 8))
            if len(self.delays) % 30 == 0:
                self.rss.append(psutil.Process().memory_info().rss / 1024**2)
        self.last = now

    def frame(self):
        now = time.perf_counter()
        if self.active and self.last_frame:
            self.frames.append((now-self.last_frame)*1000)
        self.last_frame = now

    def overlay_frame(self):
        now = time.perf_counter()
        if self.active and self.last_overlay:
            self.overlay_frames.append((now-self.last_overlay)*1000)
        self.last_overlay = now


def run_probe(app, engine, backend, main, overlay, output, metrics):
    """One bounded ~14s sequence; QtTest targets our windows, never global SendInput."""
    from PySide6.QtTest import QTest
    output.mkdir(parents=True, exist_ok=True)
    state = {"started": time.perf_counter(), "step": 0, "assertions": {}, "errors": []}
    grid = main.findChild(QObject, "resourceGrid")
    search = main.findChild(QObject, "searchField")
    def check(name, condition):
        state["assertions"][name] = bool(condition)
    def advance():
        step = state["step"]
        try:
            if step == 0:
                if backend.busy and time.perf_counter()-state["started"] < 8:
                    return
                check("real_rows_loaded", backend.count > 0)
                check("no_tk_import", "tkinter" not in __import__("sys").modules)
                row = next((r for r in backend.model.rows if r["available"]), None)
                if row:
                    backend.select(row["key"])
                state["selected"] = backend.selectedKey
                state["mutations"] = backend.model.mutations
                state["created"] = main.property("createdDelegates")
                state["destroyed"] = main.property("destroyedDelegates")
                grid.setProperty("contentY", 220.0)
                state["scroll"] = grid.property("contentY")
                backend.refresh()
            elif step == 1:
                if backend.busy:
                    return
                check("refresh_zero_model_mutations", backend.model.mutations == state["mutations"])
                check("refresh_keeps_selection", backend.selectedKey == state["selected"])
                check("refresh_keeps_scroll", abs(grid.property("contentY")-state["scroll"]) < 1)
                backend.setFloor("B1")
                check("filter_clears_unselected_preview", backend.selectedKey == "" and backend.selectedImage == "")
                backend.setFloor("")
                check("return_filter_restores_selection", backend.selectedKey == state["selected"])
                state["created_after_refresh"] = main.property("createdDelegates")
                state["destroyed_after_refresh"] = main.property("destroyedDelegates")
                main.setProperty("probeAnimating", True)
                metrics.active = True
                metrics.last_frame = None
                overlay.show()
                windows_overlay_style(overlay)
                state["animation_start"] = time.perf_counter()
            elif step < 10:
                # Continuous render + scrolling while thumbnails enter the viewport.
                extent = max(0, grid.property("contentHeight") - grid.property("height"))
                grid.setProperty("contentY", min(extent, (step-2)*170.0))
                overlay.setProperty("expanded", step % 2 == 0)
            elif step == 10:
                check("thumbnails_ready", main.property("readyImages") > 0)
                check("no_thumbnail_errors", main.property("imageErrors") == 0)
                overlay.setProperty("expanded", True)
                overlay.setProperty("controlAlpha", .80)
                overlay.setProperty("mapAlpha", .55)
                before = QPoint(overlay.x(), overlay.y())
                # Local Qt events exercise the actual MouseArea without moving the OS pointer.
                QTest.mousePress(overlay, Qt.LeftButton, Qt.NoModifier, QPoint(40, 28))
                QTest.mouseMove(overlay, QPoint(75, 48), 20)
                QTest.mouseRelease(overlay, Qt.LeftButton, Qt.NoModifier, QPoint(75, 48))
                check("overlay_drag_moves", QPoint(overlay.x(), overlay.y()) != before)
                check("independent_opacity", overlay.property("controlAlpha") != overlay.property("mapAlpha"))
                backend.setClickThrough(True)
                backend.showOverlay()
                if os.name == "nt":
                    user = ctypes.WinDLL("user32")
                    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
                    style = user.GetWindowLongW(int(overlay.winId()), -20)
                    check("native_overlay_flags", style & 0x08000080 == 0x08000080 and bool(style & 0x20))
                    check("reopen_preserves_clickthrough", backend.clickThrough and bool(style & 0x20))
                backend.setClickThrough(False)
                # Snapshot after animation settles, on next step.
            elif step == 11:
                main.grabWindow().save(str(output / "desktop-1180.png"))
                overlay.grabWindow().save(str(output / "overlay-expanded.png"))
                # Exercise actual TextField focus and input dispatch.
                point = search.mapToScene(__import__("PySide6.QtCore", fromlist=["QPointF"]).QPointF(20, 16)).toPoint()
                QTest.mouseClick(main, Qt.LeftButton, Qt.NoModifier, point)
                # QWindow overload accepts keyClick, no widgets dependency required.
                QTest.keyClick(main, Qt.Key_A)
                QTest.keyClick(main, Qt.Key_B)
                QTest.keyClick(main, Qt.Key_C)
            elif step == 12:
                check("continuous_text_input", search.property("text").endswith("abc"))
                search.setProperty("text", "")
                backend.search("")
                # Force superseded worker result, then validate current mode.
                backend.setMode("difficult")
                backend.setMode("nightmare")
                backend.setMode("difficult")
                backend.setMode("nightmare")
            elif step == 13:
                if backend.busy:
                    return
                check("late_worker_cannot_change_mode", backend.context is not None and backend.context.mode == backend.mode == "nightmare")
                check("current_rows_match_mode", all(r["key"].startswith("nightmare/") for r in backend.model.rows))
                main.setWidth(940); main.setHeight(660)
            elif step == 14:
                main.grabWindow().save(str(output / "desktop-940.png"))
                metrics.active = False
                main.setProperty("probeAnimating", False)
                state["elapsed_seconds"] = round(time.perf_counter()-state["started"], 3)
                state["ui_timer_delay_ms"] = distribution(metrics.delays)
                state["main_frame_signal_interval_ms"] = distribution(metrics.frames)
                state["overlay_frame_signal_interval_ms"] = distribution(metrics.overlay_frames)
                state["rss_mib"] = distribution(metrics.rss)
                state["qt"] = qt_version
                state["windows"] = platform.platform()
                state["graphics_api"] = str(main.rendererInterface().graphicsApi())
                state["stale_results_discarded"] = backend.stale_results
                state["image_ready_events"] = main.property("readyImages")
                state["image_errors"] = main.property("imageErrors")
                state["all_assertions_pass"] = all(state["assertions"].values()) and not state["errors"]
                state["measurement_limits"] = ["QtTest events within own windows; no game, global hook, or OS pointer injection", "frameSwapped queued-signal arrival intervals, not GPU presentation latency or end-to-end game overlay latency", "frame sample includes idle overlay gaps and collapse/expand transitions; do not interpret overlay intervals as steady FPS", "memory is full Qt lab process RSS, not incremental cost or packaged product size"]
                (output / "probe.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(state, ensure_ascii=True))
                timer.stop()
                app.exit(0 if state["all_assertions_pass"] else 2)
                return
        except Exception as error:
            state["errors"].append(f"step {step}: {type(error).__name__}: {error}")
        state["step"] += 1
    timer = QTimer(app)
    timer.setInterval(600)
    timer.timeout.connect(advance)
    timer.start()
    # Keep it alive independent of callback closure.
    app._probe_timer = timer
    QTimer.singleShot(22000, lambda: app.exit(3))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", type=Path, default=ROOT / "work/qt-lab-installation")
    parser.add_argument("--probe-output", type=Path)
    parser.add_argument("--replay", type=Path, help="isolated saved render record beneath this clone/work")
    args = parser.parse_args()
    app = QGuiApplication([])
    app.setApplicationName("XingtuQtLab")
    app.setOrganizationName("XingtuQtLab")
    engine = QQmlApplicationEngine()
    backend = Backend(args.sandbox)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("resourceModel", backend.model)
    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(str(w) for w in warnings))
    engine.load(QUrl.fromLocalFile(str(HERE / "Main.qml")))
    engine.load(QUrl.fromLocalFile(str(HERE / "Overlay.qml")))
    if len(engine.rootObjects()) != 2:
        backend.shutdown()
        return 1
    window, overlay = engine.rootObjects()
    def show_overlay(replay=False):
        overlay.setProperty("replayMode", bool(replay and backend.replayAvailable))
        windows_overlay_style(overlay, backend.clickThrough)
        overlay.show()
    backend.overlayRequested.connect(lambda: show_overlay(False))
    backend.replayOverlayRequested.connect(lambda: show_overlay(True))
    backend.clickThroughRequested.connect(lambda enabled: windows_overlay_style(overlay, enabled))
    metrics = Metrics(window, overlay)
    if args.replay:
        window.setProperty("page", "replay")
        backend.loadReplay(QUrl.fromLocalFile(str(args.replay.resolve())).toString())
    if args.probe_output:
        if args.replay:
            from probe_replay import run_replay_probe
            run_replay_probe(app, backend, window, overlay, args.probe_output, metrics)
        else:
            run_probe(app, engine, backend, window, overlay, args.probe_output, metrics)
    app.aboutToQuit.connect(backend.shutdown)
    code = app.exec()
    if args.probe_output:
        (args.probe_output / "qml-warnings.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    return code

if __name__ == "__main__":
    raise SystemExit(main())
