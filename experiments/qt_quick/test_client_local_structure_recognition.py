"""Imported source preparation and real entry jobs; blank pixels grant no identity."""
import unittest
from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import Mock

import numpy as np

from client_local_structure_recognition import PreparedLocalStructureRecognition
from client_imported_providers import build_native_engine
from client_paths import RuntimePaths
from entry_evidence_work import FrameSnapshot
from s1_structure_selection import StructureCatalog
import test_structure_pose_inputs as fixture


class LocalStructureRecognitionTests(unittest.TestCase):
    setUpClass = fixture.StructurePoseInputTests.__dict__['setUpClass']
    archive = fixture.StructurePoseInputTests.__dict__['archive']
    engine = fixture.StructurePoseInputTests.engine

    def prepared(self, season='s0', mode='difficult'):
        decoded, _, current = self.engine(season, mode)
        owners = decoded.qt_shared_provider._operation_owners
        temporary = tempfile.TemporaryDirectory(prefix='local-recognition-runtime-')
        self.addCleanup(temporary.cleanup)
        engine = build_native_engine(owners.base_view, owners.controls_view,
            RuntimePaths(Path(temporary.name), False), season=season, mode=mode, require_rank=False)
        catalog = StructureCatalog.from_engine(engine)
        return engine, PreparedLocalStructureRecognition(engine, catalog), current

    def test_static_domain_and_query_caches_are_independent(self):
        engine, direct, _ = self.prepared()
        self.assertEqual(set(direct.index.domain),
            {(mid, floor) for mid in direct.catalog.map_ids for floor in direct.catalog.floors})
        first, second = direct._new_query_engine(), direct._new_query_engine()
        self.assertIsNot(first, engine)
        self.assertIsNot(first.entry_identity, engine.entry_identity)
        self.assertIsNot(first.entry_identity, second.entry_identity)
        self.assertIsNot(first.entry_identity._wall_lines, second.entry_identity._wall_lines)
        self.assertIs(first.side_entry_identity.geometry_provider.__self__, first.entry_identity)
        self.assertIs(second.side_entry_identity.author_visible_provider.__self__, second.entry_identity)
        self.assertIs(first.side_entry_identity.entries, second.side_entry_identity.entries)
        with self.assertRaises(ValueError):
            first.side_entry_identity.entries[0][1].setflags(write=True)

    def test_both_s0_modes_run_complete_real_jobs_without_mutating_live_evidence(self):
        snapshot = FrameSnapshot(np.zeros((720, 1280, 3), np.uint8))
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, direct, _ = self.prepared(mode=mode)
                before = getattr(engine, 'last_difficult_evidence', None)
                report = direct.run(snapshot, direct.catalog.floors[0])
                self.assertEqual(report['status'], 'complete')
                self.assertEqual(report['frameKey'], snapshot.frame_key)
                self.assertEqual({row['mapId'] for row in report['rows']}, set(direct.catalog.map_ids))
                self.assertFalse(any(row['identityHardGatePassed'] for row in report['rows']))
                self.assertIs(getattr(engine, 'last_difficult_evidence', None), before)

    def test_cancel_and_source_replacement_never_offer_identity(self):
        engine, direct, _ = self.prepared()
        snapshot = FrameSnapshot(np.zeros((30, 40, 3), np.uint8))
        self.assertEqual(direct.run(snapshot, '1', cancelled=lambda: True)['rows'], [])
        engine.side_entry_identity.entries = list(engine.side_entry_identity.entries)
        self.assertFalse(direct.current())
        self.assertEqual(direct.run(snapshot, '1')['status'], 'cancelled')

    def test_catalog_cannot_relabel_the_actual_resource_context(self):
        engine, direct, _ = self.prepared()
        for foreign in (replace(direct.catalog, resource_key='other-resource'),
                        replace(direct.catalog, map_ids=direct.catalog.map_ids[:-1])):
            with self.subTest(catalog=foreign):
                with self.assertRaisesRegex(ValueError, 'catalog-context-mismatch'):
                    PreparedLocalStructureRecognition(engine, foreign)

    def test_s1_prepares_the_same_numeric_index_without_upgrading_source_purpose(self):
        snapshot = FrameSnapshot(np.zeros((30, 40, 3), np.uint8))
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                _, direct, _ = self.prepared('s1', mode)
                self.assertTrue(direct.index.members)
                report = direct.run(snapshot, direct.catalog.floors[0])
                self.assertEqual(report['status'], 'unavailable')
                self.assertEqual(report['reason'], 'proposal-only-entry-evidence')
                self.assertEqual(report['rows'], [])


class LocalStructureCancellationTests(unittest.TestCase):
    def test_cancellation_after_real_collection_does_not_export_qualified_diagnostics(self):
        import test_observation_work_scheduler as observations
        source = observations.EntryObservationTests('runTest')
        source.setUp()
        self.addCleanup(source.doCleanups)
        engine = source.engine(unique=True)
        direct = object.__new__(PreparedLocalStructureRecognition)
        direct.engine, direct._revision = engine, 0
        direct.catalog = StructureCatalog('s0', 'nightmare', 'fixture-current-resource',
                                           ('map-a', 'map-b'), ('2',))
        direct.index = Mock()
        direct.current = lambda: True  # Resource preparation/cloning is outside this result-boundary case.
        direct._new_query_engine = lambda: engine
        cancelled = [False]
        collect = engine.collect_observation_rows
        def collecting(work):
            rows = collect(work)  # The original collector produces actual qualified main evidence.
            cancelled[0] = True
            return rows
        engine.collect_observation_rows = collecting
        report = direct.run(FrameSnapshot(source.fixture.frame), '2', cancelled=lambda: cancelled[0])
        self.assertTrue(any(row.get('identityVerified') for row in engine.last_difficult_evidence['tilemapCandidates']))
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(report['rows'], [])
        self.assertIsNone(report['diagnostics'])


if __name__ == '__main__':
    unittest.main()
