"""Display preferences using the existing catalog and coordinate declarations."""
from pathlib import Path


COFFIN_CONTEXT = ("zhan10-coffin", "nightmare-01", "1")
COFFIN_VARIANTS = (("variant-01", "一号路线"), ("variant-02", "二号路线"))


def _coffin_binding(engine, map_id, floor, profile, variant_id):
    catalog = getattr(engine, "qt_catalog", None)
    author = catalog.profiles.get(profile) if catalog is not None else None
    if author is None or not author.aligned_overlay_eligible:
        return None, "当前资源未提供可显示的展十全棺路线"
    path = catalog.route_path(profile, map_id, str(floor), variant_id=variant_id, include_inactive=True)
    if path is None:
        return None, "此版本的当前楼层图片未安装"
    optional = getattr(engine, "qt_optional_displays", None)
    binding = optional.binding(profile, map_id, str(floor)) if optional is not None else None
    if binding is not None:
        # An optional binding authorizes its exact bitmap only. Never lend the
        # default bitmap's matrix to another catalog variant.
        if Path(path).resolve() != Path(binding["path"]).resolve():
            return None, "已安装显示声明对应另一版本图片"
        if not binding["usable"]:
            return None, "此版本的显示注册尚未确认"
        return dict(binding, optionalPixels=True), ""
    adapters = getattr(engine, "qt_adapters", None)
    # Original packs declare a profile/map/floor, covering catalog variants in
    # that coordinate system, without requiring the later optional sidecar.
    # Honor a narrower native variant declaration when present.
    row = (getattr(adapters, "profiles", {}).get(profile, {}).get("maps", {})
           .get(map_id, {}).get("floors", {}).get(str(floor), {}))
    declared_variant = (row.get("evidence", {}).get("binding", {}) or {}).get("variant")
    if declared_variant is not None and declared_variant != variant_id:
        return None, "原坐标声明对应另一版本"
    matrix = adapters.matrix_profile_to_canonical(profile, map_id, str(floor)) if adapters is not None else None
    source = adapters.matrix_profile_to_canonical("shared-structure", map_id, str(floor)) if adapters is not None else None
    if not row or matrix is None or source is None:
        return None, "缺少原有当前楼层坐标显示声明"
    return dict(path=path, matrix=matrix, usable=True, optionalPixels=False), ""


def coffin_variant_metadata(engine, mode, profile, map_id, floor):
    context = dict(mode=str(mode), profileId=str(profile), mapId=map_id,
                   floor=str(floor), resourceIdentity=str(id(engine)))
    result = dict(context=context, options=[], message="全棺版本适用于噩梦 · 展十全棺 · 一号地图一楼")
    if mode != "nightmare" or (profile, map_id, str(floor)) != COFFIN_CONTEXT:
        return result
    catalog = getattr(engine, "qt_catalog", None)
    names = {row.id: row.display_name for row in catalog.variants(profile, map_id, include_inactive=True)} if catalog else {}
    for variant_id, name in COFFIN_VARIANTS:
        binding, reason = _coffin_binding(engine, map_id, floor, profile, variant_id)
        result["options"].append(dict(id=variant_id, name=names.get(variant_id, name),
                                      available=binding is not None, reason=reason))
    result["message"] = ("只影响本层路线显示；不改变识别位置或楼层" if any(row["available"] for row in result["options"])
                         else "当前全棺版本资源不可用；已保留原偏好")
    return result


def selected_coffin_binding(engine, map_id, floor, profile, variant_id):
    if (profile, map_id, str(floor)) != COFFIN_CONTEXT:
        return None
    binding, _ = _coffin_binding(engine, map_id, floor, profile, variant_id)
    return binding


class _DisplaySelection:
    """Thin owner protocol for the original shared display-only policy."""
    def __init__(self, engine, profile, reference):
        from types import SimpleNamespace
        self.engine, self.pack = engine, engine.pack
        self.preferred_route_profile_id = profile
        self.author_route_catalog = getattr(engine, "qt_catalog", None)
        optional = getattr(engine, "qt_optional_displays", None)
        self.optional_author_displays = (SimpleNamespace(profiles=getattr(optional, "profiles", set()),
            binding=optional.binding) if optional is not None else None)
        self.author_coordinate_adapters = engine.qt_adapters
        self.difficult_native_context = getattr(engine, "qt_native_context", None)
        self.game_mode = engine.pack.mode
        self.reference = reference

    def _base_author_status(self, map_id, floor):
        from base_display_integration import BaseDisplayIntegrationMixin
        return BaseDisplayIntegrationMixin._base_author_status(self, map_id, floor)

    def _shared_structure_mode_enabled(self): return True
    def _custom_route_active(self, map_id, floor): return False
    def _custom_gallery_selected(self): return None
    def _shared_structure_reference(self, profile, map_id, floor): return self.reference

    def _shared_display_profile_id(self):
        from author_display_policy import SharedAuthorDisplayPolicy
        return SharedAuthorDisplayPolicy._shared_display_profile_id(self)

    def _legacy_difficult_display_profile(self, map_id, floor):
        from author_display_policy import SharedAuthorDisplayPolicy
        return SharedAuthorDisplayPolicy._legacy_difficult_display_profile(self, map_id, floor)

    def effective(self, map_id, floor):
        from author_display_policy import SharedAuthorDisplayPolicy
        return SharedAuthorDisplayPolicy._effective_route_profile(self, map_id, floor)


