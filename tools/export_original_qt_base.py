"""Package the original Qt engine's data without converting its coordinates.

S0 manifest v2 declares its portable bank identity; v1 remains readable.
Only resource bytes from the
explicit source namespaces enter the base; author pixels and executables do
not. This producer never activates a catalog or changes its source tree.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

import imported_resource_archive as archives
from imported_resource_packages import ComponentRef, PackageView
import star_map_import as files


SCHEMA = 'xingtu-original-qt-base-v1'
CAPABILITY = 'original-qt-resources-v1'
DONOR = 'd7063612fc847067f58b71940071228721d79fe0'
PRIVATE_FILES = ('s1_entrance_coordinate_index.json', 'template_manifest.json', 'native_glyph_template.png')


def _generic_controls_payloads(season, mode, floors, *, visual_order=None, fixed_region=None, expanded_region=None):
    from export_native_base_package import _encoded
    from imported_s1_controls_profile import GENERIC_SCHEMA, GENERIC_DETECTOR_ID
    from s1_floor_observation import SEARCH_BOUNDS, EXPANDED_SEARCH_BOUNDS
    from s1_map_ui_geometry import SEARCH, SIZES, MIN_CORRELATION

    def _to_xywh(bounds):
        return [bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]]

    floor_rows = []
    observed_values = []
    for index, raw in enumerate(floors):
        fid = str(raw)
        if fid.lstrip('-').isdigit():
            val = int(fid)
        elif fid.upper().startswith('B') and fid[1:].isdigit():
            val = -int(fid[1:])
        elif fid.upper().startswith('F') and fid[1:].isdigit():
            val = int(fid[1:])
        else:
            val = -(index + 1)
        floor_rows.append(dict(observedValue=val, floorId=fid))
        observed_values.append(val)
    order = list(visual_order) if visual_order is not None else observed_values
    profile = dict(
        schema=GENERIC_SCHEMA,
        detectorId=GENERIC_DETECTOR_ID,
        contexts={f'{season}:{mode}': dict(floors=floor_rows, visualOrder=order)},
        fixedSearchRegion=list(fixed_region) if fixed_region is not None else _to_xywh(SEARCH_BOUNDS),
        expandedSearchRegion=list(expanded_region) if expanded_region is not None else _to_xywh(EXPANDED_SEARCH_BOUNDS),
        mapUi=dict(
            searchRegions={k: _to_xywh(v) for k, v in SEARCH.items()},
            sizes=list(SIZES),
            minimumCorrelation=dict(MIN_CORRELATION),
        ),
    )
    return {'profile.json': _encoded(profile)}


def export_original_package(source_root, output, *, package_id, resource_version,
                            source_inventory=None, season='s0', mode='nightmare',
                            s1_coordinate_asset_dir=None, season_name=''):
    import cv2
    import numpy as np
    from export_native_base_package import (_Source, _encoded, _controls_payloads,
        _s1_controls_payloads, _publish_output)
    from export_resource_preflight import validate_original_package
    from season_context import is_valid_season_id
    if not is_valid_season_id(season) or mode not in ('difficult', 'nightmare'):
        raise ValueError('original-base-context-unsupported')
    output = Path(output).absolute()
    files._no_links(output)
    if output.exists():
        raise FileExistsError('original-base-export-output-exists')
    source = _Source(source_root, source_inventory)
    if output.resolve().is_relative_to(source.root.resolve()):
        raise ValueError('original-base-export-output-inside-source')
    ComponentRef(package_id, resource_version, 'base', '0' * 64)
    if season == 's1':
        prefix = f'seasons/s1/{mode}/'
    elif season == 's0':
        prefix = ''
    else:
        nested = f'seasons/{season}/{mode}/'
        prefix = nested if (source.root / nested / 'native-map-manifest.json').is_file() else ''
    base = {}
    # These are the original read-only resource namespaces, not user/work data.
    for namespace in ('references', 'recognition'):
        directory = source.root / prefix / namespace
        files._no_links(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob('*')):
            files._no_links(path)
            if path.is_file():
                relative = path.relative_to(source.root).as_posix()
                if path.suffix.lower() not in {'.json', '.npz', '.npy', '.png', '.webp', '.gz'}:
                    raise ValueError('original-base-source-type-unsupported:' + relative)
                base[relative.removeprefix(prefix)] = source.read(relative)
    declared_season_name = str(season_name or '').strip()
    if season == 's1':
        original_manifest = 'native-map-manifest.json'
        raw_manifest = source.read(prefix + original_manifest)
        base[original_manifest] = raw_manifest
        declared = json.loads(raw_manifest)
        interfaces = deepcopy(declared['runtimeInterfaces'])
        interfaces['identityProfile'] = 'shared-structure'
        if s1_coordinate_asset_dir is None:
            raise ValueError('original-base-s1-coordinate-assets-required')
        assets = _Source(s1_coordinate_asset_dir, None)
        for name in PRIVATE_FILES:
            base['s1-private-assets/' + name] = assets.read(name)
        controls = _s1_controls_payloads(mode)
    elif season != 's0':
        original_manifest = 'native-map-manifest.json'
        raw_manifest = source.read(prefix + original_manifest)
        base[original_manifest] = raw_manifest
        declared = json.loads(raw_manifest)
        declared_season_name = declared_season_name or str(declared.get('seasonName') or declared.get('seasonTitle') or '').strip()
        interfaces = deepcopy(declared['runtimeInterfaces'])
        interfaces['identityProfile'] = 'shared-structure'
        if s1_coordinate_asset_dir is not None:
            assets = _Source(s1_coordinate_asset_dir, None)
            coord_files = []
            for path in sorted(assets.root.iterdir()):
                if path.is_file() and path.suffix.lower() in {'.json', '.png', '.npz', '.npy'}:
                    base['coordinate-assets/' + path.name] = assets.read(path.name)
                    coord_files.append(path.name)
            if coord_files:
                interfaces['coordinateAssetsDir'] = 'coordinate-assets'
                interfaces['coordinateAssetFiles'] = coord_files
        pack_preview = json.loads(base[interfaces['identityPack']])
        controls = _generic_controls_payloads(season, mode, pack_preview['floors'],
            visual_order=declared.get('visualFloorOrder'))
    elif mode == 'difficult':
        original_manifest = 'difficult-native-manifest.json'
        raw_manifest = source.read(original_manifest)
        base[original_manifest] = raw_manifest
        interfaces = deepcopy(json.loads(raw_manifest)['runtimeInterfaces'])
        interfaces['identityProfile'] = 'shared-structure'
        controls = _controls_payloads(mode)
    else:
        original_manifest = 'references/nightmare-map-pack.json'
        interfaces = dict(identityPack=original_manifest,
            typedIndex='references/nightmare-shared-structure-index.json',
            coordinatePack='references/nightmare-canonical-coordinate-pack.json',
            neutralDisplay='references/nightmare-structure-display.json',
            identityEntries='references/nightmare-entries', identityProfile='zhan10-speed',
            exitIcon='references/difficult-exit-icon.json')
        controls = _controls_payloads(mode)
    pack = json.loads(base[interfaces['identityPack']])
    context_doc = dict(schema=SCHEMA, season=season, mode=mode,
        originalManifest=original_manifest, runtimeInterfaces=interfaces)
    if declared_season_name:
        context_doc['seasonName'] = declared_season_name
    base['resource-context.json'] = _encoded(context_doc)
    payloads = dict(base=base, controls=controls)
    contexts = [dict(season=season, mode=mode)]
    provenance = dict(sourceAlgorithmCommit=DONOR, originalResourceLayout=True,
        numericRuntime=dict(numpy=np.__version__, opencv=cv2.__version__,
            opencvOptimized=bool(cv2.useOptimized()), referenceScope='export-and-preflight-runtime'))
    if season == 's0':
        provenance['fixedS0TemplateBank'] = 'control-data/s0-floor-templates-v1.npz'
    manifest = dict(schema='xingtu-resource-package', formatVersion=1,
        packageId=package_id, resourceVersion=resource_version,
        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[CAPABILITY]),
        provenance=provenance,
        components=[dict(componentId=component, type=kind, schemaVersion=1,
            contexts=contexts, entrypoint=entrypoint, dependencies={})
            for component, kind, entrypoint in [('base','recognition-base','resource-context.json'),
                ('controls','controls-profile','profile.json')]],
        files=[dict(componentId=component, path=relative, bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest())
               for component, members in payloads.items() for relative, raw in sorted(members.items())])
    if season == 's0':
        from s0_floor_templates import BANK_ID, GENERATOR_VERSION, BANK_CAPABILITY
        manifest.update(formatVersion=2, bankId=BANK_ID, generatorVersion=GENERATOR_VERSION)
        manifest['clientCompatibility']['requiredCapabilities'].append(BANK_CAPABILITY)
    manifest_raw = _encoded(manifest)
    digest = hashlib.sha256(manifest_raw).hexdigest()
    ref = ComponentRef(package_id, resource_version, 'base', digest)
    PackageView._validate_manifest(manifest, ref)
    with tempfile.TemporaryDirectory(prefix='xingtu-original-base-') as temporary:
        staged = Path(temporary) / 'package.zip'
        with zipfile.ZipFile(staged, 'x', compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr('manifest.json', manifest_raw)
            for component, members in payloads.items():
                for relative, raw in sorted(members.items()):
                    target.writestr(f'data/{component}/{relative}', raw)
        summary = archives.save_archive(staged, Path(temporary) / 'resources')
        semantic = validate_original_package(Path(temporary) / 'resources', summary.component_refs,
            season=season, mode=mode)
        with staged.open('rb') as handle:
            archive_digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        _publish_output(staged, output, archive_digest)
    return dict(output=str(output), bytes=output.stat().st_size, sha256=archive_digest,
        packageId=package_id, resourceVersion=resource_version, manifestSha256=digest,
        mapCount=len(pack['maps']), files=len(manifest['files']), sourceFiles=len(source.raw),
        originalAlgorithmCommit=DONOR, season=season, mode=mode, catalogActivated=False,
        sourceInventoryChecked=source.inventory is not None, semanticValidated=True,
        semanticValidation=semantic)
