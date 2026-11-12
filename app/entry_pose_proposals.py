"""Current-frame entry geometry is a proposal, never identity or pose authority."""
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
