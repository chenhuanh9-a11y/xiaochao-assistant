"""Decoder for externally packaged S0 signed floor-control templates.

Reads only declared bytes through PackageView. The profile describes reviewed
S0 contexts and does not make a detector available for S1.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import struct
from types import MappingProxyType

import numpy as np

import star_map_import as files
from floor_contract import FloorContract, FloorDefinition, floor_contract
from floor_control_region import normalized_region
from imported_resource_packages import PackageView
from s0_floor_templates import HEIGHT, LABELS, WIDTH


SCHEMA = 'xingtu-s0-controls-profile-v1'
DETECTOR_ID = 's0-signed-templates-v1'
SUPPORTED_CONTEXTS = frozenset({'s0:nightmare', 's0:difficult'})
_ENTRY_FIELDS = frozenset({
    'schema', 'detectorId', 'contexts', 'fixedSearchRegion',
    'expandedSearchRegion', 'templateBank',
})
_BANK_FIELDS = frozenset({'path', 'labels', 'family', 'shapeHW', 'digitHeight'})
_FLOOR_FIELDS = frozenset({'observedValue', 'floorId'})
_MAX_TEMPLATES = 4096
_MAX_NPY_HEADER = 10_000
_FAMILY = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
_FLOOR_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z')


def _reject(reason):
    code = 'imported-controls-' + reason
    raise files.ImportRejected(code, code=code)


@dataclass(frozen=True)
class TemplateBank:
    """Immutable vector bank and its full set of signed-label competitors."""

    vectors: np.ndarray
    labels: np.ndarray
    label_values: tuple[int, ...]
    family: str


@dataclass(frozen=True)
class ImportedControlsContext:
    key: str
    contract: FloorContract
    visual_order: tuple[int, ...]


@dataclass(frozen=True)
class _S0ObservationControls:
    """Decoded parameters for one synchronous observation, not a live profile."""

    context: ImportedControlsContext | None
    templates: TemplateBank
    fixed_search_region: tuple[float, ...]
    expanded_search_region: tuple[float, ...]

    def for_context(self, season, mode):
        if (isinstance(season, str) and isinstance(mode, str)
                and self.context is not None and self.context.key == f'{season}:{mode}'):
            return self.context
        return None


def _s0_contexts(component):
    if not isinstance(component, dict) or component.get('type') != 'controls-profile':
        _reject('component-type-unsupported')
    declared = component.get('contexts')
    if not isinstance(declared, list):
        _reject('component-contexts-invalid')
    keys = set()
    for row in declared:
        if (not isinstance(row, dict) or set(row) != {'season', 'mode'}
                or not isinstance(row['season'], str)
                or not isinstance(row['mode'], str)):
            _reject('component-contexts-invalid')
        key = f"{row['season']}:{row['mode']}"
        if key in keys:
            _reject('component-contexts-invalid')
        keys.add(key)
    if not keys or not keys <= SUPPORTED_CONTEXTS:
        _reject('component-context-unsupported')
    return keys


def _json(raw):
    try:
        return files._json(raw)
    except (ValueError, TypeError) as error:
        _reject('entrypoint-invalid-json')


def _search_region(value, name):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(part) not in (int, float) for part in value)):
        _reject(name + '-invalid')
    try:
        region = normalized_region(value)
    except OverflowError:
        _reject(name + '-invalid')
    if region is None:
        _reject(name + '-invalid')
    return tuple(region)


def _floor_contract(key, value):
    if not isinstance(value, dict) or set(value) != {'floors', 'visualOrder'}:
        _reject('context-invalid')
    season, mode = key.split(':', 1)
    expected = floor_contract(mode, season=season)
    if expected is None:
        _reject('context-unsupported')
    floors = value['floors']
    if not isinstance(floors, list) or not floors:
        _reject('floors-invalid')
    by_value = {}
    seen_ids = set()
    for row in floors:
        if (not isinstance(row, dict) or set(row) != _FLOOR_FIELDS
                or type(row['observedValue']) is not int
                or not isinstance(row['floorId'], str)
                or not _FLOOR_ID.fullmatch(row['floorId'])):
            _reject('floor-invalid')
        observed, floor_id = row['observedValue'], row['floorId']
        if observed in by_value or floor_id in seen_ids:
            _reject('floor-duplicate')
        by_value[observed] = floor_id
        seen_ids.add(floor_id)
    expected_values = set(expected.observed_values)
    if set(by_value) != expected_values:
        _reject('floor-contract-mismatch')

    order = value['visualOrder']
    if (not isinstance(order, list) or len(order) != len(expected.observed_values)
            or any(type(item) is not int for item in order)
            or len(set(order)) != len(order)
            or set(order) != set(expected.observed_values)):
        _reject('visual-order-invalid')
    # The S0 detector supports these observations. The package owns the IDs;
    # consumers that bind them to actual floors must validate that identity.
    contract = FloorContract(
        season, mode,
        tuple(FloorDefinition(value, by_value[value])
              for value in expected.observed_values),
        expected.control_axis,
    )
    return contract, tuple(order)


def _npy_vectors(raw):
    """Validate an NPY header and exact bounded payload before creating a view."""
    try:
        handle = BytesIO(raw)
        version = np.lib.format.read_magic(handle)
        if version not in ((1, 0), (2, 0), (3, 0)):
            _reject('npy-version-unsupported')
        shape, fortran_order, dtype = np.lib.format._read_array_header(
            handle, version, max_header_size=_MAX_NPY_HEADER)
    except files.ImportRejected:
        raise
    except (ValueError, EOFError, TypeError, OverflowError, struct.error) as error:
        raise files.ImportRejected('imported-controls-npy-header-invalid',
                                   code='imported-controls-npy-header-invalid') from error
    if (not isinstance(shape, tuple) or len(shape) != 2
            or type(shape[0]) is not int or type(shape[1]) is not int
            or not 0 < shape[0] <= _MAX_TEMPLATES or shape[1] != HEIGHT * WIDTH
            or fortran_order is not False or not isinstance(dtype, np.dtype)
            or dtype.hasobject or dtype.kind != 'f' or dtype.itemsize != 4):
        _reject('npy-shape-or-dtype-invalid')
    payload_bytes = shape[0] * shape[1] * np.dtype(np.float32).itemsize
    payload_start = handle.tell()
    if payload_bytes > files.MAX_FILE_BYTES or len(raw) - payload_start != payload_bytes:
        _reject('npy-payload-length-invalid')
    try:
        vectors = np.frombuffer(raw, dtype=dtype, count=shape[0] * shape[1],
                                offset=payload_start).astype(np.float32, copy=True)
        vectors = vectors.reshape(shape)
    except (ValueError, TypeError, OverflowError) as error:
        raise files.ImportRejected('imported-controls-npy-payload-invalid',
                                   code='imported-controls-npy-payload-invalid') from error
    if not np.isfinite(vectors).all():
        _reject('npy-nonfinite')
    means = vectors.mean(axis=1, dtype=np.float64)
    norms = np.linalg.norm(vectors.astype(np.float64), axis=1)
    # float32 serialization of the current normalized bank stays far inside
    # these tolerances; they catch unnormalized data without rejecting rounding.
    if np.any(np.abs(means) > 1e-4) or np.any(np.abs(norms - 1.0) > 2e-4):
        _reject('npy-vector-normalization-invalid')
    return vectors


class S0ControlsProfile:
    """Validated S0 profile from one controls-profile PackageView."""

    def __init__(self, view: PackageView):
        if not isinstance(view, PackageView):
            _reject('package-view-required')
        self._view, self._root = view, view.root
        self.detector_id = DETECTOR_ID
        component = view.component
        declared_s0 = _s0_contexts(component)
        raw = view.read(component['entrypoint'])
        value = _json(raw)
        if not isinstance(value, dict) or set(value) != _ENTRY_FIELDS:
            _reject('entrypoint-fields-invalid')
        if value['schema'] != SCHEMA or value['detectorId'] != DETECTOR_ID:
            _reject('detector-unsupported')
        contexts = value['contexts']
        if not isinstance(contexts, dict) or set(contexts) != declared_s0:
            _reject('contexts-component-mismatch')
        contracts = {}
        orders = {}
        for key in sorted(declared_s0):
            contracts[key], orders[key] = _floor_contract(key, contexts[key])

        fixed = _search_region(value['fixedSearchRegion'], 'fixed-search-region')
        expanded = _search_region(value['expandedSearchRegion'], 'expanded-search-region')
        bank_meta = value['templateBank']
        if not isinstance(bank_meta, dict) or set(bank_meta) != _BANK_FIELDS:
            _reject('template-metadata-invalid')
        path = bank_meta['path']
        if (not isinstance(path, str) or not path or len(path) > 240
                or '\\' in path or path.startswith('/') or ':' in path):
            _reject('template-path-invalid')
        if bank_meta['shapeHW'] != [HEIGHT, WIDTH] or bank_meta['digitHeight'] != 40:
            _reject('template-shape-unsupported')
        labels = bank_meta['labels']
        if (not isinstance(labels, list) or not labels or len(labels) > _MAX_TEMPLATES
                or any(type(label) is not int or label not in LABELS for label in labels)
                or set(labels) != set(LABELS)):
            _reject('template-labels-incomplete')
        family = bank_meta['family']
        if not isinstance(family, str) or not _FAMILY.fullmatch(family):
            _reject('template-family-invalid')

        # All payload access is through PackageView so declaration, digest,
        # stable-file identity, and an optional catalog guard remain active.
        vectors = _npy_vectors(view.read(path))
        if len(labels) != vectors.shape[0]:
            _reject('template-label-count-mismatch')

        vectors = np.frombuffer(vectors.tobytes(order='C'), dtype=np.float32).reshape(vectors.shape)
        label_array = np.frombuffer(np.asarray(labels, dtype=np.int32).tobytes(), dtype=np.int32)
        self.contracts = MappingProxyType(contracts)
        self.visual_orders = MappingProxyType(orders)
        self.fixed_search_region = fixed
        self.expanded_search_region = expanded
        self.templates = TemplateBank(vectors, label_array, tuple(LABELS), family)
        self.assert_current()

    def assert_current(self):
        if self._view.root != self._root:
            _reject('component-root-changed')

    def for_context(self, season, mode):
        """Return the declared S0 contract/order, or None for unsupported data."""
        self.assert_current()
        if not isinstance(season, str) or not isinstance(mode, str):
            return None
        key = f'{season}:{mode}'
        contract = self.contracts.get(key)
        if contract is None:
            return None
        return ImportedControlsContext(key, contract, self.visual_orders[key])

    def observation_parameters(self, season, mode):
        """Caller must check this profile again before returning pixel evidence."""
        return _S0ObservationControls(self.for_context(season, mode), self.templates,
                                      self.fixed_search_region, self.expanded_search_region)
