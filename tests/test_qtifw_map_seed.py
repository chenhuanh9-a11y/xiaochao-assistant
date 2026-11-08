"""Bundle-seed checks use real ZIP validation and resource transactions."""
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools"), str(ROOT / "tests")]

import author_resource_snapshots as snapshots
import generic_author_base as base
import test_generic_author_package as generic_fixture
import qtifw_map_seed as seed
import star_map_import


class QtIfwMapSeedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qtifw-map-seed-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.target = self.folder / "target"
        self.program = self.target / "program"
        self.program.mkdir(parents=True)

    def make_package(self):
        fixture = generic_fixture.GenericPackageTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        generic_fixture.make_base(self.program)
        fixture.root = self.program
        fixture.contract = base.resolve_layer(self.program, "s0", "nightmare", "map", "B1")
        fixture.deps = [generic_fixture.record(path, (self.program / path).read_bytes())
                        for path in fixture.contract["dependencies"]]
        archive = fixture.package()
        stored = self.target / "_installer-support/maps/accepted/package.zip"
        stored.parent.mkdir(parents=True)
        shutil.copyfile(archive, stored)
        row = dict(path=stored.relative_to(self.target).as_posix(),
                   bytes=stored.stat().st_size,
                   sha256=hashlib.sha256(stored.read_bytes()).hexdigest(),
                   packageId=fixture.profile, season="s0", versionedAuthor=True)
        return stored, row

    def test_real_versioned_import_receipt_and_exact_retry(self):
        archive, row = self.make_package()
        before_archive = archive.read_bytes()

        first = seed.install_bundled_maps(self.target, [row])
        self.assertEqual(first["installed"], 1)
        self.assertEqual(first["packages"][0]["status"], "installed")
        self.assertEqual(archive.read_bytes(), before_archive)
        verified = star_map_import.verified_installed_package(
            self.program, row["packageId"], row["sha256"])
        self.assertEqual(verified["archiveSha256"], row["sha256"])
        self.assertEqual(verified["status"], "installed")
        view = snapshots.SnapshotPackage(self.program, row["packageId"])
        receipt = view.manifest["receipt"]
        self.assertEqual((receipt["packageId"], receipt["season"], receipt["archiveSha256"]),
                         (row["packageId"], row["season"], row["sha256"]))
        original_snapshot = view.manifest["snapshotId"]

        repeated = seed.install_bundled_maps(self.target, [row])
        self.assertEqual(repeated["installed"], 1)
        self.assertEqual(repeated["packages"][0]["status"], "already-installed")
        self.assertEqual(snapshots.SnapshotPackage(self.program, row["packageId"]).manifest["snapshotId"],
                         original_snapshot)

    def test_changed_zip_rejects_before_managed_base_repair(self):
        archive, row = self.make_package()
        raw = bytearray(archive.read_bytes())
        raw[-1] ^= 1
        archive.write_bytes(raw)
        sentinel = self.program / "base-repair-must-not-run.txt"
        sentinel.write_bytes(b"unchanged")
        repair = Mock(side_effect=AssertionError("base repair called before archive preflight"))
        with patch.object(seed.managed_base_resources, "repair_managed_base_resources", repair):
            with self.assertRaisesRegex(ValueError, "bundled-map-sha256-mismatch"):
                seed.install_bundled_maps(self.target, [row])
        repair.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"unchanged")

    def test_pending_additions_is_preserved_and_rejected_by_production_repair(self):
        _archive, row = self.make_package()
        marker = self.program / "work/map-packages/pending-additions.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b'{"fixture":"unfinished"}')
        before = marker.read_bytes()
        with self.assertRaisesRegex(ValueError, "additions-recovery-required"):
            seed.install_bundled_maps(self.target, [row])
        self.assertEqual(marker.read_bytes(), before)

    def test_existing_api_flag_semantics_for_fixed_s0_and_s1_v2(self):
        fixed = seed._check_package_row(dict(path="_installer-support/maps/fixed.zip", bytes=1,
            sha256="a" * 64, packageId="nightmare-suju", season="s0", versionedAuthor=True))
        self.assertTrue(fixed["versionedAuthor"])
        # A fixed S0 identity must use the production flat-receipt verifier;
        # it has no author-snapshot activation path.
        self.assertFalse(seed._verified_existing(self.program, fixed))
        with self.assertRaisesRegex(ValueError, "invalid-entry"):
            seed._check_package_row(dict(path="_installer-support/maps/author.zip", bytes=1,
                sha256="a" * 64, packageId="author-aichicu", season="s1", versionedAuthor=False))

    def test_support_path_traversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside-support-root"):
            seed._archive_path(self.target, "_installer-support/maps/../outside.zip")


if __name__ == "__main__":
    unittest.main()
