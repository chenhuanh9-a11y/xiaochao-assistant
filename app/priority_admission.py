"""Guarded ordering only, not identity, exclusion, pose or display authority.

The caller supplies the authoritative FULL engine catalogue, not exact groups.
Load once per immutable resource/code generation. Reload after any replacement.
No source hashing, frame cache or solver calls occur in order().
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from entry_evidence_work import FrameSnapshot

INDEX_SHA256 = '96e9047ad6b14763a6a1ccb2473e4ba1e7ffdfa82ba73c63b34a93ff782a61dd'
FREEZE_SHA256 = '9d25997053802766cdf83d745b533e9c5175d168727b61ad72944f204f309c59'
ALGORITHM_SHA256 = '8f770ff0d09426a91244b5f1bd1c9cb17b85ba1fcdb0a7588b21dc5d23c4b2bf'
SOLVERS = {
    'app/single_map_structure.py': 'fba6651418cbdecddc49ff835860f714bd42720db43cfb55e101deebf0ade657',
    'app/fast_structure_alignment.py': '3f9c638748008c5da17e970496b7d35c7752403b56309584d30e93b0e299d9d9',
    'experiments/qt_quick/client_live_runtime.py': '3e11bac4dd42faab102835b22aeb14f663e9a2527686703860ce6806e5d7fc1f',
}

def sha(data):
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate-json-key')
        out[key] = value
    return out


def _json(payload):
    return json.loads(payload, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite-json')))


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


@dataclass(frozen=True)
class Scope:
    season: str
    mode: str
    floor: str

    def valid(self):
        return (type(self.season) is str and self.season == 's1'
                and type(self.mode) is str and self.mode in ('difficulty2', 'difficult', 'nightmare')
                and type(self.floor) is str and self.floor in ('-1', '-2', '-3', '-4'))


@dataclass(frozen=True)
class Candidate:
    map_id: str
    resource_path: Path


@dataclass(frozen=True)
class OrderResult:
    candidates: tuple
    used_priority: bool
    reason: str
    frame_sha256: str


class PriorityAdmission:
    def __init__(self, candidates, reason, catalog=None, rank=None):
        self._natural = tuple(candidates)
        self.reason = reason
        self._catalog = catalog
        self._rank = rank

    def invalidate(self):
        """Call on resource/code generation replacement before subsequent orders.

        Caller serializes replacement with in-flight scans; create a new loader
        after replacement. Filesystem stat/identity is never a freshness proof.
        """
        self._catalog = None
        self.reason = 'generation-invalidated'

    @property
    def admitted(self):
        return self._catalog is not None

    @property
    def natural(self):
        return self._natural

    @classmethod
    def load(cls, *, feature_dir, source_root, scope, candidates):
        """Bad index/scope/resources => complete natural order, never partial use.

        candidates is the independent authoritative engine selection in natural
        order. Duplicate/ill-typed caller candidates are programmer errors:
        no trustworthy natural catalogue can be recovered from such input.
        Source paths refer to the files actually used by that engine generation.
        """
        natural = tuple(candidates)
        _require(all(type(c) is Candidate and type(c.map_id) is str and c.map_id
                     and isinstance(c.resource_path, Path) for c in natural), 'invalid-caller-catalogue')
        ids = tuple(c.map_id for c in natural)
        _require(len(set(ids)) == len(ids), 'duplicate-caller-catalogue')
        try:
            _require(type(scope) is Scope and scope.valid(), 'wrong-scope')
            directory, root = Path(feature_dir), Path(source_root)
            payload = (directory / 'features.json').read_bytes()
            # Reject unknown bytes before exposing the JSON parser to them.
            # The parser still rejects duplicate keys/nonfinite values after
            # the independently pinned digest is verified.
            _require(sha(payload) == INDEX_SHA256, 'index-digest')
            data = _json(payload)
            freeze_bytes = (directory / 'feature-freeze.json').read_bytes()
            _require(sha(freeze_bytes) == FREEZE_SHA256, 'freeze-digest')
            freeze = _json(freeze_bytes)
            algorithm = (directory / 'local_tokens.py').read_bytes()
            _require(sha(algorithm) == ALGORITHM_SHA256, 'algorithm-digest')
            _require(type(data['schema']) is int and data['schema'] == 1, 'schema')
            _require(type(data['spec']['version']) is int and data['spec']['version'] == 1, 'feature-version')
            _require(data['algorithmSha256'] == freeze['algorithmSha256'] == ALGORITHM_SHA256, 'algorithm-binding')
            _require(data['spec'] == freeze['spec'], 'spec-binding')
            _require(freeze['sourceSolverSha256'] == SOLVERS['app/single_map_structure.py'], 'solver-binding')
            for relative, expected in SOLVERS.items():
                _require(sha((root / relative).read_bytes()) == expected, 'solver-drift:' + relative)
            key = scope.mode + ':' + scope.floor
            catalog = data['catalogs'][key]
            _require(set(catalog['vectors']) == set(ids), 'incomplete-or-extra-selected-catalogue')
            selected_sources = {k.split(':')[1]: v for k, v in data['sourceHashes'].items()
                                if k.split(':')[0] == scope.mode and k.split(':')[-1] == scope.floor}
            _require(set(selected_sources) == set(ids), 'source-catalogue')
            for candidate in natural:
                _require(sha(candidate.resource_path.read_bytes()) == selected_sources[candidate.map_id],
                         'resource-drift:' + candidate.map_id)
            # Execute only the exact reviewed bytes, not a later path reread.
            namespace = {'__name__': '_frozen_priority_math'}
            exec(compile(algorithm, '<verified-local-tokens>', 'exec'), namespace)
            _require(namespace['SPEC'] == data['spec'], 'runtime-spec')
            # Private JSON structures never escape to caller-owned objects.
            return cls(natural, 'admitted', catalog, namespace['rank'])
        except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError) as error:
            return cls(natural, str(error))

    def order(self, snapshot, extract_tokens, prior_supported=()):
        """Extract anew from owned snapshot; no object-identity cache.

        extract_tokens is a trusted adapter returning a list of frozen-math
        token dictionaries for visible components. Bind that adapter to the
        verified source generation before eventual runtime integration.
        Explicit prior support changes order only; it grants no authority.
        """
        _require(type(snapshot) is FrameSnapshot, 'snapshot-required')
        fallback = lambda reason: OrderResult(self._natural, False, reason, snapshot.sha256)
        if not self.admitted:
            return fallback(self.reason)
        try:
            queries = extract_tokens(snapshot.pixels)
            _require(type(queries) in (tuple, list) and len(queries) <= 32, 'invalid-or-overloaded-observation')
            for query in queries:
                _require(type(query) is dict, 'invalid-query')
                _require(all(type(k) is str and type(v) in (int, float) and math.isfinite(v) and v >= 0
                             for k, v in query.items()), 'invalid-query-token')
            prior = tuple(prior_supported)
            _require(all(type(mid) is str for mid in prior), 'invalid-prior')
            groups = [{'members': [c.map_id]} for c in self._natural]
            ordered, _ = self._rank(self._catalog, groups, queries, prior)
            ordered_ids = tuple(g['members'][0] for g in ordered)
            by_id = {c.map_id: c for c in self._natural}
            _require(len(ordered_ids) == len(by_id) and set(ordered_ids) == set(by_id), 'rank-not-permutation')
            return OrderResult(tuple(by_id[mid] for mid in ordered_ids), True, 'priority-only', snapshot.sha256)
        except MemoryError:
            raise
        except Exception as error:
            # Retrieval is optional. Ordinary extraction/math failure must not
            # prevent full scan; MemoryError/cancellation/SystemExit still propagate.
            return fallback('observation-or-rank:' + type(error).__name__)


def load_rebuilt(*, index_path, expected_index_sha256, algorithm_file, source_root,
                 resource_root, scope, candidates):
    """Schema 2 loader. Pin expected digest OUTSIDE the untrusted index.

    Only rebuild_priority.py produces supported new indexes. No version-label
    override or digest-update-only compatibility mode exists. All resources and
    Python source files are hashed at admission; order() does no file hashing.
    """
    from rebuild_priority import source_hashes, resource_catalogue, dependency_versions
    natural = tuple(candidates)
    _require(all(type(c) is Candidate and type(c.map_id) is str and c.map_id
                 and isinstance(c.resource_path, Path) for c in natural), 'invalid-caller-catalogue')
    ids = tuple(c.map_id for c in natural)
    _require(len(set(ids)) == len(ids), 'duplicate-caller-catalogue')
    try:
        _require(type(scope) is Scope and scope.valid(), 'wrong-scope')
        payload = Path(index_path).read_bytes()
        _require(type(expected_index_sha256) is str and len(expected_index_sha256) == 64
                 and sha(payload) == expected_index_sha256, 'index-digest')
        data = _json(payload)
        _require(type(data['schema']) is int and data['schema'] == 2 and data['season'] == 's1', 'schema-or-season')
        algorithm = Path(algorithm_file).read_bytes()
        _require(sha(algorithm) == data['algorithmSha256'] == ALGORITHM_SHA256, 'algorithm-digest')
        _require(data['dependencyVersions'] == dependency_versions(), 'dependency-version-drift')
        _require(data['sourceHashes'] == source_hashes(source_root), 'source-generation-drift')
        rows = resource_catalogue(resource_root)
        _require(data['resources'] == rows, 'resource-generation-drift')
        selected = {row['mapId']: row for row in rows if row['mode'] == scope.mode and row['floor'] == scope.floor}
        catalog = data['catalogs'][scope.mode + ':' + scope.floor]
        _require(set(selected) == set(catalog['vectors']) == set(ids), 'incomplete-or-extra-selected-catalogue')
        root = Path(resource_root).resolve()
        for candidate in natural:
            _require(candidate.resource_path.resolve() == root / selected[candidate.map_id]['resourcePath'], 'wrong-candidate-source')
        namespace = {'__name__': '_frozen_priority_math'}
        exec(compile(algorithm, '<verified-local-tokens>', 'exec'), namespace)
        _require(data['spec'] == namespace['SPEC'] and type(data['spec']['version']) is int, 'spec-binding')
        return PriorityAdmission(natural, 'admitted-rebuilt', catalog, namespace['rank'])
    except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError) as error:
        return PriorityAdmission(natural, str(error))
