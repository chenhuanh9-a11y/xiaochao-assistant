"""Actual owned QML surface, synthetic grant/pixels, no native game/capture."""
import json
from copy import deepcopy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QObject, Property, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from client_coordinator import LiveImages
from test_client_coordinator import CoordinatorTests
import test_client_live_runtime as runtime_fixtures
from client_live_runtime import LiveRuntime

APP = QGuiApplication.instance() or QGuiApplication([])


class Settings(QObject):
    values = Property("QVariantMap", lambda self: dict(sheetOpacity=.7), constant=True)


class AlignedTransformProbe(unittest.TestCase):
    def test_real_qml_moves_bounds_with_same_material_url_and_no_reload(self):
        self.run_probe()

    def test_actual_runtime_pointer_to_qml_keeps_material_and_rejects_stale_floor_scope(self):
        producer = runtime_fixtures.LiveRuntimeContracts()
        producer.setUp()
        self.addCleanup(producer.doCleanups)
        # Test-only authority boundary: fake capture/current-context oracle and
        # controlled solver/core grant. No actual foreground or pixels are read.
        producer.capture.generation = 1
        producer.capture.is_current = Mock(return_value=True)
        producer.runtime = LiveRuntime(engine_factories={"nightmare": lambda: producer.engine},
            capture_provider=producer.capture, evidence_kind="live-client-capture", clock=lambda: 1)
        producer.begin_and_capture()
        self.run_probe(producer.runtime, queued_pointer=True)

    def run_probe(self, actual_runtime=None, queued_pointer=False):
        CoordinatorTests.setUpClass()
        fixture = CoordinatorTests("test_same_token_transform_revision_preserves_material_and_rejects_scope_change")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        coordinator = fixture.coordinator
        if actual_runtime is not None:
            fixture.runtime = coordinator.runtime = actual_runtime
            actual_runtime.set_event_callback(coordinator.runtimeEvent.emit)
            fixture.session.snapshot = actual_runtime.snapshot()
            coordinator._applied_context = (fixture.session.snapshot["mode"], fixture.session.snapshot["profileId"])
        images = LiveImages()
        publish = Mock(wraps=images.publish)
        images.publish = publish
        coordinator.images = images
        coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
        if actual_runtime is None:
            fixture.runtime.reference_payload = Mock(return_value=None)
            fixture.runtime.acknowledge_render_material = Mock()
        else:
            fixture.runtime.acknowledge_render_material = Mock(wraps=actual_runtime.acknowledge_render_material)
        logs, warnings = [], []
        coordinator.traceReported.connect(logs.append)
        engine = QQmlApplicationEngine()
        engine.addImageProvider("live", images)
        settings = Settings()
        engine.rootContext().setContextProperty("clientCoordinator", coordinator)
        engine.rootContext().setContextProperty("settingsService", settings)
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientAligned.qml"))))
        self.assertEqual(len(engine.rootObjects()), 1)
        window = engine.rootObjects()[0]
        self.addCleanup(lambda engine=engine: engine.rootObjects()[0].hide())
        row = dict(token="synthetic-drag-material", liveAuthorization=True,
            matrix=[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]], transformRevision=0,
            routeWidth=100, routeHeight=80, clientWidth=800, clientHeight=600,
            originX=0, originY=0, resourceIdentity="synthetic-only", mapId="fixture", floor="1",
            roundId=1, cycleId=1, frameId="1:1", generation=1,
            pixels=np.full((80,100,4), 255, np.uint8))
        if actual_runtime is None:
            fixture.runtime.render_payload.return_value = row
        else:
            row = actual_runtime.render_payload(actual_runtime.snapshot()["render"]["token"])
        source_token, source_pixels = row["token"], row["pixels"]
        coordinator._present_runtime_state(dict(render=row), trigger="synthetic-initial")
        window.show()
        def surfaces():
            return [r["window"] for r in logs if r.get("stage") == "surface-state" and r.get("window", {}).get("name") == "aligned"]
        deadline = time.monotonic() + 2.
        while time.monotonic() < deadline and not any(r.get("materialReady") for r in surfaces()):
            APP.processEvents(); QTest.qWait(2)
        self.assertTrue(any(r.get("materialReady") for r in surfaces()), warnings)
        def pixel_bounds():
            window.requestUpdate()
            QTest.qWait(35)
            captured = window.grabWindow().convertToFormat(QImage.Format_RGBA8888)
            self.assertFalse(captured.isNull())
            pixels = np.frombuffer(captured.bits(), np.uint8).reshape(captured.height(), captured.bytesPerLine())
            alpha = pixels[:, :captured.width()*4].reshape(captured.height(), captured.width(), 4)[:,:,3]
            yy, xx = np.where(alpha > 0)
            self.assertGreater(len(xx), 0)
            return [int(xx.min()), int(yy.min()), int(xx.max()), int(yy.max())]
        before_pixels = pixel_bounds()
        before = surfaces()[-1]["mappedBounds"]
        image = window.findChild(QObject, "clientAlignedRouteImage")
        url = image.property("source").toString()
        initial_ack = fixture.runtime.acknowledge_render_material.call_count
        poses_before = deepcopy(actual_runtime._core.snapshot()["poses"]) if actual_runtime is not None else None
        root_before = actual_runtime._group.root_pose.copy() if actual_runtime is not None else None
        logs.clear()
        context = (actual_runtime.game_floor_click_context() if actual_runtime is not None
                   else dict(epoch=1, clientBounds=[0,0,800,600], frameId="1:1"))
        if actual_runtime is None:
            fixture.runtime.game_floor_click_context.return_value = context
            fixture.runtime.observe_game_floor_click = Mock(return_value=dict(success=False, reason="floor-click-not-a-control"))
        current = {"row": row}
        if actual_runtime is None:
            fixture.runtime.render_payload.side_effect = lambda token: current["row"]
        def observe(event):
            if event["phase"] == "move":
                dx, dy = event["totalDelta"]
                current["row"] = dict(current["row"], matrix=[[1.,0.,dx],[0.,1.,dy],[0.,0.,1.]],
                    transformRevision=current["row"]["transformRevision"]+1,
                    transient=True, transientOffset=[dx,dy])
            return dict(success=True)
        if actual_runtime is None:
            fixture.runtime.observe_game_drag = Mock(side_effect=observe)
        else:
            fixture.runtime.observe_game_drag = Mock(wraps=actual_runtime.observe_game_drag)
        down = dict(phase="down", gestureId=1, sequence=1, hwnd=123, x=300, y=300,
            context=context, createdAt=actual_runtime._monotonic() if actual_runtime is not None else 10.)
        source = None
        if queued_pointer:
            from client_native_keys import PassThroughKeyboardSource
            source = PassThroughKeyboardSource()
            source.set_pointer_context_provider(lambda: actual_runtime.game_floor_click_context())
            source.update_pointer_target(123)
            def drain():
                for event in source.take_pointer_events():
                    fixture.input.gamePointerEvent.emit(event)
            source.pointerAvailable.connect(drain, Qt.QueuedConnection)
            # Use the actual Win32 MSG contract. A namespace with `hwnd`
            # previously hid a production hWnd mismatch before packet decoding.
            from ctypes import wintypes as W
            from client_native_mouse import RawMouseObserver
            from test_client_native_mouse import Native, Function
            raw_native = Native()
            raw_native.GetForegroundWindow = Function(lambda: 123)
            raw_observer = RawMouseObserver(source._receive_pointer, lambda hwnd: hwnd == 123,
                user=raw_native, clock=actual_runtime._monotonic, ticks=lambda: 1000)
            raw_observer.start()
            self.addCleanup(raw_observer.stop)
        def deliver(event):
            if source is None:
                fixture.input.gamePointerEvent.emit(event)
            else:
                raw_native.mouse.buttonUnion.buttons.flags = {"down": 1, "move": 0, "up": 2}[event["phase"]]
                raw_native.mouse.lastX = int(event["phase"] == "move")
                self.assertTrue(raw_observer.handle(W.MSG(hWnd=321, message=0xff,
                    wParam=1, lParam=999, time=1000, pt=W.POINT(event["x"], event["y"]))))
        deliver(down)
        APP.processEvents()
        for revision in range(1, 6):
            # Delay the consumer deliberately: a high-rate burst must compact,
            # not cancel the gesture before the real runtime/QML see it.
            for _ in range(200 if queued_pointer else 1):
                deliver(dict(down, phase="move", sequence=revision+1,
                    x=300+revision*7, y=300+revision*3, createdAt=10.+revision))
            APP.processEvents(); QTest.qWait(2)
        deliver(dict(down, phase="up", sequence=7, x=335, y=315, createdAt=16.))
        APP.processEvents()
        after = surfaces()[-1]["mappedBounds"]
        after_pixels = pixel_bounds()
        self.assertEqual(image.property("source").toString(), url)
        aligned_publishes = sum(call.args[0] == source_token for call in publish.call_args_list)
        self.assertEqual(aligned_publishes, 1)
        self.assertEqual(fixture.runtime.acknowledge_render_material.call_count, initial_ack)
        self.assertFalse(any(r.get("imageStatus") == "loading" for r in surfaces()))
        self.assertAlmostEqual(after[0]-before[0], 35.)
        self.assertAlmostEqual(after[1]-before[1], 15.)
        self.assertEqual(np.subtract(after_pixels, before_pixels).tolist(), [35, 15, 35, 15])
        self.assertEqual(warnings, [])
        if actual_runtime is not None:
            produced = actual_runtime.render_payload(source_token)
            self.assertEqual(produced["transformRevision"], 5)
            self.assertIs(produced["pixels"], source_pixels)
            self.assertEqual(actual_runtime._core.snapshot()["poses"], poses_before)
            np.testing.assert_allclose(actual_runtime._group.root_pose, root_before)
            self.assertEqual([call.args[0]["phase"] for call in fixture.runtime.observe_game_drag.call_args_list],
                             ["down", "move", "move", "move", "move", "move", "up"])
            bad = dict(context, floor="2", frameId="wrong-floor-frame")
            rejected = actual_runtime.observe_game_drag(dict(phase="down", gestureId=2,
                pointerSequence=8, context=bad, createdAt=actual_runtime._monotonic()))
            self.assertFalse(rejected["success"])
            with patch.object(actual_runtime, "_floor", "2"):
                rejected_floor = actual_runtime.observe_game_drag(dict(phase="down", gestureId=3,
                    pointerSequence=9, context=context, createdAt=actual_runtime._monotonic()))
            self.assertFalse(rejected_floor["success"])
            self.assertEqual(actual_runtime._render["matrix"], produced["matrix"])
        print(json.dumps(dict(kind="actual-runtime-owned-qml-controlled-grant" if actual_runtime is not None else "owned-qml-synthetic-grant",
            initialBounds=before, finalBounds=after, stableUrl=url, imagePublishes=aligned_publishes,
            initialPixelBounds=before_pixels, finalPixelBounds=after_pixels,
            queuedPointerPackets=1002 if queued_pointer else 0, moveLoadingEvents=0, newMaterialAcks=0)))


if __name__ == "__main__":
    unittest.main()
