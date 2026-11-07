"""Merged author registration: exact namespaces, mode/prefs and legacy isolation."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import star_map_import as importer
from optional_author_displays import OptionalAuthorDisplays
import test_optional_author_coverage as coverage_fixture
import test_live_author_import as live_fixture

CASES = (
    ('nightmare-zhan10', 'zhan10-nightmare', 'nightmare', '展十 · 噩梦'),
    ('nightmare-suju-speed', 'suju-speed-nightmare', 'nightmare', '酥居速刷 · 噩梦'),
    ('difficult-suju-speed', 'suju-speed-difficult', 'difficult', '酥居速刷 · 困难'),
)


class RegistrationMergeTests(unittest.TestCase):
    def test_exact_namespaces_and_difficult_dependency_gate(self):
        from author_package_management import PACKAGE_NAMES
        for package, profile, mode, title in CASES:
            with self.subTest(package=package):
                self.assertEqual(importer.AUTHOR_PACKAGES[package], profile)
                self.assertEqual(PACKAGE_NAMES[package], title)
                f = live_fixture.LiveAuthorImportTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
                asset = f'_internal/assets/authors/{profile}/m1.png'
                if mode == 'difficult':
                    with self.assertRaisesRegex(importer.ImportRejected, 'base-dependencies-not-declared'):
                        importer.inspect_package(f.package(package, {asset: b'display'}), f.root)
                    f.dependencies = []
                    for index, name in enumerate(sorted(importer.DIFFICULT_AUTHOR_DEPENDENCIES)):
                        data = f'difficult-{index}'.encode(); f.write(name, data)
                        f.dependencies.append(live_fixture.row(name, data))
                self.assertEqual(importer.inspect_package(f.package(package, {asset: b'display'}), f.root)['packageId'], package)
                for forbidden in ('_internal/assets/suju/foreign.png', live_fixture.CORE):
                    with self.assertRaises(importer.ImportRejected):
                        importer.inspect_package(f.package(package, {forbidden: b'forbidden'}), f.root)

    def test_registered_profiles_keep_exact_bindings_and_mode_isolation(self):
        for _, profile, mode, title in CASES:
            with self.subTest(profile=profile):
                f = coverage_fixture.OptionalAuthorCoverageTests(); f.setUp(); self.addCleanup(f.temp.cleanup)
                f.folder = f.root / 'references/author-packs' / profile; f.folder.mkdir(parents=True)
                f.raw['profiles'][0]['id'] = profile; f.payload['profileId'] = profile
                for entry in f.entries: entry['profileId'] = profile
                f.raw['profiles'][0]['maps'].pop('m2'); f.payload['entries'] = f.entries[:1]
                (f.folder / 'catalog.json').write_text(json.dumps(f.raw), encoding='utf-8')
                (f.folder / 'display-bindings.json').write_text(json.dumps(f.payload), encoding='utf-8')
                loaded = OptionalAuthorDisplays(f.root, f.pack, None, f.adapters, f.versions, mode=mode)
                self.assertEqual(loaded.profiles, {profile}); self.assertFalse(loaded.errors)
                self.assertIsNone(loaded.binding(profile, 'm2', '1'))
                other = 'difficult' if mode == 'nightmare' else 'nightmare'
                self.assertEqual(OptionalAuthorDisplays(f.root, f.pack, None, f.adapters, f.versions, mode=other).profiles, set())


if __name__ == '__main__':
    unittest.main()
