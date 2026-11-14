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


def export_original_package(source_root, output, *, package_id, resource_version,
                            source_inventory=None, season='s0', mode='nightmare',
                            s1_coordinate_asset_dir=None):
    import cv2
    import numpy as np
    from export_native_base_package import (_Source, _encoded, _controls_payloads,
        _s1_controls_payloads, _publish_output)
    from export_resource_preflight import validate_original_package
    if season not in ('s0', 's1') or mode not in ('difficult', 'nightmare'):
        raise ValueError('original-base-context-unsupported')
    output = Path(output).absolute()
    files._no_links(output)
    if output.exists():
        raise FileExistsError('original-base-export-output-exists')
    source = _Source(source_root, source_inventory)
    if output.resolve().is_relative_to(source.root.resolve()):
        raise ValueError('original-base-export-output-inside-source')
    ComponentRef(package_id, resource_version, 'base', '0' * 64)
    prefix = f'seasons/s1/{mode}/' if season == 's1' else ''
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
    base['resource-context.json'] = _encoded(dict(schema=SCHEMA, season=season, mode=mode,
        originalManifest=original_manifest, runtimeInterfaces=interfaces))
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
