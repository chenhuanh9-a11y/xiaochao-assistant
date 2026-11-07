"""Small numeric fit evidence, never pixels, free text, paths or authority.

This copy is diagnostic-only. It does not mutate solver inputs or verdicts.
The automatic recorder owns consent, current-frame admission and disk budgets.
"""
import json
import math
import re
from itertools import islice

MAX_BYTES = 6144
STAGE_MATRICES = frozenset(('proposalMatrix', 'pointMatrix', 'wallMatrix', 'regionalMatrix', 'residualMatrix'))


def number(value, limit=1000000000):
    return value if type(value) in (int, float) and abs(value) <= limit and math.isfinite(value) else None


def vector(value, size, limit=1000000):
    if type(value) not in (list, tuple) or len(value) != size:
        return None
    result = [number(n, limit) for n in value]
    return result if all(n is not None for n in result) else None


def matrix(value):
    # Only the actual small numeric proposal, not arbitrary ndarray conversion.
    if getattr(value, 'shape', None) == (3, 3):
        value = value.tolist()
    if type(value) not in (list, tuple) or len(value) != 3:
        return None
    rows = [vector(row, 3) for row in value]
    return rows if all(row is not None for row in rows) and rows[2] == [0, 0, 1] else None


def code(value):
    from diagnostic_schema import CODES
    return value if type(value) is str and len(value) <= 512 and value in CODES else 'unknown-operation-failure'


def identity(value):
    return value if type(value) is str and re.fullmatch(r'-?[0-9]{1,20}', value) else None


def digest(value):
    return value if type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) else None


def metrics(value):
    value = value if type(value) is dict else {}
    result = {key: number(value[key]) for key in (
        'queryChains', 'sourceChains', 'markerOccludedProposalChains', 'measuredSeedCount', 'seedCount', 'seedBudget', 'candidateCount', 'validationBudget',
        'candidateCount', 'acceptedPoseCount', 'points', 'coarsePoints', 'proposalMs', 'totalMs',
        'elapsedMs', 'maskEdges', 'contrastSupportedEdges', 'withinThreePixels', 'withinSixPixels',
        'medianDistance', 'medianErrorPixels', 'count') if key in value}
    result.update({key: number(value[key]) for key in ('median', 'within3', 'within6') if key in value})
    result.update({key: value[key] for key in ('accepted', 'verified', 'regionalEdgeVerified', 'scaleLocked')
                   if type(value.get(key)) is bool})
    for key in ('reason', 'pointGeometryReason', 'wallGeometryReason'):
        if key in value:
            result[key] = code(value[key])
    if 'matrix' in value:
        result['matrix'] = matrix(value['matrix'])
    for key in STAGE_MATRICES:
        if key in value:
            result[key] = matrix(value[key])
    if 'cell' in value:
        result['cell'] = vector(value['cell'], 2, 2)
    return result


def fit_summary(fit):
    fit = fit if type(fit) is dict else {}
    diag = fit.get('diagnostics') if type(fit.get('diagnostics')) is dict else {}
    result = dict(success=fit.get('success') is True, reason=code(fit.get('reason')), statistics=metrics(diag))
    if type(diag.get('pointRejections')) is list:
        result['statistics']['pointRejectionCount'] = len(diag['pointRejections'])
    result['statistics']['markerOccludedWallProposal'] = fit.get('markerOccludedWallProposal') is True
    result['statistics']['markerOccludedWallObservation'] = fit.get('markerOccludedWallObservation') is True
    result['residual'] = metrics(fit.get('residual', fit.get('selectedRouteCurrentFrameResidual')))
    candidates = diag.get('candidates') if type(diag.get('candidates')) is list else []
    result['candidates'] = []
    for candidate in candidates[:2]:
        if type(candidate) is not dict:
            continue
        regions = candidate.get('regions') if type(candidate.get('regions')) is dict else {}
        region_rows = regions.get('regions') if type(regions.get('regions')) is list else []
        result['candidates'].append(dict(matrix=matrix(candidate.get('matrix')),
            accepted=candidate.get('accepted') is True, regions=metrics(regions),
            regionMetrics=[metrics(row) for row in region_rows[:9]],
            omittedRegionCount=max(0, len(region_rows)-9), residual=metrics(candidate.get('residual'))))
    result['omittedCandidateCount'] = max(0, len(candidates)-2)
    return result


