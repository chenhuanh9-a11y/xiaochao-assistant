"""Export the same registered no-route artwork as attached base and v1 route.

Original structure/control payloads are copied byte for byte. Only the base
context gains a display interface; its images are owned by that component, so
there is no circular manifest digest or synthetic dependency reference.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for folder in ('app', 'tools', 'experiments/qt_quick'):
    sys.path.insert(0, str(ROOT / folder))

from export_author_display_component import export_component, _encoded
from export_native_base_package import _publish_output
from imported_author_display import (BASE_RENDERED_SCHEMA, BASE_RENDERED_ENTRYPOINT,
    BASE_RENDERED_CAPABILITY)
from imported_resource_archive import inspect_archive, save_archive
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import ComponentResourceReader
import star_map_import as files


def export_dual_pack(base_archive, registration, image_root, base_output, route_output, *,
                     resource_version, route_package_id, route_resource_version):
    base_archive = Path(base_archive).absolute()
    outputs = [Path(p).absolute() for p in (base_output, route_output)]
    if outputs[0] == outputs[1] or base_archive in outputs:
        raise ValueError('rendered-map-output-collision')
    for output in outputs:
        files._no_links(output)
        if output.resolve().is_relative_to(Path(image_root).resolve()):
            raise ValueError('rendered-map-output-inside-images')
        if output.exists():
            raise FileExistsError('rendered-map-output-exists')
        if not output.parent.is_dir():
            raise ValueError('rendered-map-output-parent-unavailable')
    inspected = inspect_archive(base_archive)
    with tempfile.TemporaryDirectory(prefix='rendered-map-export-') as temporary:
        temporary = Path(temporary)
        resources = temporary/'resources'
        saved = save_archive(base_archive, resources, expected_archive_sha256=inspected.archive_sha256)
        refs = {r.component_id:r for r in saved.component_refs}
        if set(refs) != {'base','controls'}:
            raise ValueError('rendered-map-original-base-and-controls-required')
        if resource_version == refs['base'].resource_version:
            raise ValueError('rendered-map-new-base-version-required')
        base_view = PackageView(resources, refs['base'])
        if base_view.component['type'] != 'recognition-base':
            raise ValueError('rendered-map-base-component-required')
        context, = base_view.component['contexts']
        route_staged = temporary/'rendered.xingtu-route'
        route = export_component(resources, refs['base'], registration, image_root, route_staged,
            package_id=route_package_id, resource_version=route_resource_version)
        with zipfile.ZipFile(route_staged) as archive:
            route_manifest = json.loads(archive.read('manifest.json'))
            display_component, = route_manifest['components']
            prefix = 'data/'+display_component['componentId']+'/'
            display = json.loads(archive.read(prefix+display_component['entrypoint']))
            if (display['profileId'] != 'author-rendered-map'
                    or display['displayName'] != '无路线渲染图' or display['authorName'] != '无路线渲染图'):
                raise ValueError('rendered-map-clear-display-label-required')
            attached = deepcopy(display)
            attached.update(schema=BASE_RENDERED_SCHEMA, baseDependency=None)
            additions = {}
            for row in attached['layers']:
                original = row['displayPath']
                row['displayPath'] = 'rendered-display/'+original
                additions[row['displayPath']] = archive.read(prefix+original)
            additions[BASE_RENDERED_ENTRYPOINT] = _encoded(attached)
        manifest = base_view.manifest
        manifest['resourceVersion'] = resource_version
        capabilities = manifest['clientCompatibility']['requiredCapabilities']
        if BASE_RENDERED_CAPABILITY not in capabilities:
            capabilities.append(BASE_RENDERED_CAPABILITY)
        manifest.setdefault('provenance', {})['renderedDisplay'] = dict(
            sourceBaseArchiveSha256=inspected.archive_sha256,
            routeArchiveSha256=route['sha256'], sharedArtwork=True)
        entrypoint = base_view.component['entrypoint']
        document = ComponentResourceReader(base_view).json(entrypoint)
        if (document.get('schema') != 'xingtu-original-qt-base-v1'
                or 'renderedDisplay' in document['runtimeInterfaces']
                or any(path.startswith('rendered-display/') for path in base_view.inventory)):
            raise ValueError('rendered-map-original-unattached-base-required')
        document['runtimeInterfaces']['renderedDisplay'] = BASE_RENDERED_ENTRYPOINT
        additions[entrypoint] = _encoded(document)
        manifest['files'] = [row for row in manifest['files']
            if (row['componentId'],row['path']) != ('base',entrypoint)]
        manifest['files'].extend(dict(componentId='base',path=path,bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest()) for path,raw in sorted(additions.items()))
        manifest_raw = _encoded(manifest)
        PackageView._validate_manifest(manifest, ComponentRef(manifest['packageId'],resource_version,
            'base',hashlib.sha256(manifest_raw).hexdigest()))
        base_staged = temporary/'rendered.xingtu-base'
        with zipfile.ZipFile(base_archive) as source, zipfile.ZipFile(base_staged,'x',
                compression=zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                if name not in {'manifest.json','data/base/'+entrypoint}:
                    target.writestr(name,source.read(name))
            target.writestr('manifest.json',manifest_raw)
            for path,raw in additions.items():
                target.writestr('data/base/'+path,raw)
        checked = save_archive(base_staged, temporary/'attached-resources')
        checked_refs = {r.component_id:r for r in checked.component_refs}
        from client_imported_providers import load_native_resources
        bundle, _, _, provider, current = load_native_resources(
            PackageView(temporary/'attached-resources',checked_refs['base']),
            PackageView(temporary/'attached-resources',checked_refs['controls']),
            season=context['season'],mode=context['mode'])
        for row in attached['layers']:
            reference = provider('shared-structure',row['mapId'],row['floor'])
            if bundle._values['rendered_display'].resolve(row['mapId'],row['floor'],reference).layer is None:
                raise ValueError('rendered-map-attached-roundtrip-layer-unavailable')
        current()
        base_checked = inspect_archive(base_staged)
        _publish_output(base_staged,outputs[0],base_checked.archive_sha256)
        _publish_output(route_staged,outputs[1],route['sha256'])
    return dict(base=dict(output=str(outputs[0]),bytes=outputs[0].stat().st_size,
            sha256=base_checked.archive_sha256,packageId=manifest['packageId'],resourceVersion=resource_version,
            manifestSha256=base_checked.manifest_sha256),
        route=dict(route,output=str(outputs[1])),context=context,
        layers=len(attached['layers']),sourceBaseSha256=inspected.archive_sha256,
        structureAndControlsUnchanged=True, catalogActivated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-archive','registration','image-root','base-output','route-output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('resource-version','route-package-id','route-resource-version'):
        parser.add_argument('--'+name,required=True)
    args = parser.parse_args()
    if args.base_output.suffix.casefold() != '.xingtu-base' or args.route_output.suffix.casefold() != '.xingtu-route':
        parser.error('outputs must use .xingtu-base and .xingtu-route')
    print(json.dumps(export_dual_pack(**vars(args)),ensure_ascii=False))


if __name__ == '__main__':
    main()
