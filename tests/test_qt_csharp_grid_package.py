"""The Qt installer owns an independently runnable copy of the grid service."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_qt_client as build


class GridPackageTests(unittest.TestCase):
    def test_production_plan_describes_component_without_building(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in (build.GRID_PROJECT, "experiments/qt_quick/client.py", "experiments/qt_quick/Client.qml"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            version = dict(version="fixture", displayVersion="1.0", numericVersion="1.0.0.0")
            (root / "version.json").write_text(json.dumps(version))
            inventory = root / "inventory.json"
            inventory.write_text("{}")
            source = dict(commit="1" * 40, clean=True)
            resources = {build.BRAND_ICON: dict(sha256=build.BRAND_ICON_SHA256)}
            stdout = io.StringIO()
            with (patch.object(build, "ROOT", root),
                  patch.object(build, "environment", return_value=dict(success=True, errors=[])),
                  patch.object(build, "grid_sdk_info", return_value={"version": build.GRID_SDK}),
                  patch.object(build, "source_identity", return_value=source),
                  patch.object(build, "validate_resources",
                               return_value=({"resourceVersion": "fixture"}, resources, {}, {}, {})),
                  patch.object(build, "publish_csharp_grid") as publish,
                  contextlib.redirect_stdout(stdout)):
                args = ["--plan-only", "--instance", "production", "--source-commit", source["commit"],
                        "--resource-root", str(root / "assets"), "--resource-inventory", str(inventory),
                        "--iscc", "fixture.exe"]
                self.assertEqual(build.main(args), 0)
            plan = json.loads(stdout.getvalue())
            self.assertEqual(plan["product"], "星图")
            self.assertEqual(plan["csharpGrid"], dict(project="hybrid/StarMap.Grid/StarMap.Grid.csproj",
                sourceCommit=source["commit"], directory="_internal/csharp-grid",
                runtimeIdentifier="win-x64", selfContained=True))
            self.assertFalse(Path(plan["output"]).exists())
            publish.assert_not_called()

    def test_framework_dependent_source_build_is_rejected_as_a_frozen_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            directory = stage / "_internal/csharp-grid"
            directory.mkdir(parents=True)
            framework = dict(name="Microsoft.NETCore.App", version="10.0.0")
            (directory / "StarMap.Grid.runtimeconfig.json").write_text(json.dumps(dict(
                runtimeOptions=dict(tfm="net10.0", framework=framework))))
            with self.assertRaisesRegex(ValueError, "include its own"):
                build.verify_csharp_grid(stage)

    @unittest.skipUnless(sys.platform == "win32" and os.environ.get("STARMAP_DOTNET"),
                         "set STARMAP_DOTNET in the isolated Windows packaging environment")
    def test_published_grid_runs_from_installed_location_and_enters_program_inventory(self):
        import numpy as np
        import psutil
        import PySide6
        from csharp_grid_backend import GridBackend

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            stage = output / "freeze/星图"
            internal = stage / "_internal"
            internal.mkdir(parents=True)
            pack = build.publish_csharp_grid(output, stage, build.grid_sdk_info(None))
            directory = internal / "csharp-grid"
            # Use the production protocol owner, with no usable path to a shared runtime.
            backend = GridBackend(directory / "StarMap.Grid.exe", output / "cache")
            nowhere = str(output / "no-shared-runtime")
            isolated = dict(DOTNET_ROOT=nowhere, DOTNET_ROOT_X64=nowhere, DOTNET_MULTILEVEL_LOOKUP="0",
                            PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
            try:
                with patch.dict(os.environ, isolated), backend.request("packaging", 1, 15):
                    distance = np.zeros((32, 32), dtype=np.uint16)
                    prepared = SimpleNamespace(distance=distance, shape=distance.shape)
                    rows = backend(np.array([[2., 3.], [4., 7.]], dtype=np.float32), prepared, 1., None, None)
                    self.assertTrue(rows)
                    process = psutil.Process(backend.process_id)
                    self.assertIn(directory / "coreclr.dll", {Path(m.path) for m in process.memory_maps()})
            finally:
                backend.close()
            self.assertFalse(process.is_running())

            # A real QtCore + all published DLLs exercise the existing PE/ICU closure;
            # only the unrelated Qt main EXE/resources are fixtures for the projection.
            shutil.copy2(Path(PySide6.__file__).parent / "Qt6Core.dll", internal)
            closure = build.prune_unneeded_qt(stage)
            build.collect_notices(stage, closure, pack)
            notices = build._read(internal / "qt-notices/NOTICE-INVENTORY.json")
            for name in ("LICENSE.TXT", "THIRD-PARTY-NOTICES.TXT"):
                row = next(row for row in notices["dependencyNotices"] if
                           row["distribution"] == build.GRID_RUNTIME_PACK and row["path"].endswith("/" + name))
                self.assertEqual((internal / "qt-notices" / row["path"]).read_bytes(), (pack / name).read_bytes())
            for name in build.QML_MODULES:
                module = internal / "PySide6/qml" / name
                module.mkdir(parents=True, exist_ok=True)
                (module / "qmldir").touch()
            for name in ("app/map_identity_engine.py", "PySide6/plugins/platforms/qwindows.dll"):
                (internal / name).parent.mkdir(parents=True, exist_ok=True)
                (internal / name).touch()
            with patch.object(build, "verify_administrator_manifest", return_value=1):
                frozen = build.verify_frozen(stage, {}, [], "星图")
            self.assertTrue(frozen["csharpGrid"]["selfContained"])
            (stage / "星图.exe").write_bytes(b"fixture")
            (stage / "_installer-owned-files.txt").touch()
            source = dict(commit=build._git("rev-parse", "HEAD"), clean=True)
            record = dict(releaseReport=build.stage_release_report(stage, product="星图", source=source,
                version=dict(version="fixture", displayVersion="1.0", numericVersion="1.0.0.0"),
                resource_version="fixture", resource_inventory_sha256="0" * 64))
            report = build._read(stage / "release-report.json")
            self.assertEqual(report["csharpGrid"]["sourceCommit"], source["commit"])
            _, record["fileProjection"] = build.write_production_files_include(stage, {}, output)
            build.write_program_ownership(stage, record)
            inventory = build._read(build.write_installer_program_inventory(stage, record, output, source))
            for path in directory.iterdir():
                relative = path.relative_to(stage).as_posix()
                self.assertIn(relative, record["fileProjection"]["families"]["program"])
                self.assertEqual(inventory["files"][relative]["sha256"], build.sha256(path))
            (directory / "System.Text.Json.dll").unlink()
            with (patch.object(build, "verify_administrator_manifest", return_value=1),
                  self.assertRaisesRegex(ValueError, "System.Text.Json.dll")):
                build.verify_frozen(stage, {}, [], "星图")


if __name__ == "__main__":
    unittest.main()
