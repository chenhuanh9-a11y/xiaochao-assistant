"""Static reference placement: mixed DPI, provenance and reachable work areas."""
import math
import threading
import time
import unittest
from types import SimpleNamespace
import numpy as np
from PySide6.QtCore import QCoreApplication, QRect
from client_reference_presentation import (LEGACY_PHYSICAL, QT_LOGICAL,
    ReferencePreviewProvider, reference_position_record, restore_reference_geometry)


class Screen:
    def __init__(self, name, rect, available=None, dpr=1.):
        self._name, self._rect = name, QRect(*rect)
        self._available, self._dpr = QRect(*(available or rect)), dpr

    def name(self): return self._name
    def geometry(self): return self._rect
    def availableGeometry(self): return self._available
    def devicePixelRatio(self): return self._dpr


class ReferencePlacementTests(unittest.TestCase):
    def setUp(self):
        self.first = Screen("first", (0, 0, 2560, 1440), (0, 0, 2560, 1400))
        self.second = Screen("EDID friendly", (2560, 0, 1280, 720), (2560, 0, 1280, 680), 1.5)
        self.screens = [self.first, self.second]
        self.monitors = [dict(device="DISPLAY2", rect=[2560, 0, 4480, 1080])]

    def test_legacy_physical_projection_keeps_second_monitor_origin(self):
        p = restore_reference_geometry([2860, 300], [200, 100], self.screens,
            position_space=LEGACY_PHYSICAL, native_monitors=self.monitors)
        self.assertIs(p.screen, self.second)
        self.assertEqual(p.position, (2760., 200.))
        self.assertEqual(p.diagnostics["conversion"], "verified-physical-to-logical")

    def test_marked_qt_position_is_never_divided_again(self):
        record = reference_position_record([2760, 200])
        p = restore_reference_geometry(record, [200, 100], self.screens, native_monitors=self.monitors)
        self.assertEqual(record["space"], QT_LOGICAL)
        self.assertEqual(p.position, (2760., 200.))
        self.assertEqual(p.diagnostics["conversion"], "none")

    def test_unmarked_historical_qt_list_is_not_guessed_as_physical(self):
        p = restore_reference_geometry([2760, 200], [200, 100], self.screens,
            native_monitors=self.monitors)
        self.assertEqual(p.position, (2760., 200.))
        self.assertEqual(p.diagnostics["conversion"], "ambiguous-clamp-only")

    def test_removed_monitor_position_is_reachable_without_rewriting_input(self):
        position = [9000, -500]
        p = restore_reference_geometry(position, [520, 300], [self.first])
        self.assertEqual(p.position, (2035., 5.))
        self.assertEqual(position, [9000, -500])

    def test_oversize_and_missing_native_mapping_still_fit_visible_work_area(self):
        p = restore_reference_geometry([2800, 100], [2000, 1000], [self.second],
            position_space=LEGACY_PHYSICAL)
        self.assertEqual(p.diagnostics["conversion"], "unverified-clamp-only")
        self.assertAlmostEqual(p.width / p.height, 2.)
        self.assertGreaterEqual(p.x, 2565)
        self.assertLessEqual(p.x + p.width, 3835)
        self.assertLessEqual(p.y + p.height, 675)

    def test_invalid_numbers_and_missing_screens_cannot_authorize_placement(self):
        for position, size, screens in [([math.nan, 1], [10, 10], self.screens),
                                       ([1, 2], [math.inf, 10], self.screens),
                                       ([1, 2], [10, 10], [])]:
            with self.subTest(position=position, size=size):
                with self.assertRaises(ValueError):
                    restore_reference_geometry(position, size, screens)


class ReferencePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        maps = [SimpleNamespace(id=x, layers={"1": "installed", "2": "installed"}) for x in ("a", "b")]
        self.engine = SimpleNamespace(pack=SimpleNamespace(maps=maps, by_id={x.id: x for x in maps}),
                                      identity_floors=("1", "2"))
        self.factory_threads = []

    def provider(self, loader):
        def factory():
            self.factory_threads.append(threading.get_ident())
            return self.engine
        p = ReferencePreviewProvider({"nightmare": factory}, display_loader=loader)
        self.addCleanup(lambda: (p.close(), p.pool.waitForDone(1500)))
        return p

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), "bounded preview worker deadline")

    @staticmethod
    def layer(*args, **kwargs):
        return dict(pixels=np.full((8, 10, 4), [11, 22, 33, 255], np.uint8), resourceIdentity="installed")

    def test_installed_display_is_worker_only_and_never_an_authorization(self):
        p = self.provider(self.layer); ready = []
        p.ready.connect(lambda payload: ready.append((threading.get_ident(), payload)))
        p.request("nightmare", "selected-author", floor="2", context={"libraryEpoch": 3})
        self.wait(lambda: bool(ready))
        thread, row = ready[0]
        self.assertEqual(thread, threading.get_ident())
        self.assertNotEqual(self.factory_threads[0], threading.get_ident())
        self.assertEqual((row["mapId"], row["floor"]), ("a", "2"))
        self.assertEqual(row["pixels"][0, 0].tolist(), [33, 22, 11, 255])
        self.assertTrue(row["previewOnly"])
        self.assertFalse(row["referenceAuthorized"] or row["liveAuthorization"] or p.busy)
        self.assertNotIn("pixels", p.snapshot)

    def test_replaced_context_cannot_publish_late_preview(self):
        entered, release = threading.Event(), threading.Event()
        def load(engine, map_id, *args, **kwargs):
            if map_id == "a":
                entered.set()
                if not release.wait(1):
                    raise RuntimeError("test release deadline")
            return self.layer()
        p = self.provider(load); ready = []; p.ready.connect(ready.append)
        p.request("nightmare", "author", map_id="a", context="old")
        self.assertTrue(entered.wait(1))
        p.request("nightmare", "author", map_id="b", context="new")
        release.set(); self.wait(lambda: bool(ready))
        self.assertEqual([(x["mapId"], x["context"]) for x in ready], [("b", "new")])

    def test_bad_resource_reports_failure_without_preview_pixels(self):
        def broken(*args, **kwargs): raise ValueError("missing installed layer")
        p = self.provider(broken); failed = []; ready = []
        p.failed.connect(failed.append); p.ready.connect(ready.append)
        p.request("nightmare", "author")
        self.wait(lambda: bool(failed))
        self.assertFalse(ready or p.busy)
        self.assertIn("missing installed layer", failed[0])

    def test_close_drops_late_result_and_worker_drains(self):
        entered, release = threading.Event(), threading.Event()
        def load(*args, **kwargs):
            entered.set(); release.wait(1); return self.layer()
        p = self.provider(load); ready = []; p.ready.connect(ready.append)
        p.request("nightmare", "author"); self.assertTrue(entered.wait(1))
        p.close(); release.set(); self.assertTrue(p.pool.waitForDone(1000))
        self.app.processEvents(); self.assertFalse(ready or p.busy)


if __name__ == "__main__":
    unittest.main()