def prepare_display_layer(engine, map_id, floor, preferred, *, variant_id="variant-01"):
    """Original author -> custom/base (missing only) -> structural display chain.

    The selected preference is input-only. Every returned transform bridges
    display pixels into the existing typed reference; none reaches recognition.
    """
    import cv2
    import numpy as np
    from author_display_policy import shared_route_validation_spec
    from nightmare_candidate import imread_unicode

    reference = engine.qt_shared_provider("shared-structure", map_id, floor)
    if reference is None:
        raise ValueError("display-reference-unavailable")

    def metadata(layer, *, author_status="none", fallback_reason=""):
        actual = "shared-structure" if preferred.startswith("custom:") else layer["routeProfileId"]
        return dict(layer, preferredProfileId=preferred, effectiveProfileId=actual,
            authorStatus=author_status, fallback=bool(preferred != actual and not preferred.startswith("custom:")),
            fallbackReason=fallback_reason)

    custom = getattr(engine, "qt_custom_display", None)
    if callable(custom):
        layer = custom(preferred, map_id, str(floor), reference)
        if layer is not None:
            return metadata(layer)
    if preferred.startswith("custom:"):
        # Legacy active snapped-route failures must not become a straight base
        # route or another author's pixels while keeping the custom selection.
        raise ValueError("custom-display-unavailable")

    owner = _DisplaySelection(engine, preferred, reference)
    optional = getattr(engine, "qt_optional_displays", None)
    effective = owner.effective(map_id, floor)
    coffin = (engine.pack.mode == "nightmare" and (preferred, map_id, str(floor)) == COFFIN_CONTEXT)
    binding = selected_coffin_binding(engine, map_id, floor, preferred, variant_id) if coffin else None
    if coffin:
        effective = preferred if binding is not None else "shared-structure"
    status = owner._base_author_status(map_id, floor)
    failure = "selected-author-layer-unavailable" if effective != preferred else ""
    catalog = owner.author_route_catalog
    versions = getattr(getattr(engine, "qt_shared_provider", None), "versions", {})
    matching = [value for key, value in versions.items() if key[1] == str(map_id) and key[3] == str(floor)]
    version = matching[0] if len(matching) == 1 else {}
    if not coffin:
        binding = optional.binding(effective, map_id, floor) if optional is not None else None
    available = binding.get("usable") is True if binding is not None else not version.get("authorError")
    if effective != "shared-structure":
        profile = catalog.profiles.get(effective) if catalog is not None else None
        path = (catalog.route_path(effective, map_id, str(floor),
            variant_id=variant_id if coffin else None, include_inactive=True) if catalog else None)
        source = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
        display = engine.qt_adapters.matrix_profile_to_canonical(effective, map_id, floor)
        if (profile is not None and profile.aligned_overlay_eligible and available
                and path is not None and source is not None and display is not None):
            try:
                # Optional registrations own their exact bitmap; catalog
                # variants cannot borrow another image's display matrix.
                if binding is not None and Path(path).resolve() != Path(binding["path"]).resolve():
                    raise ValueError("author-binding-path-mismatch")
                pixels = (optional.pixels(path, "aligned-overlay")
                    if binding is not None and binding.get("optionalPixels", True)
                    else imread_unicode(path, cv2.IMREAD_UNCHANGED))
                if (pixels is None or pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4
                        or shared_route_validation_spec(owner, effective, map_id, floor, np.eye(3), pixels) is None):
                    raise ValueError("author-display-canvas-or-image-unavailable")
                return metadata(dict(pixels=pixels, matrixDisplayToReference=np.linalg.inv(source) @ display,
                    resourceIdentity=str(path), displayKind="author-route", displayLabel=effective,
                    referencePath=str(path), routeProfileId=effective,
                    variantId=variant_id if coffin else None), fallback_reason=failure)
            except (OSError, ValueError, KeyError, TypeError, cv2.error):
                failure = "author-display-load-or-bridge-error"
        elif not available:
            failure = "author-registration-or-version-unavailable"
        else:
            failure = failure or "author-display-binding-unavailable"

    if status == "missing":
        base = getattr(engine, "qt_base_display", None)
        # The old native difficult path does not use the nightmare base atlas.
        if engine.pack.mode == "nightmare" and owner.difficult_native_context is None and callable(base):
            layer = base(map_id, str(floor), reference)
            if layer is not None:
                return metadata(layer, author_status=status, fallback_reason="selected-author-layer-missing")
    elif status == "available":
        status = "load-error" if available else "registration-failed"
    pixels = np.zeros((*reference.shape, 4), np.uint8)
    pixels[reference.clean_mask != 0] = (218, 228, 238, 255)
    pixels.setflags(write=False)
    return metadata(dict(pixels=pixels, matrixDisplayToReference=np.eye(3),
        resourceIdentity=dict(path=str(reference.path), binding=reference.metadata["binding"],
            structureVersionKey=reference.metadata.get("structureVersionKey")),
        displayKind="shared-structure", displayLabel=("结构底图（作者路线版本不匹配）"
            if version.get("authorError") == "author-structure-version-or-base-snapshot-mismatch"
            else "结构底图（作者路线不可用）"),
        routeProfileId="shared-structure"), author_status=status, fallback_reason=failure or "selected-author-layer-missing")
