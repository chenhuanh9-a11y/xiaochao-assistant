"""Native gallery adapter tests; fixtures are not geometry or game acceptance."""
from concurrent.futures import CancelledError
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "experiments/qt_quick")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from map_resource_context import load_browse_context
from s1_native_resources import MODE_LAYOUTS, S1_FLOORS, REQUIRED_INTERFACES
from season_map_resources import INDEX, SOURCE_VERSION, native_builtin_summaries


def fixture(root, mode):
    base = root / "seasons/s1" / mode
    files = []
    def write(name, data):
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    maps = []
    for mid in sorted(MODE_LAYOUTS[mode]):
        layers = {}
        for floor in S1_FLOORS:
            # Readability is tested separately against real producer PNGs.
            write(f"assets/game-native/{mid}/{floor}.png", b"fixture image")
            layers[floor] = f"references/entries/{mid}-{floor}.npz"
            write(layers[floor], b"fixture structure")
        maps.append(dict(id=mid, name=mid, sourceLayout=mid, sourceVersion=SOURCE_VERSION,
                         identityProfileId="shared-structure", layers=layers))
    interfaces = {name: f"references/{name}.json" for name in REQUIRED_INTERFACES}
    interfaces["identityPack"] = "references/map-pack.json"
    interfaces["identityEntries"] = "references/entries"
    for name, relative in interfaces.items():
        if name not in {"identityPack", "identityEntries"}:
            write(relative, b"{}")
    for name in ("entries.json", "side-entries.json", "entry-passports.json"):
        write(f"references/entries/{name}", b"{}")
    pack = dict(schemaVersion=1, season="s1", mode=mode, sourceVersion=SOURCE_VERSION,
                floors=list(S1_FLOORS), defaultEntryFloor=-1, maps=maps)
    write(interfaces["identityPack"], json.dumps(pack).encode())
    manifest = dict(schemaVersion=1, season="s1", mode=mode, sourceVersion=SOURCE_VERSION,
                    runtimeInterfaces=interfaces, capabilities=dict(sideEntries=False), files=files)
    path = base / "native-map-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


class NativeBuiltinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest, self.raw = fixture(self.root, "difficult")

    def context(self, mode="difficult"):
        return load_browse_context(self.root, mode, season="s1")

    def test_selected_mode_reuses_all_native_images_and_explicit_floor_mapping(self):
        context = self.context()
        self.assertFalse(context.error, context.error)
        profile, = context.profiles
        self.assertEqual((profile.map_count, profile.available_layers), (14, 56))
        self.assertIsNone(profile.package_id)
        self.assertIsNone(context.pack)
        for item in profile.maps:
            self.assertIn(item.id, MODE_LAYOUTS["difficult"])
            for key, path in item.variants[0].layers.items():
                floor = context.floor_metadata[key]["gameFloor"]
                self.assertEqual(path.name, f"{floor}.png")
                self.assertIn("seasons/s1/difficult/assets/game-native", path.as_posix())
                self.assertEqual(len(context.path_stamps[path]), 3)

    def test_other_mode_and_s0_never_fill_missing_native_mode(self):
        context = self.context("nightmare")
        self.assertTrue(context.error)
        self.assertFalse(context.profiles)
        self.assertNotIn("difficult/assets", context.error)
        self.assertTrue(load_browse_context(self.root, "nightmare", season="s0").error)

    def test_existing_broken_offline_index_never_falls_back_to_valid_native(self):
        path = self.root / INDEX
        path.parent.mkdir(parents=True)
        path.write_text("broken json", encoding="utf-8")
        self.assertTrue(self.context().error)
        self.assertFalse(self.context().profiles)

    def test_bad_hash_duplicate_inventory_and_context_binding_fail_closed(self):
        for mutate in (lambda d: d.update(mode="nightmare"),
                       lambda d: d.update(sourceVersion="old"),
                       lambda d: d["files"][0].update(sha256="0" * 64),
                       lambda d: d["files"].append(d["files"][0]),
                       lambda d: d["files"][0].update(path="../../../s0.png")):
            with self.subTest(mutate=mutate):
                raw = json.loads(json.dumps(self.raw))
                mutate(raw)
                self.manifest.write_text(json.dumps(raw), encoding="utf-8")
                self.assertTrue(self.context().error)
                self.assertFalse(self.context().profiles)

    def test_missing_native_png_fails_even_with_other_mode_present(self):
        fixture(self.root, "nightmare")
        image = next((self.manifest.parent / "assets").rglob("*.png"))
        image.unlink()
        self.assertTrue(self.context().error)
        self.assertFalse(self.context().profiles)
        self.assertFalse(self.context("nightmare").error)

    def test_builtin_status_uses_no_receipts_and_cannot_be_removed(self):
        rows = native_builtin_summaries(self.root)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertTrue(row["builtin"])
            self.assertFalse(row["canRemove"])
            self.assertNotIn("receipt", row)
        ready = next(row for row in rows if row["mode"] == "difficult")
        self.assertEqual((ready["status"], ready["mapCount"], ready["layerCount"]), ("bundled", 14, 56))
        self.assertEqual({row["status"] for row in rows}, {"bundled", "incomplete"})

    def test_cancellation_propagates(self):
        with self.assertRaises(CancelledError):
            load_browse_context(self.root, "difficult", season="s1", cancelled=lambda: True)

    def test_qt_rows_filter_each_registered_floor_without_changing_context(self):
        from PySide6.QtGui import QGuiApplication
        from backend import Backend
        app = QGuiApplication.instance() or QGuiApplication([])
        paths = SimpleNamespace(installation_root=self.root, resource_root=self.root)
        with patch("backend.runtime_paths", return_value=paths), patch.object(Backend, "refresh"):
            backend = Backend(self.root)
        try:
            backend._season, backend._mode = "s1", "difficult"
            backend.context = self.context()
            backend._profile = backend.context.profiles[0].profile_id
            for option in backend.floorOptions[1:]:
                backend._floor = option["value"]
                backend._apply_rows()
                self.assertEqual(len(backend.model.rows), 14)
                self.assertTrue(all(row["available"] and row["imageUrl"].startswith("file:")
                                    for row in backend.model.rows))
                self.assertEqual({row["label"] for row in backend.model.rows}, {option["name"]})
            self.assertEqual((backend.season, backend.mode, backend.profileId),
                             ("s1", "difficult", "s1-native-structure"))
        finally:
            backend.shutdown()

    def test_home_native_fallback_preserves_solver_profile_and_uses_b1_preview(self):
        from client_info import ClientInfoService
        rows = ClientInfoService._home_rows(self.root, "difficult", "shared-structure", "s1")
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["floorLabel"] for row in rows}, {"B1"})
        self.assertTrue(all(row["profileId"] == "shared-structure" and row["displayKind"] == "原生底图"
                            and row["imageUrl"].startswith("file:") for row in rows))
        self.assertFalse(any(row["structure"] for row in rows))
        self.assertEqual(ClientInfoService._home_rows(self.root, "difficulty2", "shared-structure", "s1"), [])
        fixture(self.root, "difficulty2")
        rows = ClientInfoService._home_rows(self.root, "difficulty2", "shared-structure", "s1")
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["mode"] for row in rows}, {"difficulty2"})

    def test_home_context_accepts_s1_difficulty2_and_rejects_late_worker(self):
        from client_info import ClientInfoService
        # A bounded service state exercises the actual queued receiver without
        # starting unrelated notice, diagnostics, or update workers.
        from PySide6.QtCore import QObject
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance() or QGuiApplication([])
        service = ClientInfoService.__new__(ClientInfoService)
        QObject.__init__(service)
        service._closed = False
        service._home_context = None
        service._home_preview_generation = 0
        service._home_previews = []
        service._home_preview_job = None
        service._home_preview_pending = False
        service._jobs = set()
        jobs = []
        service._pool = SimpleNamespace(start=jobs.append)
        service.sandbox = self.root
        service.paths = SimpleNamespace(resource_root=self.root)
        self.assertTrue(service.setHomeContext("difficult", "shared-structure", season="s1"))
        old = jobs[0]
        self.assertTrue(service.setHomeContext("difficulty2", "shared-structure", season="s1"))
        old.signals.done.emit([dict(imageUrl="stale")], None)
        app.processEvents()
        self.assertEqual(service.homePreviews, [])
        self.assertEqual(service._home_context, ("difficulty2", "shared-structure", "s1"))
        self.assertEqual(len(jobs), 2)
        jobs[1].signals.done.emit([dict(imageUrl="new")], None)
        app.processEvents()
        self.assertEqual(service.homePreviews, [dict(imageUrl="new")])
        self.assertFalse(service.setHomeContext("difficulty2", "shared-structure", season="s0"))
        service._closed = True



if __name__ == "__main__":
    unittest.main()
