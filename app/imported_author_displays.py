"""Display-only composition over selected independent resource components."""
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import threading

import cv2
import numpy as np

from author_route_catalog import AuthorRouteProfile, AuthorRouteVariant
from imported_author_display import decode_author_display
from optional_author_displays import OptionalAuthorDisplayAdapters
from s1_author_displays import S1AuthorCatalog


class _DisplayCatalog(S1AuthorCatalog):
    def __init__(self, owner, base):
        super().__init__(base)
        self._owner = owner

    def route_path(self, profile_id, map_id, floor, variant_id=None, *, include_inactive=False):
        self._owner.assert_current()
        if profile_id in self._owner.profiles:
            row = self._owner.binding(profile_id, map_id, floor)
            result = (row['path'] if row is not None and row['usable']
                      and variant_id in (None, 'variant-01') else None)
        else:
            result = super().route_path(profile_id, map_id, floor, variant_id,
                                       include_inactive=include_inactive)
        self._owner.assert_current()
        return result

    def profile_has_assets(self, profile_id):
        self._owner.assert_current()
        if profile_id in self._owner.profiles:
            result = any(self.binding_available(profile_id, mid, floor)
                         for profile, mid, floor in self._owner.bindings if profile == profile_id)
        else:
            result = super().profile_has_assets(profile_id)
        self._owner.assert_current()
        return result

    def binding_available(self, profile, map_id, floor):
        row = self._owner.binding(profile, map_id, floor)
        return row is not None and row['usable']


class _DisplayAdapters(OptionalAuthorDisplayAdapters):
    def __init__(self, owner, base, canvases):
        super().__init__(base, owner.bindings, owner.profiles)
        self._owner, self._base = owner, base
        # The validation consumer needs the actual reference canvas. Enrich a
        # private copy from the provider; authors cannot supply this authority.
        self.maps = deepcopy(base.maps)
        for (map_id, floor), canvas in canvases.items():
            row = self.maps.setdefault(map_id, {}).setdefault('floors', {}).setdefault(floor, {})
            row.update(canvas)

    def matrix_profile_to_canonical(self, profile_id, map_id, floor):
        self._owner.assert_current()
        if profile_id in self.display_profiles:
            row = self._owner.binding(profile_id, map_id, floor)
            matrix = row['matrix'] if row is not None and row['usable'] else None
        else:
            matrix = self._base.matrix_profile_to_canonical(profile_id, map_id, floor)
        self._owner.assert_current()
        return matrix


class ImportedAuthorDisplays:
    def __init__(self, views, base, reference_provider, *, catalog=None, check_current=None):
        self._base, self._check_current = base, check_current
        self.bindings, self.profiles, self.errors = {}, set(), {}
        self.preparation_failed = False
        self._cache, self._lock = OrderedDict(), threading.RLock()
        self.catalog = _DisplayCatalog(self, catalog)
        packages, conflicts, canvases = {}, set(), {}
        for index, view in enumerate(views):
            try:
                package = decode_author_display(view, base, reference_provider)
                profile = package.profile_id
                if profile in packages:
                    conflicts.add(profile)
                packages[profile] = package
            except (OSError, ValueError) as error:
                self.assert_current()
                self.preparation_failed = True
                self.errors[f'display-component-{index + 1}'] = getattr(error, 'code', 'display-component-invalid')
        adapters = base.adapters
        for profile, package in packages.items():
            if profile in conflicts or profile in self.catalog.profiles:
                self.errors[profile] = 'display-profile-conflict'
                continue
            try:
                bindings, profile_canvases, maps = {}, {}, {}
                for layer in package.layers:
                    reference = reference_provider('shared-structure', layer.map_id, layer.floor)
                    source = adapters.matrix_profile_to_canonical('shared-structure', layer.map_id, layer.floor)
                    if reference is None or source is None:
                        self.errors[profile] = 'display-reference-bridge-unavailable'
                        continue
                    matrix = source @ np.asarray(layer.matrix_display_to_reference, dtype=np.float64)
                    matrix.setflags(write=False)
                    path = package.image_path(layer.map_id, layer.floor)
                    bindings[(profile, layer.map_id, layer.floor)] = dict(path=path, matrix=matrix,
                        usable=True, status=package.qualification, package=package, layer=layer)
                    maps.setdefault(layer.map_id, {})[layer.floor] = path
                    profile_canvases[(layer.map_id, layer.floor)] = dict(shapeHW=list(reference.shape),
                        coordinateSpace=reference.metadata['canvas']['coordinateSpace'])
                if package.gaps:
                    self.errors[profile] = 'display-reference-role-unavailable'
                variants = {mid:(AuthorRouteVariant(profile, mid, 'variant-01', package.author_name, layers,
                    'display-only-user-provided', 'display-only'),) for mid, layers in maps.items()}
                route_profile = AuthorRouteProfile(profile, package.display_name, package.author_name,
                    base.season.upper(), True, True, True, variants)
                package.assert_current()
            except (OSError, ValueError) as error:
                self.assert_current()
                self.preparation_failed = True
                self.errors[profile] = getattr(error, 'code', 'display-component-invalid')
                continue
            self.bindings.update(bindings)
            canvases.update(profile_canvases)
            self.catalog.profiles[profile] = route_profile
            self.profiles.add(profile)
        self.adapters = _DisplayAdapters(self, adapters, canvases)
        self.assert_current()

    def assert_current(self):
        if self._check_current is not None:
            self._check_current()
        self._base.assert_current()

    def binding(self, profile, map_id, floor):
        self.assert_current()
        row = self.bindings.get((str(profile), str(map_id), str(floor)))
        if row is not None:
            try:
                row['package'].assert_layer_current(str(map_id), str(floor))
            except (OSError, ValueError):
                self.assert_current()
                return dict(row, usable=False, status='display-component-unavailable')
        self.assert_current()
        return row

    def pixels(self, path, role):
        self.assert_current()
        if role not in {'full-reference', 'minimap', 'aligned-overlay'}:
            raise ValueError('imported-author-display-role-invalid')
        path = Path(path).resolve()
        row = next((value for value in self.bindings.values() if value['path'] == path), None)
        if row is None:
            raise ValueError('imported-author-display-path-unregistered')
        package, layer = row['package'], row['layer']
        key = (package.component_ref, layer.map_id, layer.floor)
        data = package.image_bytes(layer.map_id, layer.floor)
        with self._lock:
            pixels = self._cache.get(key)
            if pixels is not None:
                self._cache.move_to_end(key)
        if pixels is None:
            pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            if (pixels is None or pixels.dtype != np.uint8 or tuple(pixels.shape[:2]) != layer.display_shape_hw
                    or pixels.ndim not in (2, 3) or (pixels.ndim == 3 and pixels.shape[2] not in (3, 4))):
                raise ValueError('imported-author-display-decode-failed')
            if pixels.ndim == 2:
                pixels = cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGRA)
            elif pixels.shape[2] == 3:
                pixels = cv2.cvtColor(pixels, cv2.COLOR_BGR2BGRA)
            pixels.setflags(write=False)
        # Decode may yield while either the catalog or physical bytes change.
        # A cache hit uses the same bounded readers and does not bypass checks.
        package.image_bytes(layer.map_id, layer.floor)
        self.assert_current()
        with self._lock:
            self._cache[key] = pixels
            self._cache.move_to_end(key)
            while len(self._cache) > 6 or sum(value.nbytes for value in self._cache.values()) > 96 * 1024 * 1024:
                self._cache.popitem(last=False)
        package.assert_current()
        self.assert_current()
        return pixels
