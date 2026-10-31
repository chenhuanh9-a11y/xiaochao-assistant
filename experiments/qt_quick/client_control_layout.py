"""Qt layout migration: game mode and manual map choice are independent."""
from copy import deepcopy
from control_layout import DISPLAY_OPTIONS, validate_layout as validate_legacy

MODULES = ('display', 'mode', 'manual_map', 'recognition', 'round')


def preset_layout(name='compact'):
    if name not in ('compact', 'common'):
        raise ValueError('unknown-control-layout-preset')
    common = name == 'common'
    return dict(schemaVersion=2, order=list(MODULES),
                enabled=['display', 'mode', 'manual_map', 'round'] if common else ['display', 'round'],
                display=dict(alignedOpacity=common, referenceOpacity=common, referencePosition=False))


def validate_layout(value):
    if isinstance(value, dict) and value.get('schemaVersion') == 1:
        old = validate_legacy(value)
        expand = lambda items: [part for item in items for part in (('mode', 'manual_map') if item == 'map' else (item,))]
        return dict(schemaVersion=2, order=expand(old['order']), enabled=expand(old['enabled']), display=old['display'])
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'order', 'enabled', 'display'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 2):
        raise ValueError('invalid-control-layout-schema')
    order, enabled, display = value['order'], value['enabled'], value['display']
    if (not isinstance(order, list) or any(not isinstance(item, str) for item in order)
            or len(order) != len(MODULES) or set(order) != set(MODULES)):
        raise ValueError('invalid-control-layout-order')
    if (not isinstance(enabled, list) or any(not isinstance(item, str) for item in enabled)
            or len(set(enabled)) != len(enabled) or not set(enabled) <= set(MODULES)):
        raise ValueError('invalid-control-layout-modules')
    if (not isinstance(display, dict) or set(display) != set(DISPLAY_OPTIONS)
            or any(type(item) is not bool for item in display.values())):
        raise ValueError('invalid-control-layout-display-options')
    return dict(schemaVersion=2, order=list(order), enabled=[item for item in order if item in enabled], display=dict(display))


def load_layout(value):
    try:
        return validate_layout(value)
    except (TypeError, ValueError):
        return preset_layout()


def layout_preset(value):
    layout = validate_layout(value)
    return next((name for name in ('compact', 'common') if layout == preset_layout(name)), None)


def copy_layout(value):
    return deepcopy(validate_layout(value))
