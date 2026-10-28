"""Canonical UI route labels survive older installed catalog metadata."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from map_resource_model import MapResourceModel


class ResourceModelLabelTests(unittest.TestCase):
    def test_current_route_name_wins_with_or_without_installed_author_layers(self):
        for installed in (False, True):
            with self.subTest(installed=installed):
                layers = {'1': Path('fixture-author.png')} if installed else {}
                item = SimpleNamespace(id='test-map', name='Test map', layers=layers)
                pack = SimpleNamespace(maps=[item], route_profiles={'test-map': 'nightingale'})
                profile = SimpleNamespace(runtime_enabled=True, settings_selectable=True,
                                          display_name='夜莺', author_name='夜莺', route_family_name='路线', maps={})
                catalog = SimpleNamespace(profiles={'nightingale': profile})
                model = MapResourceModel(pack, catalog, [('nightingale', '爱吃醋')])
                self.assertEqual(model.profiles['nightingale'], '爱吃醋')
                self.assertEqual(model.author_details['nightingale'], '爱吃醋 · 路线')
                self.assertEqual(profile.display_name, '夜莺')
                self.assertEqual(model.maps('nightingale')[0].variants[0].layers, layers)

    def test_catalog_only_profile_keeps_its_display_name(self):
        profile = SimpleNamespace(runtime_enabled=False, settings_selectable=True,
                                  display_name='Catalog author', author_name='Author', route_family_name='Route')
        model = MapResourceModel(SimpleNamespace(maps=[]), SimpleNamespace(profiles={'catalog-only': profile}),
                                 [('nightingale', '爱吃醋')])
        self.assertEqual(model.profiles, {'nightingale': '爱吃醋', 'catalog-only': 'Catalog author'})

    def test_uninstalled_nightingale_without_runtime_option_uses_canonical_name(self):
        profile = SimpleNamespace(runtime_enabled=False, settings_selectable=True,
                                  display_name='夜莺', author_name='夜莺', route_family_name='路线')
        model = MapResourceModel(SimpleNamespace(maps=[]), SimpleNamespace(profiles={'nightingale': profile}))
        self.assertEqual(model.profiles['nightingale'], '爱吃醋')
        self.assertEqual(model.author_details['nightingale'], '爱吃醋 · 路线')
        self.assertEqual(profile.display_name, '夜莺')
        self.assertEqual(profile.author_name, '夜莺')

    def test_other_profile_id_is_not_renamed_by_matching_legacy_text(self):
        profile = SimpleNamespace(runtime_enabled=False, settings_selectable=True,
                                  display_name='夜莺', author_name='夜莺', route_family_name='路线')
        model = MapResourceModel(SimpleNamespace(maps=[]), SimpleNamespace(profiles={'other-author': profile}))
        self.assertEqual(model.profiles['other-author'], '夜莺')
        self.assertEqual(model.author_details['other-author'], '夜莺 · 路线')


if __name__ == '__main__':
    unittest.main()
