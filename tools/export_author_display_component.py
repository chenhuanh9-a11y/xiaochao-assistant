"""Create an inert author display component package for one installed native base.

Input registrations are intentionally smaller than the consumer payload: they
contain only author labels, map/floor bindings, source image paths, and the
display-to-reference affine. All reference metadata and runtime context come
from the selected, public-decoded native base.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

APP_ROOT = Path(__file__).resolve().parents[1] / 'app'
TOOLS_ROOT = Path(__file__).resolve().parent
QUICK_ROOT = Path(__file__).resolve().parents[1] / 'experiments' / 'qt_quick'
for _path in (str(APP_ROOT), str(TOOLS_ROOT), str(QUICK_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import generic_author_package as generic
import imported_resource_archive as archives
from author_display_validation import (MAX_IMAGE_PIXELS, valid_display_shape,
    validate_display_affine, validate_display_image)
from imported_author_display import SCHEMA, decode_author_display
from imported_native_base import decode_native_base
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import MAX_JSON_BYTES, decode_resource_json
from resource_snapshot_io import read_stable_file
from resource_package_types import ROUTE_SUFFIX
import star_map_import as files


INPUT_SCHEMA = 'xingtu-author-display-registration-v1'
DISPLAY_ENTRYPOINT = 'display.json'
_INPUT_FIELDS = {'schema', 'profileId', 'authorName', 'displayName', 'layers'}
_INPUT_LAYER_FIELDS = {'mapId', 'floor', 'image', 'matrixDisplayToReference'}


def _reject(reason):
    raise ValueError('author-display-export-' + reason)


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        _reject('cancelled')


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def _read(path, limit, label):
    try:
        raw, _ = read_stable_file(Path(path), limit, no_links=files._no_links,
                                  reject=lambda reason: _reject(label + '-' + reason))
    except files.ImportRejected as error:
        raise ValueError('author-display-export-' + label + '-unavailable') from error
    return raw


def _read_registration(path):
    try:
        document = decode_resource_json(_read(path, MAX_JSON_BYTES, 'registration'))
    except (files.ImportRejected, UnicodeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith('author-display-export-'):
            raise
        raise ValueError('author-display-export-registration-invalid') from error
    if (not isinstance(document, dict) or set(document) != _INPUT_FIELDS
            or document.get('schema') != INPUT_SCHEMA
            or not generic.is_generic_profile(document.get('profileId'))
            or not generic._label(document.get('authorName'))
            or not generic._label(document.get('displayName'))
            or not isinstance(document.get('layers'), list)
            or not 1 <= len(document['layers']) <= generic.MAX_LAYERS):
        _reject('registration-invalid')
    return document


def _context(base_view):
    component = base_view.component
    contexts = component.get('contexts')
    if (component.get('type') != 'recognition-base' or component.get('schemaVersion') != 1
            or not isinstance(contexts, list) or len(contexts) != 1):
        _reject('base-component-invalid')
    context = contexts[0]
    if (not isinstance(context, dict) or set(context) != {'season', 'mode'}
            or context.get('season') not in ('s0', 's1')
            or context.get('mode') not in ('difficult', 'nightmare')):
        _reject('base-context-invalid')
    return context['season'], context['mode']


def _runtime_reference_provider(base_view, base):
    # Use the same version-checked provider the client consumes. No registration
    # field can supply or override its reference path or metadata.
    from client_providers import SharedReferenceProvider
    return SharedReferenceProvider(base_view.root, base.resources, base.versions,
                                   check_current=base.assert_current)


def _check_image(raw, relative):
    extension = Path(relative).suffix.casefold().lstrip('.')
    if extension == 'jpg':
        extension = 'jpeg'
    if extension not in ('png', 'jpeg'):
        _reject('image-extension-invalid')
    # The first image open reads headers only. Check stored PNG depth and the
    # bounded dimensions before Pillow allocates decoded pixels.
    try:
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(raw)) as image:
            shape = [int(image.height), int(image.width)]
        if not valid_display_shape(shape) or shape[0] * shape[1] > MAX_IMAGE_PIXELS:
            _reject('image-shape-invalid')
        validate_display_image(raw, extension, shape, reject=_reject,
                               decode_images=False)
        validate_display_image(raw, extension, shape, reject=_reject,
                               decode_images=True)
    except (OSError, ValueError, TypeError, Image.DecompressionBombError) as error:
        if isinstance(error, ValueError) and str(error).startswith('author-display-export-'):
            raise
        _reject('image-invalid')
    return shape, extension


def _layer_reference(row, base, base_view, provider):
    map_id, floor = row['mapId'], row['floor']
    if (not isinstance(map_id, str) or not isinstance(floor, str)
            or map_id not in base.pack.by_id or floor not in base.pack.floors):
        _reject('layer-context-invalid')
    try:
        validate_display_affine(row['matrixDisplayToReference'], reject=_reject)
    except (KeyError, TypeError) as error:
        raise ValueError('author-display-export-matrix-invalid') from error
    reference = provider('shared-structure', map_id, floor)
    if reference is None:
        _reject('typed-reference-unavailable:' + map_id + '/' + floor)
    base.assert_current()
    metadata = reference.metadata
    binding = metadata.get('binding', {})
    if (metadata.get('sourceKind') != 'game-rendered-shared-structure'
            or binding.get('profile') != base.pack.identity_profiles[map_id]
            or binding.get('mapId') != map_id or binding.get('floor') != floor
            or binding.get('variant') != 'variant-01'):
        _reject('typed-reference-role-unavailable:' + map_id + '/' + floor)
    try:
        reference_path = reference.path.resolve(strict=True)
        relative_reference = reference_path.relative_to(base_view.root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as error:
        raise ValueError('author-display-export-reference-outside-base') from error
    # Require the public base reader to validate the exact referenced file.
    base.reader(reference_path)
    canvas = metadata.get('canvas')
    if not isinstance(canvas, dict) or canvas.get('shapeHW') != list(reference.shape):
        _reject('typed-reference-canvas-invalid')
    return dict(mapId=map_id, floor=floor, referenceResource=relative_reference,
        referenceShapeHW=list(reference.shape), coordinateSpace=canvas.get('coordinateSpace'),
        structureVersionKey=metadata.get('structureVersionKey'),
        baseSnapshot=metadata.get('baseSnapshot'), sourceVersion=metadata.get('sourceVersion'),
        matrixDisplayToReference=row['matrixDisplayToReference'])


def _roundtrip(staged, base, reference_provider, component_id, layer_count, cancelled):
    _cancel(cancelled)
    inspected = archives.inspect_archive(staged, cancelled=cancelled)
    with tempfile.TemporaryDirectory(prefix='author-display-roundtrip-') as temporary:
        root = Path(temporary) / 'resources'
        saved = archives.save_archive(staged, root,
            expected_archive_sha256=inspected.archive_sha256, cancelled=cancelled)
        refs = {ref.component_id: ref for ref in saved.component_refs}
        if set(refs) != {component_id}:
            _reject('roundtrip-component-inventory')
        view = PackageView(root, refs[component_id])
        decoded = decode_author_display(view, base, reference_provider)
        if decoded.base_ref != base.component_ref or decoded.gaps:
            _reject('roundtrip-reference-gap')
        if len(decoded.layers) != layer_count:
            _reject('roundtrip-layer-inventory')
        for layer in decoded.layers:
            _cancel(cancelled)
            if not decoded.image_bytes(layer.map_id, layer.floor):
                _reject('roundtrip-image-empty')
        _cancel(cancelled)
        return dict(layerCount=len(decoded.layers), archiveBytes=Path(staged).stat().st_size,
                    archiveSha256=inspected.archive_sha256)


def export_component(base_resource_root, base_ref, registration_path, image_root, output, *,
                     package_id, resource_version, component_id=None, cancelled=None):
    """Export and exclusively publish a component bound to an installed base.

    ``base_ref`` must be the exact installed ``ComponentRef`` (including its
    manifest digest). Source image bytes are copied unchanged into the ZIP.
    The function writes no installation state, catalog, or sidecar.
    """
    if not isinstance(base_ref, ComponentRef):
        _reject('base-reference-required')
    output = Path(output).absolute()
    files._no_links(output)
    if output.exists():
        raise FileExistsError('author-display-export-output-exists')
    if not output.parent.is_dir():
        _reject('output-directory-unavailable')

    base_root = Path(base_resource_root).absolute()
    image_root = Path(image_root).absolute()
    for root, label in ((base_root, 'base-root'), (image_root, 'image-root')):
        files._no_links(root)
        if not root.is_dir():
            _reject(label + '-unavailable')
        if output.resolve().is_relative_to(root.resolve()):
            _reject('output-inside-input')

    base_view = PackageView(base_root, base_ref)
    base_view.validate_inventory()
    context = _context(base_view)
    season, mode = context
    if component_id is None:
        component_id = f'display-{season}-{mode}'
    ComponentRef(package_id, resource_version, component_id, '0' * 64)
    base = decode_native_base(base_view, season=context[0], mode=context[1])
    if not base.ready:
        _reject('base-not-ready')
    provider = _runtime_reference_provider(base_view, base)
    registration = _read_registration(registration_path)

    layer_docs = []
    seen = set()
    total_image_bytes = 0
    for row in registration['layers']:
        _cancel(cancelled)
        if not isinstance(row, dict) or set(row) != _INPUT_LAYER_FIELDS:
            _reject('layer-registration-invalid')
        if not isinstance(row.get('mapId'), str) or not isinstance(row.get('floor'), str):
            _reject('layer-context-invalid')
        key = (row.get('mapId'), row.get('floor'))
        if key in seen:
            _reject('duplicate-layer')
        seen.add(key)
        relative_image = row.get('image')
        try:
            source_path = files._target(image_root, relative_image)
        except (files.ImportRejected, TypeError, ValueError) as error:
            raise ValueError('author-display-export-image-path-invalid') from error
        source_raw = _read(source_path, files.MAX_FILE_BYTES, 'image')
        shape, extension = _check_image(source_raw, relative_image)
        total_image_bytes += len(source_raw)
        if total_image_bytes > files.MAX_TOTAL_BYTES:
            _reject('package-size-limit')
        reference_doc = _layer_reference(row, base, base_view, provider)
        display_path = f'images/{row["mapId"]}/{row["floor"]}.{"jpg" if extension == "jpeg" else extension}'
        reference_doc.update(displayPath=display_path, displayShapeHW=shape)
        layer_docs.append((reference_doc, display_path, source_path, len(source_raw),
                           hashlib.sha256(source_raw).hexdigest()))

    display_document = dict(schema=SCHEMA, profileId=registration['profileId'],
        authorName=registration['authorName'], displayName=registration['displayName'],
        season=season, mode=mode, baseDependency='base',
        layers=[row for row, _path, _source, _size, _sha in layer_docs], identityAuthority=False,
        structureValidationAuthority=False, crossFloorProjectionAllowed=False,
        currentFrameGeometryFitRequired=True)
    display_raw = _encoded(display_document)
    payload_bytes = len(display_raw) + total_image_bytes
    if payload_bytes > files.MAX_TOTAL_BYTES or len(layer_docs) + 1 > files.MAX_FILES:
        _reject('package-size-limit')
    dependency = dict(packageId=base_ref.package_id,
        resourceVersion=base_ref.resource_version, componentId=base_ref.component_id,
        manifestSha256=base_ref.manifest_sha256)
    if (package_id, resource_version) == (base_ref.package_id, base_ref.resource_version):
        _reject('self-dependency')
    file_specs = [(DISPLAY_ENTRYPOINT, len(display_raw), hashlib.sha256(display_raw).hexdigest())]
    file_specs.extend((display_path, size, digest)
        for _, display_path, _source, size, digest in layer_docs)
    manifest = dict(schema='xingtu-resource-package', formatVersion=1,
        packageId=package_id, resourceVersion=resource_version,
        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
        components=[dict(componentId=component_id, type='display', schemaVersion=1,
            contexts=[dict(season=season, mode=mode)], entrypoint=DISPLAY_ENTRYPOINT,
            dependencies={'base': dependency})],
        files=[dict(componentId=component_id, path=path, bytes=size, sha256=digest)
               for path, size, digest in sorted(file_specs)])
    manifest_raw = _encoded(manifest)
    PackageView._validate_manifest(manifest, ComponentRef(package_id, resource_version,
        component_id, hashlib.sha256(manifest_raw).hexdigest()))

    # Validate every generated byte through the public ZIP and display codecs
    # before creating the caller-visible destination.
    with tempfile.TemporaryDirectory(prefix='author-display-export-') as temporary:
        staged = Path(temporary) / 'component.zip'
        with zipfile.ZipFile(staged, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for _, display_path, source_path, expected_size, expected_sha in layer_docs:
                _cancel(cancelled)
                raw = _read(source_path, files.MAX_FILE_BYTES, 'image')
                if (len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_sha):
                    _reject('image-changed')
                archive.writestr(f'data/{component_id}/{display_path}', raw)
            archive.writestr(f'data/{component_id}/{DISPLAY_ENTRYPOINT}', display_raw)
            archive.writestr('manifest.json', manifest_raw)
        roundtrip = _roundtrip(staged, base, provider, component_id,
                               len(layer_docs), cancelled)
        _cancel(cancelled)
        with staged.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        _cancel(cancelled)
        # Reuse the repository's established no-replace publication path.
        from export_native_base_package import _publish_output
        _publish_output(staged, output, digest,
                        before_move=lambda: _cancel(cancelled))

    return dict(output=str(output), bytes=output.stat().st_size, sha256=digest,
        packageId=package_id, resourceVersion=resource_version,
        componentId=component_id,
        baseReference=dependency, context=dict(season=season, mode=mode),
        catalogActivated=False, **roundtrip)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-resource-root', required=True, type=Path,
        help='已安装的资源根目录；仅读取指定的 recognition-base ComponentRef。')
    parser.add_argument('--base-package-id', required=True)
    parser.add_argument('--base-resource-version', required=True)
    parser.add_argument('--base-component-id', required=True)
    parser.add_argument('--base-manifest-sha256', required=True,
        help='精确 ComponentRef 中的 manifest SHA256。')
    parser.add_argument('--registration', required=True, type=Path,
        help=f'{INPUT_SCHEMA} JSON；每层仅含 mapId、floor、image 和 matrixDisplayToReference。')
    parser.add_argument('--image-root', required=True, type=Path,
        help='注册 JSON 中 image 相对路径的根目录。')
    parser.add_argument('--output', required=True, type=Path,
        help=f'用户可见作者路线包输出路径，必须以 {ROUTE_SUFFIX} 结尾；内部内容仍为 ZIP，已有文件不会覆盖。')
    parser.add_argument('--package-id', required=True)
    parser.add_argument('--resource-version', required=True)
    parser.add_argument('--component-id',
        help='组件主键。默认按 base 上下文生成 display-{season}-{mode}，便于同包并存多模式。')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.output.suffix.casefold() != ROUTE_SUFFIX:
        parser.error(
            f'--output 必须以 {ROUTE_SUFFIX} 结尾；.zip 是内部 ZIP 容器名，不能作为作者路线包输出后缀。')
    base_ref = ComponentRef(args.base_package_id, args.base_resource_version,
        args.base_component_id, args.base_manifest_sha256)
    result = export_component(args.base_resource_root, base_ref, args.registration,
        args.image_root, args.output, package_id=args.package_id,
        resource_version=args.resource_version, component_id=args.component_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
