import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "package_profiles", Path(__file__).resolve().parents[1] / "tools/package_profiles.py")
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


class PackageProfileTests(unittest.TestCase):
    def test_display_images_omitted_but_identity_and_import_metadata_preserved(self):
        names = ["assets/suju/map.png", "assets/zhan10-native/map.webp",
                 "assets/zhan10-legacy-clean/map.png", "assets/authors/nightingale/map.png",
                 "assets/authors/wang-qingxin/map.png", "references/fast-structure/1.npz",
                 "recognition/legacy-clean-map29/index.json", "references/author-packs/nightingale/catalog.json",
                 "references/author-route-catalog.json", "difficult-native-manifest.json",
                 "references/native-similarity-groups.json.gz", "assets/brand/xingtu.ico"]
        rows = {name: {"bytes": i, "sha256": str(i)} for i, name in enumerate(names)}
        retained, omitted = profiles.partition_resources(rows)
        self.assertEqual(set(omitted), set(names[:5]))
        self.assertEqual(set(retained), set(names[5:]))
        self.assertEqual(retained | omitted, rows)
        for name in rows:
            self.assertIs((retained | omitted)[name], rows[name])

    def test_invalid_inventory_paths_are_rejected(self):
        for name in ("../map.png", "/map.png", "C:/map.png", "assets\\suju\\map.png",
                     "assets//suju/map.png", "", "."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                profiles.partition_resources({name: {}})

    def test_software_delivery_is_setup(self):
        self.assertEqual(profiles.SOFTWARE_DELIVERY_FORMAT, "setup")


if __name__ == "__main__":
    unittest.main()