def fast_summary(event):
    event = event if type(event) is dict else {}
    result = metrics(event)
    regions = event.get('regions') if type(event.get('regions')) is dict else {}
    rows = regions.get('regions') if type(regions.get('regions')) is list else []
    result.update(regions=metrics(regions), regionMetrics=[metrics(row) for row in rows[:9]],
                  omittedRegionCount=max(0, len(rows)-9), residual=metrics(event.get('residual')))
    return result


def observation_summary(frame, context, fit, strategy, reference=None):
    result = fit_summary(fit)
    result.update(strategy=code(strategy), frameShapeHW=list(frame.shape[:2]))
    if reference is not None:
        import hashlib
        metadata = reference.metadata
        geometry = metadata.get('sourceGeometry', {})
        binding = metadata.get('binding', {})
        result['resourceBinding'] = dict(mapId=identity(binding.get('mapId')), floor=identity(binding.get('floor')),
            referenceShapeHW=list(reference.clean_mask.shape),
            referenceMetadataSha256=hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            sourceCatalogSha256=digest(geometry.get('sourceCatalogSha256')),
            rendererMetadataSha256=digest(geometry.get('rendererMetadataSha256')))
    if context is not None:
        import numpy as np
        result['observationBounds'] = vector(context.get('observationBounds'), 4)
        query = context.get('query')
        if isinstance(query, np.ndarray) and query.ndim == 2:
            result['queryPixelCount'] = int(np.count_nonzero(query))
        for source, key in (('nodes', 'nodeCount'), ('lines', 'lineCount')):
            if source in context:
                result[key] = len(context[source])
        observed = context.get('observed')
        if isinstance(observed, np.ndarray) and observed.ndim == 2:
            result['observedEdgePixels'] = int(np.count_nonzero(observed))
    return result


def scan_summary(events, *, mode, resource_key, floor, frame_shape, event_counts=None, outcomes=()):
    """Allowlist at the source; a second copy is checked before persistence."""
    fits = [e for e in events if e.get('event') == 's1-fit-observation']
    priors = [e for e in events if e.get('event') == 'experimental-pose-prior']
    fast = [e for e in events if e.get('event') == 'experimental-fast-alignment']
    totals = event_counts or {}
    result = dict(schemaVersion=1, mode=code(mode), resourceKey=digest(resource_key),
        floor=identity(str(floor)), frameShapeHW=vector(list(frame_shape[:2]), 2),
        observations=fits[-2:], omittedObservationCount=max(0, totals.get('s1-fit-observation', len(fits))-2),
        priorAttempts=[dict(mapId=identity(str(e.get('mapId'))), floor=identity(str(e.get('floor'))),
            matrix=matrix(e.get('priorMatrix')), accepted=e.get('accepted') is True,
            fallback=e.get('fallback') is True, elapsedMs=number(e.get('elapsedMs')),
            rejection=fast_summary(e.get('rejection'))) for e in priors[-3:]],
        omittedPriorCount=max(0, totals.get('experimental-pose-prior', len(priors))-3),
        fastAttempts=[dict(fast_summary(e), searchKind=code(e.get('searchKind'))) for e in fast[-3:]],
        omittedFastCount=max(0, totals.get('experimental-fast-alignment', len(fast))-3),
        outcomes=[dict(mapId=identity(row.get('mapId')), reason=code(row.get('reason')),
                       outcome=code(row.get('outcome'))) for row in outcomes[:2]],
        omittedOutcomeCount=max(0, len(outcomes)-2))
    return safe_evidence(result)


