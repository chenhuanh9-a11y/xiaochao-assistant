"""Offline synthetic static-cache A/B and soak; never game/visible-pixel latency.

Run this same script against both --source-root checkouts. Network entry points
are blocked before product imports. No game, user resources or system changes.
"""
from collections import deque
from contextlib import ExitStack
import argparse
import gc
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def quantiles(values):
    rows = sorted(values)
    return dict(n=len(rows), p50=statistics.median(rows), p95=rows[math.ceil(.95*len(rows))-1],
                max=max(rows), method='nearest-rank; bounded-latest-window') if rows else None


def run(args):
    root = Path(args.source_root).resolve()
    sys.path.insert(0, str(root / 'app'))
    attempts = []
    def deny(*a, **kw):
        attempts.append(True)
        raise AssertionError('synthetic cache probe attempted network')
    with ExitStack() as stack:
        for name in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto',
                     'socket.create_connection', 'socket.getaddrinfo'):
            stack.enter_context(patch(name, new=deny))
        import numpy as np
        import cv2
        import psutil
        import fast_structure_alignment as fast
        import idvb0059_steady as steady
        # Identical measurement implementation even for an unmodified baseline.
        spec = importlib.util.spec_from_file_location('probe_array_accounting',
            Path(__file__).resolve().parents[1] / 'app' / 'structure_cache_memory.py')
        account = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(account)
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        mask = np.zeros((args.size, args.size), np.uint8)
        mask[16:-16, 16:-16] = 1
        mask[32:-32:32, 32:-32] = 0
        metadata = {'binding': {'mapId': 'synthetic', 'floor': '1', 'profile': 'synthetic'}}
        path = temp / 'references' / 'synthetic.npz'
        resource = fast.prepare(mask)  # real kernels, setup excluded from timed loop
        prepared_path = temp / 'references' / 'fast-structure' / 'synthetic-fast.npz'
        prepared_path.parent.mkdir(parents=True)
        np.savez(prepared_path, sourceMetadata=json.dumps(metadata), distance=resource.distance,
                 chains=resource.chains, rays=resource.rays, pitch=resource.pitch, shape=resource.shape)
        del resource
        process = psutil.Process()
        samples = deque(maxlen=128)
        hot_times, cold_times = deque(maxlen=4096), deque(maxlen=256)
        active = deque(maxlen=3)
        expected = None
        mismatch, hot_misses, cold_count, cycle = 0, 0, 0, 0
        peak_rss = 0
        def make():
            return SimpleNamespace(clean_mask=mask.copy(), validation_mask=mask.copy(),
                known_boundary=np.zeros_like(mask), unknown_mask=np.zeros_like(mask),
                metadata=metadata, path=path, resource_root=temp)
        def arrays(entries, fields):
            return [a for reference, product in entries for a in
                    (*account.reference_arrays(reference), *(getattr(product, key) for key in fields))]
        def fingerprint(product):
            h = hashlib.sha256()
            for name in ('distance', 'chains', 'rays', 'edge', 'k3', 'k5', 'reference_points'):
                a = getattr(product, name, None)
                if a is not None:
                    h.update(str((a.shape, str(a.dtype))).encode())
                    h.update(memoryview(a).cast('B'))
            return h.hexdigest()
        def snapshot():
            nonlocal peak_rss
            with fast._prepared_lock:
                fa = arrays(fast._prepared.values(), ('distance', 'chains', 'rays'))
                fn = len(fast._prepared)
            with steady._cache_lock:
                sa = arrays(steady._cache.values(), ('edge', 'k3', 'k5', 'distance', 'reference_points'))
                sn = len(steady._cache)
            rss = process.memory_info().rss
            peak_rss = max(peak_rss, rss)
            row = dict(elapsedSeconds=time.perf_counter()-start, cycle=cycle, rssBytes=rss,
                fastEntries=fn, steadyEntries=sn, fastArrayBytes=account.retained_array_bytes(fa),
                steadyArrayBytes=account.retained_array_bytes(sa),
                unionArrayBytes=account.retained_array_bytes(fa+sa))
            assert fn <= 4 and sn <= 6
            for module, size, count in ((fast, row['fastArrayBytes'], fn), (steady, row['steadyArrayBytes'], sn)):
                if hasattr(module, 'PREPARED_CACHE_MAX_BYTES'):
                    assert size <= module.PREPARED_CACHE_MAX_BYTES or count == 1
            samples.append(row)
            return row
        start = time.perf_counter()
        # Fill beyond both old entry limits, keeping only the latest three
        # caller references. Older references are then held only by caches.
        for _ in range(12):
            ref = make()
            tick = time.perf_counter_ns()
            f, s = fast.load_prepared(ref), steady.prepare(ref)
            cold_times.append((time.perf_counter_ns()-tick)/1e6)
            cold_count += 1
            current = (fingerprint(f), fingerprint(s))
            if expected is None:
                expected = current
            mismatch += current != expected
            active.append((ref, f, s))
            snapshot()
        warm_start = time.perf_counter()
        next_sample, next_progress = warm_start, warm_start + 30
        while time.perf_counter()-warm_start < args.seconds or cycle < args.cycles:
            ref, previous_f, previous_s = active[cycle % 3]
            tick = time.perf_counter_ns()
            f, s = fast.load_prepared(ref), steady.prepare(ref)
            hot_times.append((time.perf_counter_ns()-tick)/1e6)
            hot_misses += f is not previous_f or s is not previous_s
            if args.churn_every and cycle and cycle % args.churn_every == 0:
                ref = make()
                tick = time.perf_counter_ns()
                f, s = fast.load_prepared(ref), steady.prepare(ref)
                cold_times.append((time.perf_counter_ns()-tick)/1e6)
                cold_count += 1
                mismatch += (fingerprint(f), fingerprint(s)) != expected
                active.append((ref, f, s))
            cycle += 1
            now = time.perf_counter()
            if now >= next_sample:
                snapshot()
                next_sample = now + max(1., args.seconds / 100)
            if now >= next_progress:
                print('synthetic cache soak: %d cycles, %.1fs' % (cycle, now-warm_start), file=sys.stderr, flush=True)
                next_progress = now + 30
            if args.interval_ms:
                time.sleep(args.interval_ms / 1000)
        last = snapshot()
        elapsed = time.perf_counter()-start
        with fast._prepared_lock:
            fast._prepared.clear()
        with steady._cache_lock:
            steady._cache.clear()
        # Results held by a live caller stay valid after cache invalidation.
        mismatch += (fingerprint(f), fingerprint(s)) != expected
        active.clear()
        del ref, previous_f, previous_s, f, s, mask
        gc.collect()  # measurement teardown only, never production periodic GC
        assert mismatch == 0 and hot_misses == 0 and not attempts
        report = dict(scope='synthetic-preparation-caches-only; not-game-latency-or-global-memory-cap',
            sourceLabel=args.source_label, sizeHW=[args.size, args.size], cycles=cycle,
            elapsedSeconds=elapsed, warmElapsedSeconds=time.perf_counter()-warm_start,
            intervalMs=args.interval_ms, churnEvery=args.churn_every, coldPreparations=cold_count,
            hotCacheMisses=hot_misses, arrayFingerprintMismatches=mismatch, networkAttempts=len(attempts),
            fastBudgetBytes=getattr(fast, 'PREPARED_CACHE_MAX_BYTES', None),
            steadyBudgetBytes=getattr(steady, 'PREPARED_CACHE_MAX_BYTES', None),
            hotLatencyMs=quantiles(hot_times), coldLatencyMs=quantiles(cold_times),
            sampledPeakRssBytes=peak_rss, finalRssBytes=process.memory_info().rss,
            lastBeforeClear=last, sampleCount=len(samples), samples=list(samples),
            afterClearEntries=[len(fast._prepared), len(steady._cache)],
            python=sys.version.split()[0], numpy=np.__version__, opencv=cv2.__version__, platform=sys.platform)
        print(json.dumps(report, separators=(',', ':')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--source-label', default='candidate')
    parser.add_argument('--size', type=int, default=512)
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--cycles', type=int, default=3000)
    parser.add_argument('--churn-every', type=int, default=0)
    parser.add_argument('--interval-ms', type=float, default=0)
    args = parser.parse_args()
    if not (128 <= args.size <= 3072 and 0 <= args.seconds <= 3600 and 1 <= args.cycles <= 1000000
            and 0 <= args.churn_every <= 1000000 and 0 <= args.interval_ms <= 1000):
        parser.error('synthetic probe bounds exceeded')
    run(args)
