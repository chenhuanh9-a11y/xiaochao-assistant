"""Future-provider seams stay fail-closed; synthetic IDs are not S1 resources."""
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from client_group_registry import ConfirmedFrontGroups, CONFIRMED_FRONT_GROUPS
from client_providers import EngineFactories, ProviderUnavailable, _build_memory_store


class SeasonProviderBoundaryTests(unittest.TestCase):
    def test_s0_binding_preserves_exact_root_and_context_and_is_immutable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            factories = EngineFactories(root)
            for mode in factories.MODES:
                binding = factories._resource_binding(mode, 's0')
                self.assertEqual(binding.root, root)
                self.assertEqual(binding.context.key, 's0:' + mode)
                with self.assertRaises(FrozenInstanceError):
                    binding.root = root / 'other'

    def test_s1_cannot_reach_io_or_cached_s0_engine_even_with_forged_readiness(self):
        factories = EngineFactories('/nonexistent-season-fixture')
        for mode in factories.MODES:
            factories._engines['s1:' + mode] = object()
            factories._reports['s1:' + mode] = dict(available=True, prepared=True)
            with patch('client_providers._read', side_effect=AssertionError('resource IO')) as read:
                for getter in (factories.get_engine, factories._build):
                    with self.assertRaisesRegex(ProviderUnavailable, 'season-resources-unavailable:s1'):
                        getter(mode, season='s1')
                read.assert_not_called()
        factories.invalidate()
        self.assertFalse(factories.capabilities('s1')['nightmare']['available'])

    def test_each_mode_keeps_its_own_engine_and_build_receives_explicit_season(self):
        factories = EngineFactories('/nonexistent-season-fixture')
        engines = {mode: SimpleNamespace(pack=SimpleNamespace(maps=['same-synthetic-id']),
                   identity_floors=('1',)) for mode in factories.MODES}
        def build(mode, *, season):
            self.assertEqual(season, 's0')
            return engines[mode]
        with patch.object(factories, '_build', side_effect=build) as builder:
            for _ in range(2):
                for mode in factories.MODES:
                    self.assertIs(factories.get_engine(mode, 's0'), engines[mode])
                    self.assertEqual(engines[mode].qt_context_key, 's0:' + mode)
            self.assertEqual(builder.call_count, 2)
            for mode in factories.MODES:
                with self.assertRaises(ProviderUnavailable):
                    factories.get_engine(mode, 's1')
            self.assertEqual(builder.call_count, 2)

    def test_future_season_cannot_inherit_s0_confirmed_groups_with_identical_map_ids(self):
        for mode, groups in CONFIRMED_FRONT_GROUPS.items():
            prefix = 'nightmare-' if mode == 'nightmare' else 'difficult-map-'
            pack = SimpleNamespace(mode=mode, by_id={prefix+n: object() for group in groups for n in group})
            old = ConfirmedFrontGroups(pack)
            future = ConfirmedFrontGroups(pack, season='s1')
            self.assertTrue(old.groups)
            self.assertEqual(future.groups, ())
            self.assertNotEqual(old.index_digest, future.index_digest)
            for map_id in pack.by_id:
                self.assertIsNone(future.group_for(map_id, floor='1', entrance_kind='front'))
            with self.assertRaisesRegex(ValueError, 'members-unavailable'):
                future.validate(old.groups[0], Mock())

    def test_non_s0_memory_helper_never_reads_legacy_portable_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for mode in ('nightmare', 'difficult'):
                pack = SimpleNamespace(mode=mode)
                with patch('nightmare_candidate.NightmareMemoryStore') as store:
                    _build_memory_store(pack, root/'memory', root, map_ui=Mock(), production=True, season='s0')
                    self.assertEqual(store.call_args.kwargs['portable_seed_path'],
                                     root/'references/nightmare-portable-layout-seeds.json')
                    _build_memory_store(pack, root/'s1-memory', root, map_ui=Mock(), production=True, season='s1')
                    self.assertEqual(store.call_args.args[1], root/'s1-memory')
                    self.assertIsNone(store.call_args.kwargs['portable_seed_path'])

    def test_disabled_seed_store_performs_no_read_even_if_old_file_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.qt-no-portable-seeds.json').write_text('{"seeds": []}')
            for mode in ('nightmare', 'difficult'):
                memory = _build_memory_store(SimpleNamespace(mode=mode, by_id={}),
                    root / 'memory' / mode, root, map_ui=Mock(), production=True, season='s1')
                self.assertIsNone(memory.portable_seed_path)
                with patch.object(Path, 'read_text', side_effect=AssertionError('seed IO')):
                    self.assertEqual(memory.portable_layout_seeds(), [])

    def test_invalid_context_never_resolves_a_binding(self):
        factories = EngineFactories('/nonexistent-season-fixture')
        for mode, season in (('nightmare', 's999'), ('other', 's0')):
            with self.assertRaises(ValueError):
                factories._resource_binding(mode, season)


if __name__ == '__main__':
    unittest.main()
