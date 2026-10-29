"""Pure control-panel layout preferences; never stores runtime/game facts."""
from __future__ import annotations

from copy import deepcopy

MODULES = ('display', 'map', 'recognition', 'round')
MODULE_LABELS = {'display': '显示', 'map': '模式与选图',
                 'recognition': '候选与再识别', 'round': '本局动作'}
DISPLAY_OPTIONS = {'alignedOpacity': '贴合不透明度',
                   'referenceOpacity': '定点不透明度', 'referencePosition': '定点位置编辑'}


def preset_layout(name='compact') -> dict:
    if name not in ('compact', 'common'):
        raise ValueError('unknown-control-layout-preset')
    common = name == 'common'
    return dict(schemaVersion=1, order=list(MODULES),
        enabled=['display', 'map', 'round'] if common else ['display', 'round'],
        display=dict(alignedOpacity=common, referenceOpacity=common, referencePosition=False))


def validate_layout(value) -> dict:
    """Validate an apply proposal and return a detached canonical preference."""
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'order', 'enabled', 'display'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1):
        raise ValueError('invalid-control-layout-schema')
    order, enabled, display = value['order'], value['enabled'], value['display']
    if (not isinstance(order, list) or not all(isinstance(item, str) for item in order)
            or len(order) != len(MODULES) or set(order) != set(MODULES)):
        raise ValueError('invalid-control-layout-order')
    if (not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled)
            or len(enabled) != len(set(enabled)) or not set(enabled) <= set(MODULES)):
        raise ValueError('invalid-control-layout-modules')
    if (not isinstance(display, dict) or set(display) != set(DISPLAY_OPTIONS)
            or any(type(flag) is not bool for flag in display.values())):
        raise ValueError('invalid-control-layout-display-options')
    return dict(schemaVersion=1, order=list(order), enabled=[item for item in order if item in enabled],
                display=dict(display))


def load_layout(value) -> dict:
    """Old, malformed and future/unknown layouts recover to the compact preset."""
    try:
        return validate_layout(value)
    except (TypeError, ValueError):
        return preset_layout()


def layout_preset(value) -> str | None:
    layout = validate_layout(value)
    return next((name for name in ('compact', 'common') if layout == preset_layout(name)), None)


def copy_layout(value) -> dict:
    return deepcopy(validate_layout(value))
