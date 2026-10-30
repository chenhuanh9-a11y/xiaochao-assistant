"""Prepare replacement display pixels/matrices without acquiring live authority.

The runtime owner checks its current pose/grant and commits a new display token.
This module never captures, identifies, fits, or edits canonical pose stores.
"""
from copy import deepcopy

import numpy as np

from client_route_preferences import prepare_display_layer


def _matrix(value, name):
    try:
        matrix = np.array(value, dtype=np.float64, copy=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}-matrix-invalid") from error
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError(f"{name}-matrix-invalid")
    return matrix


def prepare_route_reprojection(engine, map_id, floor, preferred, canonical_matrix=None,
                               *, variant_id="variant-01"):
    """Use the original display policy and an optional canonical-to-client H.

    ``matrixDisplayToReference`` maps display pixels into the typed *shared
    reference*, not canonical pixels.  The adapter's shared-reference-to-
    canonical basis is required between that bridge and the canonical pose.
    Omitting the pose prepares only a static layer; neither form grants display.
    All returned data is detached from display caches and caller-owned matrices.
    """
    canonical = None if canonical_matrix is None else _matrix(canonical_matrix, "canonical-client")
    layer = deepcopy(prepare_display_layer(engine, map_id, str(floor), preferred, variant_id=variant_id))
    result = dict(layer=layer)
    if canonical is None:
        return result
    basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, str(floor))
    if basis is None:
        raise ValueError("shared-reference-canonical-basis-unavailable")
    matrix = canonical @ _matrix(basis, "shared-reference-canonical") @ _matrix(
        layer["matrixDisplayToReference"], "display-reference")
    if not np.isfinite(matrix).all():
        raise ValueError("display-client-matrix-invalid")
    result["matrixSourceToClient"] = matrix
    return result
