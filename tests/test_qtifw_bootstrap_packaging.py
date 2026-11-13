"""Compile and inspect native QtIFW support; never run Setup or write ARP."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_qtifw_trial as build
from probe_qtifw_lifecycle import write_xml
from qtifw_upgrade import materialize_repository


class QtIfwBootstrapPackagingTests(unittest.TestCase):
    def test_real_native_setup_contains_one_program_archive_and_rebuilds_offline_repository(self):
        ifw = Path(os.environ.get("STARMAP_TEST_IFW_BIN", r"E:\codex\tools\qtifw-4.11.0\bin"))
        if not (ifw / "binarycreator.exe").is_file():
            self.skipTest("QtIFW 4.11 native compiler is unavailable")
        with tempfile.TemporaryDirectory(prefix="星图 native QtIFW compile ") as temporary:
            output = Path(temporary)
            packages, config = output / "packages", output / "config"
            config.mkdir()
            payload = packages / build.PACKAGE / "data/program"
            payload.mkdir(parents=True)
            raw = b"synthetic program, never executed\n" * 2048
            (payload / "fixture.bin").write_bytes(raw)
            for name in (build.PACKAGE, build.DATA_PACKAGE):
                write_xml(packages / name / "meta/package.xml", "Package", dict(
                    DisplayName="Native compile fixture", Version="1.1.5", ReleaseDate="2026-11-13", Default="true"))
            (config / "controller.js").write_text("function Controller() {}\n", encoding="utf-8")
            write_xml(config / "config.xml", "Installer", dict(Name="Owned compile fixture", Version="1.1.5",
                Title="Compile only", Publisher="Synthetic", TargetDir=str(output / "never-installed"),
                ControlScript="controller.js"))
            record = build.build_upgrade_support(output, packages, config, ifw, compression=3)
            layout = record["archiveLayout"]
            self.assertEqual(len(layout), 1)
            self.assertFalse(payload.exists())
            self.assertTrue((packages / build.PACKAGE / "data/program.7z").is_file())
            self.assertFalse((output / "upgrade-support/boot/repository" / layout[0]["repository"]).exists())
            self.assertFalse((output / "upgrade-support/boot/native-setup.exe").exists())
            qrc = ET.Element("RCC")
            entries = ET.SubElement(qrc, "qresource", prefix="/qtifw-upgrade")
            for path in (output / "upgrade-support").rglob("*"):
                if path.is_file():
                    ET.SubElement(entries, "file", alias=path.relative_to(output / "upgrade-support").as_posix()).text = str(path)
            ET.ElementTree(qrc).write(output / "support.qrc", encoding="utf-8", xml_declaration=True)
            setup = output / "native-Setup.exe"
            base = build.administrator_installer_base(ifw, output)
            subprocess.run([str(ifw / "binarycreator.exe"), "--offline-only", "--compression", "3", "--resources",
                str(output / "support.qrc"), "-t", str(base), "-c", str(config / "config.xml"), "-p", str(packages), str(setup)],
                check=True, capture_output=True)
            build.build.verify_administrator_manifest(setup)
            result = materialize_repository(output / "upgrade-support", setup)
            self.assertEqual(result, dict(materialized=True, archives=1))
            archive = output / "upgrade-support/boot/repository" / layout[0]["repository"]
            self.assertEqual(archive.read_bytes(), (packages / build.PACKAGE / "data/program.7z").read_bytes())
            dumped = list((output / "upgrade-support/boot").glob("archive-dump-*/metadata/*/*program.7z"))
            self.assertEqual(len(dumped), 1)
            # Read the official native PE without launching it.
            import pefile
            with pefile.PE(str(setup)) as pe:
                native_code = next(section.get_data() for section in pe.sections if section.Name.rstrip(b"\0") == b".text")
            with pefile.PE(str(ifw / "installerbase.exe")) as pe:
                official_code = next(section.get_data() for section in pe.sections if section.Name.rstrip(b"\0") == b".text")
            self.assertEqual(native_code, official_code)
            self.assertNotIn(b"Inno Setup", setup.read_bytes())


if __name__ == "__main__":
    unittest.main()
