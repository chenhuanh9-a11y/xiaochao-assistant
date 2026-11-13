"""Read-only presentation entry for the same catalog used by recognition.

Cards describe registered declarations, not qualification of every bitmap.
Only requested layers enter the actual structure and author pixel decoders.
"""
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
import threading
from types import MappingProxyType

from map_resource_context import BrowseContext, BrowsePack, BrowseMap, ProfileSummary
from map_resource_model import ResourceMap, ResourceVariant, floor_label


class _AuthorPreviewUnavailable(ValueError):
    """Only the selected optional display failed after its reference decoded."""


class ImportedBrowseAssets:
    """Declared display layers; actual selected bytes are decoded on demand."""
    def __init__(self, source, authors, layers, signature, check_current, retain_snapshot, cancelled):
        self._source, self._authors = source, MappingProxyType(dict(authors))
        self._layers = MappingProxyType(dict(layers))
        self._keys = frozenset(layers.values())
        self.signature, self.assert_current = signature, check_current
        self.retain_snapshot = retain_snapshot
        self._cancelled = cancelled

    def key(self, path):
        # Used by the UI to form a provider URL. This is a pure mapping lookup.
        return self._layers.get(Path(path))

    def pixels(self, key):
        self.assert_current()
        if key not in self._keys:
            raise ValueError('imported-gallery-layer-unregistered')
        profile, map_id, floor = key
        from shared_structure_display import structure_display_layer
        with self._source.view.read_scope(self._cancelled) as required:
            reference = self._source.reference(map_id, floor)
            if reference is None:
                raise ValueError('imported-gallery-layer-unavailable')
            if profile in {'shared-structure', 's1-native-structure'}:
                pixels = structure_display_layer(reference)['pixels']
            else:
                view, row = self._authors[key]
                try:
                    with view.read_scope(self._cancelled):
                        pixels = self._author_pixels(view, row, reference)
                except (OSError, ValueError) as error:
                    # Catalog/cancellation and the already consumed base must
                    # still be valid before classifying this as optional.
                    self.assert_current()
                    required.verify()
                    raise _AuthorPreviewUnavailable(str(error)) from error
            self.assert_current()
            return pixels

    @staticmethod
    def _author_pixels(view, row, reference):
        import cv2
        import numpy as np
        from imported_resource_reader import ComponentResourceReader
        from author_display_validation import validate_display_image, validate_display_reference, validate_display_affine
        reader = ComponentResourceReader(view)
        reader.json(view.component['entrypoint'])
        if reference.metadata.get('sourceKind') != 'game-rendered-shared-structure':
            raise ValueError('imported-gallery-author-reference-unavailable')
        def reject(reason):
            raise ValueError('imported-gallery-author-'+reason)
        validate_display_affine(row['matrixDisplayToReference'], reject=reject)
        validate_display_reference(row, dict(referenceResource=reference.path.resolve().relative_to(reference.resource_root.resolve()).as_posix(),
            referenceShapeHW=list(reference.shape), coordinateSpace=reference.metadata['canvas']['coordinateSpace'],
            **{name:reference.metadata.get(name) for name in ('structureVersionKey','baseSnapshot','sourceVersion')}), reject=reject)
        path = view.path(row['displayPath'])
        extension = path.suffix.casefold().lstrip('.')
        encoding = 'jpeg' if extension == 'jpg' else extension
        raw = reader(path)
        validate_display_image(raw, encoding, row['displayShapeHW'], reject=reject)
        pixels = cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_UNCHANGED)
        if pixels is None or pixels.dtype != np.uint8 or tuple(pixels.shape[:2]) != tuple(row['displayShapeHW']):
            reject('decode-failed')
        if pixels.ndim == 2:
            pixels = cv2.cvtColor(pixels,cv2.COLOR_GRAY2BGRA)
        elif pixels.ndim == 3 and pixels.shape[2] == 3:
            pixels = cv2.cvtColor(pixels,cv2.COLOR_BGR2BGRA)
        if pixels.ndim != 3 or pixels.shape[2] != 4:
            reject('channels-invalid')
        pixels.setflags(write=False)
        return pixels


