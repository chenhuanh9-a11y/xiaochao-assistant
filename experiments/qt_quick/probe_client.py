"""Bounded full-client UI checks; never enumerate/bind/capture a game or read keys."""
from pathlib import Path
import json
import time
import sys
from PySide6.QtCore import QObject, QPointF, QTimer, Qt, QMetaObject
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


class ResourceContextAcceptance:
    """Bounded real-client checks after opening the deferred management page."""
    contexts = (("s0", "difficult"), ("s1", "nightmare"),
                ("s1", "difficult"), ("s1", "difficulty2"), ("s0", "nightmare"))

    def __init__(self, client, check, capture, original, clock=time.monotonic):
        self.client, self.check, self.capture, self.clock = client, check, capture, clock
        self.records, self.stage, self.deadline = [], "open", clock() + 60
        self.original = (original.get("gameSeason", "s0"), original["gameMode"])
        self.targets = list(self.contexts)
        if self.targets[-1] != self.original:
            self.targets.append(self.original)
        self.index, self.expected_ids = 0, None
        self.frozen = bool(getattr(sys, "frozen", False))

    def go(self, stage):
        self.stage, self.deadline = stage, self.clock() + 60

    def idle(self):
        c = self.client
        return not (c.session.busy or c.settings.busy or c.resources.busy
                    or c.package_manager.busy or c.coordinator._pending_context
                    or c.session.snapshot.get("contextPending")
                    or c.session.snapshot.get("resourceLocked"))

    def page(self):
        return next((o for o in self.client.main.findChildren(QObject)
                     if o.metaObject().indexOfProperty("visibleResourceRows") >= 0), None)

    def rows(self):
        page = self.page()
        value = page.property("visibleResourceRows") if page is not None else []
        return value.toVariant() if hasattr(value, "toVariant") else value

    @staticmethod
    def builtin_ids(rows):
        return sorted(r["packageId"] for r in rows if r.get("builtin") or r.get("status") == "bundled")

    @staticmethod
    def declarations(rows):
        builtins = [r for r in rows if r.get("builtin") or r.get("status") == "bundled"]
        return bool(builtins) and all(r.get("status") == "declared"
            and not r.get("canRemove") and r.get("verified") is not True
            and not any(k in r for k in ("mapCount", "layerCount", "bundledFiles"))
            for r in builtins)

    def context_ready(self, target):
        c = self.client
        season, mode = target
        snapshot = c.runtime.snapshot()
        return (self.idle() and (c.session.appliedSeason, c.session.appliedMode) == target
            and (c.session.currentSeason, c.session.currentMode) == target
            and (snapshot.get("season"), snapshot.get("mode")) == target
            and c.providers.capabilities(season).get(mode, {}).get("available") is True)

    def startup_ready(self):
        c = self.client
        return (c._initialized and not c.backend.busy and self.context_ready(self.original)
                and c.session.snapshot.get("commands", {}).get("start_round", {}).get("available") is True)

    def token(self):
        return self.client.resources.operationGeneration, self.client.package_manager._token

    def record(self, name, **fields):
        self.records.append(dict(event=name, at=self.clock(), **fields))

    def list_ready(self):
        view = self.client.main.findChild(QObject, "managedPackageList")
        return (self.idle() and bool(self.client.resources.packageRows)
                and view is not None and view.property("count") > 0)

    def advance(self):
        if self.stage != "open" and self.clock() > self.deadline:
            raise TimeoutError("resource/context acceptance: " + self.stage)
        c, stage = self.client, self.stage
        if stage == "open":
            self.record("resource-page-requested")
            c.coordinator.navigate("resources")
            self.go("declarations")
        elif stage == "declarations":
            if not self.list_ready():
                return False
            rows = self.rows()
            self.check("resource_management_ready_after_open", True)
            self.check("resource_declarations_not_verified", not c.resources.inventoryVerified
                       and self.declarations(rows))
            self.expected_ids = self.builtin_ids(rows)
            self.record("resource-declarations-ready", rows=rows,
                        managerRows=c.package_manager.packageRows)
            self.capture("resources-declared.png")
            self.saved_token, self.wait_until = self.token(), self.clock() + .35
            c.coordinator.navigate("home")
            self.go("hidden")
        elif stage == "hidden":
            if self.clock() < self.wait_until:
                return False
            self.check("hidden_resource_page_no_scan", self.token() == self.saved_token)
            c.coordinator.navigate("resources")
            self.go("reopened")
        elif stage == "reopened":
            if not self.list_ready() or self.token() == self.saved_token:
                return False
            self.check("resource_page_reentry_has_real_rows",
                       self.builtin_ids(self.rows()) == self.expected_ids)
            self.saved_token, self.wait_until = self.token(), self.clock() + .35
            c.coordinator.navigate("resources")
            self.go("same-page")
        elif stage == "same-page":
            if self.clock() < self.wait_until:
                return False
            self.check("same_resource_page_no_rescan", self.token() == self.saved_token)
            self.record("strict-verification-requested")
            if not QMetaObject.invokeMethod(self.page(), "refreshPackages"):
                raise RuntimeError("QML resource verification action not invoked")
            self.go("verified")
        elif stage == "verified":
            if not self.idle() or not c.resources.inventoryVerified:
                return False
            rows = self.rows()
            self.check("resource_explicit_verification_complete",
                       bool(rows) and self.builtin_ids(rows) == self.expected_ids
                       and all(r.get("status") == "bundled" for r in rows
                               if r.get("builtin") or r.get("status") == "bundled"))
            self.record("strict-verification-complete", rows=rows)
            self.capture("resources-verified.png")
            c.resources.resourcesChanged.emit()
            self.check("resource_invalidation_revokes_verified_state",
                       not c.resources.inventoryVerified and self.declarations(self.rows()))
            self.record("resource-verification-invalidated", rows=self.rows())
            c.coordinator.navigate("home")
            self.go("invalidation")
        elif stage == "invalidation":
            if not self.context_ready(self.original):
                return False
            self.go("mode-request")
        elif stage == "mode-request":
            self.target = self.targets[self.index]
            self.saved_token = self.token()
            self.record("context-requested", season=self.target[0], mode=self.target[1])
            c.coordinator.requestGameContext(*self.target)
            self.go("mode-wait")
        elif stage == "mode-wait":
            if not self.context_ready(self.target):
                return False
            key = ":".join(self.target) + ":" + str(self.index)
            self.check(key + ":context_ready", True)
            self.check(key + ":hidden_management_no_scan", self.token() == self.saved_token)
            self.check(key + ":native_input_disabled", not c.input.enabled
                       and not c.capture.snapshot["enabled"])
            start_command = c.session.snapshot.get("commands", {}).get("start_round", {})
            self.check(key + ":start_permission_matches_identity",
                       bool(start_command.get("available")) == (self.frozen or self.target[0] == "s0"))
            self.record("context-ready", season=self.target[0], mode=self.target[1],
                        startCommand=start_command,
                        snapshotContext={k:c.runtime.snapshot().get(k) for k in ("mode", "season", "profileId")})
            c.coordinator.navigate("resources")
            self.go("mode-list")
        elif stage == "mode-list":
            if not self.list_ready() or self.token() == self.saved_token:
                return False
            key = ":".join(self.target) + ":" + str(self.index)
            self.check(key + ":list_context_isolated", self.context_ready(self.target)
                       and self.builtin_ids(self.rows()) == self.expected_ids
                       and not c.resources.inventoryVerified and self.declarations(self.rows()))
            self.record("context-resource-list-ready", season=self.target[0], mode=self.target[1],
                        rows=self.rows())
            self.capture("resources-" + str(self.index) + "-" + "-".join(self.target) + ".png")
            self.index += 1
            if self.index == len(self.targets):
                self.go("done")
                return True
            c.coordinator.navigate("home")
            self.go("mode-request")
        return stage == "done"


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
                snapshot = client.session.snapshot
                readiness = dict(initialized=client._initialized, backendBusy=backend.busy,
                    appliedContext=[client.session.appliedSeason, client.session.appliedMode],
                    savedContext=list(acceptance.original), contextPending=snapshot.get("contextPending"),
                    resourceLocked=snapshot.get("resourceLocked"),
                    startCommand=snapshot.get("commands", {}).get("start_round", {}))
                state.setdefault("startup_first_observation", readiness)
                if not acceptance.startup_ready():
                    if time.monotonic()-started < 60:
                        return
                    state["startup_terminal_observation"] = readiness
                    raise TimeoutError("startup context and start-round command were not ready")
                state["startup_ready_observation"] = readiness
                window.grabWindow()
                state["registered_surface_names"] = sorted(client.windows._windows)
                check("registered_surfaces", {
                    "main", "controls", "reference", "aligned", "replay", "preview", "toast"
                }.issubset(client.windows._windows))
                check("no_implicit_native_input", not client.input.enabled and not client.capture.snapshot["enabled"])
                check("no_tk_import", "tkinter" not in __import__("sys").modules)
                check("resource_list_ready", backend.count > 0)
                check("resource_management_deferred_at_startup",
                      not client.resources.packageRows and not client.resources.inventoryVerified)
                check("unbound_preparation_start_available", bool(window.findChild(QObject,"clientStartRound").property("enabled")))
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
                if client.resources.busy or client.package_manager.busy or not client.resources.packageRows:
                    if time.monotonic() - started < 60:
                        return
                    raise TimeoutError("resource list did not load after opening page")
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
                # A hidden probe window may not polish a newly opened layout
                # until a frame is requested. Measure the same rendered frame
                # that the screenshot evidence uses, rather than stale geometry.
                window.grabWindow()
                image = window.findChild(QQuickItem, "customMapImage")
                # Resource preparation may consume the earlier startup budget.
                # Give this newly opened editor its own bounded layout/load wait.
                deadline = state.setdefault("editor_ready_deadline", time.monotonic() + 8)
                if (client.custom_galleries.busy or image is None
                        or image.property("paintedWidth") <= 0) and time.monotonic() < deadline:
                    return
                state["editor_viewport"] = ({key:image.property(key) for key in
                    ("width", "height", "paintedWidth", "paintedHeight")} if image else None)
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
            elif step == 14:
                if not acceptance.advance():
                    return
                state["resource_context_acceptance"] = acceptance.records
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
            state["resource_context_acceptance"] = acceptance.records
            state["all_assertions_pass"] = False
            state["qml_warnings"] = client.warnings
            (output/"client-probe.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            timer.stop()
            client.coordinator.quitRequested.connect(lambda: app.exit(2))
            client.coordinator.requestClose()
            return
        state["step"] += 1
    acceptance = ResourceContextAcceptance(client, check, capture, original)
    timer = QTimer(app)
    timer.setInterval(650)
    timer.timeout.connect(advance)
    timer.start()
    app._client_probe_timer = timer
    QTimer.singleShot(300000,lambda: app.exit(3))
