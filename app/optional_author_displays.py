"""Read installed optional author sidecars as display-only coordinate bindings."""
from pathlib import Path
import copy
import json

import cv2
import numpy as np

from author_route_catalog import AuthorRouteCatalog
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
from nightmare_candidate import imread_unicode


class OptionalAuthorDisplayAdapters(AuthorCoordinateAdapters):
    def __init__(self, base, bindings, profiles):
        self.__dict__.update(base.__dict__)
        self.profiles = copy.deepcopy(base.profiles)
        self.display_bindings = bindings
        self.display_profiles = set(profiles)
        for profile in profiles:
            # This role permits lookup of the same typed geometry; it does
            # not turn the author's asset into an identity/validation source.
            self.profiles[profile] = dict(role='display-only')

    def matrix_profile_to_canonical(self, profile_id, map_id, floor):
        if profile_id in self.display_profiles:
            row = self.display_bindings.get((profile_id,str(map_id),str(floor)))
            return row['matrix'] if row is not None and row['usable'] else None
        return super().matrix_profile_to_canonical(profile_id,map_id,floor)


class OptionalAuthorDisplays:
    def __init__(self, root, pack, catalog, adapters, versions):
        self.root = Path(root).resolve()
        self.bindings = {}
        self.profiles = set()
        self.errors = {}
        self.catalog = copy.copy(catalog) if catalog is not None else None
        if self.catalog is not None:
            self.catalog.profiles = dict(catalog.profiles)
            self.catalog._raw = copy.deepcopy(catalog._raw)
        for profile in ('wang-qingxin','nightingale','lianghapi-single'):
            folder = self.root/'references/author-packs'/profile
            sidecar = folder/'catalog.json'
            if not sidecar.is_file():
                continue
            try:
                optional = AuthorRouteCatalog(sidecar, asset_base=self.root)
                payload = json.loads((folder/'display-bindings.json').read_text(encoding='utf-8'))
                rows = self._read_profile(profile,optional,payload,pack,adapters,versions)
                if self.catalog is None:
                    self.catalog = copy.copy(optional)
                    self.catalog.profiles = {}
                    self.catalog._raw = dict(schemaVersion=1,profiles=[])
                self.catalog.profiles[profile] = optional.profiles[profile]
                self.catalog._raw['profiles'] = [row for row in self.catalog._raw['profiles'] if row['id'] != profile]
                self.catalog._raw['profiles'].extend(optional._raw['profiles'])
                self.bindings.update(rows)
                self.profiles.add(profile)
            except (OSError,ValueError,KeyError,TypeError) as error:
                self.errors[profile] = str(error)
        self.adapters = (OptionalAuthorDisplayAdapters(adapters,self.bindings,self.profiles)
                         if self.profiles else adapters)

    def _read_profile(self, profile, catalog, payload, pack, adapters, versions):
        if (payload.get('schema') != 'canonical-author-display-bindings-v1'
                or payload.get('profileId') != profile
                or payload.get('canonicalPackId') != adapters.canonical_pack_id
                or payload.get('coordinateReferenceProfileId') != adapters.coordinate_reference_profile_id
                or profile not in catalog.profiles):
            raise ValueError('optional author display context mismatch')
        expected = {(item.id,str(floor)) for item in pack.maps for floor in item.layers}
        declared = {(map_id,str(floor))
                    for map_id,variants in catalog.profiles[profile].maps.items()
                    for variant in variants for floor in variant.layers}
        if not declared or not declared <= expected:
            raise ValueError('optional author catalog contains no current display layers')
        rows = {}
        for declaration in payload['entries']:
            map_id,floor = str(declaration['mapId']),str(declaration['floor'])
            key = (profile,map_id,floor)
            if key in rows or (map_id,floor) not in expected:
                raise ValueError('optional author duplicate or non-current display binding')
            version = next((value for binding,value in versions.items()
                            if binding[1] == map_id and binding[3] == floor),None)
            resource = declaration['nativeResourceKey']
            version_key = f"{resource['layoutId']}@{resource['structureRawRecordSha256']}/{floor}"
            path = (self.root/declaration['assetPath']).resolve()
            declared_path = catalog.route_path(profile,map_id,floor,include_inactive=True)
            bridge = declaration.get('coordinateBridge') or {}
            if (version is None or version.get('sharedError')
                    or declaration.get('profileId') != profile or resource.get('floor') != floor
                    or version_key != version['expected']['structureVersionKey']
                    or declaration.get('structureVersionKey') != version_key
                    or declaration.get('sourceVersion',payload.get('sourceVersion')) != version['expected']['sourceVersion']
                    or not path.is_relative_to(self.root) or not path.is_file()
                    or declared_path is None or path != declared_path.resolve()
                    or declaration.get('identityAuthority') is not False
                    or declaration.get('structureValidationAuthority') is not False
                    or declaration.get('currentFrameGeometryFitRequired') is not True
                    or declaration.get('crossFloorProjectionAllowed') is not False
                    or bridge.get('baseToNativeIsIdentity') is not True
                    or bridge.get('authorGeometryErrorUnchanged') is not True):
                raise ValueError(f'optional author layer/native binding mismatch: {map_id}/{floor}')
            matrix = np.asarray(declaration['matrixAssetToCanonical'],np.float64)
            if (matrix.shape != (3,3) or not np.isfinite(matrix).all()
                    or not np.array_equal(matrix[2],[0,0,1]) or abs(np.linalg.det(matrix[:2,:2])) <= 1e-12
                    or not np.array_equal(matrix,np.asarray(declaration['matrixAssetToBase'],np.float64))):
                raise ValueError('optional author inherited display matrix is invalid')
            matrix.setflags(write=False)
            status = declaration['registration']['status']
            rows[key] = dict(path=path,matrix=matrix,usable=status=='confirmed',status=status,
                declaration=declaration)
        if {(key[1],key[2]) for key in rows} != declared:
            raise ValueError('optional author current display inventory is incomplete')
        return rows

    def binding(self, profile, map_id, floor):
        return self.bindings.get((str(profile),str(map_id),str(floor)))

    def pixels(self, path, role):
        path = Path(path).resolve()
        rows = [row for row in self.bindings.values() if row['path'] == path]
        if not rows or role not in {'full-reference','minimap','aligned-overlay'}:
            raise ValueError('optional author display resource role mismatch')
        if role == 'aligned-overlay' and not any(row['usable'] for row in rows):
            raise ValueError('author registration is unavailable for aligned display')
        pixels = imread_unicode(path,cv2.IMREAD_UNCHANGED)
        if pixels is None or pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4:
            raise ValueError('optional author requires original BGRA display pixels')
        return pixels
