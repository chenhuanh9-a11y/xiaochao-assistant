import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from bundled_author_resources import display_path, bundled_summary


class BundledAuthorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.local = self.root / 'user'
        self.default = self.root / 'program'
        self.relative = 'assets/zhan10-native/map/floor-1.webp'
        self.path = self.local / self.relative
        self.bundled = self.default / self.relative
        self.bundled.parent.mkdir(parents=True)
        self.bundled.write_bytes(b'default-display')
        self.sha = hashlib.sha256(self.bundled.read_bytes()).hexdigest()

    def test_missing_display_uses_hash_bound_default_without_writing_user_tree(self):
        self.assertEqual(display_path(self.path, self.local, self.default, self.sha), self.bundled)
        self.assertFalse(self.local.exists())

    def test_existing_user_file_wins_even_when_different(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'custom')
        self.assertEqual(display_path(self.path, self.local, self.default, self.sha), self.path)
        self.assertEqual(self.path.read_bytes(), b'custom')

    def test_hash_size_and_resource_kind_must_match(self):
        self.assertEqual(display_path(self.path, self.local, self.default, '0'*64), self.path)
        self.assertEqual(display_path(self.path, self.local, self.default, self.sha, 1), self.path)
        for relative in ('references/map.npz', 'assets/authors/other/map.png', '../outside.png'):
            path = self.local / relative
            self.assertEqual(display_path(path, self.local, self.default, self.sha), path)

    def test_bundled_inventory_has_no_import_or_removal_ownership(self):
        inventory = dict(schema='xingtu-bundled-author-v1', packageId='difficult-zhan10',
            files={self.relative:dict(bytes=self.bundled.stat().st_size, sha256=self.sha)})
        (self.default/'inventory.json').write_text(json.dumps(inventory), encoding='utf8')
        row = bundled_summary(self.default)
        self.assertEqual(row['status'], 'bundled')
        self.assertFalse(row['canRemove'])
        self.assertFalse(self.local.exists())
        self.bundled.write_bytes(b'corrupt')
        self.assertIsNone(bundled_summary(self.default))

    def test_linked_default_is_never_selected(self):
        link = self.root/'linked'
        try:
            link.symlink_to(self.default, target_is_directory=True)
        except OSError as error:
            self.skipTest(str(error))
        self.assertEqual(display_path(self.path, self.local, link, self.sha), self.path)

    def test_opaque_full_sheet_normalizes_alpha_but_overlay_stays_strict(self):
        from difficult_native_context import DifficultNativeContext
        from PIL import Image
        path = self.root/'opaque.png'
        Image.new('RGB', (3, 2), (15, 25, 35)).save(path)
        context = DifficultNativeContext.__new__(DifficultNativeContext)
        context._full = {'map':path}
        context._display_paths = {path}
        pixels = context.display_pixels(path, 'full-reference')
        self.assertEqual(pixels.shape, (2, 3, 4))
        self.assertTrue((pixels[:,:,3] == 255).all())
        with self.assertRaises(ValueError):
            context.display_pixels(path, 'aligned-overlay')


if __name__ == '__main__':
    unittest.main()
