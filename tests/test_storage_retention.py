"""Bounded on-disk diagnostics, samples and download caches."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import nightmare_candidate as candidate  # noqa: E402
from star_map_download import prune_installed_downloads  # noqa: E402


def function(filename, name, namespace):
    """Load one top-level function without importing the Windows/Tk runtime."""
    tree = ast.parse((APP / filename).read_text(encoding='utf-8-sig'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), filename, 'exec'), namespace)
    return namespace[name]


def touch(path: Path, age: int, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    stamp = 1_700_000_000 + age
    os.utime(path, (stamp, stamp))
    return path


def frame(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (60, 120, 3), dtype=np.uint8)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        # Resolved: /var on macOS is a symlink and the import guards reject links.
        self.root = Path(self.folder.name).resolve()
        pack = SimpleNamespace(pack_id='p', by_id={'m': SimpleNamespace(id='m', name='图')},
                               maps=[SimpleNamespace(id='m', name='图')])
        self.store = candidate.NightmareMemoryStore(pack, root=self.root / 'memory',
                                                    portable_seed_path=self.root / 'none.json')

    def tearDown(self):
        self.folder.cleanup()

    def test_event_and_startup_logs_keep_newest_per_kind(self):
        prune = function('identityv_daily_assistant.py', 'prune_diagnostic_logs',
                         dict(Path=Path, DIAGNOSTIC_LOG_LIMIT=8))
        live = self.root / 'live'
        for age in range(12):
            touch(live / f'events-{age:02d}.jsonl', age)
        touch(live / 'startup-error-a.log', 1)
        touch(live / 'hang-diagnostics' / 'thread-stacks-1.jsonl', 0)
        prune(live, 7)
        self.assertEqual(sorted(p.name for p in live.glob('events-*.jsonl')),
                         [f'events-{age:02d}.jsonl' for age in range(5, 12)])
        self.assertTrue((live / 'startup-error-a.log').is_file())
        self.assertTrue((live / 'hang-diagnostics' / 'thread-stacks-1.jsonl').is_file())

    def test_failure_budget_evicts_oldest_images_not_the_journal(self):
        failures = self.root / 'memory' / 'failures' / 'm' / '1'
        for age in range(5):
            touch(failures / f'{age}.jpg', age, 100)
        touch(failures / '.inflight.tmp', 0, 100)
        self.store._failure_sampling.max_disk_bytes = 450
        self.store._reserve_failure_bytes(150)
        # 600 bytes counted (the temporary too); three oldest images go to fit 150 more.
        self.assertEqual(sorted(p.name for p in failures.glob('*.jpg')), ['3.jpg', '4.jpg'])
        self.assertTrue((failures / '.inflight.tmp').is_file())
        self.assertEqual(self.store._failure_disk_bytes, 450)
        with self.assertRaisesRegex(OSError, 'budget-exhausted'):
            self.store._reserve_failure_bytes(1000)

    def test_success_frames_keep_newest_per_floor(self):
        fit = dict(success=True, profileCurrentFrameValidated=True, matrixSourceToClient=np.eye(3).tolist())
        for seed in range(9):
            self.store.record_alignment_success_evidence('m', '1', fit, frame(seed), route_profile_id='r')
        rows = (self.root / 'memory' / 'alignment-success-evidence.jsonl').read_text('utf-8').splitlines()
        kept = list((self.root / 'memory' / 'runtime-successes' / 'm' / '1').iterdir())
        self.assertEqual(len(rows), 9)
        self.assertEqual(len(kept), candidate.SUCCESS_SAMPLES_PER_FLOOR)
        self.assertIn(self.root / 'memory' / json.loads(rows[-1])['samplePath'], kept)

    def test_confirmation_crops_bounded_without_changing_exemplars(self):
        rows = [self.store.record_confirmation('m', '1', frame(seed)) for seed in range(16)]
        on_disk = list((self.root / 'memory' / 'samples' / 'm' / '1').iterdir())
        self.assertEqual(len(on_disk), candidate.CONFIRMATION_SAMPLES_ON_DISK)
        newest = [self.root / 'memory' / row['samplePath'] for row in reversed(rows[-6:])]
        self.assertEqual(self.store.exemplar_paths('1')['m'], newest)
        self.assertEqual(self.store.confirmation_counts('1')['m'], candidate.CONFIRMATION_SAMPLES_ON_DISK)
        self.assertEqual(len(self.store.records()), 16)

    def test_only_installed_download_archives_are_removed(self):
        state = self.root / 'work' / 'map-packages'
        downloads = state / 'downloads'
        installed, other = 'a' * 64, 'b' * 64
        touch(downloads / f'pack-{installed}.zip', 0)
        touch(downloads / f'pack-{other}.zip', 0)
        touch(downloads / f'new-{installed}.zip', 0)
        touch(downloads / 'star-map-x.part', 0)
        (state / 'pack.json').write_text(json.dumps(dict(packageId='pack', archiveSha256=installed)), 'utf-8')
        self.assertEqual(prune_installed_downloads(downloads, self.root), [f'pack-{installed}.zip'])
        self.assertEqual(sorted(p.name for p in downloads.iterdir()),
                         [f'new-{installed}.zip', f'pack-{other}.zip', 'star-map-x.part'])

    def test_pending_import_work_keeps_every_download(self):
        state = self.root / 'work' / 'map-packages'
        archive = touch(state / 'downloads' / f'pack-{"a" * 64}.zip', 0)
        (state / 'pack.json').write_text(json.dumps(dict(packageId='pack', archiveSha256='a' * 64)), 'utf-8')
        for marker in ('pending-upgrade.json', 'main-import-request.json', 'import.lock'):
            with self.subTest(marker=marker):
                (state / marker).write_text('{}', 'utf-8')
                self.assertEqual(prune_installed_downloads(state / 'downloads', self.root), [])
                self.assertTrue(archive.is_file())
                (state / marker).unlink()
        self.assertEqual(prune_installed_downloads(state / 'downloads', self.root), [archive.name])


if __name__ == '__main__':
    unittest.main()
