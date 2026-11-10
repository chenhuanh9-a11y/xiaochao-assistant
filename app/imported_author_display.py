"""Independent author display components bound to a selected native base.

This payload is distinct from the old V2 installation namespace. Shared V2
checks validate pixels, permissions, affines and references; no author bytes
become recognition, structure, floor or publisher authority.
"""
from dataclasses import dataclass
from pathlib import Path

from author_display_validation import (valid_display_shape, validate_display_affine,
    validate_display_authority, validate_display_image, validate_display_reference)
from generic_author_package import MAX_LAYERS, _label, is_generic_profile
from imported_native_base import NativeBaseBundle
from imported_resource_packages import FileRef, PackageView
from imported_resource_reader import ComponentResourceReader
import star_map_import as files


SCHEMA = 'xingtu-author-display-component-v1'
_FIELDS = {'schema', 'profileId', 'authorName', 'displayName', 'season', 'mode',
    'baseDependency', 'layers', 'identityAuthority', 'structureValidationAuthority',
    'crossFloorProjectionAllowed', 'currentFrameGeometryFitRequired'}
_LAYER_FIELDS = {'mapId', 'floor', 'displayPath', 'displayShapeHW',
    'matrixDisplayToReference', 'referenceResource', 'referenceShapeHW',
    'coordinateSpace', 'structureVersionKey', 'baseSnapshot', 'sourceVersion'}


def _reject(reason):
    message = 'imported-author-display-' + reason
    raise files.ImportRejected(message, code=message.split(':', 1)[0])


@dataclass(frozen=True)
class AuthorDisplayLayer:
    map_id: str
    floor: str
    image: FileRef
    reference: FileRef
    display_shape_hw: tuple
    reference_shape_hw: tuple
    coordinate_space: str
    structure_version_key: str
    source_version: str
    base_snapshot: str
    matrix_display_to_reference: tuple
    encoding: str


@dataclass(frozen=True)
class AuthorDisplayGap:
    map_id: str
    floor: str
    reason: str


class ImportedAuthorDisplay:
    def __init__(self, view, base, reader, document, layers, gaps):
        self._view, self._root, self._ref = view, view.root, view.component_ref
        self._base, self._base_ref = base, base.component_ref
        self._reader = reader
        self._entrypoint = view.component['entrypoint']
        self._profile = document['profileId']
        self._author, self._name = document['authorName'], document['displayName']
        self._layers = {(layer.map_id, layer.floor):layer for layer in layers}
        self._gaps = tuple(gaps)

    def assert_current(self):
        self._base.assert_current()
        if (self._view.component_ref != self._ref or self._view.root != self._root
                or self._base.component_ref != self._base_ref):
            _reject('component-changed')

    def _metadata(self, value):
        self.assert_current()
        return value

    profile_id = property(lambda self:self._metadata(self._profile))
    author_name = property(lambda self:self._metadata(self._author))
    display_name = property(lambda self:self._metadata(self._name))
    component_ref = property(lambda self:self._metadata(self._ref))
    base_ref = property(lambda self:self._metadata(self._base_ref))
    layers = property(lambda self:self._metadata(tuple(self._layers.values())))
    gaps = property(lambda self:self._metadata(self._gaps))
    qualification = property(lambda self:self._metadata('user-provided-unreviewed'))

    def layer(self, map_id, floor):
        self.assert_current()
        return self._layers.get((str(map_id), str(floor)))

    def image_bytes(self, map_id, floor):
        layer = self.layer(map_id, floor)
        if layer is None:
            _reject('layer-unavailable')
        # Recheck the registration bytes as well as the selected bitmap before
        # a consumer can return even an already decoded image from its cache.
        self._reader.json(self._entrypoint)
        raw = self._reader(self._view.path(layer.image.path))
        self.assert_current()
        return raw

    def assert_layer_current(self, map_id, floor):
        layer = self.layer(map_id, floor)
        if layer is None:
            _reject('layer-unavailable')
        self._reader.json(self._entrypoint)
        self._view.path(layer.image.path)
        self.assert_current()

    def image_path(self, map_id, floor):
        layer = self.layer(map_id, floor)
        return self._view.path(layer.image.path) if layer is not None else None


