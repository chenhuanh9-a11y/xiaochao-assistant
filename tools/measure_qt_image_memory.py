"""Offline synthetic provider soak, not a game/renderer/Windows performance claim.

Run this same file with --source-root pointing to each clean A/B source tree.
Uses only synthetic pixels. All Python socket/DNS paths are blocked during
imports, loops and cleanup. It neither observes nor controls a desktop/game.
"""
from contextlib import ExitStack
import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch


def quantiles(values):
    ordered = sorted(values)
    return dict(n=len(values), p50=statistics.median(values),
                p95=ordered[math.ceil(.95*len(ordered))-1], max=max(values),
                percentileMethod='nearest-rank')


def run(args):
    root = Path(args.source_root).resolve()
    sys.path[:0] = [str(root/'app'), str(root/'experiments/qt_quick')]
    attempts = []
    def no_network(*a, **kw):
        attempts.append(True)
        raise RuntimeError('Offline memory probe attempted network access')
    with ExitStack() as guards:
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto',
                       'socket.create_connection', 'socket.getaddrinfo'):
            guards.enter_context(patch(target, side_effect=no_network))
        import numpy as np
        import psutil
        from PySide6.QtCore import qVersion
        from client_coordinator import LiveImages
        process = psutil.Process()
        images = LiveImages()
        pixels = np.full((args.height, args.width, 4), 127, np.uint8)
        pixels[:,:,3] = 255
        scope = ('synthetic', 'nightmare', 'fixture', '1')
        metrics, samples = [], []
        peak_owned, peak_rss, failures = 0, 0, 0
        initial_rss = process.memory_info().rss
        first = images.publish('reference:fixture' if args.scenario=='staging' else 'warm', pixels,
                               material_scope=None if args.scenario=='staging' else scope)
        front = back = None
        start = time.perf_counter()
        for i in range(args.cycles):
            before = time.perf_counter_ns()
            if args.scenario == 'staging':
                pixels[:,:,0] = i % 251
                url = images.publish('source:'+str(i), pixels, material_scope=scope)
                images.publish('pending:'+str(i), pixels, material_scope=('target', i % 3))
                peak_owned = max(peak_owned, sum(x.sizeInBytes() for x in images._images.values()))
                images.finish_staging(i, bool(i % 2))
                if i % 2:
                    url = 'image://live/pending:'+str(i)
            else:
                if args.scenario == 'changing':
                    pixels[:,:,0] = i % 251
                url = images.publish('frame:'+str(i), pixels, material_scope=scope)
            key = url.removeprefix('image://live/')
            front, back = images.requestImage(key, None, None), images.requestImage(key, None, None)
            metrics.append((time.perf_counter_ns()-before)/1e6)
            if i % 100 == 0 or i == args.cycles-1:
                expected = hashlib.sha256(pixels.tobytes()).hexdigest()
                actual = hashlib.sha256(bytes(front.constBits())).hexdigest()
                if expected != actual or front.isNull() or front != back:
                    failures += 1
                owned = sum(x.sizeInBytes() for x in images._images.values())
                rss = process.memory_info().rss
                peak_owned, peak_rss = max(peak_owned, owned), max(peak_rss, rss)
                samples.append(dict(cycle=i, elapsedSeconds=time.perf_counter()-start,
                                    rssBytes=rss, providerOwnedBytes=owned, entries=len(images._images)))
                assert len(images._images) <= (3 if images._staged_key else 2)
                assert owned <= (3 if images._staged_key else 2)*pixels.nbytes
            if args.scenario in ('staging', 'clear-reopen'):
                images.clear_aligned()
            if args.interval_ms:
                time.sleep(args.interval_ms/1000)
        elapsed = time.perf_counter()-start
        images.clear()
        # Provider eviction cannot invalidate snapshots held by a consumer.
        assert not front.isNull() and front == back
        retained_consumer_bytes = front.sizeInBytes()
        del front, back, pixels
        gc.collect()
        final_rss = process.memory_info().rss
        assert not images._images
        assert not attempts
        assert not failures
        memory = process.memory_info()
        lifetime_peak = getattr(memory, 'peak_wset', None)
        lifetime_peak_source = 'psutil-peak-wset' if lifetime_peak is not None else 'unavailable'
        if lifetime_peak is None:
            try:
                import resource
                lifetime_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                lifetime_peak *= 1 if sys.platform == 'darwin' else 1024
                lifetime_peak_source = 'getrusage-ru-maxrss'
            except ImportError:
                pass
        report = dict(scope='synthetic-provider-only-not-game-or-visible-render', scenario=args.scenario,
            cycles=args.cycles, width=args.width, height=args.height, intervalMs=args.interval_ms,
            elapsedSeconds=elapsed, qt=qVersion(), python=sys.version.split()[0], platform=sys.platform,
            latencyMs=quantiles(metrics), initialRssBytes=initial_rss, sampledPeakRssBytes=peak_rss,
            processLifetimePeakRssBytes=lifetime_peak, processLifetimePeakRssSource=lifetime_peak_source,
            finalRssBytes=final_rss, peakProviderOwnedBytes=peak_owned,
            consumerBytesAfterProviderClear=retained_consumer_bytes, providerEntriesAfterClear=0,
            pixelMismatchCount=failures, networkAttempts=len(attempts), samples=samples)
        print(json.dumps(report, separators=(',', ':')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--scenario', choices=('reopen', 'changing', 'staging', 'clear-reopen'), default='reopen')
    parser.add_argument('--cycles', type=int, default=5000)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--interval-ms', type=float, default=0)
    args = parser.parse_args()
    if not (0 < args.cycles <= 100000 and 0 < args.width*args.height <= 20000000
            and args.width > 0 and args.height > 0 and 0 <= args.interval_ms <= 1000):
        parser.error('Probe bounds exceeded')
    run(args)
