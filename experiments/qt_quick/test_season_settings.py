"""Synthetic season migration contracts, not installed Windows/game validation."""
import json
from pathlib import Path
import unittest

from client_paths import RuntimePaths
from client_settings import _validate
from season_context import CONTEXTS, SeasonContext, context_key, selected_profile
from test_client_settings import SettingsTests


class ContextTests(unittest.TestCase):
    def test_five_contexts_are_distinct_and_registration_is_declared(self):
        self.assertEqual(set(CONTEXTS), {"s0:nightmare", "s0:difficult", "s1:nightmare", "s1:difficult", "s1:difficulty2"})
        self.assertEqual(sum(c.registered for c in CONTEXTS.values()), 4)
        for bad in ("S1", "s2", "", None):
            with self.assertRaises(ValueError):
                SeasonContext(bad, "nightmare")
        with self.assertRaises(ValueError):
            context_key("s1:nightmare")

    def test_old_author_preferences_are_only_s0(self):
        values = dict(gameMode="nightmare", routeProfileId="custom:old", modeRouteProfiles={"difficult": "old-difficult"})
        self.assertEqual(selected_profile(values), "custom:old")
        self.assertEqual(selected_profile(values, "difficult"), "old-difficult")
        self.assertEqual(selected_profile(values, "nightmare", "s1"), "")
        values["contextRouteProfiles"] = {"s1:nightmare": "new-author"}
        self.assertEqual(selected_profile(values, "nightmare", "s1"), "new-author")
        self.assertEqual(selected_profile(values, "difficult", "s1"), "")

    def test_memory_and_legacy_settings_paths(self):
        paths = RuntimePaths(Path('/synthetic-install'), True)
        self.assertEqual(paths.memory_root("nightmare"), paths.work_root / "nightmare-memory")
        self.assertEqual(paths.settings_path, paths.work_root / 'nightmare-memory/ui-settings.json')
        roots = {paths.memory_root(c.mode, c.season) for c in CONTEXTS.values()}
        self.assertEqual(len(roots), 5)
        self.assertEqual(paths.memory_root("nightmare", "s1"), paths.work_root / 'seasons/s1/nightmare-memory')
        with self.assertRaises(ValueError):
            paths.memory_root("nightmare", "../../escape")

    def test_settings_context_whitelist(self):
        self.assertEqual(_validate("gameSeason", "s1"), "s1")
        self.assertEqual(_validate("contextRouteProfiles", {"s0:nightmare": "custom:legacy"}), {"s0:nightmare": "custom:legacy"})
        for table in ({"nightmare": "author"}, {"s2:nightmare": "author"}, {"s1:nightmare": ""}):
            with self.assertRaises(ValueError):
                _validate("contextRouteProfiles", table)


class SeasonPersistenceTests(SettingsTests):
    # Reuse the mature isolated sandbox/worker harness without rerunning its suite.
    def test_old_settings_restart_preserves_custom_galleries_and_unknown_keys(self):
        legacy = dict(gameMode="difficult", modeRouteProfiles={"nightmare": "custom:keep", "difficult": "old-author"},
                      routeProfileId="old-author", unrelated={"keep": [1, 2]})
        self.write(legacy)
        galleries = self.sandbox / 'custom-galleries.json'
        galleries.write_text('{"private-user-gallery":true}', encoding='utf-8')
        service = self.service()
        self.assertEqual(service.persistedValues['gameSeason'], 's0')
        self.assertEqual(selected_profile(service.persistedValues), 'old-author')
        self.assertEqual(json.loads(self.path.read_text()), legacy)
        self.assertTrue(service.setValue('gameSeason', 's1'))
        self.assertTrue(service.saveFields(['gameSeason']))
        self.wait(lambda: not service.busy)
        restarted = self.service()
        self.assertEqual(restarted.persistedValues['gameSeason'], 's1')
        self.assertEqual(selected_profile(restarted.persistedValues), '')
        disk = json.loads(self.path.read_text())
        self.assertEqual(disk['modeRouteProfiles'], legacy['modeRouteProfiles'])
        self.assertEqual(disk['unrelated'], legacy['unrelated'])
        self.assertEqual(galleries.read_text(), '{"private-user-gallery":true}')
        self.assertTrue(restarted.setValue('gameSeason', 's0'))
        self.assertEqual(selected_profile(restarted.values), 'old-author')

    def test_migration_freezes_each_legacy_s0_author_before_global_changes(self):
        self.write(dict(routeProfileId="legacy-global"))
        service = self.service()
        self.assertEqual(service.persistedValues["contextRouteProfiles"],
                         {"s0:nightmare": "legacy-global", "s0:difficult": "legacy-global"})
        service.setValue("routeProfileId", "new-nightmare-author")
        choices = dict(service.values["contextRouteProfiles"])
        choices["s0:nightmare"] = "new-nightmare-author"
        service.setValue("contextRouteProfiles", choices)
        self.assertEqual(selected_profile(service.values, "difficult", "s0"), "legacy-global")
        self.assertTrue(service.saveFields(["routeProfileId", "contextRouteProfiles"]))
        self.wait(lambda: not service.busy)
        restarted = self.service()
        self.assertEqual(selected_profile(restarted.persistedValues, "nightmare", "s0"), "new-nightmare-author")
        self.assertEqual(selected_profile(restarted.persistedValues, "difficult", "s0"), "legacy-global")
        self.assertEqual(selected_profile(restarted.persistedValues, "nightmare", "s1"), "")

    def test_round_guard_blocks_season_change_and_late_save(self):
        service = self.service()
        service.setModeChangeGuard(lambda: False)
        self.assertFalse(service.setValue('gameSeason', 's1'))
        self.assertEqual(service.values['gameSeason'], 's0')
        self.assertTrue(service.setValue('gameSeason', 's0'))
        service.setModeChangeGuard(lambda: True)
        self.assertTrue(service.setValue('gameSeason', 's1'))
        service.setModeChangeGuard(lambda: False)
        self.assertFalse(service.saveFields(['gameSeason']))
        self.assertFalse(self.path.exists())

    def test_imported_context_projection_blocks_direct_changes_and_allows_atomic_pair(self):
        service = self.service()
        original = service.values
        service.setAvailableContexts([])
        self.assertFalse(service.setGameContext('s1', 'difficult'))
        self.assertFalse(service.setValue('gameMode', 'difficult'))
        self.assertEqual(service.values, original)
        service.setAvailableContexts(['s1:difficult'])
        # The final pair must not visit an unavailable intermediate context.
        self.assertTrue(service.setGameContext('s1', 'difficult'))
        self.assertEqual((service.values['gameSeason'], service.values['gameMode']), ('s1', 'difficult'))
        flags = {row['id'] + ':' + mode: value['available']
                 for row in service.seasonOptions for mode, value in row['capabilities'].items()}
        self.assertTrue(flags['s1:difficult'])
        self.assertFalse(flags['s1:nightmare'])
        service.setAvailableContexts([])
        self.assertFalse(next(row for row in service.seasonOptions if row['id'] == 's1')['capabilities']['difficult']['available'])
        self.assertEqual((service.values['gameSeason'], service.values['gameMode']), ('s1', 'difficult'))


# Inherited tests belong to their original module; leave only this class's new cases.
for _name in dir(SettingsTests):
    if _name.startswith('test_') and _name not in SeasonPersistenceTests.__dict__:
        setattr(SeasonPersistenceTests, _name, None)
del SettingsTests

if __name__ == '__main__':
    unittest.main()
