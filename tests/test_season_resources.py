"""Season fail-closed contracts with generated metadata and synthetic engines only."""
from pathlib import Path
import io
import json
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "app", ROOT / "experiments/qt_quick"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from client_providers import EngineFactories, ProviderUnavailable
from client_engine import EngineAdapter
from client_live_runtime import LiveRuntime
from client_custom_display import CustomGalleryDisplay
from star_map_import import (ImportRejected, inspect_package, install_package,
                             install_author_package_live, author_profiles_for_mode, _validate, MANIFEST)


class SeasonResourceTests(unittest.TestCase):
    def test_context_capabilities_are_distinct_and_s1_never_builds(self):
        factories = EngineFactories("/nonexistent-fixture-root")
        with patch.object(factories, "_build") as build:
            for mode in factories.MODES:
                with self.assertRaisesRegex(ProviderUnavailable, "season-resources-unavailable"):
                    factories.get_engine(mode, "s1")
            factories.prepare(season="s1")
            build.assert_not_called()
        self.assertEqual(set(factories.context_capabilities()),
                         {f"{season}:{mode}" for season in ("s0", "s1") for mode in factories.MODES})
        self.assertFalse(factories.capabilities()["nightmare"]["prepared"])
        self.assertTrue(factories.capabilities("s1")["nightmare"]["prepared"])

    def test_s0_cache_compatibility_and_invalidation_do_not_unlock_s1(self):
        factories = EngineFactories("/nonexistent-fixture-root")
        engine = SimpleNamespace(pack=SimpleNamespace(maps=[]), identity_floors=("1",))
        with patch.object(factories, "_build", return_value=engine) as build:
            self.assertIs(factories["nightmare"](), factories.get_engine("nightmare", "s0"))
            self.assertEqual(build.call_count, 1)
            self.assertEqual(set(factories._engines), {"s0:nightmare"})
            factories.invalidate()
            with self.assertRaises(ProviderUnavailable):
                factories.get_engine("nightmare", "s1")
            self.assertEqual(build.call_count, 1)

    def test_saved_adapter_s1_never_uses_legacy_factory(self):
        factory = Mock()
        adapter = EngineAdapter(engine_factories={"nightmare": factory})
        result = adapter.dispatch("set_context", {"season": "s1", "mode": "nightmare"})
        self.assertTrue(result["success"])
        self.assertEqual(result["contextKey"], "s1:nightmare")
        self.assertFalse(adapter.snapshot()["savedFrameEngineAvailable"])
        result = adapter.dispatch("rank_saved_frame", {"path": "/not-read"})
        self.assertFalse(result["success"])
        self.assertTrue(result["unavailable"])
        factory.assert_not_called()

    def test_live_same_difficulty_new_season_revokes_engine_and_publishes_context(self):
        runtime = LiveRuntime(engine_factories={})
        runtime._engine = object()
        epoch = runtime._epoch
        with patch.object(runtime, "_reproject_route_display") as reproject:
            result = runtime.dispatch("set_context", {"season": "s1", "mode": "nightmare",
                                                       "profileId": "another-author"})
            reproject.assert_not_called()
        self.assertTrue(result["success"])
        self.assertGreater(runtime._epoch, epoch)
        self.assertIsNone(runtime._engine)
        self.assertEqual(result["resultContext"]["season"], "s1")
        self.assertEqual(runtime.snapshot()["contextKey"], "s1:nightmare")
        self.assertFalse(runtime.snapshot()["liveEngineAvailable"])
        self.assertEqual(runtime.manual_map_catalog()["season"], "s1")
        runtime.dispatch("set_route_profile", {"profileId": "shared-structure"})
        self.assertEqual(runtime.snapshot()["season"], "s1")

    def test_convenience_season_change_does_not_inherit_author(self):
        for boundary in (LiveRuntime(engine_factories={}), EngineAdapter(engine_factories={})):
            boundary.dispatch("set_route_profile", {"profileId": "s0-author"})
            self.assertEqual(boundary.snapshot()["profileId"], "s0-author")
            boundary.dispatch("set_season", {"season": "s0"})
            self.assertEqual(boundary.snapshot()["profileId"], "s0-author")
            boundary.dispatch("set_season", {"season": "s1"})
            self.assertEqual(boundary.snapshot()["profileId"], "")
            boundary.dispatch("set_route_profile", {"profileId": "target-explicit"})
            boundary.dispatch("set_context", {"mode": "nightmare"})
            self.assertEqual(boundary.snapshot()["season"], "s1")
            self.assertEqual(boundary.snapshot()["profileId"], "target-explicit")
            boundary.dispatch("set_context", {"season": "s0", "profileId": "s0-explicit"})
            self.assertEqual(boundary.snapshot()["profileId"], "s0-explicit")

    def test_nested_offline_season_change_clears_only_implicit_author(self):
        adapter = EngineAdapter(engine_factories={})
        adapter.dispatch("set_route_profile", {"profileId": "s0-author"})
        adapter.dispatch("set_context", {"context": {"season": "s1"}})
        self.assertEqual(adapter.snapshot()["profileId"], "")
        adapter.dispatch("set_context", {"context": {"season": "s0", "profileId": "explicit"}})
        self.assertEqual(adapter.snapshot()["profileId"], "explicit")

    def test_precancelled_context_mutators_preserve_actual_state(self):
        for boundary in (LiveRuntime(engine_factories={}), EngineAdapter(engine_factories={})):
            for action, payload in (("set_context", {"season": "s1", "profileId": "target"}),
                                    ("set_season", {"season": "s1"}),
                                    ("set_game_mode", {"mode": "difficult"}),
                                    ("set_route_profile", {"profileId": "target"})):
                before = boundary.snapshot()
                revision = getattr(boundary, "_epoch", getattr(boundary, "_revision", None))
                cancelled = threading.Event()
                cancelled.set()
                result = boundary.dispatch(action, payload, cancelled)
                self.assertTrue(result["cancelled"])
                self.assertFalse(result["success"])
                after = boundary.snapshot()
                for key in ("season", "mode", "profileId"):
                    self.assertEqual(after[key], before[key])
                self.assertEqual(getattr(boundary, "_epoch", getattr(boundary, "_revision", None)), revision)

    def test_cancelled_queued_mutator_cannot_change_context_after_lock_wait(self):
        class PausedLock:
            def __init__(self, lock, pause_entry):
                self.lock, self.pause_entry, self.entries = lock, pause_entry, 0
                self.waiting, self.release = threading.Event(), threading.Event()
            def __enter__(self):
                self.entries += 1
                if self.entries == self.pause_entry:
                    self.waiting.set()
                    if not self.release.wait(5):
                        raise TimeoutError("test did not release queued context write")
                return self.lock.__enter__()
            def __exit__(self, *args):
                return self.lock.__exit__(*args)

        for boundary, pause_entry in ((LiveRuntime(engine_factories={}), 1),
                                      (EngineAdapter(engine_factories={}), 2)):
            before = boundary.snapshot()
            revision = getattr(boundary, "_epoch", getattr(boundary, "_revision", None))
            gate = PausedLock(boundary._lock, pause_entry)
            boundary._lock = gate
            cancelled, result = threading.Event(), {}
            worker = threading.Thread(target=lambda: result.update(boundary.dispatch(
                "set_context", {"season": "s1", "profileId": "target"}, cancelled)))
            worker.start()
            try:
                self.assertTrue(gate.waiting.wait(5))
                cancelled.set()
            finally:
                gate.release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result["cancelled"])
            after = boundary.snapshot()
            for key in ("season", "mode", "profileId"):
                self.assertEqual(after[key], before[key])
            self.assertEqual(getattr(boundary, "_epoch", getattr(boundary, "_revision", None)), revision)

    def test_cancelled_live_end_round_still_closes_round(self):
        runtime = LiveRuntime(engine_factories={})
        runtime._active = True
        cancelled = threading.Event()
        cancelled.set()
        result = runtime.dispatch("end_round", {}, cancelled)
        self.assertTrue(result["success"])
        self.assertFalse(runtime.snapshot()["roundActive"])

    def test_s1_live_start_is_unavailable_without_invoking_s0_or_capture(self):
        factory, capture = Mock(), Mock()
        runtime = LiveRuntime(engine_factories={"nightmare": factory}, capture_provider=capture)
        runtime.dispatch("set_context", {"season": "s1"})
        capture.reset_mock()
        result = runtime.dispatch("start_round")
        self.assertFalse(result["success"])
        self.assertTrue(result["unavailable"])
        self.assertIn("season-resources-unavailable", result["reason"])
        factory.assert_not_called()
        capture.capture.assert_not_called()
        capture.enable.assert_not_called()
        self.assertFalse(runtime.snapshot()["roundPreparing"])

    def test_active_and_preparing_rounds_reject_season_and_difficulty_changes(self):
        for active, preparing in ((True, False), (False, True)):
            for update in ({"season": "s1"}, {"mode": "difficult"}):
                runtime = LiveRuntime(engine_factories={})
                runtime._active, runtime._round_preparing = active, preparing
                result = runtime.dispatch("set_context", update)
                self.assertFalse(result["success"])
                self.assertEqual(runtime.snapshot()["season"], "s0")
                self.assertEqual(runtime.snapshot()["mode"], "nightmare")

    def test_custom_s1_never_reads_s0_gallery_or_base_library(self):
        service = Mock()
        display = CustomGalleryDisplay(service)
        self.assertFalse(display.eligibility("id", "nightmare", "s1")["allowed"])
        self.assertFalse(display.use_request("", "nightmare", "s1")["success"])
        self.assertIsNone(display.base_layer("map", "1", None, "nightmare", "s1"))
        self.assertIsNone(display.layer("custom:id", "map", "1", None, "nightmare", "s1"))
        self.assertEqual(service.mock_calls, [])
        self.assertEqual(author_profiles_for_mode("nightmare", "s1"), ())
        self.assertTrue(author_profiles_for_mode("nightmare"))

    def test_s1_import_target_rejected_before_archive_or_installation_io(self):
        for operation in (inspect_package, install_package, install_author_package_live):
            with self.assertRaisesRegex(ImportRejected, "unsupported-package-season"):
                operation("/does-not-exist.zip", "/does-not-exist-install", season="s1")

    def test_s1_manifest_cannot_be_imported_as_legacy_s0(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(MANIFEST, json.dumps({"season": "s1", "packageId": "nightmare-suju"}))
            archive.writestr("fixture.json", "{}")
        data.seek(0)
        with zipfile.ZipFile(data) as archive, tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ImportRejected, "unsupported-package-season"):
                _validate(archive, Path(root))


if __name__ == "__main__":
    unittest.main()
