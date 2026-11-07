"""Offline static cache ownership/budget checks, not game latency tests."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import gc
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

import numpy as np
import fast_structure_alignment as fast
import idvb0059_steady as steady
from structure_cache_memory import retained_array_bytes, trim_prepared_cache, reference_arrays


def make_reference(root, name, size=256):
    mask = np.zeros((size, size), np.uint8)
    mask[16:-16, 16:-16] = 1
    mask[32:-32:32, 32:-32] = 0
    metadata = {'binding': {'mapId': name, 'floor': '1', 'profile': 'synthetic'}}
    reference = SimpleNamespace(clean_mask=mask, validation_mask=mask, known_boundary=None,
                                unknown_mask=None, metadata=metadata,
                                path=Path(root) / 'references' / (name + '.npz'), resource_root=Path(root))
    path = fast.prepared_path_for(reference)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, sourceMetadata=json.dumps(metadata), distance=np.zeros(mask.shape, np.uint16),
             chains=np.zeros((0, 3, 2), np.float32), rays=np.zeros((0, 2, 2), np.float32),
             pitch=0., shape=mask.shape)
    return reference


class CacheMemoryTests(unittest.TestCase):
    def setUp(self):
        self.network_attempts = []
        def forbidden(*args, **kwargs):
            self.network_attempts.append(True)
            raise AssertionError('offline test attempted network')
        for owner, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                            (socket.socket, 'sendto'), (socket, 'create_connection'),
                            (socket, 'getaddrinfo')):
            self.enterContext(patch.object(owner, name, forbidden))
        self.tmp = self.enterContext(TemporaryDirectory())
        self.clear()
        self.addCleanup(self.clear)

    @staticmethod
    def clear():
        with fast._prepared_lock:
            fast._prepared.clear()
        with steady._cache_lock:
            steady._cache.clear()

    def tearDown(self):
        self.assertEqual(self.network_attempts, [])

    def test_views_count_retained_base_once_not_view_size(self):
        owner = np.zeros((1024, 1024), np.uint8)
        small = owner[10:12, 10:12]
        negative = owner[::-1]
        stride = np.lib.stride_tricks.as_strided(small, (100, 100), (0, 0))
        self.assertEqual(retained_array_bytes([small, negative, stride, owner.view(np.uint16)]), owner.nbytes)

    def test_shared_python_buffer_owner_counted_once(self):
        owner = bytearray(8192)
        a = np.frombuffer(owner, np.uint8, count=10)
        b = np.frombuffer(memoryview(owner), np.uint16, offset=100, count=10)
        self.assertEqual(retained_array_bytes([a, b, None]), 8192)

    def test_budget_uses_union_of_shared_owners_and_original_entry_limit(self):
        a = np.zeros(100, np.uint8)
        cache = OrderedDict((i, (a[i:i+1],)) for i in range(4))
        trim_prepared_cache(cache, max_entries=4, max_bytes=100, arrays_for=lambda entry: entry)
        self.assertEqual(len(cache), 4)
        trim_prepared_cache(cache, max_entries=3, max_bytes=100, arrays_for=lambda entry: entry)
        self.assertEqual(list(cache), [1, 2, 3])

    def test_oversized_singleton_is_retained_and_old_holders_survive(self):
        a, b = np.zeros(10, np.uint8), np.ones(100, np.uint8)
        cache = OrderedDict([(1, (a,)), (2, (b,))])
        trim_prepared_cache(cache, max_entries=6, max_bytes=20, arrays_for=lambda entry: entry)
        self.assertEqual(list(cache), [2])
        self.assertEqual(int(a.sum()), 0)
        self.assertEqual(int(b.sum()), 100)

    def test_fast_budget_lru_keeps_active_result_and_exact_reference_identity(self):
        refs = [make_reference(self.tmp, str(i)) for i in range(4)]
        unit = 3 * refs[0].clean_mask.nbytes
        with patch.object(fast, 'PREPARED_CACHE_MAX_BYTES', unit * 2):
            a, b = fast.load_prepared(refs[0]), fast.load_prepared(refs[1])
            self.assertIs(fast.load_prepared(refs[0]), a)
            c = fast.load_prepared(refs[2])
            self.assertIs(fast.load_prepared(refs[0]), a)
            self.assertEqual(fast.prepared_memory_snapshot()['entries'], 2)
            self.assertEqual(fast.prepared_memory_snapshot()['retainedArrayBytes'], unit * 2)
            self.assertEqual(int(b.distance.sum()), 0)  # evicted, still in flight
            replacement = SimpleNamespace(**vars(refs[0]))
            self.assertIsNot(fast.load_prepared(replacement), a)
            self.assertEqual(c.distance.shape, refs[2].clean_mask.shape)

    def test_steady_budget_lru_and_shared_mask_count(self):
        refs = [make_reference(self.tmp, str(i)) for i in range(3)]
        first = steady.prepare(refs[0])
        unit = steady.prepared_memory_snapshot()['retainedArrayBytes']
        expected = refs[0].clean_mask.nbytes + sum(a.nbytes for a in
            (first.edge, first.k3, first.k5, first.distance, first.reference_points))
        self.assertEqual(unit, expected)
        with patch.object(steady, 'PREPARED_CACHE_MAX_BYTES', unit * 2):
            second = steady.prepare(refs[1])
            self.assertIs(steady.prepare(refs[0]), first)
            steady.prepare(refs[2])
            self.assertEqual(steady.prepared_memory_snapshot()['entries'], 2)
            self.assertIs(steady.prepare(refs[0]), first)
            self.assertTrue(np.isfinite(second.distance).all())
            self.assertFalse(first.edge.flags.writeable)

    def test_oversized_real_products_remain_hot_without_preparation_or_accounting(self):
        ref = make_reference(self.tmp, 'large')
        for module, call in ((fast, fast.load_prepared), (steady, steady.prepare)):
            with patch.object(module, 'PREPARED_CACHE_MAX_BYTES', 1):
                product = call(ref)
                self.assertTrue(module.prepared_memory_snapshot()['oversizedSingleton'])
                with patch.object(module, 'trim_prepared_cache', side_effect=AssertionError('hot accounting')):
                    with patch.object(np, 'load', side_effect=AssertionError('hot disk load')):
                        with patch.object(steady.cv2, 'morphologyEx', side_effect=AssertionError('hot preparation')):
                            self.assertIs(call(ref), product)

    def test_invalid_fast_archive_does_not_evict_valid_hot_entry(self):
        ref = make_reference(self.tmp, 'valid')
        good = fast.load_prepared(ref)
        bad = make_reference(self.tmp, 'bad')
        np.savez(fast.prepared_path_for(bad), distance=np.zeros((1, 1)))
        self.assertIsNone(fast.load_prepared(bad))
        self.assertIs(fast.load_prepared(ref), good)
        self.assertEqual(fast.prepared_memory_snapshot()['entries'], 1)

    def test_snapshots_are_counts_only_and_array_aliases_are_not_double_counted(self):
        ref = make_reference(self.tmp, 'private-map-name')
        ref.known_boundary = ref.clean_mask[:, ::-1]
        fast.load_prepared(ref)
        row = fast.prepared_memory_snapshot()
        self.assertEqual(row['retainedArrayBytes'], ref.clean_mask.nbytes * 3)
        text = json.dumps(row)
        self.assertNotIn('private-map-name', text)
        self.assertNotIn(self.tmp, text)
        self.assertFalse(row['oversizedSingleton'])

    def test_eviction_releases_reference_unless_another_holder_keeps_it(self):
        ref = make_reference(self.tmp, 'old')
        mask_ref = weakref.ref(ref.clean_mask)
        product = steady.prepare(ref)
        del ref
        for i in range(6):
            steady.prepare(make_reference(self.tmp, str(i)))
        gc.collect()
        self.assertIsNone(mask_ref())
        self.assertTrue(np.isfinite(product.distance).all())

    def test_normal_six_floor_working_set_is_unchanged_under_default_budget(self):
        refs = [make_reference(self.tmp, 'floor-' + str(i), 512) for i in range(6)]
        steady_results = [steady.prepare(ref) for ref in refs]
        for _ in range(100):
            for ref, product in zip(refs, steady_results):
                self.assertIs(steady.prepare(ref), product)
        self.assertEqual(steady.prepared_memory_snapshot()['entries'], 6)
        fast_results = [fast.load_prepared(ref) for ref in refs[:4]]
        for _ in range(100):
            for ref, product in zip(refs[:4], fast_results):
                self.assertIs(fast.load_prepared(ref), product)
        self.assertEqual(fast.prepared_memory_snapshot()['entries'], 4)

    def test_six_large_cross_map_references_remain_hot_at_default_budget(self):
        # The rejected 256MiB default thrashes this existing six-entry window.
        refs, products = [], []
        for i in range(6):
            mask = np.zeros((2048, 2048), np.uint8)
            mask[16:-16, 16:-16] = 1
            mask[32:-32:32, 32:-32] = 0
            ref = SimpleNamespace(clean_mask=mask, validation_mask=mask.copy(),
                known_boundary=np.zeros_like(mask), unknown_mask=np.zeros_like(mask), metadata={})
            refs.append(ref)
            products.append(steady.prepare(ref))
        self.assertGreater(steady.prepared_memory_snapshot()['retainedArrayBytes'], 256 * 1024**2)
        with patch.object(steady.cv2, 'morphologyEx', side_effect=AssertionError('hot window rebuilt')):
            for _ in range(5):
                for ref, product in zip(refs, products):
                    self.assertIs(steady.prepare(ref), product)

    def test_registered_s1_six_largest_standard_arrays_fit_budget(self):
        index = json.loads((Path(__file__).resolve().parents[1] /
                            's1-private-assets/s1_entrance_coordinate_index.json').read_text())
        combined = []
        for mode in ('nightmare', 'difficult'):
            areas = sorted((row['shapeHW'][0] * row['shapeHW'][1]
                            for floors in index['maps'][mode].values() for row in floors.values()), reverse=True)
            # Four uint8 reference masks + 3 uint8 products + float32 field +
            # at most one int32 xy point per pixel. Not metadata or an RSS bound.
            self.assertLessEqual(19 * sum(areas[:6]), steady.PREPARED_CACHE_MAX_BYTES, mode)
            combined.extend(areas)
        self.assertLessEqual(19 * sum(sorted(combined, reverse=True)[:6]), steady.PREPARED_CACHE_MAX_BYTES)

    def test_concurrent_cold_insert_and_snapshot_keep_bounds(self):
        refs = [make_reference(self.tmp, str(i)) for i in range(9)]
        with patch.object(fast, 'PREPARED_CACHE_MAX_BYTES', 400_000), \
             patch.object(steady, 'PREPARED_CACHE_MAX_BYTES', 1_500_000):
            def run(offset):
                for i in range(30):
                    reference = refs[(i + offset) % len(refs)]
                    self.assertEqual(fast.load_prepared(reference).distance.shape, reference.clean_mask.shape)
                    self.assertEqual(steady.prepare(reference).edge.shape, reference.clean_mask.shape)
                    for module in (fast, steady):
                        row = module.prepared_memory_snapshot()
                        self.assertLessEqual(row['entries'], row['maxEntries'])
                        self.assertTrue(row['retainedArrayBytes'] <= row['budgetBytes'] or row['oversizedSingleton'])
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(run, range(3)))


if __name__ == '__main__':
    unittest.main()
