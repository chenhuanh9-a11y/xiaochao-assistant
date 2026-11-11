"""Render the actual reference mask without granting identity or pose rights."""
import numpy as np


def structure_display_layer(reference):
    pixels = np.zeros((*reference.shape, 4), np.uint8)
    pixels[reference.clean_mask != 0] = (218, 228, 238, 255)
    pixels.setflags(write=False)
    draft = reference.metadata.get('sourceClass') == 'legacy-clean-working-draft'
    return dict(pixels=pixels, matrixDisplayToReference=np.eye(3),
        resourceIdentity=dict(path=str(reference.path), binding=reference.metadata['binding'],
            structureVersionKey=reference.metadata.get('structureVersionKey')),
        displayKind='shared-structure', displayLabel='历史结构草稿' if draft else '结构底图',
        routeProfileId='shared-structure')
