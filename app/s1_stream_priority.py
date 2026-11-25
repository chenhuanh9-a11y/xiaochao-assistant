"""Recorded research adapter: complete catalog ordering, never pose authority."""
from pathlib import Path
import time
from priority_admission import Candidate, Scope, load_rebuilt


def candidate_order(engine, catalog, floor, snapshot, prior, policy, config, handles):
    started = time.monotonic()
    natural = tuple(catalog.map_ids)
    details = dict(policy=policy, admissionSeconds=0., usedPriority=False)
    if policy == 'natural':
        shortlist = getattr(engine, 'qt_generic_structure_shortlist', None)
        if callable(shortlist) and len(natural) > 1 and snapshot is not None:
            try:
                ordered = tuple(shortlist(snapshot.pixels, floor, natural))
                if len(ordered) == len(natural) and set(ordered) == set(natural):
                    return ordered, dict(details, usedPriority=True, reason='generic-geometry-fingerprint',
                        orderSeconds=time.monotonic()-started)
            except Exception:
                pass
        return natural, dict(details, reason='natural-catalogue', orderSeconds=time.monotonic()-started)
    if policy == 'history':
        hints = tuple(mid for mid in prior if mid in natural)
        return hints + tuple(mid for mid in natural if mid not in hints), dict(details,
            reason='positive-history-only', orderSeconds=time.monotonic()-started)
    if (not isinstance(config, dict) or not isinstance(config.get('index_path'), (str, Path))
            or not isinstance(config.get('expected_index_sha256'), str)):
        return natural, dict(details, reason='priority-index-not-configured', orderSeconds=time.monotonic()-started)
    import local_tokens
    source = Path(__file__).resolve().parents[1]
    algorithm = source / 'app/local_tokens.py'
    if Path(local_tokens.__file__).resolve() != algorithm:
        return natural, dict(details, reason='extraction-module-source-mismatch', orderSeconds=time.monotonic()-started)
    inventory_root = getattr(engine, 'qt_s1_index_resource_root', None)
    if inventory_root is None:
        return natural, dict(details, reason='complete-resource-root-unavailable', orderSeconds=time.monotonic()-started)
    root = Path(inventory_root).resolve()
    mode_root = Path(engine.qt_resource_root).resolve()
    key = (id(engine), catalog.resource_key, catalog.mode, floor)
    handle = handles.get(key)
    if handle is None:
        before = time.monotonic()
        candidates = tuple(Candidate(mid, mode_root / 'references/entries' / f'{mid}-{floor}-shared.npz') for mid in natural)
        handle = load_rebuilt(index_path=config['index_path'],
            expected_index_sha256=config['expected_index_sha256'], algorithm_file=algorithm,
            source_root=source, resource_root=root, scope=Scope(catalog.season, catalog.mode, floor), candidates=candidates)
        handles[key] = handle
        details['admissionSeconds'] = time.monotonic()-before
    def extract(frame):
        tokens, info = local_tokens.observation_tokens(frame)
        if info.get('overload'):
            raise ValueError('observation-component-overload')
        return tokens
    result = handle.order(snapshot, extract, prior_supported=prior)
    return tuple(c.map_id for c in result.candidates), dict(details, usedPriority=result.used_priority,
        reason=result.reason, orderSeconds=time.monotonic()-started)