class _BrowseStructureSource:
    def __init__(self, view, reader, interfaces, pack, entries):
        self.view, self.reader, self.interfaces, self.pack = view, reader, interfaces, pack
        self.entries = entries

    def reference(self, map_id, floor):
        self.reader.json(self.interfaces['typedIndex'])
        row = self.entries[(map_id,floor)]
        if row.get('legacy'):
            from imported_legacy_references import ImportedLegacyReferences
            declaration = self.interfaces['legacyClean']
            legacy = ImportedLegacyReferences(self.view,declaration,expected_maps=declaration['mapIds'],
                floors=self.pack.floors,reader=self.reader)
            return legacy.reference(map_id,floor)
        from shared_structure_reference import load_shared_structure
        # Preserve the declared lexical path until the PackageView reader has
        # checked it. Store.load resolves it first, hiding a newly inserted link.
        reference = load_shared_structure(self.view.path(row['resourcePath']), resource_root=self.view.root,
            expected_binding=dict(profile=row['profile'],mapId=map_id,floor=floor,variant='variant-01'),
            read_resource=self.reader)
        if reference is not None and (reference.metadata.get('sourceKind') != 'game-rendered-shared-structure'
                or any(reference.metadata.get(name) != row.get(name)
                       for name in ('structureVersionKey','sourceVersion','baseSnapshot'))):
            raise ValueError('imported-gallery-reference-version-mismatch')
        return reference


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
    def stopped():
        return not retained.is_set() and cancelled()
    def cancel():
        if stopped():
            raise CancelledError()
    def check():
        # Cancellation owns the unfinished metadata job. Once the provider
        # accepts it, snapshot + provider generation own the published pixels;
        # a new healthy refresh must not cancel an independent preview window.
        cancel()
        resources.assert_current()

    if resources.base is None:
        message = '尚未启用本模式的基础结构，请前往本地资源页。'
    else:
        from imported_original_base import require_original_base
        from imported_resource_packages import PackageView
        from difficult_native_identity import _validate_imported_identity_ids
        from imported_native_base import _bounded_list
        from imported_resource_reader import ComponentResourceReader
        from floor_contract import floor_contract
        # Bind cancellation to the same production readers used by decoding,
        # not just to the beginning/end of a potentially large author package.
        base_view = PackageView(resolver.root, resources.base.component_ref, assert_current=check)
        with base_view.read_scope(stopped):
            reader, context = require_original_base(base_view,season=season,mode=mode)
            interfaces = context['runtimeInterfaces']
            identity = reader.json(interfaces['identityPack'])
            floors = tuple(map(str,identity.get('floors',('1','2','B1') if (season,mode)==('s0','nightmare') else ())))
            _validate_imported_identity_ids(dict(identity,floors=list(floors)))
            contract = floor_contract(mode,season=season)
            versions = (1, 2) if (season,mode) == ('s0','nightmare') else (1,)
            if (type(identity.get('schemaVersion')) is not int or identity['schemaVersion'] not in versions
                    or identity.get('season',season) != season
                    or identity.get('mode',mode) != mode or contract is None or set(floors) != set(contract.floor_ids)):
                raise ValueError('imported-gallery-base-context-invalid')
            if ((season,mode) == ('s0','nightmare')
                    and any(row.get('resourceBase') != 'resource-root' for row in identity['maps'])):
                raise ValueError('imported-gallery-base-external-path-unsupported')
            items = tuple(BrowseMap(row['id'],row['name'],MappingProxyType({})) for row in identity['maps'])
            pack = BrowsePack(mode,base_view.root,base_view.root,base_view.path(interfaces['identityPack']),
                identity['packId'],identity.get('displayName','结构底图'),str(identity.get('defaultEntryFloor',floors[0])),
                floors,items,MappingProxyType({item.id:item for item in items}),MappingProxyType({}),'structure-reference')
            if pack.default_entry_floor not in floors:
                raise ValueError('imported-gallery-default-floor-invalid')
            identity_profiles = {row['id']:row.get('identityProfileId','zhan10-speed') for row in identity['maps']}
            entries = {}
            paths = set()
            index = reader.json(interfaces['typedIndex'])
            if (index.get('schemaVersion'),index.get('resourceKind')) != (1,'shared-structure'):
                raise ValueError('imported-gallery-typed-index-invalid')
            for row in _bounded_list(index['entries'],1024,'browse typed layers'):
                key = (row['mapId'],str(row['floor']))
                relative = row['resourcePath']
                path = base_view.path(relative)
                if (key in entries or key[0] not in pack.by_id or key[1] not in floors
                        or row.get('profile') != identity_profiles[key[0]]
                        or row.get('variant','variant-01') != 'variant-01' or path.suffix.lower() != '.npz'
                        or relative.casefold() in paths
                        or not all(isinstance(row.get(name),str) and row[name]
                                   for name in ('structureVersionKey','sourceVersion','baseSnapshot'))):
                    raise ValueError('imported-gallery-typed-binding-invalid')
                entries[key] = dict(row)
                paths.add(relative.casefold())
            legacy = interfaces.get('legacyClean')
            if legacy:
                for mid in legacy['mapIds']:
                    for floor,canvas in legacy['referenceCanvas'].items():
                        key = (mid,str(floor))
                        if key in entries or mid not in pack.by_id or str(floor) not in floors:
                            raise ValueError('imported-gallery-legacy-binding-invalid')
                        entries[key] = dict(canvas,profile=legacy['profileId'],legacy=True)
            source = _BrowseStructureSource(base_view,reader,interfaces,pack,entries)
            return _browse_declarations(resolver,snapshot,resources,source,check,cancel,stopped,retained)

    check()
    return ImportedBrowseContext(mode, resolver.root, None, None, None, (), MappingProxyType({}),
                                 warnings=(message,), season=season)


