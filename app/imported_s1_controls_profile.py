"""Bounded external parameters for the registered S1 signed-column observer.

These declarations supply search geometry, not templates or evidence authority.
Source replay qualification remains owned by s1_floor_observation.
"""
from dataclasses import dataclass
import math
from types import MappingProxyType

from floor_contract import FloorContract, floor_contract
from floor_control_region import normalized_region
from imported_resource_packages import PackageView
from imported_resource_reader import decode_resource_json
import star_map_import as files


SCHEMA = 'xingtu-s1-controls-profile-v1'
DETECTOR_ID = 's1-signed-column-geometry-v1'
SUPPORTED_CONTEXTS = frozenset(('s1:difficult', 's1:nightmare'))
MAX_PROFILE_BYTES = 64 * 1024
MAX_SHAPE_SIZES = 32
_FIELDS = frozenset(('schema', 'detectorId', 'contexts', 'fixedSearchRegion',
                     'expandedSearchRegion', 'mapUi'))
_SHAPES = frozenset(('close', 'plus', 'minus'))


@dataclass(frozen=True)
class MapUiGeometryParameters:
    """Data-only external declaration; the original observer owns decisions."""
    search: object
    sizes: tuple[float, ...]
    minimum_correlation: object


def _reject(reason):
    code = 'imported-s1-controls-' + reason
    raise files.ImportRejected(code, code=code)


def _number(value, low, high, label):
    if type(value) not in (int, float):
        _reject(label + '-invalid')
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        _reject(label + '-invalid')
    return float(value)


def _region(value, label):
    if not isinstance(value, list) or len(value) != 4:
        _reject(label + '-invalid')
    values = tuple(_number(part, 0, 1, label) for part in value)
    region = normalized_region(values)
    if region is None or region[0] + region[2] > 1 or region[1] + region[3] > 1:
        _reject(label + '-invalid')
    return region


def _bounds(region):
    x, y, width, height = region
    return x, y, x + width, y + height


@dataclass(frozen=True)
class ImportedS1ControlsContext:
    key: str
    contract: FloorContract
    visual_order: tuple[int, ...]


@dataclass(frozen=True)
class _S1ObservationControls:
    context: ImportedS1ControlsContext | None
    fixed_search_region: tuple[float, ...]
    expanded_search_region: tuple[float, ...]
    map_ui: MapUiGeometryParameters


def _context(key, value):
    if not isinstance(value, dict) or set(value) != {'floors', 'visualOrder'}:
        _reject('context-fields-invalid')
    season, mode = key.split(':', 1)
    contract = floor_contract(mode, season=season)
    expected = {floor.observed_value: floor.floor_id for floor in contract.floors}
    floors = value['floors']
    if not isinstance(floors, list) or len(floors) != len(expected):
        _reject('floors-invalid')
    actual = {}
    for row in floors:
        if (not isinstance(row, dict) or set(row) != {'observedValue', 'floorId'}
                or type(row['observedValue']) is not int or not isinstance(row['floorId'], str)
                or row['observedValue'] in actual):
            _reject('floor-invalid')
        actual[row['observedValue']] = row['floorId']
    if actual != expected:
        _reject('floor-contract-mismatch')
    order = value['visualOrder']
    # The first narrow digit is -1; the other three rows have wider glyphs.
    # A package cannot relabel that reviewed visual grammar.
    if (not isinstance(order, list) or any(type(item) is not int for item in order)
            or tuple(order) != contract.observed_values):
        _reject('visual-order-invalid')
    return ImportedS1ControlsContext(key, contract, tuple(order))


