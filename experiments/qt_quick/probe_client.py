"""Bounded full-client UI checks; never enumerate/bind/capture a game or read keys."""
from pathlib import Path
import json
import time
from PySide6.QtCore import QObject, QTimer


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
    def advance():
        step = state["step"]
        try:
            if step == 0:
                if (not client._initialized or client.session.busy or client.resources.busy or backend.busy) and time.monotonic()-started < 30:
                    return
                check("five_surfaces", len(client.engine.rootObjects()) == 5)
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
                grid = window.findChild(QObject,"clientResourceGrid")
                grid.setProperty("contentY", 220.)
                state["scroll"] = grid.property("contentY")
                state["mutations"] = backend.model.mutations
                backend.refresh()
            elif step == 2:
                if backend.busy:
                    return
                grid = window.findChild(QObject,"clientResourceGrid")
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
                client.coordinator.showReference()
                client.coordinator.beginReferenceEdit()
                reference = client.windows._windows["reference"]
                state["before_reference"] = [reference.x(), reference.y()]
                x, y = reference.x()+20, reference.y()+20
                client.coordinator.referencePointer("press",x,y)
                client.coordinator.referencePointer("move",x+60,y+35)
                client.coordinator.referencePointer("release",x+60,y+35)
                check("release_keeps_reference_edit", client.coordinator.referenceEditing)
                check("reference_pointer_moves", [reference.x(),reference.y()] != state["before_reference"])
                client.coordinator.cancelReferenceEdit()
                check("reference_cancel_restores", [reference.x(),reference.y()] == state["before_reference"])
                check("reference_cancel_restores_passthrough", client.windows.snapshot["reference"]["clickThrough"])
                client.coordinator.beginReferenceEdit()
                client.coordinator.finishReferenceEdit()
            elif step == 4:
                if client.settings.busy:
                    return
                check("reference_finish_persists", client.settings.persistedValues["sheetPosition"] is not None)
                client.windows._windows["reference"].grabWindow().save(str(output/"reference.png"))
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
                check("native_input_still_disabled", not client.input.enabled and not client.capture.snapshot["enabled"])
                check("no_qml_warnings", not client.warnings)
                state["qml_warnings"] = client.warnings
                state["elapsed_seconds"] = round(time.monotonic()-started,3)
                state["all_assertions_pass"] = all(state["assertions"].values()) and not state["errors"]
                state["scope"] = "Own Qt windows and isolated resource/config writes; local update fixture; no native game enumeration/capture/keys; not a live-game test"
                (output/"client-probe.json").write_text(json.dumps(state,indent=2),encoding="utf-8")
                print(json.dumps(state))
                timer.stop()
                app.exit(0 if state["all_assertions_pass"] else 2)
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
