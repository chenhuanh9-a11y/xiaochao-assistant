"""Progressive pose publication through the live runtime and owned QML surface.

The capture, route bitmap, fit and peer-review verdict are deterministic test
inputs. LiveRuntime._solve_observation, its _AlignmentHost usable-fit callback,
ClientCoordinator._present_runtime_state, LiveImages.publish/requestImage and
ClientAligned.qml remain real. A read-only scenegraph callback observes the
QSGTexture owned by the QML Image after a successful first pixel grab.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest


HERE = Path(__file__).resolve().parent
PROBE_PREFIX = "PROGRESSIVE_PIXELS_JSON="


class ProgressiveClientPixels(unittest.TestCase):
    def run_probe(self, verdict):
        task_tmp = str(HERE.parents[1] / "work" / "tmp")
        env = dict(os.environ, TMP=task_tmp, TEMP=task_tmp,
                   QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                   QSG_RENDER_LOOP="basic",
                   QT_LOGGING_RULES="qt.scenegraph.time.texture=true;qt.scenegraph.general=true;qt.rhi.*=true",
                   PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                  "--probe", verdict], cwd=HERE, env=env,
                                 capture_output=True, text=True, timeout=45)
        self.assertEqual(process.returncode, 0, process.stdout + "\n" + process.stderr)
        rows = [line[len(PROBE_PREFIX):] for line in process.stdout.splitlines()
                if line.startswith(PROBE_PREFIX)]
        self.assertEqual(len(rows), 1, process.stdout + "\n" + process.stderr)
        return json.loads(rows[0])

    def test_valid_peer_review_corrects_position_without_reloading_qml_material(self):
        row = self.run_probe("corrected")
        self.assertEqual(row["case"], "corrected")
        self.assertTrue(row["pendingVisible"])
        self.assertEqual(row["pendingReviewStatus"], "pending")
        self.assertEqual(row["finalReviewStatus"], "complete")
        self.assertNotEqual(row["pendingToken"], row["finalToken"])
        self.assertEqual(row["pendingUrl"], row["finalUrl"])
        self.assertEqual(row["imagePublishCount"], 2)
        self.assertEqual(row["imageProviderRequestCount"], 1)
        self.assertTrue(row["qsgTextureFirstObserved"])
        self.assertEqual(row["qsgTextureUniqueObservedCount"], 1)
        self.assertEqual(row["pendingTexture"]["pointer"], row["completeTexture"]["pointer"])
        self.assertEqual(row["pendingTexture"]["lifeId"], row["completeTexture"]["lifeId"])
        self.assertEqual(row["pendingTexture"]["comparisonKey"], row["completeTexture"]["comparisonKey"])
        self.assertFalse(row["pendingTexture"]["ownedByPython"])
        self.assertEqual(row["qsgTextureDestroyedBeforeTeardownCount"], 0)
        self.assertEqual(row["qsgTextureDestroyedAtTeardownCount"], 1)
        self.assertEqual(row["qsgTextureDestroyedCount"], 1)
        self.assertEqual(row["pendingPixelSamples"], row["expectedInitialSamples"])
        self.assertEqual(row["finalPixelSamples"], row["expectedCorrectedSamples"])
        self.assertEqual(row["materialAckTokens"], [row["pendingToken"], row["finalToken"]])
        self.assertFalse(row["pendingMaterialAckSuccess"])
        self.assertTrue(row["finalMaterialAckSuccess"])
        self.assertEqual(row["alignedHideCallsDuringReview"], 0)

    def test_invalid_peer_review_revokes_preliminary_pixels(self):
        row = self.run_probe("invalid")
        self.assertEqual(row["case"], "invalid")
        self.assertTrue(row["pendingVisible"])
        self.assertEqual(row["pendingReviewStatus"], "pending")
        self.assertFalse(row["visibleAfterRevoke"])
        self.assertEqual(row["sourceAfterRevoke"], "")
        self.assertEqual(row["alignedTokenAfterRevoke"], "")
        self.assertFalse(row["pendingMaterialAckSuccess"])
        self.assertEqual(row["imageProviderRequestCount"], 1)
        self.assertFalse(row["pendingTexture"]["ownedByPython"])
        self.assertEqual(row["qsgTextureDestroyedBeforeTeardownCount"], 0)
        self.assertEqual(row["qsgTextureDestroyedAtTeardownCount"], 1)
        self.assertGreaterEqual(row["qsgTextureDestroyedCount"], 1)
        self.assertEqual(row["qsgTextureLifecycle"][0]["pointer"], row["pendingTexture"]["pointer"])

    def test_corrected_pixel_assertion_rejects_stale_coordinator_matrix(self):
        row = self.run_probe("stale_matrix")
        self.assertTrue(row["staleMatrixControlApplied"])
        self.assertTrue(row["correctedPixelAssertionRejected"])
        self.assertEqual(row["finalReviewStatus"], "complete")
        self.assertEqual(row["staleConsumerFinalSamples"], row["expectedInitialSamples"])
        self.assertNotEqual(row["staleConsumerFinalSamples"], row["expectedCorrectedSamples"])
        self.assertEqual(row["consumerFinalMatrix"], row["pendingMatrix"])
        self.assertEqual(row["runtimeFinalMatrix"], row["correctedMatrix"])
        self.assertEqual(row["pendingTexture"]["pointer"], row["completeTexture"]["pointer"])
        self.assertFalse(row["pendingMaterialAckSuccess"])
        self.assertTrue(row["finalMaterialAckSuccess"])


def _probe(verdict):
    import numpy as np
    from unittest.mock import Mock, patch

    from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QUrl, Qt
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    import shiboken6

    from client_coordinator import LiveImages
    from client_live_runtime import LiveRuntime, _AlignmentHost
    from client_region_capture import ClientCaptureContext, RegionCapture
    from shared_structure_reference import SharedStructureReference
    from test_client_coordinator import CoordinatorTests
    from test_client_live_runtime import LiveRuntimeContracts

    app = QGuiApplication.instance() or QGuiApplication([])
    os.environ.setdefault("QT_LOGGING_RULES",
        "qt.scenegraph.time.texture=true;qt.scenegraph.general=true;qt.rhi.*=true")
    fixture = LiveRuntimeContracts()
    fixture.setUp()
    engine = None
    runtime = None
    coordinator_fixture = None
    coordinator_cleaned = False
    try:
        # Keep the fixture's real observation/floor adapters, but replace its
        # single-fit shortcut so this probe reaches the production host callback.
        fixture.patches[2].stop()
        capture = fixture.capture
        capture.frame = np.zeros((720, 1280, 3), np.uint8)
        capture.roi = (0, 0, 1280, 720)
        capture.generation = 1
        capture.is_current = Mock(return_value=True)
        capture.capture = lambda cancelled=None: RegionCapture(capture.frame.copy(), capture.roi,
            capture.roi, ClientCaptureContext((1280, 720), (20, 30, 1300, 750), 96), "progressive-fixture")

        pixels_rgba = np.zeros((80, 100, 4), np.uint8)
        pixels_rgba[10:30, 10:30] = (230, 30, 40, 255)
        pixels_rgba[10:30, 50:70] = (20, 200, 80, 255)
        pixels_rgba[40:60, 10:30] = (30, 60, 220, 255)
        pixels_rgba[40:60, 50:70] = (250, 190, 20, 255)
        pixels_bgra = np.ascontiguousarray(pixels_rgba[:, :, [2, 1, 0, 3]])
        pixels_bgra.setflags(write=False)

        binding = dict(profile="shared-structure", mapId="fixture-map", floor="1", variant="fixture")
        reference = SharedStructureReference(np.ones((80, 100), np.uint8),
            np.ones((80, 100), np.uint8),
            dict(binding=binding, canvas=dict(shapeHW=[80, 100], coordinateSpace="fixture-pixels"),
                 structureVersionKey="progressive-fixture"), Path("progressive-fixture.npz"))
        fixture.engine.qt_shared_provider = lambda *args: reference

        runtime = LiveRuntime(engine_factories={"nightmare": lambda: fixture.engine,
            "difficult": lambda: fixture_engine_difficult(fixture.engine)},
            capture_provider=capture, evidence_kind="live-client-capture", clock=time.monotonic)
        display = dict(pixels=pixels_bgra, matrixDisplayToReference=np.eye(3),
            resourceIdentity="progressive-rgba-fixture", displayKind="shared-structure",
            displayLabel="synthetic pixels", routeProfileId="shared-structure",
            effectiveProfileId="shared-structure", fallback=False, authorStatus="none")
        runtime._display_layer = lambda *args, **kwargs: display

        CoordinatorTests.setUpClass()
        coordinator_fixture = CoordinatorTests("runTest")
        coordinator_fixture.setUp()
        coordinator = coordinator_fixture.coordinator
        coordinator_fixture.runtime = coordinator.runtime = runtime
        coordinator_fixture.settings.values["sheetOpacity"] = 1.0
        coordinator_fixture.settings.persistedValues["sheetOpacity"] = 1.0
        state = runtime.snapshot()
        coordinator._applied_context = (state["mode"], state["profileId"], state["season"])
        coordinator_fixture.session.snapshot = dict(state, roundActive=True, mapOpen=True,
            resourceLocked=False, availableCommands=["refresh_map"], alignedVisible=False)
        coordinator_fixture.session.dispatchMaintenance = Mock(return_value=True)
        coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.0)

        class ObservedLiveImages(LiveImages):
            """Keep the production Image provider while counting its real reads."""
            def __init__(self):
                super().__init__()
                self.requests = []

            def requestImage(self, identifier, size, requested_size):
                image = super().requestImage(identifier, size, requested_size)
                self.requests.append(dict(id=str(identifier), size=[image.width(), image.height()],
                    requested=[requested_size.width(), requested_size.height()],
                    format=str(image.format()), null=image.isNull()))
                return image

        images = ObservedLiveImages()
        publishes = []
        production_publish = images.publish
        def observe_publish(key, image, *, material_scope=None):
            url = production_publish(key, image, material_scope=material_scope)
            publishes.append(dict(key=str(key), url=url,
                pixels=np.asarray(image).copy(), scope=material_scope))
            return url
        images.publish = observe_publish
        coordinator.images = images

        engine = QQmlApplicationEngine()
        engine.addImageProvider("live", images)

        class Settings(QObject):
            values = Property("QVariantMap", lambda self: dict(sheetOpacity=1.0), constant=True)

        settings = Settings()
        engine.rootContext().setContextProperty("clientCoordinator", coordinator)
        engine.rootContext().setContextProperty("settingsService", settings)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        engine.load(QUrl.fromLocalFile(str(HERE / "ClientAligned.qml")))
        if len(engine.rootObjects()) != 1:
            raise AssertionError("ClientAligned.qml did not create one root window: " + repr(warnings))
        window = engine.rootObjects()[0]
        window.show()

        shown, hidden = [], []
        def show_window(name):
            shown.append(name)
            if name == "aligned":
                window.show()
        def hide_window(name):
            hidden.append(name)
            if name == "aligned":
                window.hide()
        coordinator.windows.show.side_effect = show_window
        coordinator.windows.hide.side_effect = hide_window
        coordinator._trace("clear", reason="probe-start")
        runtime_events = []
        runtime.set_event_callback(lambda row: (runtime_events.append(row), coordinator.runtimeEvent.emit(row)))
        traces = []
        coordinator.traceReported.connect(traces.append)
        stale_matrix_applied = []
        if verdict == "stale_matrix":
            production_present = coordinator._present_runtime_state
            def present_stale_matrix(state, *, trigger):
                production_present(state, trigger=trigger)
                if coordinator._aligned.get("reviewStatus") == "complete":
                    # Negative control: after production presentation, make the
                    # QML binding consume the old pending matrix deliberately.
                    coordinator._aligned["matrix"] = np.eye(3).tolist()
                    stale_matrix_applied.append(True)
                    coordinator.changed.emit()
            coordinator._present_runtime_state = present_stale_matrix
        ack_calls = []
        real_ack = runtime.acknowledge_render_material
        def record_ack(token):
            result = real_ack(token)
            ack_calls.append(dict(token=str(token), result=dict(result)))
            return result
        runtime.acknowledge_render_material = record_ack

        def dispatch(action, **payload):
            current = runtime.snapshot()
            context = {key: current[key] for key in ("mode", "profileId", "roundId", "frameId")}
            context.update(generation=22, **payload)
            return runtime.dispatch(action, context)

        started = dispatch("start_round")
        if not started.get("success"):
            raise AssertionError("start_round failed: " + repr(started))

        pending_offered = threading.Event()
        allow_review = threading.Event()
        worker_result = {}
        worker_error = []
        first_matrix = np.eye(3)
        corrected_matrix = np.array([[1., 0., 8.], [0., 1., 6.], [0., 0., 1.]])
        residual = dict(verified=True, poseVerifiedAgainstCanonicalGeometry=True,
            profileRenderDerivedFromCanonical=True, coordinatePackId="fixture-pack",
            validationBasis="shared-canonical-geometry-current-frame")
        solver_times = {}

        def fit_result(matrix):
            return dict(success=True, matrixSourceToClient=np.asarray(matrix, np.float64),
                selectedRouteCurrentFrameResidual=dict(residual), strategy="controlled-fit",
                reason="controlled-current-frame-fit")

        def gated_fit(host, frame, map_id, floor, cancelled, entry_candidate=None, **kwargs):
            host_started = time.monotonic()
            baseline = runtime.snapshot()
            solver_times["baselineGateSelection"] = runtime._gate.selected_id
            solver_times["baselineDurableMapId"] = baseline.get("durableMapId", "")
            solver_times["baselineGroupIsNone"] = runtime._group is None
            solver_times["baselineCorePoses"] = runtime._core.snapshot().get("poses")
            first = fit_result(first_matrix)
            # Real shared-host gate and runtime-installed callback. Runtime then
            # performs its real pending commit and emits the queued render event.
            host._offer_usable_fit(first)
            solver_times["hostOfferElapsedMs"] = (time.monotonic() - host_started) * 1000.0
            pending_offered.set()
            review_wait_started = time.monotonic()
            if not allow_review.wait(15.0):
                solver_times["reviewTimedOut"] = True
                return dict(success=False, reason="controlled-review-timeout")
            solver_times["reviewBlockMs"] = (time.monotonic() - review_wait_started) * 1000.0
            if verdict in ("corrected", "stale_matrix"):
                return fit_result(corrected_matrix)
            return dict(success=False, reason="controlled-independent-review-rejected")

        fixture_fit_patch = patch.object(_AlignmentHost, "fit", gated_fit)
        fixture_fit_patch.start()
        injected_at = time.monotonic()
        def run_capture():
            try:
                worker_result.update(dispatch("capture_now"))
            except BaseException as error:
                worker_error.append(error)
        worker = threading.Thread(target=run_capture, name="progressive-fit-probe", daemon=True)
        worker.start()

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not pending_offered.is_set():
            app.processEvents()
            QTest.qWait(5)
        if not pending_offered.is_set():
            worker.join(1.0)
            raise AssertionError("host did not offer its first usable fit: " + repr({
                "workerResult": worker_result, "workerError": [repr(item) for item in worker_error],
                "runtimeEvents": runtime_events[-12:], "runtime": runtime.snapshot(),
                "captureEnabled": capture.enabled, "captureCount": capture.count}))

        def pump_until(predicate, seconds=5.0):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                if predicate():
                    return True
                QTest.qWait(5)
            app.processEvents()
            return bool(predicate())

        def aligned_surfaces():
            return [row.get("window", {}) for row in traces
                    if row.get("stage") == "surface-state" and row.get("window", {}).get("name") == "aligned"]

        def has_pending_pixels():
            return (coordinator.alignedData.get("reviewStatus") == "pending"
                and window.isVisible() and any(row.get("materialReady") is True for row in aligned_surfaces()))
        if not pump_until(has_pending_pixels):
            raise AssertionError("pending QML material was not visible/ready: " + repr({
                "render": runtime.snapshot().get("render"), "aligned": coordinator.alignedData,
                "traces": aligned_surfaces(), "provider": images.requests, "warnings": warnings}))
        hidden_at_pending_visibility = sum(name == "aligned" for name in hidden)

        pixel_points = ((11, 11), (35, 26), (51, 11), (75, 26),
                        (11, 41), (35, 56), (51, 41), (75, 56), (95, 75))

        def samples():
            window.requestUpdate()
            QTest.qWait(30)
            app.processEvents()
            grabbed = window.grabWindow().convertToFormat(QImage.Format_RGBA8888)
            if grabbed.isNull():
                raise AssertionError("grabWindow returned a null image")
            bits = grabbed.bits()
            raw = np.frombuffer(bits, np.uint8).reshape(grabbed.height(), grabbed.bytesPerLine())
            array = raw[:, :grabbed.width() * 4].reshape(grabbed.height(), grabbed.width(), 4)
            return [[int(value) for value in array[y, x]] for x, y in pixel_points], [grabbed.width(), grabbed.height()]

        initial_samples, window_size = samples()
        initial_expected = [[230, 30, 40, 255], [0, 0, 0, 0],
                            [20, 200, 80, 255], [0, 0, 0, 0],
                            [30, 60, 220, 255], [0, 0, 0, 0],
                            [250, 190, 20, 255], [0, 0, 0, 0], [0, 0, 0, 0]]
        if initial_samples != initial_expected:
            raise AssertionError("pending visible pixels mismatch: " + repr({
                "actual": initial_samples, "expected": initial_expected, "size": window_size,
                "provider": images.requests, "warnings": warnings}))
        pending_grab_at = time.monotonic()  # Matching pixels have now been read and verified.

        # Do not call textureProvider until the ordinary ClientAligned Image has
        # completed its first real QML grab; the observer only reads scenegraph
        # state while afterSynchronizing is emitting on the scenegraph thread.
        route_image = window.findChild(QObject, "clientAlignedRouteImage")
        texture_phase = ["pending"]
        texture_observations = []
        texture_lifecycle = []
        texture_changes = []
        texture_errors = []
        active_texture_lifetimes = {}
        observed_life_ids = set()
        watched_life_ids = set()
        next_life_id = [1]
        provider_signal_connected = [False]

        def observe_scenegraph_texture():
            try:
                texture_provider = route_image.textureProvider()
                if texture_provider is None:
                    return
                if not provider_signal_connected[0]:
                    texture_provider.textureChanged.connect(
                        lambda *_args: texture_changes.append(texture_phase[0]), Qt.DirectConnection)
                    provider_signal_connected[0] = True
                texture = texture_provider.texture()
                if texture is None:
                    return
                pointer = int(shiboken6.getCppPointer(texture)[0])
                key = repr(texture.comparisonKey())
                size = texture.textureSize()
                class_name = str(texture.metaObject().className())
                is_atlas = bool(texture.isAtlasTexture())
                owned_by_python = bool(shiboken6.ownedByPython(texture))
                life_id = active_texture_lifetimes.get(pointer)
                if life_id is None:
                    life_id = next_life_id[0]
                    next_life_id[0] += 1
                    active_texture_lifetimes[pointer] = life_id
                    observed_life_ids.add(life_id)
                    texture_lifecycle.append(dict(event="firstObserved", sequence=len(texture_lifecycle) + 1,
                        lifeId=life_id, pointer=pointer, comparisonKey=key, className=class_name,
                        size=[size.width(), size.height()], isAtlasTexture=is_atlas,
                        ownedByPython=owned_by_python, phase=texture_phase[0]))
                state = dict(phase=texture_phase[0], pointer=pointer, comparisonKey=key,
                    className=class_name, size=[size.width(), size.height()], isAtlasTexture=is_atlas,
                    ownedByPython=owned_by_python, lifeId=life_id)
                if not texture_observations or any(texture_observations[-1].get(key) != value
                        for key, value in state.items() if key != "phase") or texture_observations[-1].get("phase") != state["phase"]:
                    texture_observations.append(state)
                if life_id not in watched_life_ids:
                    watched_life_ids.add(life_id)
                    def record_destroyed(*_args, pointer=pointer, key=key,
                                         class_name=class_name, life_id=life_id):
                        texture_lifecycle.append(dict(event="destroyed", sequence=len(texture_lifecycle) + 1,
                            lifeId=life_id, pointer=pointer, comparisonKey=key, className=class_name,
                            phase=texture_phase[0]))
                        if active_texture_lifetimes.get(pointer) == life_id:
                            active_texture_lifetimes.pop(pointer, None)
                    texture.destroyed.connect(record_destroyed, Qt.DirectConnection)
            except Exception as error:
                texture_errors.append(type(error).__name__ + ":" + str(error)[:180])

        window.afterSynchronizing.connect(observe_scenegraph_texture, Qt.DirectConnection)
        # Drive one frame after connecting. The callback itself only appends
        # scalar metadata; all waits, grabs and Qt event processing stay here.
        window.requestUpdate()
        QTest.qWait(15)
        app.processEvents()
        window.grabWindow()
        if not pump_until(lambda: any(row["phase"] == "pending" for row in texture_observations), 2.0):
            raise AssertionError("QSGTexture was not observable after first grab: " + repr({
                "observations": texture_observations, "errors": texture_errors,
                "providerRequests": images.requests}))

        pending = runtime.render_payload(runtime.snapshot()["render"]["token"])
        pending_token = pending["token"]
        pending_url = coordinator.alignedData["routeUrl"]
        pending_matrix = np.asarray(coordinator.alignedData["matrix"]).tolist()
        pending_pixels_ref = pending["pixels"]
        pending_poses = runtime._core.snapshot().get("poses")
        pending_group = runtime._group
        pending_gate = runtime._gate.selected_id
        pending_durable_map_id = runtime.snapshot().get("durableMapId", "")
        aligned_hide_before_release = (sum(name == "aligned" for name in hidden)
                                       - hidden_at_pending_visibility)
        pending_acks = list(ack_calls)
        pending_ack_success = next((call["result"].get("success") for call in pending_acks
                                    if call["token"] == pending_token), None)
        if pending_ack_success is not False:
            raise AssertionError("pending pixels must not register steady material: " + repr(pending_acks))
        if any(call["token"] == pending_token for call in coordinator_fixture.session.dispatchMaintenance.call_args_list):
            raise AssertionError("pending token queued a steady-memory write")

        texture_phase[0] = "complete" if verdict in ("corrected", "stale_matrix") else "revoking"
        released_at = time.monotonic()
        allow_review.set()
        worker.join(8.0)
        if worker.is_alive():
            raise AssertionError("capture worker did not finish after review release")
        fixture_fit_patch.stop()
        if worker_error:
            raise worker_error[0]

        final_token = ""
        final_url = ""
        final_samples = []
        final_ack_success = None
        corrected_grab_at = None
        revoke_elapsed_ms = None
        visible_after_revoke = None
        source_after_revoke = None
        aligned_token_after_revoke = None
        corrected_pixel_assertion_rejected = False
        stale_consumer_final_samples = []
        stale_pixel_assertion_message = ""
        consumer_final_matrix = []
        runtime_final_matrix = []
        corrected_matrix_expected = corrected_matrix.tolist()
        if verdict in ("corrected", "stale_matrix"):
            def has_final_pixels():
                return (coordinator.alignedData.get("reviewStatus") == "complete"
                    and coordinator.alignedData.get("token") != pending_token
                    and any(row.get("token") == coordinator.alignedData.get("token")
                            and row.get("materialReady") is True for row in aligned_surfaces()))
            if not pump_until(has_final_pixels):
                raise AssertionError("final token was not reported by the unchanged QML image: " + repr({
                    "aligned": coordinator.alignedData, "traces": aligned_surfaces(),
                    "acks": ack_calls, "provider": images.requests, "warnings": warnings}))
            final_samples, corrected_size = samples()
            expected_corrected = [[0, 0, 0, 0], [230, 30, 40, 255],
                                  [0, 0, 0, 0], [20, 200, 80, 255],
                                  [0, 0, 0, 0], [30, 60, 220, 255],
                                  [0, 0, 0, 0], [250, 190, 20, 255], [0, 0, 0, 0]]

            def require_corrected_pixel_samples(actual):
                if actual != expected_corrected:
                    raise AssertionError("corrected visible pixels mismatch: " + repr({
                        "actual": actual, "expected": expected_corrected, "size": corrected_size,
                        "aligned": coordinator.alignedData, "provider": images.requests,
                        "warnings": warnings}))

            if verdict == "stale_matrix":
                stale_consumer_final_samples = list(final_samples)
                try:
                    require_corrected_pixel_samples(final_samples)
                except AssertionError as error:
                    stale_pixel_assertion_message = str(error)
                    corrected_pixel_assertion_rejected = True
                    if final_samples != initial_expected:
                        raise AssertionError("stale-matrix control failed for an unexpected pixel result: "
                                             + repr(final_samples)) from error
                else:
                    raise AssertionError("corrected pixel assertion accepted the deliberately stale matrix")
                if not stale_matrix_applied:
                    raise AssertionError("negative control did not override the coordinator matrix")
            else:
                require_corrected_pixel_samples(final_samples)
                corrected_grab_at = time.monotonic()
            final_token = coordinator.alignedData["token"]
            final_url = coordinator.alignedData["routeUrl"]
            if not pump_until(lambda: any(row["phase"] == "complete" for row in texture_observations), 2.0):
                window.requestUpdate()
                QTest.qWait(15)
                app.processEvents()
                window.grabWindow()
            if not any(row["phase"] == "complete" for row in texture_observations):
                raise AssertionError("complete QSGTexture was not observed: " + repr({
                    "observations": texture_observations, "errors": texture_errors}))
            final_ack_success = next((call["result"].get("success") for call in ack_calls
                                      if call["token"] == final_token), None)
            if pending_pixels_ref is not runtime.render_payload(final_token)["pixels"]:
                raise AssertionError("review did not retain the original immutable route ndarray")
            if not coordinator_fixture.session.dispatchMaintenance.called:
                raise AssertionError("complete token was not queued for material-memory handling")
            if not any(call.args[0] == "remember_render_material" and call.args[1].get("renderToken") == final_token
                       for call in coordinator_fixture.session.dispatchMaintenance.call_args_list):
                raise AssertionError("maintenance queue did not receive the complete token")
            consumer_final_matrix = np.asarray(coordinator.alignedData["matrix"]).tolist()
            runtime_final_matrix = runtime.render_payload(final_token)["matrix"]
        else:
            if not pump_until(lambda: not window.isVisible() and not coordinator.alignedData
                              and not getattr(coordinator, "_last_render_token", None)):
                raise AssertionError("invalid review did not withdraw QML pixels: " + repr({
                    "visible": window.isVisible(), "aligned": coordinator.alignedData,
                    "lastToken": coordinator._last_render_token, "traces": aligned_surfaces(),
                    "provider": images.requests, "warnings": warnings}))
            visible_after_revoke = window.isVisible()
            source_after_revoke = window.findChild(QObject, "clientAlignedRouteImage").property("source").toString()
            aligned_token_after_revoke = str(coordinator.alignedData.get("token", ""))
            if visible_after_revoke or source_after_revoke or aligned_token_after_revoke:
                raise AssertionError("revocation did not clear the actual QML source and window")
            revoke_elapsed_ms = (time.monotonic() - released_at) * 1000.0

        route_publishes = [row for row in publishes if not row["key"].startswith("reference:")]
        if len(route_publishes) != (2 if verdict in ("corrected", "stale_matrix") else 1):
            raise AssertionError("unexpected route LiveImages.publish count: " + repr(
                [(row["key"], row["url"]) for row in publishes]))
        if len(images.requests) != 1 or images.requests[0].get("null"):
            raise AssertionError("QML did not consume exactly one stored image request: " + repr(images.requests))
        if verdict in ("corrected", "stale_matrix") and route_publishes[0]["url"] != route_publishes[1]["url"]:
            raise AssertionError("same-scope same-pixel content did not retain its published URL")
        if verdict in ("corrected", "stale_matrix") and not np.array_equal(route_publishes[0]["pixels"], route_publishes[1]["pixels"]):
            raise AssertionError("pending and complete publication pixel contents differ")
        if (pending_group is not None or pending_poses != solver_times.get("baselineCorePoses")
                or pending_gate != solver_times.get("baselineGateSelection")
                or pending_durable_map_id != solver_times.get("baselineDurableMapId")):
            raise AssertionError("preliminary fit changed group/gate/durable identity/pose state")
        aligned_hide_after_release = (sum(name == "aligned" for name in hidden)
                                      - hidden_at_pending_visibility)
        if aligned_hide_before_release != 0 or (verdict in ("corrected", "stale_matrix")
                and aligned_hide_after_release != aligned_hide_before_release):
            raise AssertionError("aligned window was hidden while review was pending")

        pending_texture = next((row for row in reversed(texture_observations)
                                if row["phase"] == "pending"), None)
        complete_texture = next((row for row in reversed(texture_observations)
                                 if row["phase"] == "complete"), None)
        if pending_texture is None:
            raise AssertionError("pending QSGTexture was not observed: " + repr(texture_observations))
        if pending_texture["ownedByPython"] is not False:
            raise AssertionError("observed QSGTexture is unexpectedly Python-owned: " + repr(pending_texture))
        if verdict in ("corrected", "stale_matrix") and (complete_texture is None
                or complete_texture["pointer"] != pending_texture["pointer"]
                or complete_texture["lifeId"] != pending_texture["lifeId"]
                or complete_texture["comparisonKey"] != pending_texture["comparisonKey"]):
            raise AssertionError("corrected pose changed the QSGTexture: " + repr(texture_observations))
        visible_through_review = window.isVisible() if verdict in ("corrected", "stale_matrix") else False
        destroyed_before_teardown_count = sum(row["event"] == "destroyed" for row in texture_lifecycle)
        if destroyed_before_teardown_count != 0:
            raise AssertionError("QSGTexture was destroyed before explicit scenegraph teardown: " + repr(texture_lifecycle))

        # Release the real QSG object through normal scenegraph teardown. The
        # observer never deletes or reparents a texture.
        texture_phase[0] = "teardown"
        window.hide()
        release_resources = getattr(window, "releaseResources", None)
        if callable(release_resources):
            release_resources()
        QTest.qWait(20)
        app.processEvents()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QTest.qWait(20)
        app.processEvents()
        destroyed_pointers = [row["pointer"] for row in texture_lifecycle if row["event"] == "destroyed"]
        destroyed_at_teardown_count = sum(row["event"] == "destroyed" and row["phase"] == "teardown"
                                           for row in texture_lifecycle)
        if destroyed_at_teardown_count != 1:
            raise AssertionError("expected one real QSGTexture destruction at teardown: " + repr(texture_lifecycle))
        if verdict == "invalid" and pending_texture["pointer"] not in destroyed_pointers:
            raise AssertionError("revoked QSGTexture destruction was not observed: " + repr({
                "pendingTexture": pending_texture, "lifecycle": texture_lifecycle,
                "observations": texture_observations, "textureChanged": texture_changes,
                "errors": texture_errors}))
        runtime.set_event_callback(None)
        coordinator_fixture.tearDown()
        coordinator_cleaned = True
        runtime.shutdown()
        coordinator_fixture.doCleanups()

        report = dict(case=verdict, backend="software/offscreen", capture="synthetic 1280x720",
            fit="controlled host.fit; actual _offer_usable_fit helper and runtime callback; synthetic residual evidence",
            peerReview="controlled blocking verdict", pendingVisible=True,
            pendingReviewStatus="pending", pendingToken=pending_token,
            finalReviewStatus=(coordinator.alignedData.get("reviewStatus", "")
                               if verdict in ("corrected", "stale_matrix") else "revoked"),
            finalToken=final_token, pendingUrl=pending_url, finalUrl=final_url,
            imagePublishCount=len(route_publishes), totalLiveImagesPublishCount=len(publishes),
            publishedPixelContentsEqual=(len(route_publishes) < 2 or np.array_equal(
                route_publishes[0]["pixels"], route_publishes[1]["pixels"])),
            imageProviderRequestCount=len(images.requests),
            qsgTextureFirstObserved=(pending_texture is not None),
            qsgTextureUniqueObservedCount=len(observed_life_ids),
            qsgTextureUniquePointerCount=len({row["pointer"] for row in texture_observations}),
            qsgTextureDestroyedCount=len(destroyed_pointers),
            qsgTextureDestroyedBeforeTeardownCount=destroyed_before_teardown_count,
            qsgTextureDestroyedAtTeardownCount=destroyed_at_teardown_count,
            qsgTextureCreateTextureCallObserved=False,
            qsgTextureComparisonKeyObserved=(pending_texture.get("comparisonKey") is not None),
            pendingTexture=pending_texture, completeTexture=complete_texture,
            qsgTextureLifecycle=texture_lifecycle, qsgTextureChanges=texture_changes,
            qsgTextureObservationErrors=texture_errors,
            qsgTextureOwnedByPython=pending_texture["ownedByPython"],
            pixelFormat="RGBA8888 via production LiveImages storage and QSGTexture",
            pixelPoints=[list(point) for point in pixel_points],
            pendingPixelSamples=initial_samples, expectedInitialSamples=initial_expected,
            finalPixelSamples=final_samples,
            expectedCorrectedSamples=(expected_corrected if verdict in ("corrected", "stale_matrix") else []),
            staleMatrixControlApplied=bool(stale_matrix_applied),
            correctedPixelAssertionRejected=corrected_pixel_assertion_rejected,
            stalePixelAssertionMessage=stale_pixel_assertion_message,
            staleConsumerFinalSamples=stale_consumer_final_samples,
            pendingMatrix=pending_matrix, consumerFinalMatrix=consumer_final_matrix,
            runtimeFinalMatrix=runtime_final_matrix, correctedMatrix=corrected_matrix_expected,
            windowSize=window_size, alignedHideCallsDuringReview=aligned_hide_before_release,
            alignedHideCallsAfterRelease=aligned_hide_after_release,
            alignedVisibleThroughReview=visible_through_review,
            materialAckTokens=[call["token"] for call in ack_calls],
            pendingMaterialAckSuccess=pending_ack_success, finalMaterialAckSuccess=final_ack_success,
            visibleAfterRevoke=visible_after_revoke, sourceAfterRevoke=source_after_revoke,
            alignedTokenAfterRevoke=aligned_token_after_revoke,
            pendingPosesEmpty=not bool(pending_poses), pendingGroupNone=pending_group is None,
            pendingGateUnselected=not bool(pending_gate), pendingGateUnchanged=(pending_gate == solver_times.get("baselineGateSelection")),
            pendingDurableMapId=pending_durable_map_id,
            pendingIdentityUnchanged=(pending_durable_map_id == solver_times.get("baselineDurableMapId")),
            imageProviderEvents=images.requests,
            hostOfferElapsedMs=solver_times.get("hostOfferElapsedMs"),
            productionSolverComputationMs=None,
            injectedToPendingGrabMs=(pending_grab_at - injected_at) * 1000.0,
            controlledReviewBlockMs=solver_times.get("reviewBlockMs"),
            reviewReleaseToCorrectedGrabMs=((corrected_grab_at - released_at) * 1000.0
                if corrected_grab_at is not None else None),
            reviewReleaseToRevokeMs=revoke_elapsed_ms,
            pendingPixelTimeIncludes="synthetic dispatch injection through matching grabWindow samples",
            correctionPixelTimeIncludes="release of controlled peer review through matching grabWindow samples",
            timingLimit="solver and peer verdict are substituted; measured times are synthetic path only, not 500/300ms acceptance",
            warnings=warnings, workerResult=worker_result)
        print(PROBE_PREFIX + json.dumps(report, separators=(",", ":"), ensure_ascii=False))
        sys.stdout.flush()
    finally:
        if runtime is not None:
            try:
                runtime.set_event_callback(None)
                runtime.shutdown()
            except Exception:
                pass
        if engine is not None:
            try:
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
            except Exception:
                pass
        if coordinator_fixture is not None and not coordinator_cleaned:
            try:
                coordinator_fixture.tearDown()
                coordinator_fixture.doCleanups()
            except Exception:
                pass
        fixture.doCleanups()


def fixture_engine_difficult(engine):
    # This branch is unreachable for the selected nightmare runtime, but the
    # engine factory contract is explicit and never loads external resources.
    return engine


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        _probe(sys.argv[2])
    else:
        unittest.main()
