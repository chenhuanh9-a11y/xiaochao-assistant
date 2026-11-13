"""Read-only presentation entry for the same catalog used by recognition.

No installed-tree or author sidecar discovery belongs to this entry. Display
decoders must be explicitly admitted before their images enter the gallery.
"""
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
import threading
from types import MappingProxyType

from map_resource_context import BrowseContext, ProfileSummary
from map_resource_model import ResourceMap, ResourceVariant, floor_label


class ImportedBrowseAssets:
    """Selected structure/author layers; no arbitrary file-path reader."""
    def __init__(self, base, provider, displays, layers, signature, check_current, retain_snapshot):
        self._base, self._provider = base, provider
        self._displays, self._layers = displays, MappingProxyType(dict(layers))
        self._keys = frozenset(layers.values())
        self.signature, self.assert_current = signature, check_current
        self.retain_snapshot = retain_snapshot

    def key(self, path):
        # Used by the UI to form a provider URL. This is a pure mapping lookup.
        return self._layers.get(Path(path))

    def pixels(self, key):
        self.assert_current()
        if key not in self._keys:
            raise ValueError('imported-gallery-layer-unregistered')
        profile, map_id, floor = key
        if profile in {'shared-structure', 's1-native-structure'}:
            from shared_structure_display import structure_display_layer
            reference = self._provider('shared-structure', map_id, floor)
            if reference is None:
                raise ValueError('imported-gallery-layer-unavailable')
            # The provider's warm reference cache must not authorize changed
            # NPZ bytes. Reuse the exact component reader before/after drawing.
            self._base.reader(reference.path)
            pixels = structure_display_layer(reference)['pixels']
            self._base.reader(reference.path)
            self.assert_current()
            return pixels
        binding = self._displays.binding(profile, map_id, floor)
        if binding is None or not binding['usable']:
            raise ValueError('imported-gallery-layer-unavailable')
        pixels = self._displays.pixels(binding['path'], 'full-reference')
        self.assert_current()
        return pixels


@dataclass(frozen=True)
class ImportedBrowseContext(BrowseContext):
    assets: ImportedBrowseAssets | None = None
    package_keys: object = None


