"""Read-only S1 author display adapter over reviewed original JPEG canvases.

The native typed provider, identity catalog and recognition matrices are never
modified. Missing B4 author art remains an explicit canonical-native fallback.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import threading
from pathlib import Path

import cv2
import numpy as np

from author_route_catalog import AuthorRouteCatalog, AuthorRouteProfile, AuthorRouteVariant
from optional_author_displays import OptionalAuthorDisplayAdapters
from s1_author_package import (PACKAGE_ID, PROFILE_ID, TITLE, BINDINGS_PATH,
    SOURCE_VERSION, validate_bindings, receipt_rows)


class InstalledAuthorPackage:
    def __init__(self, resource_root):
        import star_map_import as api
        api._no_links(Path(resource_root))
        self.root=Path(resource_root).resolve()
        owned,deps,_=api._installed_inventory(self.root.parent,PACKAGE_ID)
        if not owned:
            raise FileNotFoundError('s1-author-not-installed')
        self._inventory={**deps,**owned}
        raw=self._read(BINDINGS_PATH)
        self.document=validate_bindings(raw,owned,deps,self._read)
        self.registration_sha=hashlib.sha256(raw).hexdigest()

    def _path(self, relative):
        import star_map_import as api
        if not relative.startswith('_internal/'):
            raise ValueError('s1-author-resource-path-invalid')
        return api._target(self.root,relative[len('_internal/'):])

    def _read(self, relative):
        row=self._inventory.get(relative.casefold())
        if row is None:
            raise ValueError('s1-author-resource-undeclared')
        with self._path(relative).open('rb') as stream:
            data=stream.read(row['bytes']+1)
        if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ValueError('s1-author-resource-changed')
        return data


class S1AuthorCatalog(AuthorRouteCatalog):
    """S1 floor namespace without widening the established S0 catalog."""
    def __init__(self, base):
        self.__dict__.update(base.__dict__)
        self.profiles=dict(base.profiles)
        self._raw=copy.deepcopy(base._raw)

    def route_path(self, profile_id, map_id, floor, variant_id=None, *, include_inactive=False):
        if profile_id!=PROFILE_ID or str(floor) not in {'-1','-2','-3'}:
            return None
        variants=self.variants(profile_id,str(map_id),include_inactive=include_inactive)
        variant=next((v for v in variants if v.id==(variant_id or 'variant-01')),None)
        path=variant.layers.get(str(floor)) if variant else None
        return path if path is not None and path.is_file() else None


class S1AuthorDisplays:
    def __init__(self, resource_root, native):
        import star_map_import as api
        self.root=Path(resource_root).resolve()
        self.native=native
        self.bindings={}
        self.profiles=set()
        self.errors={}
        self._cache=OrderedDict()
        self._cache_lock=threading.RLock()
        self.catalog=S1AuthorCatalog(native.catalog)
        self.adapters=native.adapters
        if native.mode not in ('difficult','nightmare'):
            return
        if native.root != self.root/'seasons/s1'/native.mode:
            self.errors[PROFILE_ID]='s1-author-resource-root-mismatch'
            return
        receipt=api._target(self.root.parent,f'work/map-packages/{PACKAGE_ID}.json')
        if not receipt.exists():
            return
        try:
            package=InstalledAuthorPackage(self.root)
            self._read,self._path=package._read,package._path
            doc=package.document
            self.registration_sha=package.registration_sha
            maps={}
            for row in doc['layers']:
                if row['mode']!=native.mode:
                    continue
                path=self._path(row['displayPath'])
                matrix=np.asarray(row['matrixDisplayToReference'],dtype=np.float64)
                matrix.setflags(write=False)
                self.bindings[(PROFILE_ID,row['mapId'],row['floor'])]=dict(
                    path=path,matrix=matrix,usable=True,status='offline-reviewed',declaration=row)
                maps.setdefault(row['mapId'],{})[row['floor']]=path
            variants={mid:(AuthorRouteVariant(PROFILE_ID,mid,'variant-01','加页手记',layers,
                'display-only-reviewed','display-only'),) for mid,layers in maps.items()}
            self.catalog.profiles[PROFILE_ID]=AuthorRouteProfile(PROFILE_ID,TITLE,'加页手记','S1',
                True,True,True,variants)
            self.catalog._raw['profiles'].append(dict(id=PROFILE_ID,maps={mid:[dict(id='variant-01',
                layerMeta={floor:dict(sha256=self.bindings[(PROFILE_ID,mid,floor)]['declaration']['displaySha256'])
                    for floor in layers})] for mid,layers in maps.items()}))
            self.profiles.add(PROFILE_ID)
            self.adapters=OptionalAuthorDisplayAdapters(native.adapters,self.bindings,self.profiles)
        except (OSError,ValueError,KeyError,TypeError,cv2.error) as error:
            self.bindings.clear()
            self.errors[PROFILE_ID]=str(error)

    def binding(self, profile, map_id, floor):
        return self.bindings.get((str(profile),str(map_id),str(floor)))

    def pixels(self, path, role):
        if role not in {'full-reference','minimap','aligned-overlay'}:
            raise ValueError('s1-author-display-role-invalid')
        path=Path(path).resolve()
        binding=next((r for r in self.bindings.values() if r['path']==path),None)
        if binding is None:
            raise ValueError('s1-author-display-path-unregistered')
        row=binding['declaration']
        # Revalidate before cache hits. Invalid data never revives earlier pixels.
        data=self._read(row['displayPath'])
        self._read(BINDINGS_PATH)
        self._read(row['referenceResource'])
        key=(row['displaySha256'],self.registration_sha)
        with self._cache_lock:
            pixels=self._cache.get(key)
            if pixels is not None:
                self._cache.move_to_end(key)
        if pixels is None:
            pixels=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
            if pixels is None or list(pixels.shape[:2])!=row['displayShapeHW']:
                raise ValueError('s1-author-display-decode-failed')
            # Preserve the entire author canvas and annotations, including margins.
            # This is format conversion only: no crop, rescale, threshold or rewrite.
            pixels=cv2.cvtColor(pixels,cv2.COLOR_BGR2BGRA)
            pixels.setflags(write=False)
            with self._cache_lock:
                self._cache[key]=pixels
                self._cache.move_to_end(key)
                while len(self._cache)>6 or sum(p.nbytes for p in self._cache.values())>96*1024*1024:
                    self._cache.popitem(last=False)
        return pixels


def append_browse_profile(context):
    """Join a reviewed display snapshot without constructing any runtime engine."""
    from dataclasses import replace
    from types import MappingProxyType
    from map_resource_context import ProfileSummary
    from map_resource_model import ResourceMap, ResourceVariant
    if context.mode not in ('difficult','nightmare') or context.error:
        return context
    try:
        package=InstalledAuthorPackage(context.root)
    except FileNotFoundError:
        return context
    except (OSError,ValueError,KeyError,TypeError) as error:
        return replace(context,warnings=(*context.warnings,'s1-author-unavailable:'+str(error)))
    maps={}
    stamps=dict(context.path_stamps)
    for row in package.document['layers']:
        if row['mode']!=context.mode:
            continue
        path=package._path(row['displayPath'])
        info=path.stat()
        stamps[path]=(info.st_mtime_ns,info.st_size,row['displaySha256'])
        maps.setdefault(row['mapId'],{})[f"source-height-{5+int(row['floor'])}"]=path
    items=tuple(ResourceMap(mid,mid,(ResourceVariant('variant-01','加页手记',layers),))
        for mid,layers in sorted(maps.items()))
    first=next((item.id,floor,path) for item in items
        for variant in item.variants for floor,path in variant.layers.items())
    profile=ProfileSummary(PROFILE_ID,TITLE,PACKAGE_ID,len(items),len(items),0,len(items),
        len(items)*4,len(items)*3,first,items)
    return replace(context,profiles=(*context.profiles,profile),path_stamps=MappingProxyType(stamps),
        warnings=(*context.warnings,'s1-author-b4-not-provided;canonical-native-fallback'))
