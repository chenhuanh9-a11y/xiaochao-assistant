"""Compare real download services using unchanged packages on a local HTTP source.

All outputs/caches stay in a new caller-selected workspace directory. No user
cache is cleared, no client/game is launched, and no resources are uploaded.
This controlled loopback result is NOT a real Internet-source measurement.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import zipfile


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def load_transport(repo, name):
    spec = importlib.util.spec_from_file_location(name, repo / 'app/star_map_download.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def timings(module):
    values = dict(networkReadMs=0., networkBytes=0, diskDigestMs=0.,
                  diskDigestBytes=0, writeMs=0., flushMs=0., fsyncMs=0.)
    open_response, locked, file_digest, fsync = (
        module.MapPackageDownloadService._open, module._locked_file,
        module.hashlib.file_digest, module.os.fsync)

    def measure(key, function, *args, **kwargs):
        start = time.perf_counter_ns()
        try:
            return function(*args, **kwargs)
        finally:
            values[key] += (time.perf_counter_ns() - start) / 1e6

    class Response:
        def __init__(self, response): self.response = response
        def __getattr__(self, name): return getattr(self.response, name)
        def __enter__(self): return self
        def __exit__(self, *args): self.response.close()
        def read1(self, size):
            block = measure('networkReadMs', self.response.read1, size)
            values['networkBytes'] += len(block)
            return block

    class Sink:
        def __init__(self, stream): self.stream = stream
        def __getattr__(self, name): return getattr(self.stream, name)
        def write(self, block): return measure('writeMs', self.stream.write, block)
        def flush(self): return measure('flushMs', self.stream.flush)

    @contextmanager
    def timed_lock(*args, **kwargs):
        with locked(*args, **kwargs) as stream:
            yield Sink(stream) if kwargs.get('writable') else stream

    def digest(stream, algorithm):
        values['diskDigestBytes'] += os.fstat(stream.fileno()).st_size
        return measure('diskDigestMs', file_digest, stream, algorithm)

    with patch.object(module.MapPackageDownloadService, '_open',
                      lambda service, *args: Response(open_response(service, *args))), \
            patch.object(module, '_locked_file', side_effect=timed_lock), \
            patch.object(module.hashlib, 'file_digest', side_effect=digest), \
            patch.object(module.os, 'fsync', side_effect=lambda fd: measure('fsyncMs', fsync, fd)):
        yield values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--base-package', type=Path, required=True)
    parser.add_argument('--route-package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    workspace = Path.cwd().resolve()
    output = args.output.resolve()
    if not output.is_relative_to(workspace) or output.exists():
        raise ValueError('Use a new output directory below the current workspace')
    output.mkdir(parents=True)
    sys.path.insert(0, str(args.baseline.resolve() / 'app'))
    modules = {name: load_transport(repo.resolve(), 'download_benchmark_' + name)
               for name, repo in [('baseline', args.baseline), ('candidate', args.candidate)]}
    inputs, sources = [], {}
    for kind, path in [('base', args.base_package), ('route', args.route_package)]:
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        endpoint = '/package-' + kind + path.suffix
        sources[endpoint] = path
        inputs.append(dict(kind=kind, packageId=manifest['packageId'], bytes=size, sha256=sha,
                           title=kind, summary='controlled unchanged delivery package', url=endpoint))
    body = json.dumps(dict(schemaVersion=1,
        packages=[{k: v for k, v in item.items() if k != 'kind'} for item in inputs])).encode()
    request_counts = dict(index=0, package=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == '/index.json':
                request_counts['index'] += 1
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path in sources:
                request_counts['package'] += 1
                source = sources[self.path]
                self.send_response(200)
                self.send_header('Content-Length', str(source.stat().st_size))
                self.end_headers()
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, self.wfile, 64 * 1024)
            else:
                self.send_error(404)

    result = dict(createdAtUtc=datetime.now(timezone.utc).isoformat(),
        source='controlled-loopback-http', python=platform.python_version(),
        windows=platform.platform(), processor=platform.processor(),
        endpoint='download_package invocation to return of verified handoff-ready Path',
        conditions='Fresh isolated directory per first download; immediate same-service cache reuse; '
                   'no OS-cache purge; source hashing before timing warms reads; background tasks not stopped',
        timingNotes='networkReadMs is time blocked in response.read1, not total Internet transfer time; '
                    'write/flush/fsync/diskDigest are non-overlapping calls; '
                    'total includes HTTP open, Python work and locked publication; importer excluded',
        realSource=dict(status='not-measured', reason='No configured online index URL in dispatched environment'),
        versions={name: dict(commit=git(repo, 'rev-parse', 'HEAD'),
                             dirty=bool(git(repo, 'status', '--porcelain', '--', 'app', 'experiments/qt_quick')),
                             module='app/star_map_download.py')
                  for name, repo in [('baseline', args.baseline), ('candidate', args.candidate)]},
        inputs=[{k: v for k, v in item.items() if k not in ('url', 'title', 'summary')}
                for item in inputs], samples=[], summary=[])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for round_number in range(1, args.repeats + 1):
            order = ['baseline', 'candidate'] if round_number % 2 else ['candidate', 'baseline']
            for variant in order:
                module = modules[variant]
                for item in inputs:
                    cache = output / f'{round_number}-{variant}-{item["kind"]}'
                    started = time.perf_counter_ns()
                    service = module.MapPackageDownloadService(
                        f'http://127.0.0.1:{server.server_port}/index.json', cache,
                        allow_loopback_http_for_tests=True)
                    construction_ms = (time.perf_counter_ns() - started) / 1e6
                    started = time.perf_counter_ns()
                    rows = service.read_index()
                    index_ms = (time.perf_counter_ns() - started) / 1e6
                    assert any(row['packageId'] == item['packageId'] for row in rows)
                    for condition in ('first-download', 'cache-hit'):
                        before = request_counts['package']
                        with timings(module) as phases:
                            started = time.perf_counter_ns()
                            path = service.download_package(item['packageId'])
                            elapsed_ms = (time.perf_counter_ns() - started) / 1e6
                        with path.open('rb') as stream:
                            assert os.fstat(stream.fileno()).st_size == item['bytes']
                            assert hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256']
                        assert request_counts['package'] - before == (condition == 'first-download')
                        result['samples'].append(dict(round=round_number, variant=variant, kind=item['kind'],
                            condition=condition, totalMs=elapsed_ms, indexMs=index_ms,
                            constructionMs=construction_ms, packageRequests=request_counts['package'] - before,
                            **phases))
                        (output / 'measurements.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        for kind in ('base', 'route'):
            for condition in ('first-download', 'cache-hit'):
                for variant in ('baseline', 'candidate'):
                    rows = [row for row in result['samples'] if
                            (row['kind'], row['condition'], row['variant']) == (kind, condition, variant)]
                    result['summary'].append(dict(kind=kind, condition=condition, variant=variant,
                        n=len(rows), medianMs=statistics.median(row['totalMs'] for row in rows),
                        samplesMs=[row['totalMs'] for row in rows],
                        phaseMedians={key: statistics.median(row[key] for row in rows) for key in
                            ('indexMs', 'networkReadMs', 'writeMs', 'flushMs', 'fsyncMs', 'diskDigestMs')}))
        result['requests'] = request_counts
        (output / 'measurements.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result['summary'], indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
