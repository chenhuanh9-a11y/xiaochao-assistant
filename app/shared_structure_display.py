"""Render the actual reference mask without granting identity or pose rights."""
from copy import deepcopy

import numpy as np


def structure_display_identity(reference):
    """Small source snapshot, detached from a provider's mutable metadata."""
    return dict(path=str(reference.path), binding=deepcopy(reference.metadata['binding']),
        structureVersionKey=reference.metadata.get('structureVersionKey'))


def structure_display_layer(reference):
    pixels = np.empty((*reference.shape, 4), np.uint8)
    occupied = reference.clean_mask != 0
    for channel, value in enumerate((218, 228, 238, 255)):
        np.multiply(occupied, np.uint8(value), out=pixels[..., channel])
    pixels.setflags(write=False)
    draft = reference.metadata.get('sourceClass') == 'legacy-clean-working-draft'
    return dict(pixels=pixels, matrixDisplayToReference=np.eye(3),
        resourceIdentity=structure_display_identity(reference),
        displayKind='shared-structure', displayLabel='历史结构草稿' if draft else '结构底图',
        routeProfileId='shared-structure')
