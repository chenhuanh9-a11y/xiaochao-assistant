"""Real resource worker/inspection to problem log/export, with synthetic ZIPs.

The coordinator uses existing fake native/window services. No game, installed
application, file dialog, network request or private player archive is exercised.
"""
import hashlib
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from client import QtClient
from client_problems import ProblemsService
from client_resources import package_problem, package_cancelled
from client_resource_errors import safe_error_details
from diagnostic_export import export_safe_diagnostic_bundle
import test_client_resources as fixtures
import test_client_coordinator as coordinator_fixtures


DEPENDENCY = "_internal/references/difficult-native/difficult-map-01-1-shared.npz"
CONTENT = b"synthetic-shared-structure"


class ResourceErrorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.dependencies.append(fixtures.row(DEPENDENCY, CONTENT))
        self.fixture.write(DEPENDENCY, CONTENT)
        self.service = self.fixture.service

    def assert_rejected_dependency(self, actual):
        if actual is None:
            (self.fixture.root / DEPENDENCY).unlink()
        else:
            self.fixture.write(DEPENDENCY, actual)
        self.fixture.install()
        self.assertEqual(len(self.fixture.problems), 1)
        problem = self.fixture.problems[0]
        self.assertIn("地图文件有缺失，或版本与当前软件不匹配", problem["summary"])
        self.assertNotIn("文件与包内说明不一致", problem["summary"])
        self.assertEqual(problem["errorCode"], "missing-or-incompatible-dependency")
        self.assertEqual(problem["errorDetails"], dict(path=DEPENDENCY,
            expectedBytes=len(CONTENT), actualBytes=None if actual is None else len(actual),
            expectedSha256=hashlib.sha256(CONTENT).hexdigest(),
            actualSha256=None if actual is None else hashlib.sha256(actual).hexdigest(),
            missing=actual is None))
        self.assertIn("missing-or-incompatible-dependency", problem["raw"])
        self.assertEqual(self.fixture.changes, [])
        self.assertEqual(self.fixture.completions, [])
        self.assertFalse(self.fixture.lease.active)
        self.assertFalse((self.fixture.root / fixtures.IMAGE).exists())
        self.assertFalse((self.fixture.root / "work/map-packages/nightmare-suju.json").exists())
        return problem

    def test_real_worker_missing_shared_dependency_keeps_dependency_classification(self):
        self.assert_rejected_dependency(None)

    def test_real_worker_size_mismatch_keeps_expected_and_actual(self):
        self.assert_rejected_dependency(b"short")

    def test_real_worker_hash_mismatch_keeps_expected_and_actual(self):
        self.assert_rejected_dependency(b"x" * len(CONTENT))

    def test_valid_shared_dependency_still_installs_and_notifies_once(self):
        self.fixture.install()
        self.assertEqual(self.fixture.problems, [])
        self.assertEqual(self.fixture.changes, [True])
        self.assertEqual(len(self.fixture.completions), 1)
        self.assertEqual((self.fixture.root / fixtures.IMAGE).read_bytes(), b"route")

    def test_malformed_archive_is_package_failure(self):
        archive = self.fixture.folder / "malformed-shared.zip"
        archive.write_bytes(b"not a ZIP")
        self.fixture.install(archive)
        self.assertIn("适用于当前软件版本的 ZIP 文件", self.fixture.problems[-1]["summary"])
        self.assertEqual(self.fixture.problems[-1]["errorCode"], "package-unreadable")
        self.assertEqual(self.fixture.changes, [])

    def test_unsupported_package_is_package_failure(self):
        archive = self.fixture.package()
        with zipfile.ZipFile(archive) as source:
            records = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(records["map-package.json"])
        manifest["schemaVersion"] = 999
        records["map-package.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(archive, "w") as target:
            for name, content in records.items():
                target.writestr(name, content)
        self.fixture.install(archive)
        self.assertIn("适用于当前软件版本的 ZIP 文件", self.fixture.problems[-1]["summary"])
        self.assertEqual(self.fixture.problems[-1]["errorCode"], "unsupported-package-version")
        self.assertEqual(self.fixture.changes, [])

    def test_archive_hash_mismatch_remains_integrity_failure(self):
        archive = self.fixture.package(data=b"route")
        with zipfile.ZipFile(archive) as source:
            records = {name: source.read(name) for name in source.namelist()}
        records["data/" + fixtures.IMAGE] = b"other"
        with zipfile.ZipFile(archive, "w") as target:
            for name, content in records.items():
                target.writestr(name, content)
        self.fixture.install(archive)
        problem = self.fixture.problems[-1]
        self.assertEqual(problem["errorCode"], "file-integrity-mismatch")
        self.assertIn("文件与包内说明不一致", problem["summary"])
        self.assertNotIn("地图文件有缺失，或版本与当前软件不匹配", problem["summary"])
        self.assertEqual(self.fixture.changes, [])

    def test_code_wins_over_conflicting_sha_deadline_and_cancel_words(self):
        problem = package_problem(dict(code="missing-or-incompatible-dependency",
            reason="sha-integrity deadline cancelled: shared.npz"), "inspect")
        self.assertIn("地图文件有缺失，或版本与当前软件不匹配", problem["summary"])
        unknown = package_problem(dict(code="future-resource-error",
            reason="missing-or-incompatible-dependency: shared.npz"), "inspect")
        self.assertNotIn("地图文件有缺失，或版本与当前软件不匹配", unknown["summary"])
        self.assertEqual(unknown["errorCode"], "future-resource-error")
        self.assertFalse(package_cancelled(dict(code="future-resource-error", reason="cancelled")))
        self.assertTrue(package_cancelled(dict(code="import-cancelled", reason="sha deadline")))

    def test_player_labels_keep_unknown_tokens_out_of_default_text(self):
        from client_resources import package_title, recovery_status_label
        self.assertEqual(package_title("future-private-package"), "待确认的图包")
        self.assertEqual(recovery_status_label("future-private-status"), "需要重新核对")
        self.assertIn("临时文件", recovery_status_label("upgrade-cleanup-available"))
        problem = package_problem(dict(code="missing-or-incompatible-dependency",
            reason="sha mismatch", details=dict(path=DEPENDENCY, missing=True)), "inspect")
        self.assertEqual(problem["errorCode"], "missing-or-incompatible-dependency")
        self.assertEqual(problem["raw"], "sha mismatch")
        self.assertEqual(problem["errorDetails"], dict(path=DEPENDENCY, missing=True))
        self.assertNotIn("sha", problem["summary"])

    def test_details_are_bounded_and_reject_noncanonical_paths(self):
        for path in ("_internal/references/../private", "_internal/references//private",
                     "_internal/references/" + "x" * 241, "_internal/references/\ud800",
                     "_internal/references/file\x1b.npz"):
            self.assertEqual(safe_error_details(dict(path=path)), {})
        details = safe_error_details(dict(path=DEPENDENCY, expectedBytes=2 ** 200, actualBytes=False,
            expectedSha256="a" * 64000, actualSha256=None, missing=True, extra={"secret": "unused"}))
        self.assertEqual(details, dict(path=DEPENDENCY, actualSha256=None, missing=True))

    def test_legacy_prefix_is_exact_and_absolute_paths_are_redacted(self):
        problem = package_problem("missing-or-incompatible-dependency: " + DEPENDENCY, "inspect")
        self.assertIn("地图文件有缺失，或版本与当前软件不匹配", problem["summary"])
        unrelated = package_problem("message mentions missing-or-incompatible-dependency: shared.npz", "inspect")
        self.assertNotIn("地图文件有缺失，或版本与当前软件不匹配", unrelated["summary"])
        for private in ("C:/Users/Private Player/package.zip", r"C:\Users\Private Player\package.zip",
                        "/home/private-player/package.zip", "//server/private-player/package.zip"):
            problem = package_problem(dict(code="package-unreadable", reason="package-unreadable: " + private,
                details=dict(path=private, expectedBytes=-1, actualBytes=True,
                    expectedSha256="bad", actualSha256="bad", missing="yes", extra="private")), "inspect")
            self.assertNotIn(private, json.dumps(problem))
            self.assertNotIn("Private Player", problem["raw"])
            self.assertNotIn("private-player", problem["raw"])
            self.assertEqual(problem["errorDetails"], {})
        bounded = package_problem("unexpected-error: " + "原因" * 9000, "inspect")
        self.assertLessEqual(len(bounded["raw"].encode("utf-8")), 4096)
        expanded = package_problem("unexpected-error: " + "/x\n" * 9000, "inspect")
        self.assertLessEqual(len(expanded["raw"].encode("utf-8")), 4096)

    def test_helper_failure_preserves_structured_fields_without_success_notice(self):
        details = dict(path=DEPENDENCY, expectedBytes=len(CONTENT), actualBytes=None,
            expectedSha256=hashlib.sha256(CONTENT).hexdigest(), actualSha256=None, missing=True)
        self.service.recordImportReceipt(dict(success=False, reason="sha checksum detail",
            errorCode="missing-or-incompatible-dependency", errorDetails=details))
        fixtures.APP.processEvents()
        self.assertIn("地图文件有缺失，或版本与当前软件不匹配", self.fixture.problems[-1]["summary"])
        self.assertEqual(self.fixture.problems[-1]["errorDetails"], details)
        self.assertEqual(self.fixture.completions, [])

    def test_composition_coordinator_problem_details_and_standard_export(self):
        coordinator_fixtures.CoordinatorTests.setUpClass()
        coordinator_case = coordinator_fixtures.CoordinatorTests()
        coordinator_case.setUp()
        self.addCleanup(coordinator_case.tearDown)
        problems = ProblemsService(SimpleNamespace(work_root=self.fixture.root / "work"))
        def drain():
            problems.shutdown()
            deadline = time.monotonic() + 5
            while problems.pendingWrites and time.monotonic() < deadline:
                fixtures.APP.processEvents()
                time.sleep(.005)
            self.assertFalse(problems.pendingWrites)
        self.addCleanup(drain)
        client = QtClient.__new__(QtClient)
        client._closed, client.problems = False, problems
        client.coordinator = coordinator_case.coordinator
        notices = []
        client.coordinator.notificationRequested.connect(lambda *args: notices.append(args))
        self.service.errorReported.connect(client._resource_problem)
        original = self.assert_rejected_dependency(b"x" * len(CONTENT))
        drain()
        self.assertEqual(len(notices), 1)
        self.assertIn("地图文件有缺失，或版本与当前软件不匹配", notices[0][1])
        row = problems.recent[0]
        self.assertEqual(row["errorCode"], original["errorCode"])
        self.assertEqual(row["errorDetails"], original["errorDetails"])
        self.assertIn("expectedSha256", problems.detailText(row["id"]))
        result = export_safe_diagnostic_bundle(self.fixture.root, problems.log_path.parent,
            app_version="synthetic", build_fingerprint="RESOURCE-TEST")
        with zipfile.ZipFile(result["path"]) as archive:
            rows = [json.loads(line) for name in archive.namelist() if name.startswith("logs/events-")
                    for line in archive.read(name).decode("utf-8").splitlines()]
        exported = next(row for row in rows if row.get("event") == "qt-runtime-problem")
        self.assertEqual(exported["errorCode"], original["errorCode"])
        self.assertEqual(exported["errorDetails"], original["errorDetails"])
        self.assertNotIn(str(self.fixture.folder), json.dumps(exported))


if __name__ == "__main__":
    unittest.main()
