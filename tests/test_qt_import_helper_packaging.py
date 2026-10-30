"""Exercise exact helper staging with inert bytes; no compiler or EXE run."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_qt_client as build


class ImportHelperPackagingTests(unittest.TestCase):
    def test_png_npz_json_install_and_upgrade_without_image_or_array_libraries(self):
        # The production importer validates archive CRC/bytes/SHA and ownership;
        # decoding belongs to resource consumers. A clean interpreter rejects
        # these dependencies so accidental dynamic imports fail this test too.
        script = r'''
import sys, importlib.abc, io, struct, zlib, json, hashlib, zipfile, tempfile
from pathlib import Path
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'cv2', 'PIL'}:
            raise RuntimeError('unexpected image/array dependency: ' + fullname)
sys.meta_path.insert(0, Reject())
sys.path.insert(0, sys.argv[1])
import star_map_import_helper as helper
import star_map_import as importer
def row(name, data):
    return dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
def chunk(kind, data):
    return struct.pack('>I',len(data)) + kind + data + struct.pack('>I',zlib.crc32(kind+data))
png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0)) + chunk(b'IDAT',zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND',b'')
header = b"{'descr': '|u1', 'fortran_order': False, 'shape': (1,), }"
header += b' ' * ((16 - ((10 + len(header) + 1) % 16)) % 16) + b'\n'
npy = b'\x93NUMPY\x01\x00' + struct.pack('<H',len(header)) + header + b'\x01'
stream = io.BytesIO()
with zipfile.ZipFile(stream,'w') as archive:
    archive.writestr('structure.npy',npy)
files = {'_internal/assets/suju/fixture.png': png, '_internal/assets/suju/fixture.npz': stream.getvalue(),
    '_internal/assets/suju/fixture.json': b'{"synthetic":1}'}
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    dependencies = []
    for name in sorted(importer.BASE_DEPENDENCIES):
        target = root/name; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'{}'); dependencies.append(row(name,b'{}'))
    (root/'_internal/version.json').write_text(json.dumps(dict(displayVersion='1.0.5',numericVersion='1.0.0.9')))
    for revision in range(2):
        if revision: files['_internal/assets/suju/fixture.json'] = b'{"synthetic":2}'
        archive_path = root/('package'+str(revision)+'.zip')
        manifest = dict(schemaVersion=1, revision=1,packageId='nightmare-suju',clientCompatibility=importer.CLIENT_COMPATIBILITY,
            requiresFiles=dependencies,files=[row(name,data) for name,data in files.items()])
        with zipfile.ZipFile(archive_path,'w') as archive:
            archive.writestr('map-package.json',json.dumps(manifest))
            for name,data in files.items(): archive.writestr('data/'+name,data)
        result = helper.install_package(archive_path,root,application_stopped=True)
        for name,data in files.items(): assert (root/name).read_bytes() == data
        if revision: assert result['upgradeTransactionId']
assert not {'PIL','numpy','cv2'} & set(sys.modules)
'''
        result = subprocess.run([sys.executable, "-I", "-c", script, str(ROOT / "app")],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_import_helper_stages_separate_runtime_and_hash_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            stage = output / "stage"; stage.mkdir()
            frozen = output / "import-helper-freeze/star-map-import-helper"
            runtime = frozen / "_star_map_import_runtime"; runtime.mkdir(parents=True)
            (frozen / "star-map-import-helper.exe").write_bytes(b"synthetic PE placeholder")
            (runtime / "library.zip").write_bytes(b"synthetic runtime")
            with patch.object(build.subprocess, "run") as compiler, patch.object(
                    build, "verify_invoker_manifest", return_value=1) as manifest:
                result = build.assemble_recovery_helper(output, stage, {}, kind="import")
            self.assertIn("qt-import-helper.spec", str(compiler.call_args.args))
            self.assertEqual(result["runtimeDirectory"], "_star_map_import_runtime")
            self.assertFalse(result["launched"])
            self.assertEqual(manifest.call_count, 2)
            for name, row in result["files"].items():
                self.assertEqual(build.sha256(stage / name), row["sha256"])
            self.assertEqual({path.name for path in stage.iterdir()},
                {"star-map-import-helper.exe", "_star_map_import_runtime"})


if __name__ == "__main__":
    unittest.main()
