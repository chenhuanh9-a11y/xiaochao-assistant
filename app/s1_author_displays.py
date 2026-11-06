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
    SOURCE_VERSION, validate_bindings, receipt_rows, PROFILE_IDS, TRANSPARENT_PROFILE_ID, bindings_path, title)


class InstalledAuthorPackage:
    def __init__(self, resource_root, package_id=PACKAGE_ID):
        import star_map_import as api
        api._no_links(Path(resource_root))
        self.root=Path(resource_root).resolve()
        owned,deps,_=api._installed_inventory(self.root.parent,package_id)
        if not owned:
            raise FileNotFoundError('s1-author-not-installed')
        self._inventory={**deps,**owned}
        self.profile_id = package_id
        self.bindings_path = bindings_path(package_id)
        raw=self._read(self.bindings_path)
        self.document=validate_bindings(raw,owned,deps,self._read)
        if self.document["profileId"] != package_id:
            raise ValueError("s1-author-binding-context-mismatch")
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
        if profile_id not in PROFILE_IDS or str(floor) not in {'-1','-2','-3'}:
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
        for profile_id in PROFILE_IDS:
            receipt=api._target(self.root.parent,f'work/map-packages/{profile_id}.json')
            if not receipt.exists():
                continue
            try:
                package=InstalledAuthorPackage(self.root, profile_id)
                doc=package.document
                maps={}
                for row in doc['layers']:
                    if row['mode']!=native.mode:
                        continue
                    path=package._path(row['displayPath'])
                    matrix=np.asarray(row['matrixDisplayToReference'],dtype=np.float64)
                    matrix.setflags(write=False)
                    self.bindings[(profile_id,row['mapId'],row['floor'])]=dict(
                        path=path,matrix=matrix,usable=True,status='offline-reviewed',declaration=row,package=package)
                    maps.setdefault(row['mapId'],{})[row['floor']]=path
                variants={mid:(AuthorRouteVariant(profile_id,mid,'variant-01','爱吃醋',layers,
                    'display-only-reviewed','display-only'),) for mid,layers in maps.items()}
                self.catalog.profiles[profile_id]=AuthorRouteProfile(profile_id,title(profile_id),'爱吃醋','S1',
                    True,True,True,variants)
                self.catalog._raw['profiles'].append(dict(id=profile_id,maps={mid:[dict(id='variant-01',
                    layerMeta={floor:dict(sha256=self.bindings[(profile_id,mid,floor)]['declaration']['displaySha256'])
                        for floor in layers})] for mid,layers in maps.items()}))
                self.profiles.add(profile_id)
                self.adapters=OptionalAuthorDisplayAdapters(native.adapters,self.bindings,self.profiles)
            except (OSError,ValueError,KeyError,TypeError,cv2.error) as error:
                self.bindings={key: value for key, value in self.bindings.items() if key[0] != profile_id}
                self.errors[profile_id]=str(error)

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
        package=binding['package']
        data=package._read(row['displayPath'])
        package._read(package.bindings_path)
        package._read(row['referenceResource'])
        key=(row['displaySha256'],package.registration_sha)
        with self._cache_lock:
            pixels=self._cache.get(key)
            if pixels is not None:
                self._cache.move_to_end(key)
        if pixels is None:
            pixels=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_UNCHANGED)
            if pixels is None or list(pixels.shape[:2])!=row['displayShapeHW']:
                raise ValueError('s1-author-display-decode-failed')
            # Preserve the entire author canvas and annotations, including margins.
            # This is format conversion only: no crop, rescale, threshold or rewrite.
            if package.profile_id == TRANSPARENT_PROFILE_ID:
                if pixels.ndim != 3 or pixels.shape[2] != 4:
                    raise ValueError('s1-author-transparent-alpha-missing')
            elif pixels.ndim == 2:
                pixels=cv2.cvtColor(pixels,cv2.COLOR_GRAY2BGRA)
            else:
                pixels=cv2.cvtColor(pixels,cv2.COLOR_BGR2BGRA)
            pixels.setflags(write=False)
            with self._cache_lock:
                self._cache[key]=pixels
                self._cache.move_to_end(key)
                while len(self._cache)>6 or sum(p.nbytes for p in self._cache.values())>96*1024*1024:
                    self._cache.popitem(last=False)
        return pixels


def append_browse_profile(context):
    for profile_id in PROFILE_IDS:
        context = _append_browse_profile(context, profile_id)
    return context


def _append_browse_profile(context, profile_id):
    """Join a reviewed display snapshot without constructing any runtime engine."""
    from dataclasses import replace
    from types import MappingProxyType
    from map_resource_context import ProfileSummary
    from map_resource_model import ResourceMap, ResourceVariant
    if context.mode not in ('difficult','nightmare') or context.error:
        return context
    try:
        package=InstalledAuthorPackage(context.root, profile_id)
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
    items=tuple(ResourceMap(mid,mid,(ResourceVariant('variant-01','爱吃醋',layers),))
        for mid,layers in sorted(maps.items()))
    first=next((item.id,floor,path) for item in items
        for variant in item.variants for floor,path in variant.layers.items())
    profile=ProfileSummary(profile_id,title(profile_id),profile_id,len(items),len(items),0,len(items),
        len(items)*4,len(items)*3,first,items)
    return replace(context,profiles=(*context.profiles,profile),path_stamps=MappingProxyType(stamps),
        warnings=(*context.warnings,'s1-author-b4-not-provided;canonical-native-fallback'))
