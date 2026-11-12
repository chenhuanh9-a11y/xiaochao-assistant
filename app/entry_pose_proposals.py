"""Entry geometry supplies proposals, never identity or persistent pose authority."""
from pathlib import Path

import numpy as np


def current_entry_pose_seeds(rows, floor):
    seeds = {}
    for row in rows:
        entry = row.get('difficultEntry') or {}
        checked = entry.get('verification') or {}
        if (entry.get('mapId') != row.get('mapId') or str(entry.get('floor', floor)) != str(floor)
                or checked.get('success') is not True or entry.get('poseAmbiguous')
                or (entry.get('entryCrossCheck') or {}).get('contradicted') is True):
            continue
        try:
            matrix = np.asarray(checked.get('matrixSourceToClient'), dtype=float)
            if matrix.shape == (3, 3) and np.isfinite(matrix).all():
                seeds[row['mapId']] = entry
        except (ValueError, TypeError):
            pass
    return seeds


def entry_pose_proposals_for_reference(candidate, map_id, floor, reference, adapters):
    """Keep the proposal only when its canvas and this reference share a basis.

    The entry verifier already returned a source-to-client matrix. Comparing
    the bridges proves the canvas; applying either bridge again would change it.
    The initial solver must still refine and validate it against current pixels.
    """
    if not candidate or reference.metadata.get('authorizesStructureValidation') is False:
        return ()
    entry = current_entry_pose_seeds([dict(mapId=map_id, difficultEntry=candidate)], floor).get(map_id)
    binding = reference.metadata.get('binding') or {}
    if (entry is None or not entry.get('profileId') or not binding.get('profile')
            or binding.get('mapId') != map_id or str(binding.get('floor')) != str(floor)):
        return ()
    try:
        source = np.asarray(adapters.matrix_profile_to_canonical(entry['profileId'], map_id, floor), dtype=float)
        target = np.asarray(adapters.matrix_profile_to_canonical(binding['profile'], map_id, floor), dtype=float)
        if (source.shape == target.shape == (3, 3) and np.isfinite(source).all()
                and np.isfinite(target).all() and np.allclose(source, target, rtol=0, atol=1e-9)):
            return (np.array(entry['verification']['matrixSourceToClient'], dtype=float, copy=True),)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return ()


def completed_entry_pose_candidate(observation, frame, map_id, floor):
    """Read a pose only from its actual completed current-frame competition.

    The work owner stays out of public rows. A newly created snapshot or a row
    carrying old uniqueness flags cannot stand in for the work that ran the
    source jobs and reduction on these exact immutable pixels.
    """
    from map_observation_work import MapObservationWork
    if (not isinstance(observation, MapObservationWork)
            or observation.frame_snapshot is None
            or observation.frame_snapshot.pixels is not frame or observation.frame is not frame
            or observation._complete is not True or observation.entry_complete is not True
            or observation._failed or observation.errors or observation.scope_invalid or observation.entry_unresolved
            or observation._pending is not None or str(observation.floor) != str(floor)):
        return None
    rows = observation.finish()
    # Do not discard a competing qualified pose before deriving uniqueness.
    poses = [row['difficultEntry'] for row in rows
             if isinstance(row.get('difficultEntry'), dict)
             and row['difficultEntry'].get('poseVerified') is True]
    if len(poses) != 1:
        return None
    candidate = poses[0]
    return candidate if (candidate.get('mapId') == map_id
        and str(candidate.get('floor')) == str(floor)) else None


def qualified_entry_pose_for_reference(candidate, map_id, floor, reference, adapters, *, sources):
    """Select one complete, purpose-eligible proposal in the prepared canvas.

    This only checks source/canvas qualification, not frame ownership or pose
    uniqueness. The caller must get the candidate from completed current work,
    then recheck H against current pixels without reusing the row's residual.
    Source declarations come from the actual locator, not the row.
    """
    if (not isinstance(candidate, dict) or candidate.get('poseVerified') is not True
            or candidate.get('competitionComplete') is not True
            or candidate.get('identityOnly') is True or candidate.get('poseAmbiguous')
            or candidate.get('proposalOnly') is True or candidate.get('authorizesPose') is False):
        return None
    members = candidate.get('entryPoseCandidates')
    if members is not None and (not isinstance(members, (list, tuple)) or len(members) != 1):
        return None
    proposals = entry_pose_proposals_for_reference(candidate, map_id, floor, reference, adapters)
    if len(proposals) != 1:
        return None
    matrix = proposals[0]
    if (matrix[0, 0] <= 0 or not np.allclose(matrix[2], [0., 0., 1.])
            or not np.allclose(matrix[:2, :2], np.eye(2) * matrix[0, 0])):
        return None
    if members is not None:
        member = members[0]
        try:
            if (not isinstance(member, dict) or member.get('mapId') != map_id
                    or str(member.get('floor')) != str(floor) or member.get('poseAmbiguous')
                    or (member.get('verification') or {}).get('success') is not True
                    or not np.array_equal(np.asarray(member['verification']['matrixSourceToClient']), matrix)):
                return None
        except (ValueError, TypeError, KeyError):
            return None
    matches = [source for source in sources if all(source.get(key) == candidate.get(key)
        for key in ('mapId', 'floor', 'sourceProduct'))
        and (candidate.get('region') is None or source.get('region') == candidate['region'])]
    if len(matches) != 1:
        return None
    source = matches[0]
    if (source.get('proposalOnly') is True or source.get('authorizesPose') is False
            or source.get('featureRole') == 'local-contour-proposals-not-semantic-side-exits'
            or source.get('sourceClass') == 'legacy-clean'
            or source.get('sourceKind') == 'legacy-manually-reconstructed-clean-structure'):
        return None
    # Lexical metadata comparisons only: geometry never reopens the resource.
    # Declarations use either a basename or a resource-root-relative name.
    # Keep any declared directories: merely equal basenames are insufficient.
    product = source.get('sourceProduct')
    try:
        expected = (Path(reference.path).relative_to(reference.resource_root).as_posix()
                    if '/' in product or '\\' in product else Path(reference.path).name)
    except (ValueError, TypeError):
        return None
    if product != expected:
        return None
    binding = reference.metadata.get('binding') or {}
    if source.get('variantId', binding.get('variant', 'variant-01')) != binding.get('variant', 'variant-01'):
        return None
    for key in ('sourceVersion', 'structureVersionKey'):
        if source.get(key) != reference.metadata.get(key):
            return None
    return matrix
