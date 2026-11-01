"""Shared bounded ZIP rules with synthetic text; no UI, capture or native calls."""
import ast
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
import diagnostic_export as export


class DiagnosticExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.live = self.root / "work/nightmare-live"
        self.live.mkdir(parents=True)

    def bundle(self, **kwargs):
        return export.export_safe_diagnostic_bundle(self.root, self.live,
            app_version="synthetic", build_fingerprint="TEST1234",
            process_integrity=dict(level="fixture"), package_metadata=dict(status="fixture"), **kwargs)

    def test_moved_pure_log_rules_are_ast_identical_to_original_entry(self):
        original = ast.parse((APP / "identityv_daily_assistant.py").read_text("utf-8-sig"))
        shared = ast.parse((APP / "diagnostic_export.py").read_text("utf-8"))
        names = {"safe_diagnostic_log_files", "_bounded_log_tail", "_redact_local_paths", "_event_log_session_id"}
        functions = lambda tree: {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in names:
            self.assertEqual(ast.dump(functions(original)[name]), ast.dump(functions(shared)[name]), name)

    def test_only_allowed_recent_logs_are_bounded_and_paths_redacted(self):
        (self.live / "events-fixture.jsonl").write_text(
            "old-data\n" * 30 + '{"time":"2026-10-30T12:00:00+00:00","path":"C:/private/player/file"}\n', encoding="utf-8")
        (self.live / "ui-settings.json").write_text("private-setting", encoding="utf-8")
        (self.live / "unrelated.log").write_text("private-log", encoding="utf-8")
        result = self.bundle(file_byte_limit=120, total_byte_limit=120)
        with zipfile.ZipFile(result["path"]) as archive:
            text = archive.read("logs/events-fixture.jsonl")
            self.assertNotIn(b"C:/private", text)
            self.assertIn(b"<local-path>", text)
            manifest = json.loads(archive.read("diagnostic-manifest.json"))
            self.assertTrue(manifest["logs"][0]["truncatedToRecentLines"])
            self.assertTrue(manifest["textOnly"])
            self.assertEqual(manifest["runtimeProcessIntegrity"], dict(level="fixture"))
            self.assertFalse(any("settings" in name or "unrelated" in name for name in archive.namelist()))

    def test_selected_zip_has_redacted_summary_and_refuses_overwrite(self):
        destination = self.root / "chosen.zip"
        self.bundle(destination=destination, summary=dict(executable="E:/private/assistant.exe"))
        payload = destination.read_bytes()
        with zipfile.ZipFile(destination) as archive:
            summary = archive.read("qt-client-summary.json")
            self.assertNotIn(b"E:/private", summary)
            self.assertIn(b"<local-path>", summary)
        with self.assertRaises(FileExistsError):
            self.bundle(destination=destination)
        self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(list(self.root.glob(".diagnostic-export-*")), [])

    def test_default_collision_retains_old_exports_and_zip_extension_is_required(self):
        moment = datetime(2026, 10, 30, 12, 0, 0)
        first, second = self.bundle(exported_at=moment), self.bundle(exported_at=moment)
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(first["path"].is_file())
        with self.assertRaisesRegex(ValueError, "ZIP"):
            self.bundle(destination=self.root / "report.json")


if __name__ == "__main__":
    unittest.main()
