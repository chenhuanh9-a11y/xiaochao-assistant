"""Actual owned QML surface, synthetic grant/pixels, no native game/capture."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import time
import unittest
from unittest.mock import Mock

import numpy as np
from PySide6.QtCore import QObject, Property, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from client_coordinator import LiveImages
from test_client_coordinator import CoordinatorTests

APP = QGuiApplication.instance() or QGuiApplication([])


class Settings(QObject):
    values = Property("QVariantMap", lambda self: dict(sheetOpacity=.7), constant=True)


class AlignedTransformProbe(unittest.TestCase):
    def test_real_qml_moves_bounds_with_same_material_url_and_no_reload(self):
        CoordinatorTests.setUpClass()
        fixture = CoordinatorTests("test_same_token_transform_revision_preserves_material_and_rejects_scope_change")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        coordinator = fixture.coordinator
        images = LiveImages()
        publish = Mock(wraps=images.publish)
        images.publish = publish
        coordinator.images = images
        coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
        fixture.runtime.reference_payload = Mock(return_value=None)
        fixture.runtime.acknowledge_render_material = Mock()
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
        fixture.runtime.render_payload.return_value = row
        coordinator._present_runtime_state(dict(render=row), trigger="synthetic-initial")
        window.show()
        def surfaces():
            return [r["window"] for r in logs if r.get("stage") == "surface-state" and r.get("window", {}).get("name") == "aligned"]
        deadline = time.monotonic() + 2.
        while time.monotonic() < deadline and not any(r.get("materialReady") for r in surfaces()):
            APP.processEvents(); QTest.qWait(2)
        self.assertTrue(any(r.get("materialReady") for r in surfaces()), warnings)
        before = surfaces()[-1]["mappedBounds"]
        image = window.findChild(QObject, "clientAlignedRouteImage")
        url = image.property("source").toString()
        initial_ack = fixture.runtime.acknowledge_render_material.call_count
        logs.clear()
        context = dict(epoch=1, clientBounds=[0,0,800,600], frameId="1:1")
        fixture.runtime.game_floor_click_context.return_value = context
        fixture.runtime.observe_game_floor_click = Mock(return_value=dict(success=False, reason="floor-click-not-a-control"))
        current = {"row": row}
        fixture.runtime.render_payload.side_effect = lambda token: current["row"]
        def observe(event):
            if event["phase"] == "move":
                dx, dy = event["totalDelta"]
                current["row"] = dict(current["row"], matrix=[[1.,0.,dx],[0.,1.,dy],[0.,0.,1.]],
                    transformRevision=current["row"]["transformRevision"]+1,
                    transient=True, transientOffset=[dx,dy])
            return dict(success=True)
        fixture.runtime.observe_game_drag = Mock(side_effect=observe)
        down = dict(phase="down", gestureId=1, sequence=1, hwnd=123, x=300, y=300,
            context=context, createdAt=10.)
        fixture.input.gamePointerEvent.emit(down)
        for revision in range(1, 6):
            fixture.input.gamePointerEvent.emit(dict(down, phase="move", sequence=revision+1,
                x=300+revision*7, y=300+revision*3, createdAt=10.+revision))
            APP.processEvents(); QTest.qWait(2)
        fixture.input.gamePointerEvent.emit(dict(down, phase="up", sequence=7, x=335, y=315, createdAt=16.))
        after = surfaces()[-1]["mappedBounds"]
        self.assertEqual(image.property("source").toString(), url)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(fixture.runtime.acknowledge_render_material.call_count, initial_ack)
        self.assertFalse(any(r.get("imageStatus") == "loading" for r in surfaces()))
        self.assertAlmostEqual(after[0]-before[0], 35.)
        self.assertAlmostEqual(after[1]-before[1], 15.)
        self.assertEqual(warnings, [])
        print(json.dumps(dict(kind="owned-qml-synthetic-grant", initialBounds=before, finalBounds=after,
            stableUrl=url, imagePublishes=publish.call_count, moveLoadingEvents=0, newMaterialAcks=0)))


if __name__ == "__main__":
    unittest.main()
