"""Factory isolation and resource-failure contracts; no installed assets needed."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

from client_engine import EngineAdapter
from client_providers import build_engine_factories, SharedReferenceProvider, ProviderUnavailable


class ClientProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_constructor_is_lazy_and_no_heavy_or_ui_imports(self):
        code = ("import sys; from client_providers import build_engine_factories; "
                "f=build_engine_factories('does-not-exist'); "
                "assert not any(x['prepared'] for x in f.capabilities().values()); "
                "assert not ({'tkinter','identityv_daily_assistant','identityv_live_mvp','cv2','numpy'} & set(sys.modules))")
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                       capture_output=True, text=True, check=True)

    def test_missing_contracts_report_exact_mode_and_root_without_fallback(self):
        factories = build_engine_factories(self.root)
        engine = EngineAdapter(engine_factories=factories, allowed_read_root=self.root)
        self.assertNotIn("rank_saved_frame", engine.snapshot()["availableCommands"])
        self.assertIn("inspect_engine_resources", engine.snapshot()["availableCommands"])
        result = engine.dispatch("inspect_engine_resources")
        self.assertTrue(result["success"])
        reports = engine.snapshot()["savedFrameCapabilities"]
        self.assertIn("missing-resource:references/nightmare-map-pack.json", reports["nightmare"]["reason"])
        self.assertIn("missing-resource:difficult-native-manifest.json", reports["difficult"]["reason"])
        self.assertNotIn("rank_saved_frame", engine.snapshot()["availableCommands"])
        self.assertFalse(engine.snapshot()["liveEngineAvailable"])

    def test_source_root_and_escaping_paths_rejected_before_pack_construction(self):
        for row, reason in ((dict(resourceBase="source-root"), "must bind resource-root"),
                            (dict(resourceBase="resource-root", oneFlow="../escape.png", layers={}), "escapes explicit root")):
            self.write("references/nightmare-map-pack.json", dict(maps=[row]))
            result = build_engine_factories(self.root).prepare()["nightmare"]
            self.assertFalse(result["available"])
            self.assertIn(reason, result["reason"])

    def test_capability_reads_do_not_wait_for_resource_build(self):
        factories = build_engine_factories(self.root)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def build(mode):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timed out")
            return SimpleNamespace(pack=SimpleNamespace(maps=[]), identity_floors=("1",))
        factories._build = build
        worker = threading.Thread(target=lambda: factories["nightmare"]())
        worker.start()
        self.assertTrue(entered.wait(5))
        output = []
        reader = threading.Thread(target=lambda: (output.append(factories.capabilities()), finished.set()))
        reader.start()
        try:
            self.assertTrue(finished.wait(1), "snapshot waited on a heavy resource build")
            self.assertFalse(output[0]["nightmare"]["prepared"])
        finally:
            release.set(); worker.join(5); reader.join(5)
        self.assertTrue(factories.capabilities()["nightmare"]["available"])

    def test_rank_capability_only_ready_for_prepared_mode(self):
        factories = build_engine_factories(self.root)
        factories._build = lambda mode: SimpleNamespace(pack=SimpleNamespace(maps=[]), identity_floors=("1",))
        engine = EngineAdapter(engine_factories=factories)
        factories["nightmare"]()
        self.assertIn("rank_saved_frame", engine.snapshot()["availableCommands"])
        engine.dispatch("set_game_mode", dict(mode="difficult"))
        self.assertNotIn("rank_saved_frame", engine.snapshot()["availableCommands"])
        self.assertTrue(engine.snapshot()["savedFrameCapabilities"]["nightmare"]["available"])

    def test_resource_invalidation_discards_late_build_without_blocking(self):
        factories = build_engine_factories(self.root)
        entered, release = threading.Event(), threading.Event()
        def build(mode):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return SimpleNamespace(pack=SimpleNamespace(maps=[]), identity_floors=("1",))
        factories._build = build
        errors = []
        def run():
            try:
                factories["nightmare"]()
            except ProviderUnavailable as error:
                errors.append(str(error))
        worker = threading.Thread(target=run)
        worker.start(); self.assertTrue(entered.wait(5))
        factories.invalidate()
        self.assertFalse(factories.capabilities()["nightmare"]["prepared"])
        release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["resource-context-superseded"])
        self.assertFalse(factories.capabilities()["nightmare"]["available"])

    def test_typed_reference_reuses_original_loader_and_rejects_version_mismatch(self):
        import numpy as np
        from shared_structure_reference import SharedStructureResourceStore
        path = self.root / "structure.npz"
        binding = dict(profile="geometry", mapId="fixture", variant="variant-01", floor="1")
        canvas = dict(shapeHW=[12, 16], width=16, height=12, floor="1", coordinateSpace="fixture-pixels")
        expected = dict(structureVersionKey="fixture-version", sourceVersion="fixture-source", baseSnapshot="fixture-base")
        metadata = dict(sourceKind="game-rendered-shared-structure", binding=binding, canvas=canvas,
                        roles=dict(clean="clean", validation="validation"), **expected)
        np.savez_compressed(path, metadata=np.asarray(json.dumps(metadata)),
                            clean=np.ones((12, 16), np.uint8), validation=np.ones((12, 16), np.uint8))
        index = self.write("index.json", dict(schemaVersion=1, resourceKind="shared-structure", entries=[
            dict(binding, resourcePath="structure.npz", **expected)]))
        resources = SharedStructureResourceStore(index, self.root)
        versions = {("geometry", "fixture", "variant-01", "1"): dict(
            expected=expected, binding=binding, path=path, canvas=canvas, sharedError=None)}
        provider = SharedReferenceProvider(self.root, resources, versions)
        reference = provider("display-choice", "fixture", "1")
        self.assertIsNotNone(reference)
        self.assertEqual(reference.metadata["binding"], binding)
        versions[next(iter(versions))]["expected"] = dict(expected, sourceVersion="wrong-version")
        provider = SharedReferenceProvider(self.root, resources, versions)
        self.assertIsNone(provider("geometry", "fixture", "1"))

    def test_observation_and_entry_preprocessing_stay_tk_free(self):
        code = ("import sys, numpy as np, client_providers; from difficult_entry_identity import entry_geometry; "
                "from single_map_structure import current_geometry; "
                "a=np.zeros((128,128,3),np.uint8); a[32:96,32:96]=120; "
                "assert entry_geometry(a) is not None; current_geometry(a); "
                "assert not ({'tkinter','identityv_daily_assistant','identityv_live_mvp'} & set(sys.modules))")
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                       capture_output=True, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
