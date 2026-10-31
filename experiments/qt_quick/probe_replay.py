"""Bounded local-window rendering checks, never a live game test."""
from pathlib import Path
import json
import sys
import time
import numpy as np
from PySide6.QtCore import QObject, QPointF, QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest


def run_replay_probe(app, backend, main, overlay, output, metrics):
    output.mkdir(parents=True, exist_ok=True)
    state = dict(step=0, assertions={}, errors=[])
    started = time.perf_counter()
    canvas = main.findChild(QObject, "replayCanvas")
    def check(name, value):
        state["assertions"][name] = bool(value)
    def pixels(image):
        rgba = image.convertToFormat(QImage.Format_RGBA8888)
        return np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.width(), 4).copy()
    def advance():
        step = state["step"]
        try:
            if step == 0:
                if not backend.replayAvailable or not canvas.property("imagesReady"):
                    if time.perf_counter()-started < 9:
                        return
                check("replay_loaded", backend.replayAvailable)
                check("exact_images_decoded", canvas.property("imagesReady"))
                check("no_live_authorization", backend.replayData.get("liveAuthorization") is False)
                check("synthetic_input_disclosed", backend.replayData.get("evidenceKind") == "production-result-on-synthetic-frame")
                main.setProperty("replayAlpha", 0.0)
                metrics.active = True
                metrics.last_frame = None
            elif step == 1:
                state["frame_only"] = pixels(main.grabWindow())
                main.setProperty("replayAlpha", 1.0)
            elif step == 2:
                overlaid = pixels(main.grabWindow())
                changed = int(np.count_nonzero(np.any(overlaid != state.pop("frame_only"), axis=2)))
                state["changed_pixels_at_full_route_alpha"] = changed
                check("recorded_route_draws_pixels", changed > 1000)
                route = canvas.findChild(QObject, "recordedRouteImage")
                viewport = canvas.findChild(QObject, "replayViewport")
                x, y = backend.replayData["routeWidth"]*.37, backend.replayData["routeHeight"]*.41
                actual = route.mapToItem(viewport, QPointF(x, y))
                h = backend.replayData["matrix"]
                denominator = h[2][0]*x+h[2][1]*y+h[2][2]
                expected = ((h[0][0]*x+h[0][1]*y+h[0][2])/denominator,
                            (h[1][0]*x+h[1][1]*y+h[1][2])/denominator)
                error = float(np.hypot(actual.x()-expected[0], actual.y()-expected[1]))
                state["matrix_mapping_error_client_pixels"] = error
                check("recorded_matrix_reaches_scene", error < .001)
                slider = main.findChild(QObject, "replayAlphaSlider")
                begin = slider.mapToScene(QPointF(slider.width()*.2, slider.height()/2)).toPoint()
                end = slider.mapToScene(QPointF(slider.width()*.7, slider.height()/2)).toPoint()
                QTest.mousePress(main, Qt.LeftButton, Qt.NoModifier, begin)
                QTest.mouseMove(main, end, 20)
                QTest.mouseRelease(main, Qt.LeftButton, Qt.NoModifier, end)
            elif step == 3:
                check("replay_slider_accepts_drag", .5 < main.property("replayAlpha") < .9)
                main.setProperty("replayAlpha", .55)
                backend.showReplayOverlay()
            elif step == 4:
                overlay_canvas = overlay.findChild(QObject, "overlayReplayCanvas")
                if not overlay_canvas.property("imagesReady") and time.perf_counter()-started < 12:
                    return
                check("overlay_replays_same_record", overlay.property("replayMode") and overlay_canvas.property("imagesReady"))
                main.grabWindow().save(str(output / "replay-1180.png"))
                overlay.grabWindow().save(str(output / "replay-overlay.png"))
                main.setWidth(940); main.setHeight(660)
            elif step == 5:
                main.grabWindow().save(str(output / "replay-940.png"))
                main.setProperty("page", "resources")
                main.setProperty("page", "replay")
                check("page_switch_keeps_record", backend.replayAvailable and canvas.property("imagesReady"))
                check("no_tk_or_game_runtime", "tkinter" not in sys.modules and "identityv_daily_assistant" not in sys.modules)
                metrics.active = False
                from main import distribution
                state["ui_timer_delay_ms"] = distribution(metrics.delays)
                state["rss_mib"] = distribution(metrics.rss)
                state["elapsed_seconds"] = round(time.perf_counter()-started, 3)
                state["input_kind"] = backend.replayData.get("evidenceKind")
                state["record_sha256"] = backend.replayData.get("recordSha256")
                state["all_assertions_pass"] = all(state["assertions"].values()) and not state["errors"]
                state["limits"] = ["existing production solver result on a synthetic saved frame", "no solver rerun or game capture, no global hook, no live display authorization", "Qt recorded-matrix mapping and visible pixel delta, not CPU-renderer pixel parity or game latency"]
                (output / "replay-probe.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
                print(json.dumps(state))
                timer.stop()
                app.exit(0 if state["all_assertions_pass"] else 2)
                return
        except Exception as error:
            state["errors"].append(f"step {step}: {type(error).__name__}: {error}")
        state["step"] += 1
    timer = QTimer(app)
    timer.setInterval(700)
    timer.timeout.connect(advance)
    timer.start()
    app._probe_timer = timer
    QTimer.singleShot(16000, lambda: app.exit(3))
