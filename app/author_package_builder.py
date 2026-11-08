"""Prepare the existing generic V2 ZIP from images and explicit per-floor maps."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

from PIL import Image

import generic_author_package as protocol
from generic_author_base import resolve_layer, browse_validation_request
from resource_transaction import resource_read_lock, rename_no_replace


SOURCE_SCHEMA = 'xingtu-author-package-source-v1'


def build_author_package(prepared_directory, install_root, output_path):
    """Read-only base resolution, then the player's validator before publication.

    The recipe is a producer input, not a second runtime package format. Base
    coordinates, versions and dependency hashes always come from the target.
    """
    import star_map_import as api

    source = Path(prepared_directory).absolute()
    api._no_links(source)
    declaration = api._target(source, 'author-package.json')
    if declaration.stat().st_size > 2 * 1024 * 1024:
        raise api.ImportRejected('author-source-too-large')
    recipe = api._json(declaration.read_bytes())
    if (set(recipe) != {'schema', 'profileId', 'season', 'authorName', 'displayName', 'layers'}
            or recipe['schema'] != SOURCE_SCHEMA or not protocol.is_generic_profile(recipe['profileId'])
            or not protocol.valid_season(recipe['season']) or not isinstance(recipe['layers'], list)
            or not 1 <= len(recipe['layers']) <= protocol.MAX_LAYERS):
        raise api.ImportRejected('invalid-author-source')
    profile, season = recipe['profileId'], recipe['season']
    output = Path(output_path).absolute()
    api._no_links(output)
    if output.resolve().is_relative_to(source.resolve()):
        raise api.ImportRejected('author-output-inside-source')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='author-package-', dir=output.parent) as temporary:
        layers, payload_rows, dependencies, seen = [], [], {}, set()
        total = 0
        with resource_read_lock(install_root, wait=True) as locked_root, browse_validation_request():
            root = Path(locked_root)
            api._pending_upgrade(root)
            archive_path = Path(temporary) / 'package.zip'
            with zipfile.ZipFile(archive_path, 'x', zipfile.ZIP_DEFLATED) as archive:
                for item in recipe['layers']:
                    if (not isinstance(item, dict) or set(item) != {'mode', 'mapId', 'floor', 'image', 'matrixDisplayToReference'}
                            or any(not isinstance(item[key], str) for key in ('mode', 'mapId', 'floor', 'image'))):
                        raise api.ImportRejected('invalid-author-source-layer')
                    key = (item['mode'], item['mapId'], item['floor'])
                    if key in seen:
                        raise api.ImportRejected('duplicate-author-source-layer')
                    seen.add(key)
                    protocol.validate_display_affine(item['matrixDisplayToReference'])
                    base = resolve_layer(root, season, *key)
                    image_path = api._target(source, api._relative(item['image']))
                    if image_path.stat().st_size > api.MAX_FILE_BYTES:
                        raise api.ImportRejected('author-source-image-too-large')
                    data = image_path.read_bytes()
                    total += len(data)
                    if len(data) > api.MAX_FILE_BYTES or total > api.MAX_TOTAL_BYTES:
                        raise api.ImportRejected('archive-expanded-size-limit')
                    with Image.open(io.BytesIO(data)) as image:
                        extension = {'PNG': 'png', 'JPEG': 'jpeg'}.get(image.format)
                        shape = [image.height, image.width]
                    if extension is None:
                        raise api.ImportRejected('author-source-image-format')
                    destination = protocol.image_path(profile, season, *key, extension)
                    row = dict(path=destination, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                    payload_rows.append(row)
                    archive.writestr('data/' + destination, data)
                    for relative in base['dependencies']:
                        if relative in dependencies:
                            continue
                        path = api._target(root, relative)
                        if path.stat().st_size > api.MAX_FILE_BYTES:
                            raise api.ImportRejected('author-base-dependency-too-large')
                        dependency = path.read_bytes()
                        dependencies[relative] = dict(path=relative, bytes=len(dependency),
                            sha256=hashlib.sha256(dependency).hexdigest())
                    layer = {key: value for key, value in base.items() if key != 'dependencies'}
                    layer.update(season=season, mode=item['mode'], mapId=item['mapId'], floor=item['floor'],
                        displayPath=destination, displayBytes=row['bytes'], displaySha256=row['sha256'],
                        displayShapeHW=shape, matrixDisplayToReference=item['matrixDisplayToReference'],
                        referenceSha256=dependencies[base['referenceResource']]['sha256'])
                    if base.get('nativeImageResource'):
                        layer['nativeImageSha256'] = dependencies[base['nativeImageResource']]['sha256']
                    layers.append(layer)
                bindings = dict(schema=protocol.BINDINGS_SCHEMA, profileId=profile, season=season,
                    authorName=recipe['authorName'], displayName=recipe['displayName'],
                    identityAuthority=False, structureValidationAuthority=False,
                    crossFloorProjectionAllowed=False, currentFrameGeometryFitRequired=True, layers=layers)
                encoded = json.dumps(bindings, ensure_ascii=False, indent=2).encode('utf-8')
                relative = protocol.bindings_path(profile)
                payload_rows.append(dict(path=relative, bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest()))
                archive.writestr('data/' + relative, encoded)
                metadata = dict(schema=protocol.SCHEMA, schemaVersion=1, revision=1, packageId=profile,
                    season=season, clientCompatibility={'authorDisplayProtocol': 2},
                    files=payload_rows, requiresFiles=sorted(dependencies.values(), key=lambda row: row['path']))
                archive.writestr(api.MANIFEST, json.dumps(metadata, ensure_ascii=False, indent=2).encode('utf-8'))
            # Reuse exact production validation, including installed ownership,
            # per-layer resource closure, geometry rules and image decoding.
            with api._open_archive(archive_path) as (archive, digest):
                inspected = api._validate(archive, root, season=season, versioned=True)
        api._no_links(output)
        rename_no_replace(archive_path, output)
        return dict(packageId=profile, season=season, output=str(output), bytes=output.stat().st_size,
                    sha256=digest, fileCount=inspected['fileCount'], layerCount=len(layers))
