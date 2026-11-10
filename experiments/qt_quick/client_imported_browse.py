"""Read-only presentation entry for the same catalog used by recognition.

No installed-tree or author sidecar discovery belongs to this entry. Display
decoders must be explicitly admitted before their images enter the gallery.
"""
from concurrent.futures import CancelledError
from types import MappingProxyType

from map_resource_context import BrowseContext


def load_imported_browse_context(resolver, mode, *, season='s0', cancelled=lambda: False):
    if cancelled():
        raise CancelledError()
    snapshot = resolver.snapshot()
    resources = snapshot.resolve_context(season, mode)
    if resources.base is None:
        message = '尚未启用本模式的基础结构，请前往本地资源页。'
    elif resources.displays:
        message = '显示资源尚未通过当前程序的解码校验。'
    else:
        message = '尚未启用作者显示资源，识别能力请查看当前模式状态。'
    if cancelled():
        raise CancelledError()
    resources.assert_current()
    return BrowseContext(mode, resolver.root, None, None, None, (), MappingProxyType({}),
                         warnings=(message,), season=season)


def imported_home_rows(resolver, mode, profile_id, season='s0', *, cancelled=lambda: False):
    # A missing/unadmitted display cannot silently revive an installed author.
    # Structure previews will be supplied by the admitted base decoder.
    load_imported_browse_context(resolver, mode, season=season, cancelled=cancelled)
    return []
