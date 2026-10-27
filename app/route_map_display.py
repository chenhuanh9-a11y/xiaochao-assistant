"""Normal source-rendered editor backgrounds, separate from routing/recognition.

Images are optional licensed companion resources, never inferred by recolouring
the binary mask. Exact image and mask bindings gate their native-pixel alignment.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path,PurePosixPath
import sys
from PIL import Image
from route_topology import mask_hash

@dataclass(frozen=True)
class Background:
    image: Image.Image | None
    signature: str
    status: str

_cache={}

def _entries():
    try:
        from route_render_pilot_data import RENDERS
        return RENDERS['entries'] if RENDERS.get('schema')=='custom-route-render-v1' else []
    except (ImportError,KeyError):
        return []

def load_background(mask, *, asset_root=None):
    digest=mask_hash(mask)
    matches=[e for e in _entries() if e['maskSha256']==digest and tuple(e['shape'])==tuple(mask.shape)]
    if len(matches)!=1:
        return Background(None,'unpaired:'+digest,'未配对正常渲染图，当前为结构预览')
    entry=matches[0];rel=PurePosixPath(entry['imagePath'])
    if rel.is_absolute() or '..' in rel.parts or entry.get('sourceKind')!='source-rendered-no-author-overlays':
        return Background(None,'invalid:'+digest,'渲染图绑定无效，当前为结构预览')
    root=Path(asset_root or os.environ.get('XINGTU_EDITOR_RENDER_ROOT') or
              (Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]))
    path=root/Path(*rel.parts)
    try:
        stat=path.stat();signature=f'{digest}:{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{entry["imageSha256"]}'
        if signature in _cache:
            return _cache[signature]
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['imageSha256']:
            return Background(None,signature+':bad-hash','正常渲染图校验失败，当前为结构预览')
        image=Image.open(io.BytesIO(raw)).convert('RGBA')
        if image.size!=(mask.shape[1],mask.shape[0]):
            return Background(None,signature+':bad-shape','正常渲染图尺寸不匹配，当前为结构预览')
        result=Background(image,signature,'源渲染图 · 无作者路线')
        _cache[signature]=result
        while len(_cache)>4:_cache.pop(next(iter(_cache)))
        return result
    except (OSError,ValueError):
        return Background(None,'missing:'+str(path)+':'+digest,'正常渲染图未安装，当前为结构预览')
