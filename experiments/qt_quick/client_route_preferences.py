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
