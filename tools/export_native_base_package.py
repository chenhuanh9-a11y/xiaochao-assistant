"""Export an explicit S0 nightmare source into an inert native base ZIP.

The retained structure/entry products are copied byte for byte. Only the
identity DTO, reference-only coordinate view and exit-template declaration are
adapted. This producer neither activates a catalog nor grants runtime ability.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import posixpath
import shutil
import stat
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import numpy as np

import imported_resource_archive as archives
import map_package_upgrade as publication
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import ComponentResourceReader, decode_resource_json
from resource_snapshot_io import read_stable_file
import star_map_import as files


FLOORS = ('1', '2', 'B1')
PROFILE = 'zhan10-speed'
PACK = 'references/nightmare-map-pack.json'
INDEX = 'references/nightmare-shared-structure-index.json'
VERSIONS = 'references/nightmare-structure-version-bindings.json'
COORDINATES = 'references/nightmare-canonical-coordinate-pack.json'
NEUTRAL = 'references/nightmare-structure-display.json'
ENTRIES = 'references/nightmare-entries'
EXIT = 'references/difficult-exit-icon.json'
INTERFACES = dict(identityPack=PACK, typedIndex=INDEX, coordinatePack=COORDINATES,
                  neutralDisplay=NEUTRAL, identityEntries=ENTRIES, exitIcon=EXIT,
                  identityProfile=PROFILE)
_SOURCE_FILES = (PACK, INDEX, VERSIONS, COORDINATES, NEUTRAL, EXIT,
                 *(ENTRIES + '/' + name for name in ('entries.json', 'side-entries.json',
                   'entry-passports.json', 'side-door-anchors.json', 'side-entry-search.npz')))


def _reject(reason):
    raise ValueError('native-base-export-' + reason)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _relative(value):
    if files._relative(value) != value:
        _reject('noncanonical-path')
    return value


class _Source:
    def __init__(self, root, inventory):
        self.root = Path(root).absolute()
        files._no_links(self.root)
        if not self.root.is_dir():
            _reject('source-root-unavailable')
        self.inventory = None
        if inventory is not None:
            raw, _ = read_stable_file(Path(inventory), files.MAX_FILE_BYTES,
                no_links=files._no_links, reject=_reject)
            self.inventory = decode_resource_json(raw)['files']
            if not isinstance(self.inventory, dict):
                _reject('source-inventory-invalid')
        self.raw = {}

    def read(self, relative):
        relative = _relative(relative)
        if relative not in self.raw:
            path = files._target(self.root, relative)
            raw, _ = read_stable_file(path, files.MAX_FILE_BYTES,
                no_links=files._no_links, reject=_reject)
            if self.inventory is not None:
                expected = self.inventory.get(relative)
                if expected != dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()):
                    _reject('source-inventory-mismatch:' + relative)
            self.raw[relative] = raw
        return self.raw[relative]

    def json(self, relative):
        return decode_resource_json(self.read(relative))


def _rows(rows, expected, label):
    if not isinstance(rows, list):
        _reject(label + '-invalid')
    result = {}
    for row in rows:
        key = (row['mapId'], str(row.get('floor', row.get('resourceKey', {}).get('floor'))))
        if key in result:
            _reject(label + '-duplicate-binding')
        result[key] = row
    if set(result) != expected:
        _reject(label + '-incomplete-inventory')
    return result


def _entry_resource(relative):
    # Existing entry JSON explicitly uses entry-directory-relative references.
    # Normalize that anchor, then enforce the actual component-local target.
    if not isinstance(relative, str) or relative.startswith('/') or '\\' in relative or ':' in relative:
        _reject('entry-resource-path')
    return _relative(posixpath.normpath(ENTRIES + '/' + relative))


def _controls_payloads():
    from floor_contract import floor_contract
    from floor_control_region import AUTO_VERTICAL_SEARCH_AREA, VERTICAL_SEARCH_AREA
    from imported_controls_profile import DETECTOR_ID, SCHEMA
    from s0_floor_templates import _bank, DIGIT_HEIGHT, HEIGHT, TEMPLATE_FAMILY, WIDTH
    contract = floor_contract('nightmare', season='s0')
    vectors, labels = _bank()
    target = BytesIO()
    np.save(target, vectors, allow_pickle=False)
    profile = dict(schema=SCHEMA, detectorId=DETECTOR_ID,
        contexts={'s0:nightmare': dict(
            floors=[dict(observedValue=f.observed_value, floorId=f.floor_id) for f in contract.floors],
            visualOrder=list(reversed(contract.observed_values)))},
        fixedSearchRegion=list(VERTICAL_SEARCH_AREA), expandedSearchRegion=list(AUTO_VERTICAL_SEARCH_AREA),
        templateBank=dict(path='templates.npy', labels=labels.tolist(), family=TEMPLATE_FAMILY,
                          shapeHW=[HEIGHT, WIDTH], digitHeight=DIGIT_HEIGHT))
    return {'profile.json': _encoded(profile), 'templates.npy': target.getvalue()}


def _base_payloads(source):
    raw = source.json(PACK)
    if (type(raw.get('schemaVersion')) is not int or raw['schemaVersion'] not in (1, 2)
            or raw.get('mode', 'nightmare') != 'nightmare'
            or raw.get('season', 's0') != 's0' or not raw.get('maps')
            or type(raw.get('defaultEntryFloor')) is not int or raw['defaultEntryFloor'] not in (1, 2)):
        _reject('source-context-invalid')
    maps = raw['maps']
    ids = [row['id'] for row in maps]
    if len(set(ids)) != len(ids) or len({mid.casefold() for mid in ids}) != len(ids):
        _reject('map-inventory-duplicate')
    expected = {(mid, floor) for mid in ids for floor in FLOORS}
    for row in maps:
        if (row.get('identityProfileId') != PROFILE or row.get('resourceBase') != 'resource-root'
                or set(row.get('layers', {})) != set(FLOORS)):
            _reject('map-layer-or-profile-incomplete:' + row['id'])
    index = source.json(INDEX)
    if (index.get('schemaVersion'), index.get('resourceKind'), index.get('structureVersionBindings')) != (
            1, 'shared-structure', VERSIONS):
        _reject('typed-index-contract')
    typed = _rows(index['entries'], expected, 'typed')
    paths = set()
    for (mid, floor), row in typed.items():
        path = _relative(row['resourcePath'])
        if (row.get('profile') != PROFILE or row.get('variant') != 'variant-01'
                or Path(path).suffix != '.npz' or path.casefold() in paths):
            _reject('typed-resource-binding')
        paths.add(path.casefold())
        source.read(path)
    versions = _rows(source.json(VERSIONS)['entries'], expected, 'versions')
    for row in versions.values():
        if (row.get('packReference') != PACK or row.get('adapterReference') != COORDINATES
                or row.get('neutralDisplay', {}).get('index') != NEUTRAL):
            _reject('version-reference-binding')
    neutral_index = source.json(NEUTRAL)
    if neutral_index.get('indexPath') != INDEX:
        _reject('neutral-index-binding')
    neutral = _rows(neutral_index['entries'], expected, 'neutral')
    local = _rows(source.json(ENTRIES + '/side-entries.json')['entries'], expected, 'local-entry')
    main = _rows(source.json(ENTRIES + '/entries.json')['entries'],
                 {(mid, '1') for mid in ids}, 'main-entry')
    _rows(source.json(ENTRIES + '/side-door-anchors.json')['entries'],
          {(mid, '2') for mid in ids}, 'side-door')
    for collection in (main, local):
        for key, row in collection.items():
            if (_entry_resource(row['structureResource']) != typed[key]['resourcePath']
                    or row['sourceProduct'] != Path(typed[key]['resourcePath']).name
                    or row.get('profileId') != PROFILE
                    or row.get('variantId') != 'variant-01'):
                _reject('entry-typed-binding')
            if 'sourceIndex' in row and _entry_resource(row['sourceIndex']) != INDEX:
                _reject('entry-index-binding')
    for key, row in neutral.items():
        if row.get('referenceResource') != typed[key]['resourcePath']:
            _reject('neutral-typed-binding')
    coordinates = source.json(COORDINATES)
    shared = coordinates.get('profiles', {}).get('shared-structure')
    if (not isinstance(shared, dict) or set(coordinates.get('maps', {})) != set(ids)
            or set(shared.get('maps', {})) != set(ids)
            or coordinates.get('coordinateReferenceProfileId') != 'shared-structure'
            or coordinates.get('referenceProvider', {}).get('indexPath') != INDEX):
        _reject('coordinate-inventory')
    lookups = _rows(coordinates['referenceProvider'].get('lookupBindings'), expected, 'coordinate-lookup')
    if any(row.get('profile') != PROFILE or row.get('variant') != 'variant-01' for row in lookups.values()):
        _reject('coordinate-lookup-profile')
    if coordinates.get('canonicalCrossFloorSearchCandidates') not in (None, {}):
        _reject('cross-floor-reference-requires-review')
    for mid in ids:
        for collection in (coordinates['maps'], shared['maps']):
            if set(collection[mid].get('floors', {})) != set(FLOORS):
                _reject('coordinate-floor-incomplete')
        for floor in FLOORS:
            if coordinates['maps'][mid]['floors'][floor].get('referenceResource') != typed[mid, floor]['resourcePath']:
                _reject('coordinate-typed-binding')
    converted = deepcopy(raw)
    converted.update(schemaVersion=1, sourceIdentitySchemaVersion=raw['schemaVersion'],
                     season='s0', mode='nightmare', floors=list(FLOORS))
    for row in converted['maps']:
        row.update(layers={floor: typed[row['id'], floor]['resourcePath'] for floor in FLOORS},
                   oneFlow=None, routeProfileId='shared-structure')
    from difficult_native_identity import _validate_imported_identity_ids
    _validate_imported_identity_ids(converted)
    coordinates = deepcopy(coordinates)
    coordinates['profiles'] = {'shared-structure': coordinates['profiles']['shared-structure']}
    # The selected source has no cross-floor relation; never infer one from an
    # author profile or silently discard a relation this producer cannot map.
    exit_template = dict(exitIcon=source.json(EXIT)['exitIcon'], sideDoors=[], role='exit-template-only')
    for path in _SOURCE_FILES:
        source.read(path)
    payloads = dict(source.raw)
    payloads[PACK] = _encoded(converted)
    payloads[COORDINATES] = _encoded(coordinates)
    payloads[EXIT] = _encoded(exit_template)
    payloads['resource-context.json'] = _encoded(dict(schema='xingtu-native-recognition-base-v1',
        season='s0', mode='nightmare', runtimeInterfaces=INTERFACES))
    return payloads, len(ids)


def _round_trip(path, destination):
    """Exercise the real container, installed views and current data consumers."""
    from difficult_entry_identity import MapEntryGeometryIdentity
    from difficult_native_identity import NativeDifficultPack
    from difficult_side_entry_identity import MapLocalEntryIdentity
    from imported_controls_profile import S0ControlsProfile
    from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
    from shared_structure_context import load_shared_version_context
    from shared_structure_reference import SharedStructureResourceStore
    from side_door_recognition import SideDoorRecognizer
    inspected = archives.inspect_archive(path)
    saved = archives.save_archive(path, destination,
                                  expected_archive_sha256=inspected.archive_sha256)
    refs = {ref.component_id: ref for ref in saved.component_refs}
    base = PackageView(destination, refs['base'])
    reader = ComponentResourceReader(base)
    pack = NativeDifficultPack(base.root, PACK, expected_mode='nightmare', expected_season='s0',
                               read_resource=reader)
    resources = SharedStructureResourceStore(base.path(INDEX), base.root, read_resource=reader)
    if resources.error:
        _reject('roundtrip-typed-index:' + resources.error)
    adapters = AuthorCoordinateAdapters(base.path(COORDINATES), read_resource=reader)
    entry_root = base.root / ENTRIES
    main = MapEntryGeometryIdentity(entry_root, structure_store=resources,
        profile_id=PROFILE, profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader)
    local = MapLocalEntryIdentity(entry_root, mode='nightmare', profile_id=PROFILE, floors=FLOORS,
        exit_icon_path=base.path(EXIT), structure_store=resources,
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader,
        typed_index_path=base.path(INDEX))
    errors = [main.error, local.side_door_error, local.side_search_error, local.passport_error]
    if any(errors):
        _reject('roundtrip-entry:' + ';'.join(str(error) for error in errors if error))
    SideDoorRecognizer(base.path(EXIT), read_resource=reader)
    versions = load_shared_version_context(base.root, pack, resources, adapters, None, local.entries,
        neutral_relative=NEUTRAL, entry_root_relative=ENTRIES, read_resource=reader)
    if (set(versions) != set(resources.entries)
            or any(row['sharedError'] for row in versions.values())):
        _reject('roundtrip-version-binding')
    for profile, mid, variant, floor in resources.entries:
        product = resources.load(profile, mid, floor, variant=variant)
        if (product is None or not np.any(product.clean_mask)
                or list(product.shape) != versions[profile, mid, variant, floor]['canvas'].get('shapeHW')):
            _reject('roundtrip-typed-product:' + mid + '/' + floor)
    controls = S0ControlsProfile(PackageView(destination, refs['controls']))
    if set(controls.for_context('s0', 'nightmare').contract.floor_ids) != set(pack.floors):
        _reject('roundtrip-controls-floors')
    return dict(mapCount=len(pack.maps), typedLayerCount=len(resources.entries),
                mainEntries=len(main.entries), localEntries=len(local.entries),
                controlsTemplates=len(controls.templates.labels),
                controlsLabels=list(controls.templates.label_values))


def _discard_owned_temporary(path, identity):
    """Remove only our original file; an unavailable or replaced path stays."""
    try:
        files._no_links(path)
        with path.open('rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                return False
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        # Windows reopens READ|DELETE with share READ only, then checks this
        # identity/content and applies disposition to that same native handle.
        return files._rollback_published(path, identity, digest)
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False


def _publish_output(staged, output, digest):
    """Finish and fsync a same-directory temporary before a no-replace move."""
    files._no_links(output.parent)
    descriptor, name = tempfile.mkstemp(prefix='.' + output.name + '.', suffix='.tmp', dir=output.parent)
    temporary = Path(name)
    info = os.fstat(descriptor)
    identity = (info.st_dev, info.st_ino)
    try:
        with os.fdopen(descriptor, 'wb') as target, staged.open('rb') as handle:
            shutil.copyfileobj(handle, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        # Reuse the existing owned-file move: verify exact bytes and original
        # identity, then publish without replacing any destination. On Windows
        # verification and FileRenameInfo use the same exclusive native handle.
        publication._move_owned(temporary, output,
            dict(path=temporary.name, bytes=staged.stat().st_size, sha256=digest),
            list(identity), linkless=True)
    except BaseException as error:
        if not _discard_owned_temporary(temporary, identity):
            error.add_note('native-base-export-temporary-preserved: ' + str(temporary))
        raise


def export_package(source_root, output, *, package_id, resource_version, source_inventory=None):
    """Validate and exclusively create the requested ZIP; never alter source."""
    output = Path(output).absolute()
    files._no_links(output)
    if output.exists():
        raise FileExistsError('native-base-export-output-exists')
    source = _Source(source_root, source_inventory)
    if output.resolve().is_relative_to(source.root.resolve()):
        _reject('output-inside-source')
    # Reuse the installed protocol's identity validation, not a new ID policy.
    ComponentRef(package_id, resource_version, 'base', '0' * 64)
    base, map_count = _base_payloads(source)
    controls = _controls_payloads()
    payloads = {'base': base, 'controls': controls}
    context = [dict(season='s0', mode='nightmare')]
    manifest = dict(schema='xingtu-resource-package', formatVersion=1,
        packageId=package_id, resourceVersion=resource_version,
        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
        components=[dict(componentId=component, type=kind, schemaVersion=1,
                         contexts=context, entrypoint=entry, dependencies={})
                    for component, kind, entry in [('base', 'recognition-base', 'resource-context.json'),
                        ('controls', 'controls-profile', 'profile.json')]],
        files=[dict(componentId=component, path=path, bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest())
               for component, members in payloads.items() for path, raw in sorted(members.items())])
    manifest_raw = _encoded(manifest)
    PackageView._validate_manifest(manifest,
        ComponentRef(package_id, resource_version, 'base', hashlib.sha256(manifest_raw).hexdigest()))
    with tempfile.TemporaryDirectory(prefix='xingtu-native-base-export-') as temporary:
        staged = Path(temporary) / 'package.zip'
        with zipfile.ZipFile(staged, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', manifest_raw)
            for component, members in payloads.items():
                for path, raw in sorted(members.items()):
                    archive.writestr(f'data/{component}/{path}', raw)
        checked = _round_trip(staged, Path(temporary) / 'roundtrip-resources')
        if checked['mapCount'] != map_count:
            _reject('roundtrip-map-inventory')
        with staged.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        _publish_output(staged, output, digest)
        return dict(output=str(output), bytes=output.stat().st_size, sha256=digest,
            packageId=package_id, resourceVersion=resource_version,
            sourceFiles=len(source.raw), sourceInventoryChecked=source.inventory is not None,
            files=len(manifest['files']), catalogActivated=False, **checked)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--package-id', default='s0-nightmare-native')
    parser.add_argument('--resource-version', required=True)
    parser.add_argument('--source-inventory', type=Path)
    args = parser.parse_args()
    print(json.dumps(export_package(args.source_root, args.output, package_id=args.package_id,
        resource_version=args.resource_version, source_inventory=args.source_inventory),
        ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
