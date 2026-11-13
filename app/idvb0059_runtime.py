"""Isolated 0059 steady experiment, integrated through the existing V2 owner.

The solver owns only a proposal. V2 still owns identity, current-frame authority,
pose commit, cancellation, and rollback. Cached scales are written after a real
successful commit, for that floor only; render caches never authorize a pose.
"""
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
import os
import time
from types import MappingProxyType
import weakref

import cv2
import numpy as np


def enabled():
    return os.environ.get('IDENTITYV_IDVB0059_STEADY', '1') != '0'


def _floor_key(assistant, map_id, floor, frame):
    return (str(map_id), str(floor), tuple(frame.shape[:2]),
            tuple(getattr(assistant, 'current_frame_display_signature', ()) or ()))


def remember_committed(assistant, map_id, floor, frame, fit=None):
    if not enabled() or not assistant._shared_structure_mode_enabled():
        return
    group = assistant._active_round_layer_group(map_id)
    if group is None or str(floor) not in group.canonical_geometry_verified_floors:
        return
    reference = assistant._shared_structure_reference('shared-structure', map_id, floor)
    adapter = assistant.author_coordinate_adapters.matrix_profile_to_canonical(
        'shared-structure', map_id, floor)
    canonical = group.canonical_pose_for_floor(str(floor))
    if reference is None or adapter is None or canonical is None:
        return
    matrix = np.asarray(canonical) @ adapter
    if not np.isfinite(matrix).all() or not np.allclose(
            matrix[:2, :2], np.eye(2)*matrix[0, 0], rtol=0, atol=1e-8):
        return
    state = getattr(assistant, '_idvb0059_scales', None)
    if state is None or state['group']() is not group:
        state = assistant._idvb0059_scales = dict(group=weakref.ref(group), floors={})
    # A separate row per target floor prevents a later floor's scale estimate
    # from becoming this floor's seed through the shared display root.
    key = _floor_key(assistant, map_id, floor, frame)
    previous = state['floors'].get(key) or {}
    previous_coverage = (previous.get('coverage')
                         if previous.get('reference') is reference else None)
    coverage = (fit or {}).get('idvb0059Coverage')
    if not isinstance(coverage, dict):
        # First alignment and original recovery also feed the same milestones.
        # A fallback success is not a completed 0059 refresh, so it must not
        # consume an outstanding refresh request or erase prior coverage.
        from idvb0059_steady import observe_coverage
        try:
            coverage = observe_coverage(frame, reference, matrix,
                                        coverage_state=previous_coverage)
        except (ValueError, TypeError, cv2.error) as error:
            coverage = previous_coverage
            assistant._log('idvb0059-coverage-unavailable', mapId=map_id,
                           floor=str(floor), reason=str(error))
    state['floors'][key] = dict(reference=reference, scale=float(matrix[0, 0]),
                                coverage=coverage)


def _alignment_prior(assistant, frame, map_id, floor):
    """Select the existing owner-qualified prior outside pure computation."""
    if (not enabled() or not assistant._shared_structure_mode_enabled()
            or assistant._open_cycle_transient_motion_active(map_id)):
        return None
    group = assistant._active_round_layer_group(map_id)
    state = getattr(assistant, '_idvb0059_scales', None)
    if (state is None or state['group']() is not group or group is None
            or str(floor) not in group.canonical_geometry_verified_floors):
        return None
    row = state['floors'].get(_floor_key(assistant, map_id, floor, frame))
    reference = assistant._shared_structure_reference('shared-structure', map_id, floor)
    if row is None or row['reference'] is not reference:
        return None
    return reference, row, group


def _freeze_coverage(value):
    if isinstance(value, Mapping):
        return MappingProxyType({_freeze_coverage(key): _freeze_coverage(item) for key, item in value.items()})
    if isinstance(value, np.ndarray):
        return np.frombuffer(value.tobytes(), dtype=value.dtype).reshape(value.shape)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_coverage(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool, np.generic)):
        return value
    raise TypeError('0059 coverage must contain numerical data')


