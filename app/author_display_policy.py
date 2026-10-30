"""Original shared author-selection policy; no UI, capture or pose writer."""
from pathlib import Path

DEFAULT_ROUTE_PROFILE_ID = "wang-qingxin"
SHARED_STRUCTURE_PROFILE_ID = "shared-structure"
SUJU_ROUTE_PROFILE_ID = "suju"
SOURCE_STRUCTURE_PROFILE_ID = "source-structure"


class SharedAuthorDisplayPolicy:
    def _effective_route_profile(self, map_id, floor):
        preferred=getattr(self,'preferred_route_profile_id',DEFAULT_ROUTE_PROFILE_ID)
        pack=getattr(self,'pack',None)
        if self._shared_structure_mode_enabled():
            if self._custom_route_active(map_id, floor) or (not map_id and self._custom_gallery_selected()):
                return SHARED_STRUCTURE_PROFILE_ID
            catalog = getattr(self, 'author_route_catalog', None)
            display_profile = self._shared_display_profile_id()
            profile = catalog.profiles.get(display_profile) if catalog is not None else None
            optional = getattr(self, 'optional_author_displays', None)
            if optional is not None and display_profile in optional.profiles:
                if not map_id:
                    return display_profile
                binding = optional.binding(display_profile,map_id,floor)
                if binding is not None and binding['usable']:
                    return display_profile
                return self._legacy_difficult_display_profile(map_id, floor) or SHARED_STRUCTURE_PROFILE_ID
            if (optional is not None and display_profile != SUJU_ROUTE_PROFILE_ID
                    and not (getattr(self, 'difficult_native_context', None) is not None
                             and display_profile == 'zhan10-speed')):
                # Match the selected-author intent contract: leftover files in
                # the base catalog cannot register an optional author.
                return SHARED_STRUCTURE_PROFILE_ID
            if optional is not None and profile is not None:
                path = catalog.route_path(display_profile,map_id,str(floor),include_inactive=True) if map_id else None
                assets = getattr(catalog, 'profile_has_assets', None)
                available = Path(path).is_file() if path is not None else (not map_id and (
                    assets(display_profile) if callable(assets) else any(
                        Path(layer).is_file() for variants in profile.maps.values()
                        for variant in variants for layer in variant.layers.values())))
                return display_profile if profile.runtime_enabled and available else SHARED_STRUCTURE_PROFILE_ID
            return display_profile if profile is not None and profile.runtime_enabled else SHARED_STRUCTURE_PROFILE_ID
        source_store=getattr(pack,'source_candidates',None)
        if (source_store is not None
            and source_store.source_group_for_runtime_map_id(map_id)):
            return SOURCE_STRUCTURE_PROFILE_ID
        if getattr(self,'game_mode','nightmare')!='nightmare' or map_id not in getattr(pack,'by_id',{}):
            return preferred
        actual=getattr(pack,'route_profiles',{}).get(map_id,DEFAULT_ROUTE_PROFILE_ID)
        if preferred==actual:return preferred
        catalog=getattr(self,'author_route_catalog',None)
        profile=catalog.profiles.get(preferred) if catalog is not None else None
        variant=(getattr(self,'zhan10_coffin_variant_id','variant-01')
            if preferred=='zhan10-coffin' and map_id=='nightmare-01' else None)
        if (profile is not None and profile.aligned_overlay_eligible
            and catalog.route_path(preferred,map_id,str(floor),variant_id=variant,include_inactive=True) is not None):
            return preferred
        return actual

    def _shared_display_profile_id(self):
        preferred = getattr(self,'preferred_route_profile_id',None)
        optional = getattr(self,'optional_author_displays',None)
        if optional is not None and preferred in optional.profiles:
            return preferred
        if optional is not None and getattr(self, 'difficult_native_context', None) is None:
            # Availability is resolved per map/floor, separately from intent.
            # Importing an unrelated author must not replace a saved selection.
            return preferred or DEFAULT_ROUTE_PROFILE_ID
        return 'zhan10-speed' if getattr(self, 'difficult_native_context', None) is not None else SUJU_ROUTE_PROFILE_ID

    def _legacy_difficult_display_profile(self, map_id, floor):
        """Keep the registered legacy display on floors without optional author art."""
        native = getattr(self, 'difficult_native_context', None)
        key = ('zhan10-speed', str(map_id), 'variant-01', str(floor))
        if native is None or key not in getattr(native, 'legacy_bindings', {}):
            return None
        catalog = getattr(self, 'author_route_catalog', None)
        adapters = getattr(self, 'author_coordinate_adapters', None)
        if catalog is None or adapters is None:
            return None
        path = catalog.route_path('zhan10-speed', str(map_id), str(floor), include_inactive=True)
        source = adapters.matrix_profile_to_canonical(SHARED_STRUCTURE_PROFILE_ID, str(map_id), str(floor))
        display = adapters.matrix_profile_to_canonical('zhan10-speed', str(map_id), str(floor))
        return 'zhan10-speed' if path is not None and source is not None and display is not None else None


def shared_route_validation_spec(self, profile_id, map_id, floor, render_matrix, render_route):
    """Original shared branch of the Tk selected-author validation contract."""
    import numpy as np
    reference = self._shared_structure_reference(profile_id, map_id, floor)
    if reference is None:
        return None
    adapters = getattr(self, 'author_coordinate_adapters', None)
    raw = getattr(adapters, '_raw', {})
    canvas = reference.metadata.get('canvas') or {}
    if profile_id == SHARED_STRUCTURE_PROFILE_ID:
        return dict(route=None, sharedReference=reference,
            matrixSourceToClient=render_matrix, profileToValidation=np.eye(3),
            basis='game-rendered-shared-structure', coordinatePackId=getattr(adapters, 'canonical_pack_id', self.pack.pack_id),
            profileBridgeVerified=True, sharedReferenceMetadata=reference.metadata)
    declared = ((getattr(adapters, 'maps', {}).get(str(map_id)) or {}).get('floors') or {}).get(str(floor)) or raw.get('referenceCanvas') or {}
    declared_shape = declared.get('shapeHW') or [declared.get('coordinateHeight'), declared.get('coordinateWidth')]
    same_reference = (declared.get('coordinateSpace', raw.get('referenceCoordinateSpace')) == canvas.get('coordinateSpace')
        and declared_shape == list(reference.shape))
    reference_profile = getattr(adapters, 'coordinate_reference_profile_id', SHARED_STRUCTURE_PROFILE_ID)
    source_basis = adapters.matrix_profile_to_canonical(reference_profile, map_id, floor) if adapters is not None else None
    display_basis = adapters.matrix_profile_to_canonical(profile_id, map_id, floor) if adapters is not None else None
    bridge_verified = bool(same_reference and isinstance(source_basis, np.ndarray) and isinstance(display_basis, np.ndarray))
    if bridge_verified:
        try:
            profile_to_reference = np.linalg.inv(source_basis) @ display_basis
        except np.linalg.LinAlgError:
            return None
    elif render_route is reference.clean_mask:
        profile_to_reference = np.eye(3)
    else:
        # Same-size display pixels do not establish a coordinate bridge.
        return None
    validation_matrix = (render_matrix if render_route is reference.clean_mask else
                         render_matrix @ np.linalg.inv(profile_to_reference))
    return dict(route=None, sharedReference=reference,
        matrixSourceToClient=validation_matrix, profileToValidation=profile_to_reference,
        basis='game-rendered-shared-structure',
        coordinatePackId=getattr(adapters, 'canonical_pack_id', None) if bridge_verified else None,
        profileBridgeVerified=bridge_verified,
        sharedReferenceMetadata=reference.metadata)