def _decode(view, base, reference_provider):
    if not isinstance(view, PackageView) or not isinstance(base, NativeBaseBundle):
        _reject('validated-components-required')
    base.assert_current()
    component = view.component
    wanted_context = dict(season=base.season, mode=base.mode)
    if (component.get('type') != 'display' or component.get('schemaVersion') != 1
            or component.get('contexts') != [wanted_context]):
        _reject('component-context-invalid')
    if not base.ready or not callable(reference_provider):
        _reject('base-reference-unavailable')
    reader = ComponentResourceReader(view)
    document = reader.json(component['entrypoint'])
    if (not isinstance(document, dict) or set(document) - (_FIELDS | {'provenance'})
            or not _FIELDS <= set(document) or document.get('schema') != SCHEMA):
        _reject('schema-invalid')
    if (not is_generic_profile(document['profileId']) or not _label(document['authorName'])
            or not _label(document['displayName'])
            or (document['season'], document['mode']) != (base.season, base.mode)
            or ('provenance' in document and not isinstance(document['provenance'], dict))):
        _reject('context-invalid')
    validate_display_authority(document, reject=_reject)
    alias = document['baseDependency']
    if (not isinstance(alias, str) or set(view.dependencies) != {alias}
            or view.dependencies[alias] != base.component_ref):
        _reject('base-dependency-mismatch')
    rows = document['layers']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_LAYERS:
        _reject('layers-invalid')
    inventory = view.inventory
    owned = {component['entrypoint']}
    seen, layers, gaps = set(), [], []
    base_view = base.reader.view
    base_inventory = base_view.inventory
    for row in rows:
        if not isinstance(row, dict) or set(row) != _LAYER_FIELDS:
            _reject('layer-fields-invalid')
        key = (row['mapId'], row['floor'])
        if (any(not isinstance(value, str) for value in key) or key in seen
                or key[0] not in base.pack.by_id or key[1] not in base.pack.floors):
            _reject('duplicate-or-foreign-layer')
        seen.add(key)
        image = FileRef(view.component_ref, row['displayPath'])
        reference_file = FileRef(base.component_ref, row['referenceResource'])
        image_path, reference_path = view.path(image.path), base_view.path(reference_file.path)
        if (image.path not in inventory or reference_file.path not in base_inventory
                or not valid_display_shape(row['displayShapeHW'])
                or not valid_display_shape(row['referenceShapeHW'])):
            _reject('image-declaration-invalid')
        extension = Path(image.path).suffix.casefold().lstrip('.')
        encoding = 'jpeg' if extension == 'jpg' else extension
        validate_display_affine(row['matrixDisplayToReference'], reject=_reject)
        reference = reference_provider('shared-structure', *key)
        if (reference is None or reference.path.resolve() != reference_path.resolve()
                or reference.metadata.get('binding', {}).get('mapId') != key[0]
                or reference.metadata.get('binding', {}).get('floor') != key[1]
                or reference.metadata.get('binding', {}).get('variant') != 'variant-01'
                or reference.metadata.get('binding', {}).get('profile') != base.pack.identity_profiles[key[0]]):
            _reject('reference-file-mismatch')
        metadata = reference.metadata
        expected = dict(referenceResource=reference_file.path, referenceShapeHW=list(reference.shape),
            coordinateSpace=metadata.get('canvas', {}).get('coordinateSpace'),
            **{name:metadata.get(name) for name in ('structureVersionKey', 'baseSnapshot', 'sourceVersion')})
        validate_display_reference(row, expected, reject=_reject)
        base.reader(reference_path)
        raw = reader(image_path)
        validate_display_image(raw, encoding, row['displayShapeHW'], reject=_reject)
        base.assert_current()
        view.root  # Do not admit a result decoded across a component change.
        owned.add(image.path)
        if metadata.get('sourceKind') != 'game-rendered-shared-structure':
            gaps.append(AuthorDisplayGap(*key, 'reference-role-unavailable'))
            continue
        layers.append(AuthorDisplayLayer(*key, image, reference_file,
            tuple(row['displayShapeHW']), tuple(reference.shape), expected['coordinateSpace'],
            expected['structureVersionKey'], expected['sourceVersion'], expected['baseSnapshot'],
            tuple(tuple(value for value in line) for line in row['matrixDisplayToReference']), encoding))
    if owned != set(inventory):
        _reject('unlisted-display-file')
    result = ImportedAuthorDisplay(view, base, reader, document, layers, gaps)
    result.assert_current()
    return result


def decode_author_display(view, base, reference_provider):
    from PIL import Image
    try:
        return _decode(view, base, reference_provider)
    except files.ImportRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError,
            Image.DecompressionBombError) as error:
        code = 'imported-author-display-invalid'
        raise files.ImportRejected(code, code=code) from error
