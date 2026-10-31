"""Shared resource dependency/version binding checks, independent of UI."""
from __future__ import annotations

import json
from pathlib import Path
import zipfile
import numpy as np

SHARED_STRUCTURE_PROFILE_ID = "shared-structure"
SUJU_ROUTE_PROFILE_ID = "suju"


def load_shared_version_context(root, pack, resources, adapters, catalog, side_entries, *,
                                 neutral_relative='references/nightmare-structure-display.json',
                                 author_profile_id=SUJU_ROUTE_PROFILE_ID,
                                 entry_root_relative='references/nightmare-entries'):
    """Read ordinary per-floor dependency identities once per installation."""
    root = Path(root).resolve()
    def local(relative):
        path = (root / str(relative)).resolve()
        if not path.is_relative_to(root):
            raise ValueError('shared dependency escapes its resource root')
        return path
    index = json.loads(resources.index_path.read_text(encoding='utf-8'))
    registry_path = index.get('structureVersionBindings')
    if not registry_path:
        raise ValueError('shared structure version bindings are missing')
    registry = json.loads(local(registry_path).read_text(encoding='utf-8'))
    if registry.get('schemaVersion') != 1 or registry.get('resourceKind') != 'native-structure-version-bindings':
        raise ValueError('shared structure version bindings are incompatible')
    neutral = json.loads(local(neutral_relative).read_text(encoding='utf-8'))
    if (neutral.get('schemaVersion') != 1 or neutral.get('resourceKind') != 'typed-shared-structure-display'
            or local(neutral.get('indexPath')) != resources.index_path.resolve()):
        raise ValueError('shared neutral display index is incompatible')
    def rows_by_floor(rows):
        result = {}
        for row in rows:
            key = (str(row['mapId']), str(row.get('floor') or row.get('resourceKey', {}).get('floor')))
            if key in result:
                raise ValueError('duplicate shared dependency map/floor')
            result[key] = row
        return result
    versions = rows_by_floor(registry.get('entries') or [])
    neutral_rows = rows_by_floor(neutral.get('entries') or [])
    source_rows = {str(row['id']): row for row in json.loads(pack.manifest_path.read_text(encoding='utf-8'))['maps']}
    side_rows = rows_by_floor([item[0] for item in side_entries])
    reference_profile = getattr(adapters, 'coordinate_reference_profile_id', SHARED_STRUCTURE_PROFILE_ID)
    raw_catalog = getattr(catalog, '_raw', {})
    author_profile = next((row for row in raw_catalog.get('profiles', []) if row.get('id') == author_profile_id), {})
    context = {}
    for key, entry in resources.entries.items():
        profile, map_id, variant, floor = key
        errors, author_errors = [], []
        version = versions.get((map_id, floor), {})
        resource_key = version.get('resourceKey') or {}
        source = source_rows.get(map_id) or {}
        expected_key = f"{resource_key.get('layoutId')}@{resource_key.get('structureRawRecordSha256')}/{floor}"
        expected = {name: entry.get(name) for name in ('structureVersionKey', 'sourceVersion', 'baseSnapshot')}
        binding = dict(profile=profile, mapId=map_id, floor=floor, variant=variant)
        declared_lookup = version.get('sharedLookup') or {}
        if (map_id not in pack.by_id or floor not in pack.floors
                or profile != pack.identity_profiles.get(map_id)
                or resource_key.get('floor') != floor or entry.get('structureVersionKey') != expected_key
                or not all(isinstance(value, str) and value for value in expected.values())
                or source.get('sourceLayout') != resource_key.get('layoutId')
                or source.get('structureContentSha256') != resource_key.get('structureRawRecordSha256')
                or source.get('sourceVersion') != entry.get('sourceVersion')
                or version.get('sourceVersion') != entry.get('sourceVersion')
                or any(declared_lookup.get(name) != value for name, value in binding.items())
                or declared_lookup.get('resourcePath') != entry.get('resourcePath')
                or version.get('typedReference') != entry.get('resourcePath')):
            errors.append('shared-index-source-version-or-binding-mismatch')
        display = neutral_rows.get((map_id, floor), {})
        adapter = (((getattr(adapters, 'maps', {}).get(map_id) or {}).get('floors') or {}).get(floor) or {})
        reference_row = (((getattr(adapters, 'profiles', {}).get(reference_profile) or {}).get('maps') or {}).get(map_id) or {}).get('floors', {}).get(floor) or {}
        if (any(display.get(name) != value for name, value in expected.items())
                or display.get('binding') != binding or display.get('referenceResource') != entry.get('resourcePath')
                or (version.get('neutralDisplay') or {}).get('baseResourceKey') != resource_key):
            errors.append('shared-neutral-version-or-binding-mismatch')
        if (adapter.get('structureVersionKey') != expected_key or reference_row.get('structureVersionKey') != expected_key
                or adapter.get('sourceVersion') != entry.get('sourceVersion')
                or adapter.get('referenceResource') != entry.get('resourcePath')):
            errors.append('shared-reference-adapter-version-mismatch')
        side = side_rows.get((map_id, floor))
        if side is not None and (side.get('sourceKind') != 'game-rendered-shared-structure'
                or side.get('profileId') != profile or side.get('variantId') != variant
                or local(str(Path(entry_root_relative)/str(side.get('structureResource')))) != local(entry.get('resourcePath'))
                or side.get('canvas') != display.get('canvas')):
            errors.append('shared-local-feature-binding-mismatch')
        product_path = local(entry.get('resourcePath'))
        try:
            # Read only the scalar declaration here, not every mask/SIFT
            # array. Full typed pixel checks still belong to the loader.
            with np.load(product_path, allow_pickle=False) as archive:
                metadata = json.loads(str(archive['metadata'].item()))
            if (metadata.get('binding') != binding or metadata.get('canvas') != display.get('canvas')
                    or any(metadata.get(name) != value for name, value in expected.items())):
                errors.append('shared-NPZ-version-or-canvas-mismatch')
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
            errors.append('shared-NPZ-declaration-unavailable')
        author = version.get('authorDisplay') or {}
        variants = author_profile.get('maps', {}).get(map_id) or []
        author_variant = next((row for row in variants if row.get('layers', {}).get(floor) == author.get('resource')), {})
        author_layer = (author_variant.get('layerMeta') or {}).get(floor) or {}
        author_adapter = (((getattr(adapters, 'profiles', {}).get(author_profile_id) or {}).get('maps') or {}).get(map_id) or {}).get('floors', {}).get(floor) or {}
        if catalog is None:
            author_errors.append('author-not-installed')
        elif (author.get('baseResourceKey') != resource_key
                or author_layer.get('structureVersionKey') != expected_key
                or author_adapter.get('structureVersionKey') != expected_key
                or author_variant.get('baseSnapshot') != author.get('originalBaseSnapshot')):
            author_errors.append('author-structure-version-or-base-snapshot-mismatch')
        context[key] = dict(expected=expected, binding=binding, path=product_path,
            canvas=display.get('canvas') or {}, sharedError='; '.join(errors) or None,
            authorError='; '.join(author_errors) or None)
    return context