def _coverage_copy(value):
    if isinstance(value, Mapping):
        return {key: _coverage_copy(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        return value.copy()
    if isinstance(value, tuple):
        return tuple(_coverage_copy(item) for item in value)
    return value


@dataclass(frozen=True)
class PreparedIdvb0059Input:
    """One numerical prior. No adapter, group, lock or lifetime callback.

    Keep the prepared RAM reference itself: its existing identity scopes the
    original coverage milestones. Capturing a prior does not authorize its
    result; the runtime separately checks its context and adapter generation.
    """
    map_id: str
    floor: str
    frame_shape: tuple
    reference: object
    scale: float
    coverage: object
    coordinate_pack_id: str

    def try_alignment(self, host, frame, map_id, floor, *, cancelled=None):
        if ((str(map_id), str(floor)) != (self.map_id, self.floor)
                or tuple(getattr(frame, 'shape', ())[:2]) != self.frame_shape):
            return None
        solved = _solve_prior(host, frame, map_id, floor, self.reference, self.scale,
                              _coverage_copy(self.coverage), cancelled)
        if not isinstance(solved, _SolvedPrior):
            return solved
        # The live _Owner's old outer spec guard checked this after solving.
        # The prepared path keeps that numerical check without querying it.
        if not np.isfinite(solved.matrix).all():
            return None
        return _wrap_prior(solved, self.coordinate_pack_id)


@dataclass(frozen=True)
class _SolvedPrior:
    result: dict
    diagnostics: dict
    matrix: np.ndarray
    started: float


def _solve_prior(assistant, frame, map_id, floor, reference, scale, coverage, cancelled):
    """The original numerical solve and diagnostic observer, with no owner reads."""
    from idvb0059_steady import solve
    started = time.perf_counter()
    result = solve(frame, reference, scale, cancelled=cancelled, coverage_state=coverage)
    diagnostics = dict(result['diagnostics'])
    assistant._log('idvb0059-steady', mapId=map_id, floor=str(floor),
                   accepted=result['accepted'], reason=result['reason'],
                   diagnostics=diagnostics)
    if cancelled and cancelled():
        return dict(success=False, reason='structure-work-obsolete')
    if not result['accepted']:
        return None  # Caller alone owns the original recovery route.
    matrix = np.eye(3)
    matrix[:2] = result['matrixSourceToClient']
    return _SolvedPrior(result, diagnostics, matrix, started)


def _usable_spec(spec):
    return bool(spec is not None and spec.get('profileBridgeVerified')
        and spec.get('coordinatePackId') and spec.get('basis') == 'game-rendered-shared-structure')


def _wrap_prior(solved, coordinate_pack_id):
    result, diagnostics, matrix = solved.result, solved.diagnostics, solved.matrix
    # This evidence explicitly describes VPSG3's current-frame verifier, not a
    # residual computation that did not run. Its source remains canonical.
    residual = dict(verified=True,
        validationBasis='shared-canonical-geometry-current-frame',
        validationSensorBasis='game-rendered-shared-structure',
        verificationMethod='idvb0059-vpsg3-current-frame',
        coordinatePackId=coordinate_pack_id,
        poseVerifiedAgainstCanonicalGeometry=True,
        profileRenderDerivedFromCanonical=True,
        poseVerifiedAgainstSharedStructure=True,
        poseVerifiedAgainstRenderedProfile=False, routeProfileIndependent=True,
        idvb0059=diagnostics)
    return dict(success=True, reason='idvb0059-steady',
        matrixSourceToClient=matrix, coordinateSpace='shared-structure-to-client',
        routeProfileId='shared-structure', selectedRouteCurrentFrameResidual=residual,
        idvb0059Diagnostics=diagnostics,
        idvb0059Coverage=result.get('coverageState'),
        totalMs=(time.perf_counter()-solved.started)*1000)


def try_alignment(assistant, frame, map_id, floor, *, cancelled=None):
    """Compatibility owner entry; retain dynamic prior/spec validation."""
    prior = _alignment_prior(assistant, frame, map_id, floor)
    if prior is None:
        return None
    reference, row, _ = prior
    solved = _solve_prior(assistant, frame, map_id, floor, reference, row['scale'],
                          row['coverage'], cancelled)
    if not isinstance(solved, _SolvedPrior):
        return solved
    spec = assistant._route_profile_validation_spec(
        'shared-structure', map_id, floor, solved.matrix, reference.clean_mask)
    if not _usable_spec(spec):
        return None
    return _wrap_prior(solved, spec['coordinatePackId'])


def build_display(assistant, display, matrix, frame_shape, identity_key, **options):
    """Retain full local artwork; materialize only the compatibility canvas."""
    if (not enabled() or set(options) - {'minimum_coverage', 'minimum_visible_pixels'}
            or float(options.get('minimum_coverage', .08)) != 0):
        return None
    from idvb0059_display import RetainedDisplayCache
    cache = getattr(assistant, '_idvb0059_pixels', None)
    if cache is None:
        cache = assistant._idvb0059_pixels = RetainedDisplayCache()
    output, packet, diagnostics = cache.build(display, matrix, frame_shape, identity_key)
    if output is None:
        return None
    visible = cv2.countNonZero(cv2.inRange(cv2.extractChannel(output, 3), 24, 255))
    required = max(1, int(options.get('minimum_visible_pixels', 500)))
    success = visible >= required
    metadata = dict(success=success, reason='idvb0059-retained-display' if success
        else 'shared-display-no-visible-ink', visiblePixels=visible,
        inkCoverage=visible/max(output.shape[0]*output.shape[1], 1),
        coverageBasis='typed-reference-canvas-footprint', sourceUnmodified=True,
        idvb0059Display=diagnostics)
    if not success:
        return None, metadata
    packets = getattr(assistant, '_idvb0059_packets', None)
    if packets is None:
        packets = assistant._idvb0059_packets = OrderedDict()
    for key, (owner, _) in tuple(packets.items()):
        if owner() is None:
            packets.pop(key, None)
    packets[id(output)] = (weakref.ref(output), packet)
    while len(packets) > 8:
        packets.popitem(last=False)
    return output, metadata


def packet_for(assistant, output):
    if not enabled():
        return None
    row = getattr(assistant, '_idvb0059_packets', {}).get(id(output))
    return row[1] if row is not None and row[0]() is output else None


def transfer_display_packet(assistant, source, output):
    """Transfer ownership only to an identical full-canvas ndarray view.

    This is explicit at the provenance-wrapper boundary, not inherited through
    ndarray copies or arbitrary views. Consumer authorization remains separate.
    """
    if not enabled() or not isinstance(source, np.ndarray) or not isinstance(output, np.ndarray):
        return False
    packets = getattr(assistant, '_idvb0059_packets', {})
    row = packets.get(id(source))
    if row is None or row[0]() is not source:
        return False
    if (source.shape != output.shape or source.dtype != output.dtype
            or source.strides != output.strides
            or source.ctypes.data != output.ctypes.data
            or not np.shares_memory(source, output)):
        return False
    if source is not output:
        packets.pop(id(source))
        packets[id(output)] = (weakref.ref(output), row[1])
    return True


def _release_surface(assistant, surface):
    surface.hide()
    with assistant._display_capture_lock:
        if assistant._display_capture_active:
            assistant._deferred_surface_closes.append(('idvb0059-retained', surface, 'retained-evicted'))
        else:
            assistant._close_display_surface_now('idvb0059-retained', 'retained-evicted', surface=surface)


def select_surface(assistant, packet):
    from identityv_live_mvp import LayeredOverlay
    current = assistant.aligned_overlay
    if not isinstance(current, LayeredOverlay):
        return current  # Replay supplies the same consumer API, without HWNDs.
    surfaces = getattr(assistant, '_idvb0059_surfaces', None)
    if surfaces is None:
        surfaces = assistant._idvb0059_surfaces = OrderedDict()
    key = packet.identity_key
    surface = surfaces.get(key)
    if surface is None:
        # Reuse the initial surface; subsequent floors get independent native
        # layers. Inactive layers stay hidden and own no geometry authority.
        surface = current if not surfaces else LayeredOverlay()
        surfaces[key] = surface
    if surface is not current:
        current.hide()
        assistant._protect_display_window('aligned_overlay', surface.hwnd)
        assistant.aligned_overlay = surface
    surfaces.move_to_end(key)
    while len(surfaces) > 3:
        _, expired = surfaces.popitem(last=False)
        if expired is not surface:
            _release_surface(assistant, expired)
    return surface


def close_surfaces(assistant):
    surfaces = getattr(assistant, '_idvb0059_surfaces', {})
    for surface in list(surfaces.values()):
        if surface is not getattr(assistant, 'aligned_overlay', None):
            _release_surface(assistant, surface)
    surfaces.clear()
    cache = getattr(assistant, '_idvb0059_pixels', None)
    if cache is not None:
        cache.clear()
    getattr(assistant, '_idvb0059_packets', {}).clear()
