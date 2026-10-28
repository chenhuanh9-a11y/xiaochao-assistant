"""Synthetic-only checks for Q90 tooling; no author assets or full rebuilds."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import optimize_map_packages as tool


MATRIX = b'"matrixSourceToNative": [ [1.000, 0, 12.50], [0, 1.000, -3.0], [0,0,1] ]'


def image_bytes(kind):
    pixels = np.random.default_rng(17).integers(0, 256, (48, 64, 4), dtype=np.uint8)
    pixels[:, :, 3] = np.array([0, 127, 255], np.uint8)[np.indices((48, 64))[1] % 3]
    output = io.BytesIO()
    if kind == "PNG":
        Image.fromarray(pixels).save(output, format="PNG", compress_level=0)
    else:
        Image.fromarray(pixels).save(output, format="WEBP", lossless=True, exact=True)
    return output.getvalue()


def zip_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries.items():
            archive.writestr(path, data)
    return output.getvalue()


def file_record(path, data):
    return dict(path=path, bytes=len(data), sha256=tool.sha(data))


def package_bytes(pid, contents, catalog):
    manifest = dict(packageId=pid, files=[file_record(path, data) for path, data in contents.items()],
                    requiresFiles=[file_record(tool.BASE_CATALOG, catalog)])
    # A deliberately unusual numeric representation must remain byte-identical.
    manifest_bytes = tool.jsonbytes(manifest).rstrip()[:-1] + b", " + MATRIX + b"}\n"
    return zip_bytes({"map-package.json": manifest_bytes, **{"data/" + p: d for p, d in contents.items()}})


class OptimizeMapPackagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xiaochao-q90-synthetic-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / "base"
        self.output = self.root / "candidate"
        self.png = image_bytes("PNG")
        self.webp = image_bytes("WEBP")
        self.suju_path = "_internal/assets/suju/map-fixture/floor-1.png"
        self.native_path = "_internal/assets/zhan10-native/fixture/floor-1.lossless.webp"
        self.full_path = "_internal/assets/zhan10-native/fixture/full-reference.webp"
        self.npz_path = "_internal/references/difficult-native/fixture-shared.npz"
        self.recognition_path = "_internal/recognition/native/entries.json"
        npz = io.BytesIO()
        np.savez_compressed(npz, shared_mask=np.ones((16, 16), np.uint8))
        self.npz = npz.getvalue()
        self.catalog = (b'{"entries": [' + tool.jsonbytes(file_record(self.suju_path, self.png)).strip()
                        + b"], " + MATRIX + b"}\n")
        catalog_path = self.base / tool.BASE_CATALOG
        catalog_path.parent.mkdir(parents=True)
        catalog_path.write_bytes(self.catalog)
        (catalog_path.parent / "build-fingerprint.txt").write_text("SYNTHETIC\n", encoding="ascii")
        self.optional_catalog_path = "_internal/assets/authors/nightingale/catalog.json"
        self.suju_contents = {self.suju_path: self.png}
        self.native_contents = self.make_native()
        self.source = self.root / "four-packages.zip"
        self.source.write_bytes(zip_bytes({
            "packs/nightmare-suju.zip": package_bytes("nightmare-suju", self.suju_contents, self.catalog),
            "packs/difficult-zhan10.zip": package_bytes("difficult-zhan10", self.native_contents, self.catalog),
        }))

    def make_native(self):
        full = dict(entries=[file_record(self.full_path, self.webp)])
        display_json = (b'{"displaySHA256": "' + tool.sha(self.webp).encode("ascii") + b'", ' + MATRIX + b"}\n")
        contents = {
            self.native_path: self.webp, self.full_path: self.webp,
            self.npz_path: self.npz, self.recognition_path: b'{"recognitionOnly": true}',
            tool.FULL_REFERENCES: tool.jsonbytes(full),
            "_internal/references/difficult-native-identity-pack.json": display_json,
        }
        native = dict(files=[file_record(path.removeprefix("_internal/"), data) for path, data in contents.items()],
                      resourceBytes=sum(map(len, contents.values())), fullReferences=copy.deepcopy(full["entries"]),
                      fullReferenceBytes=len(self.webp), floors=[dict(mapId="fixture", floor="1", displayBytes=len(self.webp))])
        contents[tool.NATIVE_MANIFEST] = tool.jsonbytes(native)
        return contents

    def test_encoder_retains_canvas_and_exact_alpha_with_q90_rgb_change(self):
        for path, data, allow_webp in ((self.native_path, self.webp, True), (self.suju_path, self.png, False)):
            with self.subTest(path=path):
                _, encoded, record = tool.encode_display((path, data), allow_webp=allow_webp)
                before = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))
                after = np.array(Image.open(io.BytesIO(encoded)).convert("RGBA"))
                self.assertEqual(before.shape, after.shape)
                np.testing.assert_array_equal(before[:, :, 3], after[:, :, 3])
                self.assertLess(len(encoded), len(data))
                self.assertEqual(record["sourceCanvas"], [64, 48])
                self.assertEqual(record["sourceToOutput"], tool.IDENTITY)
                self.assertEqual(record["sourceSHA256"], tool.sha(data))
                if path.endswith(".png"):
                    np.testing.assert_array_equal(before, after)
                else:
                    self.assertFalse(record["rgbExact"], "Q90 is deliberately a lossy RGB display derivative")

    def test_import_safe_help_works_from_another_directory(self):
        script = str(Path(tool.__file__).resolve())
        result = subprocess.run([sys.executable, script, "--help"], cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--expected-source-sha256", result.stdout)
        self.assertFalse(self.output.exists())

    def test_current_native_closure_updates_hashes_and_preserves_protected_bytes(self):
        source = package_bytes("difficult-zhan10", self.native_contents, self.catalog)
        manifest, contents, report = tool.optimize_package(source, workers=1)
        self.assertEqual(set(contents), set(self.native_contents))
        self.assertEqual(contents[self.npz_path], self.npz)
        self.assertEqual(contents[self.recognition_path], self.native_contents[self.recognition_path])
        identity_path = "_internal/references/difficult-native-identity-pack.json"
        self.assertIn(MATRIX, contents[identity_path])
        identity = json.loads(contents[identity_path])
        self.assertEqual(identity["displaySHA256"], tool.sha(contents[self.native_path]))
        native = json.loads(contents[tool.NATIVE_MANIFEST])
        self.assertEqual(manifest["canonicalManifestSHA256"], tool.sha(contents[tool.NATIVE_MANIFEST]))
        for record in native["files"]:
            data = contents["_internal/" + record["path"]]
            self.assertEqual(record["sha256"], tool.sha(data))
            self.assertEqual(record["bytes"], len(data))
        self.assertEqual(native["resourceBytes"], sum(r["bytes"] for r in native["files"]))
        self.assertEqual(native["fullReferences"], json.loads(contents[tool.FULL_REFERENCES])["entries"])
        self.assertEqual(native["floors"][0]["displayBytes"], len(contents[self.native_path]))
        self.assertTrue(report["allStructureAndRecognitionNonDisplayPayloadUnchanged"])

    def test_build_prerequisite_portable_reports_and_source_lock(self):
        source_before = self.source.read_bytes()
        base_before = {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        reports = tool.build_packages(self.source, self.base, self.output, workers=1,
                                      expected_source_sha256=tool.sha(source_before))
        self.assertEqual(source_before, self.source.read_bytes())
        self.assertEqual(base_before, {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()})
        prerequisite = (self.output / "base-prerequisite" / tool.BASE_CATALOG).read_bytes()
        self.assertNotEqual(prerequisite, self.catalog)
        self.assertIn(MATRIX, prerequisite)
        new_catalog = json.loads(prerequisite)
        suju_data = (self.output / "staging/nightmare-suju" / self.suju_path).read_bytes()
        self.assertEqual(new_catalog["entries"][0]["sha256"], tool.sha(suju_data))
        for report in reports:
            self.assertEqual(Path(report["path"]).name, report["path"])
            with zipfile.ZipFile(self.output / report["path"]) as archive:
                manifest_source = archive.read("map-package.json")
                self.assertIn(MATRIX, manifest_source)
                manifest = json.loads(manifest_source)
                dependency = manifest["requiresFiles"][0]
                self.assertEqual(dependency["sha256"], tool.sha(prerequisite))
                self.assertEqual(dependency["bytes"], len(prerequisite))
                for record in manifest["files"]:
                    payload = archive.read("data/" + record["path"])
                    self.assertEqual(record["sha256"], tool.sha(payload))
            self.assertFalse(report["requiresFilesUnchanged"])
            self.assertEqual(report["sha256"], tool.sha((self.output / report["path"]).read_bytes()))
        lock = json.loads((self.output / "source-lock.json").read_bytes())
        self.assertEqual(lock["sourceArchive"]["sha256"], tool.sha(source_before))
        self.assertEqual(lock["baseCatalog"]["sha256"], tool.sha(self.catalog))
        self.assertEqual(lock["baseFingerprint"]["value"], "SYNTHETIC")
        self.assertEqual(set(lock["dependencies"]), {"Pillow", "numpy", "OpenCV"})
        for name in ("build-manifest.json", "source-lock.json", "base-prerequisite-audit.json", "SHA256SUMS.txt"):
            self.assertNotIn(str(self.root), (self.output / name).read_text("utf-8"))

    def test_optional_catalog_hash_is_refreshed_with_png_only(self):
        path = "_internal/assets/authors/nightingale/fixture.png"
        catalog = tool.jsonbytes(dict(layers=[file_record(path, self.png)]))
        source = package_bytes("nightmare-nightingale", {path: self.png, self.optional_catalog_path: catalog}, self.catalog)
        _manifest, contents, report = tool.optimize_package(source, workers=1)
        record = json.loads(contents[self.optional_catalog_path])["layers"][0]
        self.assertEqual(record["sha256"], tool.sha(contents[path]))
        self.assertEqual(record["bytes"], len(contents[path]))
        self.assertTrue(report["records"][0]["rgbExact"])
        with self.assertRaisesRegex(ValueError, "Unexpected image format"):
            tool.encode_display(("_internal/assets/authors/nightingale/fixture.webp", self.webp), allow_webp=False)

    def test_json_patch_preserves_unchanged_numeric_text_and_handles_additions(self):
        source = b'{ "rows": [ { "bytes": 100, ' + MATRIX + b'} ], "empty": {} }\n'
        updated = json.loads(source)
        updated["rows"][0]["bytes"] = 50
        updated["rows"][0]["new"] = "display metadata"
        updated["empty"]["x"] = 1
        result = tool.patch_json(source, updated)
        self.assertIn(MATRIX, result)
        self.assertEqual(json.loads(result), updated)
        self.assertTrue(result.endswith(b"\n"))

    def test_existing_nested_output_and_wrong_source_pin_rejected_before_writes(self):
        self.output.mkdir()
        sentinel = self.output / "evidence.txt"
        sentinel.write_text("keep", encoding="ascii")
        for target, pin in ((self.output, None), (self.base / "nested", None), (self.root / "bad-pin", "0" * 64)):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    tool.build_packages(self.source, self.base, target, expected_source_sha256=pin)
        self.assertEqual(sentinel.read_text("ascii"), "keep")
        self.assertFalse((self.base / "nested").exists())
        self.assertFalse((self.root / "bad-pin").exists())

    def test_invalid_source_hash_and_archive_paths_are_rejected(self):
        entries = tool.read_zip(package_bytes("nightmare-suju", self.suju_contents, self.catalog))
        entries["data/" + self.suju_path] = b"tampered"
        with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
            tool.load_package(zip_bytes(entries))
        for path in ("../escape", "/absolute", "C:/absolute", "a/CON.txt", "a//b", "a/./b"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    tool.read_zip(zip_bytes({path: b"x"}))
        # Windows' ZipInfo constructor normalizes backslashes when making a ZIP;
        # validate the unnormalized spelling directly as well.
        with self.assertRaises(ValueError):
            tool.safe_path("x\\escape")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            tool.read_zip(zip_bytes({"a.json": b"x", "A.json": b"y"}))

    def test_different_base_catalog_dependency_is_rejected_before_output(self):
        (self.base / tool.BASE_CATALOG).write_bytes(self.catalog + b" ")
        with self.assertRaisesRegex(ValueError, "different base catalog"):
            tool.build_packages(self.source, self.base, self.output)
        self.assertFalse(self.output.exists())

    def test_windows_reserved_device_aliases_are_rejected_without_opening_them(self):
        stems = ("COM¹", "com²", "Com³", "LPT¹", "lpt²", "Lpt³", "CONIN$", "conout$", "CON", "LPT1")
        for stem in stems:
            for suffix in ("", ".txt", " .txt"):
                path = "data/" + stem + suffix
                with self.subTest(path=path):
                    with self.assertRaisesRegex(ValueError, "Non-portable"):
                        tool.safe_path(path)
                    # ZIP assembly/read is entirely in memory; no device file IO.
                    with self.assertRaisesRegex(ValueError, "Non-portable"):
                        tool.read_zip(zip_bytes({path: b"synthetic"}))
        for name in ("COM0.txt", "LPT0.txt", "COM10.txt", "LPT10.txt", "CONIN-output.txt", "COM⁴.txt"):
            self.assertEqual(tool.safe_path("data/" + name), "data/" + name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
