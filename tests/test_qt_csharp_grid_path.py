"""Exercise Qt's program/data path split with a real grid request."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / part) for part in ("experiments/qt_quick", "app", "tools", "tests")]


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("STARMAP_DOTNET"),
                     "requires the isolated Windows Qt environment and .NET SDK")
class QtGridLocationTests(unittest.TestCase):
    def test_grid_search_uses_program_files_for_frozen_private_production_and_source(self):
        from PySide6.QtGui import QGuiApplication
        import psutil
        import build_qt_client as build
        import client
        import client_paths
        import fast_structure_alignment as fast
        from seed_sandbox import seed
        from single_map_structure import current_geometry
        from test_hybrid_grid_backend import ambiguous_scene

        class ServicesConstructed(Exception):
            """Stop before session timers, window creation and native input registration."""

        app = QGuiApplication.instance() or QGuiApplication(["grid-path-test", "-platform", "offscreen"])
        source, frame = ambiguous_scene()
        prepared = fast.prepare(source)
        geometry = current_geometry(frame, observation_bounds=(0, 0, 640, 480))
        sdk = build.grid_sdk_info(None)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            install = output / "program"
            packaged = install / "_internal"
            (packaged / "references").mkdir(parents=True)
            build.publish_csharp_grid(output, install, sdk)
            subprocess.run([sdk["executable"], "build", str(ROOT / build.GRID_PROJECT), "-c", "Release",
                            "--nologo", "-p:UseSharedCompilation=false", "-p:NuGetAudit=false",
                            f"-p:RestoreSources={output / 'grid-build/offline-feed'}"], cwd=ROOT, check=True)
            for name, frozen, production in (("private", True, False), ("production", True, True),
                                              ("source", False, False)):
                with self.subTest(mode=name):
                    work = install / "work"
                    sandbox = install if production else work / name
                    root = packaged if frozen else ROOT
                    with (patch.multiple(client_paths, FROZEN=frozen, PRODUCTION=production,
                                         RESOURCE_ROOT=root, WORK_ROOT=work, INSTALL_ROOT=install),
                          patch.multiple(client, FROZEN=frozen, PRODUCTION=production, RESOURCE_ROOT=root,
                                         WORK_ROOT=work),
                          patch("client_input.NativeShortcutBackend"), patch("client_capture.ClientCaptureProvider"),
                          patch("client_capture_exclusion.QtCaptureExclusion"),
                          patch("client_session.SessionService", side_effect=ServicesConstructed),
                          patch.dict(os.environ, DOTNET_ROOT=str(Path(sdk["executable"]).parent))):
                        if not production:
                            seed(install, sandbox)
                            self.assertFalse((sandbox / "_internal/csharp-grid").exists())
                        created = []
                        with self.assertRaises(ServicesConstructed):
                            client.QtClient(app, sandbox, probe_output=output / "unused",
                                            grid_backend="csharp", on_created=created.append)
                        runtime = created[0].runtime
                        try:
                            with runtime.grid_runtime.fit_scope() as cancelled:
                                result = fast.propose(frame, None, prepared, context=geometry, cancelled=cancelled)
                            self.assertEqual(result[2]["reason"], "fast-ambiguous")
                            self.assertEqual(len(runtime.grid_runtime.calls), 1)
                            process = psutil.Process(runtime.grid_runtime.process_id)
                            expected = (packaged / "csharp-grid/StarMap.Grid.exe" if frozen else
                                        ROOT / "hybrid/StarMap.Grid/bin/Release/net10.0/StarMap.Grid.exe")
                            self.assertEqual(Path(process.exe()), expected)
                        finally:
                            runtime.grid_runtime.close()


if __name__ == "__main__":
    unittest.main()