def load_imported_browse_context(resolver, mode, *, season='s0', cancelled=lambda: False):
    if cancelled():
        raise CancelledError()
    snapshot = resolver.snapshot()
    resources = snapshot.resolve_context(season, mode)
    retained = threading.Event()
    def check():
        # Cancellation owns the unfinished metadata job. Once the provider
        # accepts it, snapshot + provider generation own the published pixels;
        # a new healthy refresh must not cancel an independent preview window.
        if not retained.is_set() and cancelled():
            raise CancelledError()
        resources.assert_current()

    if resources.base is None:
        message = '尚未启用本模式的基础结构，请前往本地资源页。'
    elif resources.controls is None:
        message = '本模式的楼层控件尚未启用或无法读取，请检查配套资源。'
    else:
        from client_imported_providers import load_native_resources
        from imported_author_displays import ImportedAuthorDisplays
        from imported_resource_packages import PackageView
        # Bind cancellation to the same production readers used by decoding,
        # not just to the beginning/end of a potentially large author package.
        base_view = PackageView(resolver.root, resources.base.component_ref, assert_current=check)
        controls_view = PackageView(resolver.root, resources.controls.component_ref, assert_current=check)
        base, _controls, _native, provider, _current = load_native_resources(
            base_view, controls_view, season=season, mode=mode)
        views, unavailable = [], False
        for view in resources.displays:
            try:
                views.append(PackageView(resolver.root, view.component_ref, assert_current=check))
            except (OSError, ValueError):
                check()
                unavailable = True
        displays = ImportedAuthorDisplays(views, base, provider, check_current=check)
        profiles, stamps, layers, package_keys = [], {}, {}, {}
        structure_id = 's1-native-structure' if season == 's1' else 'shared-structure'
        maps, first, count = [], None, 0
        for item in base.pack.maps:
            paths, draft = {}, False
            for floor in base.pack.floors:
                floor = str(floor)
                check()
                binding = provider.binding_metadata('shared-structure', item.id, floor)
                if binding is None:
                    continue
                path = Path(binding['path'])
                stat = path.stat()
                stamps[path] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                                stat.st_dev, stat.st_ino, base.component_ref.manifest_sha256)
                layers[path] = (structure_id, item.id, floor)
                paths[floor] = path
                count += 1
                first = first or (item.id, floor, path)
                draft |= binding['metadata'].get('sourceClass') == 'legacy-clean-working-draft'
            if paths:
                variant = ResourceVariant('variant-01', '历史结构草稿' if draft else '结构底图', paths)
                maps.append(ResourceMap(item.id, item.name, (variant,)))
        total = len(base.pack.maps)
        expected_layers = total * len(base.pack.floors)
        profiles.append(ProfileSummary(structure_id, '结构底图', base.component_ref.package_id,
            total, len(maps), total - len(maps), expected_layers - count,
            expected_layers, count, first, tuple(maps)))
        package_keys[structure_id] = f'{base.component_ref.package_id}/{base.component_ref.resource_version}'
        for profile_id, profile in displays.catalog.profiles.items():
            maps, first, count, package_id = [], None, 0, None
            for map_id, variants in profile.maps.items():
                available = []
                for variant in variants:
                    paths = {}
                    for floor, path in variant.layers.items():
                        check()
                        binding = displays.binding(profile_id, map_id, floor)
                        if binding is None or not binding['usable']:
                            continue
                        layer = binding['layer']
                        stat = path.stat()
                        stamps[path] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                                        stat.st_dev, stat.st_ino, layer.image.component.manifest_sha256)
                        layers[path] = (profile_id, map_id, floor)
                        paths[floor] = path
                        count += 1
                        first = first or (map_id, floor, path)
                        package_id = binding['package'].component_ref.package_id
                        ref = binding['package'].component_ref
                        package_keys[profile_id] = f'{ref.package_id}/{ref.resource_version}'
                    if paths:
                        available.append(ResourceVariant(variant.id, variant.display_name, paths))
                if available:
                    maps.append(ResourceMap(map_id, base.pack.by_id[map_id].name, tuple(available)))
            total = len(base.pack.maps)
            expected_layers = total * len(base.pack.floors)
            profiles.append(ProfileSummary(profile_id, profile.display_name, package_id,
                total, len(maps), total - len(maps), expected_layers - count,
                expected_layers, count, first, tuple(maps)))
        check()
        signature = (snapshot.generation, base.component_ref,
                     tuple(view.component_ref for view in views), tuple(stamps.items()))
        assets = ImportedBrowseAssets(base, provider, displays, layers, signature, check, retained.set)
        warnings = (('部分作者显示无法读取，已保留可用内容。',) if displays.errors or unavailable or
                    'display-unavailable' in resources.optional_gaps else ())
        floors = MappingProxyType({str(floor):MappingProxyType(dict(
            label=f'B{abs(int(floor))}' if season == 's1' else floor_label(str(floor)))) for floor in base.pack.floors})
        return ImportedBrowseContext(mode, resolver.root, base.pack, displays.catalog, None,
            tuple(profiles), MappingProxyType(stamps), warnings=warnings, season=season,
            floor_metadata=floors, assets=assets, package_keys=MappingProxyType(package_keys))
    check()
    return ImportedBrowseContext(mode, resolver.root, None, None, None, (), MappingProxyType({}),
                                 warnings=(message,), season=season)


def imported_home_rows(resolver, mode, profile_id, season='s0', *, cancelled=lambda: False):
    context = load_imported_browse_context(resolver, mode, season=season, cancelled=cancelled)
    browse_profile = 's1-native-structure' if season == 's1' and profile_id == 'shared-structure' else profile_id
    profile = next((profile for profile in context.profiles
                    if profile.profile_id == browse_profile and profile.available_layers), None)
    if profile is None:
        profile = next((profile for profile in context.profiles
                        if profile.profile_id in {'shared-structure', 's1-native-structure'}), None)
    if profile is None or context.assets is None:
        return []
    import base64
    import cv2
    rows = []
    structure = profile.profile_id in {'shared-structure', 's1-native-structure'}
    floors = tuple(dict.fromkeys((str(context.pack.default_entry_floor), *context.pack.floors)))
    for item in profile.maps:
        variant, floor, path = next((variant, floor, variant.layers[floor])
            for floor in floors for variant in item.variants if floor in variant.layers)
        pixels = context.assets.pixels(context.assets.key(path))
        height, width = pixels.shape[:2]
        scale = min(1, 400 / width, 250 / height)
        if scale < 1:
            pixels = cv2.resize(pixels, (max(1, int(width * scale)), max(1, int(height * scale))),
                                interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode('.png', pixels)
        if not ok:
            raise ValueError('imported-home-preview-encoding-failed')
        rows.append(dict(key='' if structure else f'{season}/{mode}/{profile_id}/{item.id}/{variant.id}/{floor}',
            title=item.name, floor=str(floor), floorLabel=context.floor_metadata[str(floor)]['label'],
            mode=mode, season=season, profileId=profile_id, structure=structure,
            displayKind=variant.name if structure else '',
            imageUrl='data:image/png;base64,' + base64.b64encode(encoded).decode('ascii')))
        context.assets.assert_current()
        if len(rows) == 3:
            return rows
    context.assets.assert_current()
    return rows
