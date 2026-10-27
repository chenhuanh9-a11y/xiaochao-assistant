"""External paths for historical author recipes; importing does not write."""
import os
from pathlib import Path
from PIL import ImageFont

REPOSITORY = Path(__file__).resolve().parents[3]
PRIVATE = REPOSITORY/'resources-private'
AUTHOR_WORK = Path(os.environ.get('XIAOCHAO_AUTHOR_WORK', str(REPOSITORY/'work'/'author-alignment')))
CANONICAL_WORK = Path(os.environ.get('XIAOCHAO_CANONICAL_WORK', str(REPOSITORY/'work'/'canonical-display')))
TILEMAP_ROOT = Path(os.environ.get('XIAOCHAO_TILEMAP_ROOT', str(PRIVATE/'tilemap-root')))
AUTHOR_ROOTS = {
    'wang-qingxin': Path(os.environ.get('XIAOCHAO_WANG_INPUT', str(PRIVATE/'authors'/'wang-qingxin'))),
    'nightingale': Path(os.environ.get('XIAOCHAO_NIGHTINGALE_INPUT', str(PRIVATE/'authors'/'nightingale'))),
}

def font(size):
    configured = os.environ.get('XIAOCHAO_CJK_FONT')
    return ImageFont.truetype(configured, size) if configured else ImageFont.load_default(size=size)
