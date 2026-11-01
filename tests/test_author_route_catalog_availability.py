"""Catalog snapshot availability, bounded disk probes and live route validation."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from author_route_catalog import AuthorRouteCatalog


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog_path = self.root / 'catalog.json'
        self.catalog_path.write_text(json.dumps(dict(
            schemaVersion=1, profiles=[dict(
                id='author', displayName='Author', authorName='Author',
                routeFamilyName='Routes', runtimeEnabled=True,
                settingsSelectable=True, alignedOverlayEligible=False,
                maps={'map': [dict(id='first', displayName='First',
                    layers={'1': 'first.png', '2': 'second.png'},
                    identityStatus='fixture', displayPolicy='fixture')]} )])), encoding='utf-8')

    def catalog(self):
        return AuthorRouteCatalog(self.catalog_path, asset_base=self.root)

    def test_missing_assets_scan_once_and_unknown_ids_are_not_cached(self):
        catalog = self.catalog()
        with patch.object(Path, 'is_file', autospec=True, return_value=False) as probe:
            self.assertFalse(catalog.profile_has_assets('author'))
            self.assertFalse(catalog.profile_has_assets('author'))
            self.assertEqual(probe.call_count, 2)
            for index in range(100):
                self.assertFalse(catalog.profile_has_assets('unknown-' + str(index)))
            self.assertEqual(probe.call_count, 2)
        self.assertEqual(len(catalog._profile_asset_cache), 1)

    def test_positive_result_short_circuits_and_is_memoized(self):
        catalog = self.catalog()
        with patch.object(Path, 'is_file', autospec=True, return_value=True) as probe:
            self.assertTrue(catalog.profile_has_assets('author'))
            self.assertTrue(catalog.profile_has_assets('author'))
            self.assertEqual(probe.call_count, 1)

    def test_equal_but_replaced_profile_rescans_by_identity(self):
        catalog = self.catalog()
        with patch.object(Path, 'is_file', autospec=True, return_value=False) as probe:
            self.assertFalse(catalog.profile_has_assets('author'))
            old = catalog.profiles['author']
            catalog.profiles['author'] = replace(old)
            self.assertEqual(old, catalog.profiles['author'])
            self.assertIsNot(old, catalog.profiles['author'])
            self.assertFalse(catalog.profile_has_assets('author'))
            self.assertEqual(probe.call_count, 4)

    def test_replacement_with_new_paths_changes_availability(self):
        catalog = self.catalog()
        self.assertFalse(catalog.profile_has_assets('author'))
        profile = catalog.profiles['author']
        variant = profile.maps['map'][0]
        actual = self.root / 'imported.png'
        actual.write_bytes(b'fixture')
        new_variant = replace(variant, layers={'1': actual})
        catalog.profiles['author'] = replace(profile, maps={'map': (new_variant,)})
        self.assertTrue(catalog.profile_has_assets('author'))

    def test_new_catalog_and_shallow_copy_rescan_after_files_change(self):
        catalog = self.catalog()
        self.assertFalse(catalog.profile_has_assets('author'))
        (self.root / 'first.png').write_bytes(b'fixture')
        self.assertFalse(catalog.profile_has_assets('author'))  # Same snapshot.
        self.assertTrue(self.catalog().profile_has_assets('author'))
        cloned = copy.copy(catalog)
        self.assertTrue(cloned.profile_has_assets('author'))
        self.assertIsNot(cloned._profile_asset_cache, catalog._profile_asset_cache)
        self.assertFalse(catalog.profile_has_assets('author'))

    def test_positive_hint_never_bypasses_live_route_path_check(self):
        catalog = self.catalog()
        actual = self.root / 'first.png'
        actual.write_bytes(b'fixture')
        self.assertTrue(catalog.profile_has_assets('author'))
        self.assertEqual(catalog.route_path('author', 'map', '1'), actual)
        actual.unlink()
        self.assertTrue(catalog.profile_has_assets('author'))
        self.assertIsNone(catalog.route_path('author', 'map', '1'))
        self.assertIsNone(catalog.route_path('author', 'map', '2'))
        self.assertFalse(self.catalog().profile_has_assets('author'))

    def test_explicit_refresh_invalidates_negative_and_positive_hints(self):
        catalog = self.catalog()
        self.assertFalse(catalog.profile_has_assets('author'))
        actual = self.root / 'first.png'
        actual.write_bytes(b'fixture')
        catalog.invalidate_asset_availability()
        self.assertTrue(catalog.profile_has_assets('author'))
        actual.unlink()
        self.assertTrue(catalog.profile_has_assets('author'))
        catalog.invalidate_asset_availability()
        self.assertFalse(catalog.profile_has_assets('author'))

    def test_copy_refresh_does_not_clear_original_cache(self):
        catalog = self.catalog()
        self.assertFalse(catalog.profile_has_assets('author'))
        cloned = copy.copy(catalog)
        cloned.invalidate_asset_availability()
        self.assertEqual(len(cloned._profile_asset_cache), 0)
        self.assertEqual(len(catalog._profile_asset_cache), 1)

    def test_removed_then_reintroduced_profile_is_not_stale(self):
        catalog = self.catalog()
        self.assertFalse(catalog.profile_has_assets('author'))
        profile = catalog.profiles.pop('author')
        self.assertFalse(catalog.profile_has_assets('author'))
        self.assertNotIn('author', catalog._profile_asset_cache)
        (self.root / 'first.png').write_bytes(b'fixture')
        catalog.profiles['author'] = profile
        self.assertTrue(catalog.profile_has_assets('author'))

    def test_cache_bound_uses_known_profiles_only(self):
        catalog = self.catalog()
        original = catalog.profiles['author']
        limit = catalog._PROFILE_ASSET_CACHE_LIMIT
        with patch.object(Path, 'is_file', autospec=True, return_value=False) as probe:
            for index in range(limit + 5):
                key = 'author-' + str(index)
                catalog.profiles[key] = replace(original, id=key)
                self.assertFalse(catalog.profile_has_assets(key))
            self.assertEqual(len(catalog._profile_asset_cache), limit)
            self.assertNotIn('author-0', catalog._profile_asset_cache)
            self.assertFalse(catalog.profile_has_assets('author-0'))
            self.assertEqual(probe.call_count, (limit+6)*2)
            self.assertEqual(len(catalog._profile_asset_cache), limit)


if __name__ == '__main__':
    unittest.main()
