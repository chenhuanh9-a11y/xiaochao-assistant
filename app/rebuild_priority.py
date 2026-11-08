"""Offline rebuild only. Always recomputes all features; no metadata rebind API.

Run in a fresh process with a stable source tree and complete resource root.
Output is NEW schema 2; the frozen schema 1 experiment is never overwritten.
"""
import argparse
import json
from pathlib import Path
import platform
import re
import sys
import tempfile
sys.dont_write_bytecode = True
import numpy as np
import cv2
from priority_admission import ALGORITHM_SHA256, _require, _json, sha

MODES = ('difficulty2', 'difficult', 'nightmare')
FLOORS = ('-1', '-2', '-3', '-4')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def dependency_versions():
    return {'python': platform.python_version(), 'numpy': np.__version__, 'opencv': cv2.__version__}


def source_hashes(root):
    root = Path(root).resolve()
    required = ('app/single_map_structure.py', 'app/shared_structure_reference.py',
                'app/fast_structure_alignment.py', 'experiments/qt_quick/client_live_runtime.py')
    _require(all((root / name).is_file() for name in required), 'missing-source-dependency')
    # Conservative startup binding: all Python dependencies in the supplied
    # source generation, including transitive helpers, not just solver labels.
    paths = sorted(root.rglob('*.py'))
    _require(all(not path.is_symlink() and path.resolve().is_relative_to(root) for path in paths), 'source-symlink')
    return {path.relative_to(root).as_posix(): sha(path.read_bytes()) for path in paths}


def resource_catalogue(root):
    root = Path(root).resolve()
    rows = []
    for mode in MODES:
        folder = root / 'seasons/s1' / mode / 'references/entries'
        _require(folder.is_dir(), 'missing-mode-directory:' + mode)
        seen = set()
        for path in sorted(folder.glob('*-shared.npz')):
            _require(not path.is_symlink() and path.resolve().is_relative_to(root), 'resource-symlink')
            match = re.fullmatch(r'([0-9]+)-(-[1-4])-shared\.npz', path.name)
            _require(match is not None, 'invalid-resource-name:' + path.name)
            map_id, floor = match.groups()
            _require((map_id, floor) not in seen, 'duplicate-resource')
            seen.add((map_id, floor))
            rows.append({'mode': mode, 'floor': floor, 'mapId': map_id,
                         'resourcePath': path.relative_to(root).as_posix(), 'sha256': sha(path.read_bytes())})
        _require(seen and {floor for _, floor in seen} == set(FLOORS), 'incomplete-floor-directory:' + mode)
    return rows


def build(resource_root, source_root, algorithm_file, output):
    resource_root, source_root, output = Path(resource_root).resolve(), Path(source_root).resolve(), Path(output).resolve()
    _require(not output.exists(), 'output-already-exists')
    algorithm = Path(algorithm_file).read_bytes()
    _require(sha(algorithm) == ALGORITHM_SHA256, 'frozen-algorithm-required')
    dependencies_before = source_hashes(source_root)
    rows = resource_catalogue(resource_root)
    # Use a fresh CLI process. Reject reuse after somebody imported a different
    # generation; never silently retarget already-loaded module globals.
    _require(not any(name in sys.modules for name in ('single_map_structure', 'shared_structure_reference', 'nightmare_candidate')),
             'fresh-build-process-required')
    # A fresh private cache prefix avoids importing stale preexisting .pyc
    # files even though bytecode writes are disabled. CLI lifetime is isolated.
    bytecode_cache = tempfile.TemporaryDirectory(prefix='build-cache-', dir=output.parent)
    sys.pycache_prefix = bytecode_cache.name
    sys.path[:0] = [str(source_root / 'app'), str(source_root / 'experiments/qt_quick')]
    import single_map_structure
    import shared_structure_reference
    _require(Path(single_map_structure.__file__).resolve() == source_root / 'app/single_map_structure.py', 'wrong-loaded-solver')
    _require(Path(shared_structure_reference.__file__).resolve() == source_root / 'app/shared_structure_reference.py', 'wrong-loaded-loader')
    namespace = {'__name__': '_frozen_priority_math'}
    exec(compile(algorithm, '<verified-frozen-math>', 'exec'), namespace)
    records = {mode + ':' + floor: {} for mode in MODES for floor in FLOORS}
    for row in rows:
        path = resource_root / row['resourcePath']
        reference = shared_structure_reference.load_shared_structure(path)
        _require(reference is not None, 'unreadable-reference:' + row['resourcePath'])
        binding = reference.metadata.get('binding', {})
        _require(type(binding.get('mapId')) is str and binding['mapId'] == row['mapId']
                 and type(binding.get('floor')) is str and binding['floor'] == row['floor']
                 and binding.get('profile') == 'shared-structure', 'reference-binding')
        records[row['mode'] + ':' + row['floor']][row['mapId']] = namespace['tokens'](single_map_structure.contour_graph(reference.clean_mask))
    _require(rows == resource_catalogue(resource_root), 'resources-changed-during-build')
    _require(dependencies_before == source_hashes(source_root), 'source-changed-during-build')
    artifact = {'schema': 2, 'season': 's1', 'algorithmSha256': ALGORITHM_SHA256,
                'spec': namespace['SPEC'], 'dependencyVersions': dependency_versions(),
                'sourceHashes': dependencies_before, 'resources': rows,
                'catalogs': {key: namespace['compile_vectors'](values) for key, values in records.items()}}
    payload = canonical(artifact)
    # Exclusive creation prevents accidental overwrite of old frozen evidence.
    with output.open('xb') as stream:
        stream.write(payload)
    bytecode_cache.cleanup()
    return {'indexSha256': sha(payload), 'resourceCount': len(rows), 'sourceFiles': len(dependencies_before),
            'output': str(output), 'algorithmSha256': ALGORITHM_SHA256}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-root', required=True)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--algorithm-file', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.resource_root, args.source_root, args.algorithm_file, args.output), indent=2))
