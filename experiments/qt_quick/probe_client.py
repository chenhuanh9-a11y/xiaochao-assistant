"""Bounded full-client UI checks; never enumerate/bind/capture a game or read keys."""
from pathlib import Path
import json
import time
from PySide6.QtCore import QObject, QPointF, QTimer, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


def run_client_probe(client, output):
    output.mkdir(parents=True, exist_ok=True)
    app, window, backend = client.app, client.main, client.backend
    state = dict(assertions={}, errors=[], step=0)
    started = time.monotonic()
    original = dict(client.settings.values)
    def check(key, value):
        state["assertions"][key] = bool(value)
    def capture(name):
        window.grabWindow().save(str(output / name))
    def click_map_point(point):
        image = window.findChild(QQuickItem, "customMapImage")
        surface = window.findChild(QQuickItem, "customMapDrawingSurface")
        asset = client.custom_galleries.library.floor("nightmare-09", "1")
        u, v, z = asset.to_image @ [point["x"], point["y"], 1]
        scale = image.property("paintedWidth") / asset.image_size[0]
        position = surface.mapToScene(QPointF(
            (surface.width()-image.property("paintedWidth"))/2 + u/z*scale,
            (surface.height()-image.property("paintedHeight"))/2 + v/z*scale))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position.toPoint())
    def advance():
        step = state["step"]
        try:
            if step == 0:
                if (not client._initialized or client.session.busy or client.resources.busy or backend.busy) and time.monotonic()-started < 30:
                    return
                check("registered_surfaces", set(client.windows._windows) == {
                    "main", "controls", "reference", "aligned", "replay", "preview", "toast"})
                check("no_implicit_native_input", not client.input.enabled and not client.capture.snapshot["enabled"])
                check("no_tk_import", "tkinter" not in __import__("sys").modules)
                check("resource_list_ready", backend.count > 0)
                check("resource_management_ready", len(client.resources.packageRows) > 0)
                check("no_game_start_without_binding", not window.findChild(QObject,"clientStartRound").property("enabled"))
                capture("home-1180.png")
                window.setProperty("page", "gallery")
                row = next(r for r in backend.model.rows if r["available"])
                backend.select(row["key"])
                state["selected"] = backend.selectedKey
            elif step == 1:
                capture("gallery-1180.png")
                grid = window.findChild(QObject,"galleryAuthorGrid")
                grid.setProperty("contentY", min(220., max(0., grid.property("contentHeight")-grid.property("height"))))
                state["scroll"] = grid.property("contentY")
                state["mutations"] = backend.model.mutations
                backend.refresh()
            elif step == 2:
                if backend.busy:
                    return
                grid = window.findChild(QObject,"galleryAuthorGrid")
                check("refresh_keeps_selection", backend.selectedKey == state["selected"])
                check("refresh_keeps_scroll", abs(grid.property("contentY")-state["scroll"]) < 1)
                check("refresh_zero_model_mutations", backend.model.mutations == state["mutations"])
                client.settings.setValue("sheetOpacity", .42)
                client.settings.saveFields(["sheetOpacity"])
                window.setProperty("page", "settings")
            elif step == 3:
                if client.settings.busy:
                    return
                check("real_settings_persisted", json.loads(client.settings.path.read_text(encoding="utf-8"))["sheetOpacity"] == .42)
                capture("settings-1180.png")
                # Gallery preview is a normal window, never a diagnostic path
                # that grants the current game's reference-sheet authority.
                client.backend.showOverlay()
            elif step == 4:
                if client.settings.busy:
                    return
                preview = client.windows._windows["preview"]
                check("gallery_preview_visible", preview.isVisible())
                check("gallery_preview_does_not_show_game_reference", not client.windows._windows["reference"].isVisible())
                preview.grabWindow().save(str(output/"preview.png"))
                preview.close()
                check("gallery_preview_close", not preview.isVisible() and window.isVisible())
                client.backend.showOverlay()
                check("gallery_preview_reopens", preview.isVisible())
                preview.close()
                client.windows.show("controls")
                window.setProperty("page", "resources")
            elif step == 5:
                capture("resources-1180.png")
                client.windows._windows["controls"].grabWindow().save(str(output/"controls.png"))
                window.setProperty("page", "help")
                client.info.checkUpdates()
            elif step == 6:
                if client.info.checking:
                    return
                check("update_service_real_parser", client.info.release.get("version") == "1.0.5")
                capture("help-1180.png")
                window.setWidth(960); window.setHeight(680)
                window.setProperty("page", "settings")
                client.settings.setValue("sheetOpacity", original["sheetOpacity"])
                client.settings.saveFields(["sheetOpacity"])
            elif step == 7:
                if client.settings.busy:
                    return
                capture("settings-960.png")
                window.setProperty("page", "custom")
            elif step == 8:
                if client.custom_galleries.busy:
                    return
                check("editor_maps_loaded", bool(client.custom_galleries.snapshot.get("maps")))
                check("custom_gallery_browser_visible", not client.custom_galleries.snapshot.get("editing"))
                capture("custom-gallery-960.png")
                marker = client.paths.work_root / ".qt-client-probe-owner"
                isolated = client.sandbox / ".qt-lab-owner"
                owned = (isolated.is_file() and isolated.read_text(encoding="utf-8").strip() == "qt-quick-isolated-v1"
                         or marker.is_file() and marker.read_text(encoding="utf-8").strip() == "qt-quick-build-probe-v1")
                if not owned:
                    raise RuntimeError("Route-writing probe requires an owned build/test directory")
                client.custom_galleries.selectMap("nightmare-09")
            elif step == 9:
                image = window.findChild(QObject, "customMapImage")
                if (client.custom_galleries.busy or image is None
                        or image.property("paintedWidth") <= 0) and time.monotonic()-started < 35:
                    return
                check("editor_viewport_has_width", image is not None and image.property("width") > 100
                      and image.property("height") > 100)
                check("editor_map_pixels_visible", image is not None and image.property("paintedWidth") > 0
                      and image.property("paintedHeight") > 0)
                capture("editor-960.png")
                client.custom_galleries.selectFloor("1")
            elif step == 10:
                if client.custom_galleries.busy:
                    return
                client.custom_galleries.createGallery("窗口绘线检查 " + str(time.monotonic_ns())[-6:])
            elif step == 11:
                image = window.findChild(QQuickItem, "customMapImage")
                if client.custom_galleries.busy or image.property("paintedWidth") <= 0:
                    return
                graph = client.custom_galleries.library.graph("nightmare-09")
                asset = client.custom_galleries.library.floor("nightmare-09", "1")
                picked = []
                for xy in graph.floors["1"].points:
                    point = graph.pick("1", *xy)
                    if not point or (picked and abs(point["x"]-picked[0]["x"]) + abs(point["y"]-picked[0]["y"]) < 400):
                        continue
                    u, v, z = asset.to_image @ [point["x"], point["y"], 1]
                    if not (10 < u/z < asset.image_size[0]-10 and 10 < v/z < asset.image_size[1]-10):
                        continue
                    if picked:
                        try:
                            if not graph.plan([picked[0], point]):
                                continue
                        except ValueError:
                            continue
                    picked.append(point)
                    if len(picked) == 2:
                        break
                if len(picked) != 2:
                    raise RuntimeError("No connected representative editor points")
                state["drawing_points"] = picked
                click_map_point(picked[0])
            elif step == 12:
                if client.custom_galleries.busy:
                    return
                check("editor_mouse_first_point", len(client.custom_galleries.snapshot["points"]) == 1)
                click_map_point(state["drawing_points"][1])
            elif step == 13:
                if client.custom_galleries.busy:
                    return
                check("editor_mouse_second_point", len(client.custom_galleries.snapshot["points"]) == 2)
                check("editor_route_drawn", bool(client.custom_galleries.snapshot["lines"]))
                capture("editor-route-960.png")
                check("native_input_still_disabled", not client.input.enabled and not client.capture.snapshot["enabled"])
                check("no_qml_warnings", not client.warnings)
                state["qml_warnings"] = client.warnings
                state["elapsed_seconds"] = round(time.monotonic()-started,3)
                state["all_assertions_pass"] = all(state["assertions"].values()) and not state["errors"]
                state["scope"] = "Own Qt windows and isolated resource/config writes; local update fixture; no native game enumeration/capture/keys; not a live-game test"
                (output/"client-probe.json").write_text(json.dumps(state,indent=2),encoding="utf-8")
                print(json.dumps(state))
                timer.stop()
                code = 0 if state["all_assertions_pass"] else 2
                client.coordinator.quitRequested.connect(lambda: app.exit(code))
                client.coordinator.requestClose()
                return
        except Exception as error:
            state["errors"].append(f"step {step}: {type(error).__name__}: {error}")
        state["step"] += 1
    timer = QTimer(app)
    timer.setInterval(650)
    timer.timeout.connect(advance)
    timer.start()
    app._client_probe_timer = timer
    QTimer.singleShot(45000,lambda: app.exit(3))