def _browse_declarations(resolver,snapshot,resources,source,check,cancel,stopped,retained):
    from imported_resource_packages import PackageView, FileRef
    from imported_resource_reader import ComponentResourceReader
    from imported_author_display import read_display_identity, _FIELDS, _LAYER_FIELDS
    from author_display_validation import validate_display_authority, validate_display_affine, valid_display_shape
    from generic_author_package import MAX_LAYERS, _label

    pack, base_view = source.pack, source.view
    mode, season = pack.mode, resources.season
    profiles, stamps, layers, package_keys, authors = [], {}, {}, {}, {}
    structure_id = 's1-native-structure' if season == 's1' else 'shared-structure'

    def stamp(path, view):
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                stat.st_dev, stat.st_ino, view.component_ref.manifest_sha256)

    maps, first, count = [], None, 0
    for item in pack.maps:
        paths, draft = {}, False
        for floor in pack.floors:
            cancel()
            binding = source.entries.get((item.id, floor))
            if binding is None:
                continue
            path = base_view.path(binding['resourcePath'])
            stamps[path] = stamp(path, base_view)
            layers[path] = (structure_id, item.id, floor)
            paths[floor] = path
            count += 1
            first = first or (item.id, floor, path)
            draft |= binding.get('sourceClass') == 'legacy-clean-working-draft'
        if paths:
            variant = ResourceVariant('variant-01', '历史结构草稿' if draft else '结构底图', paths)
            maps.append(ResourceMap(item.id, item.name, (variant,)))
    total = len(pack.maps)
    expected_layers = total * len(pack.floors)
    profiles.append(ProfileSummary(structure_id, '结构底图', base_view.component_ref.package_id,
        total, len(maps), total - len(maps), expected_layers - count,
        expected_layers, count, first, tuple(maps)))
    package_keys[structure_id] = f'{base_view.component_ref.package_id}/{base_view.component_ref.resource_version}'

    # Each optional author is staged and proved inside its own catch boundary.
    # A changed author cannot make the required structure scope fail on exit.
    declared, conflicts, unavailable = {}, set(), False
    for selected in resources.displays:
        view = PackageView(resolver.root, selected.component_ref, assert_current=check)
        try:
            with view.read_scope(stopped):
                identity = read_display_identity(view)
                reader = ComponentResourceReader(view)
                document = reader.json(view.component['entrypoint'])
                def reject(reason):
                    raise ValueError('imported-gallery-author-' + reason)
                if ((identity.season, identity.mode) != (season, mode)
                        or set(document) - (_FIELDS | {'provenance'}) or not _FIELDS <= set(document)
                        or not _label(document['authorName']) or not _label(document['displayName'])
                        or 'provenance' in document and not isinstance(document['provenance'], dict)):
                    reject('header-invalid')
                validate_display_authority(document, reject=reject)
                alias = document['baseDependency']
                if not isinstance(alias, str) or set(view.dependencies) != {alias} or view.dependencies[alias] != base_view.component_ref:
                    reject('base-dependency-mismatch')
                if not isinstance(document['layers'], list) or not 1 <= len(document['layers']) <= MAX_LAYERS:
                    reject('layers-invalid')
                owned, seen = {view.component['entrypoint']}, set()
                by_map, author_stamps, author_layers, author_rows = {}, {}, {}, {}
                first, count = None, 0
                for row in document['layers']:
                    cancel()
                    if not isinstance(row, dict) or set(row) != _LAYER_FIELDS:
                        reject('layer-fields-invalid')
                    key = (row['mapId'], row['floor'])
                    if any(not isinstance(value, str) for value in key):
                        reject('reference-binding-invalid')
                    binding = source.entries.get(key)
                    if key in seen or binding is None or row['referenceResource'] != binding['resourcePath']:
                        reject('reference-binding-invalid')
                    if (not valid_display_shape(row['displayShapeHW']) or not valid_display_shape(row['referenceShapeHW'])
                            or any(row[name] != binding.get(name) for name in ('structureVersionKey', 'sourceVersion', 'baseSnapshot'))):
                        reject('reference-declaration-invalid')
                    validate_display_affine(row['matrixDisplayToReference'], reject=reject)
                    image = FileRef(view.component_ref, row['displayPath'])
                    FileRef(base_view.component_ref, row['referenceResource'])
                    path = view.path(image.path)
                    if path.suffix.casefold() not in {'.png', '.jpeg', '.jpg'}:
                        reject('image-format-mismatch')
                    author_stamps[path] = stamp(path, view)
                    layer_key = (identity.profile_id, *key)
                    author_layers[path] = layer_key
                    author_rows[layer_key] = (view, dict(row))
                    by_map.setdefault(key[0], {})[key[1]] = path
                    count += 1
                    first = first or (*key, path)
                    seen.add(key)
                    owned.add(image.path)
                if owned != set(view.inventory):
                    reject('unlisted-display-file')
            if identity.profile_id in declared:
                conflicts.add(identity.profile_id)
            declared[identity.profile_id] = (view, document, by_map, author_stamps, author_layers, author_rows, first, count)
        except (OSError, ValueError, KeyError, TypeError):
            check()
            unavailable = True

    views = []
    for profile_id, (view, document, by_map, author_stamps, author_layers, author_rows, first, count) in declared.items():
        if profile_id in conflicts:
            unavailable = True
            continue
        stamps.update(author_stamps)
        layers.update(author_layers)
        authors.update(author_rows)
        views.append(view)
        ref = view.component_ref
        package_keys[profile_id] = f'{ref.package_id}/{ref.resource_version}'
        maps = tuple(ResourceMap(map_id, pack.by_id[map_id].name,
            (ResourceVariant('variant-01', document['authorName'], paths),)) for map_id, paths in by_map.items())
        profiles.append(ProfileSummary(profile_id, document['displayName'], ref.package_id,
            total, len(maps), total - len(maps), expected_layers - count,
            expected_layers, count, first, maps))
    check()
    signature = (snapshot.generation, base_view.component_ref,
                 tuple(view.component_ref for view in views), tuple(stamps.items()))
    assets = ImportedBrowseAssets(source, authors, layers, signature, check, retained.set, stopped)
    warnings = (('部分作者显示无法读取，已保留可用内容。',) if unavailable or
                'display-unavailable' in resources.optional_gaps else ())
    floors = MappingProxyType({str(floor):MappingProxyType(dict(
        label=f'B{abs(int(floor))}' if season == 's1' else floor_label(str(floor)))) for floor in pack.floors})
    return ImportedBrowseContext(mode, resolver.root, pack, None, None,
        tuple(profiles), MappingProxyType(stamps), warnings=warnings, season=season,
        floor_metadata=floors, assets=assets, package_keys=MappingProxyType(package_keys))


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
    try:
        return _home_profile_rows(context, profile, mode, profile_id, season)
    except _AuthorPreviewUnavailable:
        # Metadata coverage is a declaration, not decoded author availability.
        # Reuse this snapshot, but read/verify real structure pixels again.
        fallback = next((candidate for candidate in context.profiles
                         if candidate.profile_id in {'shared-structure', 's1-native-structure'}), None)
        if fallback is None:
            raise
        return _home_profile_rows(context, fallback, mode, profile_id, season)


def _home_profile_rows(context, profile, mode, profile_id, season):
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