def _map_ui(value):
    if not isinstance(value, dict) or set(value) != {'searchRegions', 'sizes', 'minimumCorrelation'}:
        _reject('map-ui-fields-invalid')
    searches, correlations = value['searchRegions'], value['minimumCorrelation']
    if not isinstance(searches, dict) or set(searches) != _SHAPES:
        _reject('map-ui-search-regions-invalid')
    if not isinstance(correlations, dict) or set(correlations) != _SHAPES:
        _reject('map-ui-correlations-invalid')
    sizes = value['sizes']
    if not isinstance(sizes, list) or not 1 <= len(sizes) <= MAX_SHAPE_SIZES:
        _reject('map-ui-sizes-invalid')
    sizes = tuple(_number(size, .005, .1, 'map-ui-size') for size in sizes)
    if any(left >= right for left, right in zip(sizes, sizes[1:])):
        _reject('map-ui-sizes-invalid')
    return MapUiGeometryParameters(
        MappingProxyType({name: _bounds(_region(searches[name], 'map-ui-search-region'))
                          for name in sorted(_SHAPES)}),
        sizes,
        MappingProxyType({name: _number(correlations[name], .5, 1, 'map-ui-correlation')
                          for name in sorted(_SHAPES)}))


class S1ControlsProfile:
    """Immutable decoded data; every observation checks its owning view."""

    detector_id = DETECTOR_ID

    def __init__(self, view: PackageView):
        if not isinstance(view, PackageView):
            _reject('package-view-required')
        self._view, self._root = view, view.root
        component = view.component
        if (component.get('type') != 'controls-profile' or component.get('schemaVersion') != 1
                or component.get('dependencies')):
            _reject('component-unsupported')
        declared = component.get('contexts')
        if not isinstance(declared, list) or not 1 <= len(declared) <= len(SUPPORTED_CONTEXTS):
            _reject('component-contexts-invalid')
        keys = set()
        for row in declared:
            if (not isinstance(row, dict) or set(row) != {'season', 'mode'}
                    or not isinstance(row['season'], str) or not isinstance(row['mode'], str)):
                _reject('component-contexts-invalid')
            key = row['season'] + ':' + row['mode']
            if key not in SUPPORTED_CONTEXTS or key in keys:
                _reject('component-context-unsupported')
            keys.add(key)
        path = component['entrypoint']
        row = view.inventory.get(path)
        if row is not None and row['bytes'] > MAX_PROFILE_BYTES:
            _reject('profile-size')
        raw = view.read(path)
        if len(raw) > MAX_PROFILE_BYTES:
            _reject('profile-size')
        value = decode_resource_json(raw)
        if not isinstance(value, dict) or set(value) != _FIELDS:
            _reject('profile-fields-invalid')
        if value['schema'] != SCHEMA or value['detectorId'] != DETECTOR_ID:
            _reject('detector-unsupported')
        contexts = value['contexts']
        if not isinstance(contexts, dict) or set(contexts) != keys:
            _reject('contexts-component-mismatch')
        self._contexts = MappingProxyType({key: _context(key, contexts[key]) for key in sorted(keys)})
        self._fixed = _region(value['fixedSearchRegion'], 'fixed-search-region')
        self._expanded = _region(value['expandedSearchRegion'], 'expanded-search-region')
        fixed, expanded = _bounds(self._fixed), _bounds(self._expanded)
        if any(expanded[i] > fixed[i] for i in (0, 1)) or any(expanded[i] < fixed[i] for i in (2, 3)):
            _reject('expanded-search-region-does-not-contain-fixed')
        self._map_ui = _map_ui(value['mapUi'])
        self.assert_current()

    def assert_current(self):
        if self._view.root != self._root:
            _reject('component-root-changed')

    def for_context(self, season, mode):
        self.assert_current()
        if not isinstance(season, str) or not isinstance(mode, str):
            return None
        return self._contexts.get(season + ':' + mode)

    def observation_parameters(self, season, mode):
        """Caller must check this profile again before returning pixel evidence."""
        return _S1ObservationControls(self.for_context(season, mode), self._fixed,
                                      self._expanded, self._map_ui)

    @property
    def fixed_search_region(self):
        self.assert_current()
        return self._fixed

    @property
    def expanded_search_region(self):
        self.assert_current()
        return self._expanded

    @property
    def map_ui(self):
        self.assert_current()
        return self._map_ui