def safe_evidence(value):
    """Exact fields/types only; hostile dictionaries cannot widen persistence."""
    if type(value) is not dict:
        return {}
    # This recursive copy has no catch-all container/string/array case.
    numeric = {'schemaVersion', 'omittedObservationCount', 'omittedPriorCount', 'omittedFastCount',
        'queryPixelCount', 'nodeCount', 'lineCount', 'observedEdgePixels', 'elapsedMs',
        'omittedCandidateCount', 'omittedRegionCount', 'omittedEvidenceCount', 'queryChains', 'sourceChains',
        'markerOccludedProposalChains', 'measuredSeedCount', 'seedCount', 'seedBudget', 'candidateCount', 'validationBudget', 'acceptedPoseCount', 'points',
        'coarsePoints', 'proposalMs', 'totalMs', 'maskEdges', 'contrastSupportedEdges',
        'withinThreePixels', 'withinSixPixels', 'medianDistance', 'medianErrorPixels', 'count', 'omittedOutcomeCount',
        'median', 'within3', 'within6', 'pointRejectionCount'}
    booleans = {'success', 'accepted', 'fallback', 'verified', 'regionalEdgeVerified', 'scaleLocked', 'truncated', 'markerOccludedWallProposal', 'markerOccludedWallObservation'}
    codes = {'mode', 'reason', 'strategy', 'pointGeometryReason', 'wallGeometryReason', 'priorReason', 'searchKind', 'outcome'}
    hashes = {'resourceKey', 'indexSha256', 'ownerScopeDigest', 'sourceCatalogSha256', 'rendererMetadataSha256', 'referenceMetadataSha256'}
    boxes = {'observationBounds': 4, 'frameShapeHW': 2, 'cropBounds': 4, 'cropOriginClientXY': 2,
             'cropShapeHW': 2, 'sampleScaleXY': 2, 'referenceShapeHW': 2, 'cell': 2}
    lists = {'observations': 2, 'priorAttempts': 3, 'fastAttempts': 3, 'candidates': 2, 'regionMetrics': 9, 'outcomes': 2}
    # Container names have meaning only at their declared location. In
    # particular a prior row cannot contain another prior list or binding.
    scalar_metric = numeric | booleans | {'reason', 'pointGeometryReason', 'wallGeometryReason', 'matrix', 'cell'} | STAGE_MATRICES
    bindings = hashes | {'mode', 'mapId', 'floor', 'sourceFloor', 'targetFloor', 'referenceShapeHW'}
    fields = {
        'root': {'schemaVersion', 'mode', 'resourceKey', 'floor', 'frameShapeHW', 'observations',
            'omittedObservationCount', 'priorAttempts', 'omittedPriorCount', 'fastAttempts', 'omittedFastCount',
            'outcomes', 'omittedOutcomeCount', 'priorBinding', 'priorReason', 'cropBounds',
            'cropOriginClientXY', 'cropShapeHW', 'sampleScaleXY', 'omittedEvidenceCount', 'truncated'},
        'observation': {'success', 'reason', 'statistics', 'residual', 'candidates', 'omittedCandidateCount',
            'strategy', 'frameShapeHW', 'resourceBinding', 'observationBounds', 'queryPixelCount', 'nodeCount',
            'lineCount', 'observedEdgePixels', 'mapId', 'floor'},
        'candidate': {'matrix', 'accepted', 'regions', 'regionMetrics', 'omittedRegionCount', 'residual'},
        'prior': {'mapId', 'floor', 'matrix', 'accepted', 'fallback', 'elapsedMs', 'rejection'},
        'fast': scalar_metric | {'searchKind', 'regions', 'regionMetrics', 'omittedRegionCount', 'residual'},
        'metric': scalar_metric,
        'binding': bindings,
        'outcome': {'mapId', 'reason', 'outcome'},
    }
    children = {
        ('root', 'priorBinding'): 'binding', ('observation', 'resourceBinding'): 'binding',
        ('observation', 'statistics'): 'metric', ('observation', 'residual'): 'metric',
        ('candidate', 'regions'): 'metric', ('candidate', 'residual'): 'metric',
        ('prior', 'rejection'): 'fast', ('fast', 'regions'): 'metric', ('fast', 'residual'): 'metric',
    }
    item_kinds = {('root', 'observations'): 'observation', ('root', 'priorAttempts'): 'prior',
        ('root', 'fastAttempts'): 'fast', ('root', 'outcomes'): 'outcome',
        ('observation', 'candidates'): 'candidate', ('candidate', 'regionMetrics'): 'metric',
        ('fast', 'regionMetrics'): 'metric'}
    previous_omitted = value.get('omittedEvidenceCount')
    omitted = [max(0, min(1000000000, previous_omitted)) if type(previous_omitted) is int else 0]
    nodes = [256]
    def copy(node, kind='root', depth=0):
        nodes[0] -= 1
        if type(node) is not dict or depth > 7 or nodes[0] < 0:
            omitted[0] += 1
            return {}
        out = {}
        for key, child in islice(node.items(), 48):
            if key not in fields[kind]:
                omitted[0] += 1
            elif key in numeric:
                out[key] = number(child)
            elif key in booleans and type(child) is bool:
                out[key] = child
            elif key in codes:
                out[key] = code(child)
            elif key in hashes:
                out[key] = digest(child)
            elif key in ('mapId', 'floor', 'sourceFloor', 'targetFloor'):
                out[key] = identity(child)
            elif key == 'matrix' or key in STAGE_MATRICES:
                out[key] = matrix(child)
            elif key in boxes:
                out[key] = vector(child, boxes[key])
            elif (kind, key) in children:
                out[key] = copy(child, children[kind, key], depth+1)
            elif (kind, key) in item_kinds and type(child) is list:
                out[key] = [copy(row, item_kinds[kind, key], depth+1) for row in child[:lists[key]]]
                omitted[0] += max(0, len(child)-lists[key])
            else:
                omitted[0] += 1
        omitted[0] += max(0, len(node)-48)
        return out
    result = copy(value)
    def size():
        # The automatic journal uses JSON's default separators too.
        return len(json.dumps(result, allow_nan=False).encode('utf8'))
    def compact_stage(stage):
        # Keep the verdict/reason and stage matrices when detailed region rows
        # would crowd out all useful local/global evidence.
        if not isinstance(stage, dict) or stage.get('truncated'):
            return False
        keep = {'reason', 'accepted', 'fallback', 'elapsedMs', 'searchKind', 'scaleLocked',
                'pointGeometryReason', 'wallGeometryReason', 'matrix'} | STAGE_MATRICES
        compact = {key: child for key, child in stage.items() if key in keep}
        for key, verdict in (('regions', 'regionalEdgeVerified'), ('residual', 'verified')):
            if verdict in stage.get(key, {}):
                compact[key] = {verdict: stage[key][verdict]}
        compact['omittedRegionCount'] = (stage.get('omittedRegionCount') or 0)+len(stage.get('regionMetrics', []))
        compact['truncated'] = True
        stage.clear()
        stage.update(compact)
        return True
    # Preserve binding and actual prior matrices before optional candidate detail.
    while size() > MAX_BYTES-80:
        obs = result.get('observations', [])
        candidate_owner = next((row for row in obs if row.get('candidates')), None)
        if candidate_owner is not None:
            candidate_owner['candidates'].pop()
            candidate_owner['omittedCandidateCount'] = (candidate_owner.get('omittedCandidateCount') or 0)+1
        elif obs:
            obs.pop(0)
            result['omittedObservationCount'] = (result.get('omittedObservationCount') or 0)+1
        else:
            # Also bound adversarial nesting of otherwise known field names.
            # Keep a compact, typed prior proposal even when optional detail is
            # pathological; do not let it consume journal ownership capacity.
            stages = [row.get('rejection') for row in result.get('priorAttempts', [])]
            stages += result.get('fastAttempts', [])
            if any(compact_stage(stage) for stage in stages):
                pass
            elif len(result.get('fastAttempts', [])) > 1:
                result['fastAttempts'].pop(0)
                result['omittedFastCount'] = (result.get('omittedFastCount') or 0)+1
            else:
                choices = [(len(json.dumps(child)), key) for key, child in result.items()
                           if isinstance(child, (dict, list))]
                if not choices:
                    break
                removed_key = max(choices)[1]
                removed = result.pop(removed_key)
                counter = {'fastAttempts': 'omittedFastCount', 'outcomes': 'omittedOutcomeCount',
                           'priorAttempts': 'omittedPriorCount'}.get(removed_key)
                if counter:
                    result[counter] = (result.get(counter) or 0)+len(removed)
        omitted[0] += 1
    result['omittedEvidenceCount'] = omitted[0]
    if omitted[0]:
        result['truncated'] = True
    # Fail closed even if a future schema extension defeats the normal pruning
    # priorities. Encoding size, including omission metadata, is authoritative.
    if size() > MAX_BYTES:
        result = dict(schemaVersion=1, truncated=True, omittedEvidenceCount=omitted[0]+1)
    return result
