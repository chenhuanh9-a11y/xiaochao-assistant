"""Resource-bound pose proposals. No identity, current-pose or display authority.

Only the runtime's internally acknowledged material supplies AcknowledgedPose.
Do not construct one from UI payloads. Current round/window/resource ownership
must be checked again by matrix_for before passing the proposal to the solver.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import numpy as np


def isotropic_matrix(value):
    """Return a finite positive fixed-axis affine matrix, or reject the hint."""
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
            or matrix[0, 0] <= 0
            or not np.allclose(matrix[2], [0, 0, 1], rtol=0, atol=1e-12)
            or not np.allclose(matrix[:2, :2], np.eye(2)*matrix[0, 0], rtol=0, atol=1e-10)):
        return None
    return matrix.copy()


@dataclass(frozen=True)
class AcknowledgedPose:
    mode: str
    resource_key: str
    map_id: str
    floor: str
    owner_scope: object
    frame_id: str
    matrix: tuple
    material_acknowledged: bool
    current_frame_geometry_verified: bool


@dataclass(frozen=True)
class PosePrior:
    mode: str
    resource_key: str
    index_sha256: str
    map_id: str
    source_floor: str
    target_floor: str
    source_frame_id: str
    owner_scope: object
    matrix: tuple
    display_authorized: bool = False
    identity_authorized: bool = False

    def matrix_for(self, *, mode, resource_key, map_id, floor, owner_scope):
        if ((mode, resource_key, map_id, str(floor), owner_scope) !=
                (self.mode, self.resource_key, self.map_id, self.target_floor, self.owner_scope)):
            return None
        return isotropic_matrix(self.matrix)


class CoordinatePriorIndex:
    def __init__(self, index_path, *, expected_sha256, mode, resource_key, provider, read_resource=None):
        data = Path(index_path).read_bytes() if read_resource is None else read_resource(Path(index_path))
        digest = hashlib.sha256(data).hexdigest()
        if not expected_sha256 or digest != expected_sha256:
            raise ValueError('pose-prior-index-digest-mismatch')
        index = json.loads(data)
        if index.get('schema') != 's1-common-entrance-coordinate-index-v1' or mode not in index.get('maps', {}):
            raise ValueError('pose-prior-index-scope-mismatch')
        self.index, self.mode, self.resource_key = index, mode, resource_key
        self.provider, self.index_sha256 = provider, digest

    def _native_to_common(self, map_id, floor):
        entry = self.index['maps'][self.mode][map_id][floor]
        reference = self.provider(map_id, floor)
        if reference is None:
            raise ValueError('pose-prior-reference-missing')
        metadata = reference.metadata
        geometry = metadata.get('sourceGeometry', {})
        binding = metadata.get('binding', {})
        if ((binding.get('mapId'), str(binding.get('floor')), binding.get('profile')) != (map_id, floor, 'shared-structure')
                or metadata.get('sourceVersion') != self.index['sourceVersion']
                or geometry.get('sourceCatalogSha256') != entry['sourceCatalogSha256']
                or geometry.get('rendererMetadataSha256') != entry['sourceRendererMetadataSha256']
                or list(reference.clean_mask.shape) != entry['shapeHW']):
            raise ValueError('pose-prior-resource-binding-mismatch')
        xz = np.asarray(entry['imageToEntranceXZ'], dtype=np.float64)
        if (xz.shape != (3, 3) or not np.isfinite(xz).all()
                or not np.array_equal(xz[:2, :2], [[1, 0], [0, -1]])
                or not np.array_equal(xz[2], [0, 0, 1])):
            raise ValueError('pose-prior-index-coordinate-contract')
        return np.diag([1., -1., 1.]) @ xz

    def make_hint(self, source, *, target_floor, current_owner_scope):
        if (type(source) is not AcknowledgedPose or source.material_acknowledged is not True
                or source.current_frame_geometry_verified is not True or not source.frame_id
                or source.owner_scope != current_owner_scope
                or source.mode != self.mode or source.resource_key != self.resource_key):
            return None
        matrix = isotropic_matrix(source.matrix)
        if matrix is None:
            return None
        try:
            old = self._native_to_common(source.map_id, str(source.floor))
            new = self._native_to_common(source.map_id, str(target_floor))
        except (KeyError, TypeError, ValueError, OSError):
            return None
        proposal = isotropic_matrix(matrix @ np.linalg.inv(old) @ new)
        if proposal is None:
            return None
        return PosePrior(self.mode, self.resource_key, self.index_sha256, source.map_id,
                         str(source.floor), str(target_floor), source.frame_id,
                         current_owner_scope, tuple(map(tuple, proposal)))
