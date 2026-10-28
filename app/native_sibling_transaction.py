"""Isolate a native group switch until its new surface has accepted the pixels.

The source core, grant and HWND are never used as scratch space. This boundary
is synchronous under the owner's alignment lock; workers still only propose fits.
"""
import copy
from functools import wraps


_MISSING = object()


class _UnpaintedSurface:
    visible = False

    def hide(self):
        pass


def new_surface():
    from identityv_live_mvp import LayeredOverlay
    return LayeredOverlay()


class NativeSiblingTransaction:
    # Only projections written before the paint/identity commit boundary.
    FIELDS = (
        'gate', 'round_layer_group', 'auto_lock_pending',
        'native_similarity_state', '_native_similarity_priority',
        '_native_similarity_condition_context', 'front_group_sibling_pending',
        'recovery_anchor_trackers', 'recovery_anchor_tracker',
        'recovery_anchor_tracker_key', '_alignment_v2_worker_failure_state',
        'overlay_display_signature', 'map_cycle_state',
        'last_spatial_route_hidden_reason',
    )

    def __init__(self, owner):
        self.owner = owner
        self.before = {key: getattr(owner, key, _MISSING) for key in self.FIELDS}
        self.core = owner.alignment_core_v2
        self.source_surface = getattr(owner, 'aligned_overlay', None)
        self.target_surface = None
        self.placeholder = _UnpaintedSurface()
        self.source_id = owner.gate.confirmed_id
        self.target_id = owner.front_group_sibling_pending['toMemberId']
        self.context = self.context_key()
        self.request_generation = getattr(owner, 'alignment_request_generation', None)
        self.pending_before = getattr(owner, 'front_group_sibling_pending', None)
        self.committed = False
        self.staged_core = copy.copy(self.core)
        for key, value in vars(self.core).items():
            if key not in ('_clock', '_event_sink'):
                setattr(self.staged_core, key, copy.deepcopy(value))

    def context_key(self):
        o = self.owner
        return tuple(getattr(o, key, None) for key in (
            'active_map_cycle_id', 'current_floor', 'current_frame_generation',
            'surface_visibility_generation', 'route_profile_id',
            '_alignment_v2_reset_generation', 'game_mode',
        )) + (id(getattr(o, 'engine', None)), tuple(getattr(o, 'current_bounds', ()) or ()),
              tuple(o._active_open_cycle_drag_offset(self.source_id)),
              tuple(o._active_open_cycle_zoom_transform(self.source_id).ravel()),
              tuple(o._active_mouse_drag_preview_offset()))

    def source_current(self):
        o = self.owner
        return (self.context == self.context_key()
                and getattr(getattr(o, 'gate', None), 'confirmed_id', None)
                    in (self.source_id, self.target_id)
                and bool(getattr(o, 'map_is_open', False))
                and not bool(getattr(o, 'map_visual_suppressed', False))
                and bool(getattr(o, 'game_foreground', True)))

    def current(self):
        return (self.source_current() and self.request_generation ==
                getattr(self.owner, 'alignment_request_generation', None))

    def source_authorized(self, authorization_id):
        token = self.core.display_authorization
        return bool(self.source_current() and token is not None
                    and token.authorization_id == authorization_id
                    and self.core.can_display(authorization=token,
                        cycle_id=self.context[0], map_id=self.source_id,
                        floor=self.context[1], frame_generation=self.context[2]))

    def begin(self):
        o = self.owner
        self.staged_values = {}
        for key, value in self.before.items():
            if value is not _MISSING:
                # The gate owns scalar selection, but references the entire
                # resource pack. Never duplicate pack images/locks here.
                setattr(o, key, copy.copy(value) if key == 'gate' else copy.deepcopy(value))
                self.staged_values[key] = getattr(o, key)
        o.alignment_core_v2 = self.staged_core
        o.aligned_overlay = self.placeholder
        o._native_sibling_transaction = self

    def surface(self):
        if self.target_surface is None:
            self.target_surface = new_surface()
            self.owner._protect_display_window('aligned_overlay', self.target_surface.hwnd)
        self.owner.aligned_overlay = self.target_surface
        return self.target_surface

    def identity_prepared(self):
        # The native identity writer replaced its staged object. It is still
        # ours until all promotion checks pass, so rollback must include it.
        for key in ('native_similarity_state', 'front_group_sibling_pending'):
            self.staged_values[key] = getattr(self.owner, key)

    def _release(self, surface):
        if surface is None or surface is self.placeholder:
            return
        from idvb0059_runtime import _release_surface
        _release_surface(self.owner, surface)

    def commit(self):
        if not self.current() or self.owner.alignment_core_v2 is not self.staged_core:
            return False
        o = self.owner
        # A user-hidden overlay still commits identity, but must leave a real
        # empty surface rather than the preparation-only placeholder behind.
        if self.target_surface is None:
            self.surface()
        # The old grant stays live until the target paint AND identity succeeded.
        self.core.revoke_display(reason='native-sibling-committed')
        self.core.__dict__.update(self.staged_core.__dict__)
        o.alignment_core_v2 = self.core
        self.committed = True
        o._native_sibling_transaction = None
        if self.source_surface is not None:
            self.source_surface.hide()
        # Keep cached source rasters/regions intact. The fresh target window is
        # registered only now, never by select_surface's pre-paint hide path.
        surfaces = getattr(o, '_idvb0059_surfaces', {})
        released = set()
        packet = getattr(self.target_surface, '_retained_packet', None)
        if packet is not None:
            replaced = surfaces.get(packet.identity_key)
            surfaces[packet.identity_key] = self.target_surface
            if replaced is not None and replaced is not self.source_surface:
                self._release(replaced)
                released.add(id(replaced))
            while len(surfaces) > 3:
                key = next(iter(surfaces))
                expired = surfaces.pop(key)
                if expired is not self.target_surface:
                    self._release(expired)
                    released.add(id(expired))
        if self.source_surface not in surfaces.values() and id(self.source_surface) not in released:
            self._release(self.source_surface)
        o._select_candidate_programmatically(o.gate.confirmed_id,
            cycle_id=o.active_map_cycle_id, preserve_route=True)
        return True

    def rollback(self):
        if self.committed:
            return
        o = self.owner
        valid = self.source_current()
        # Never recycle a token issued to a rejected target. Delayed guards
        # must not accidentally match the next successful authorization.
        for key in ('_next_observation_id', '_next_authorization_id'):
            setattr(self.core, key, max(getattr(self.core, key), getattr(self.staged_core, key)))
        self.staged_core.revoke_display(reason='native-sibling-discarded')
        # A replacement owner/core is a newer transaction: never roll it back.
        if o.alignment_core_v2 is self.staged_core:
            o.alignment_core_v2 = self.core
            for key, value in self.before.items():
                if key in ('gate', 'native_similarity_state', 'auto_lock_pending',
                           'front_group_sibling_pending'):
                    current = getattr(o, key, _MISSING)
                    if current is not self.staged_values.get(key, _MISSING):
                        continue
                if key == 'front_group_sibling_pending':
                    current = getattr(o, key, None)
                    if current is None or (isinstance(current, dict)
                            and current.get('requestId') != self.pending_before.get('requestId')):
                        continue
                if not valid and key in ('gate', 'native_similarity_state',
                        'auto_lock_pending', 'front_group_sibling_pending'):
                    # Respect a close/floor/cancel callback that already
                    # replaced these identities; undo only our staged target.
                    current = getattr(o, key, None)
                    if key == 'gate' and getattr(current, 'confirmed_id', None) not in (
                            self.source_id, (self.before['front_group_sibling_pending'] or {}).get('toMemberId')):
                        continue
                    if key == 'front_group_sibling_pending' and current is None:
                        continue
                    if key == 'native_similarity_state' and (
                            getattr(current, 'generation', None) != getattr(value, 'generation', None)
                            or getattr(current, 'group_id', None) != getattr(value, 'group_id', None)):
                        continue
                if value is _MISSING:
                    o.__dict__.pop(key, None)
                else:
                    setattr(o, key, value)
            o.aligned_overlay = self.source_surface
            if not valid:
                self.core.close(reason='native-sibling-context-expired')
                if self.source_surface is not None:
                    self.source_surface.hide()
        self._release(self.target_surface)
        o._native_sibling_transaction = None


def isolate_native_sibling(method):
    @wraps(method)
    def run(owner, map_id, floor, previous_floor=None, event=None):
        pending = getattr(owner, 'front_group_sibling_pending', None)
        if (not isinstance(pending, dict) or not pending.get('nativeSimilarity')
                or str(map_id) != str(pending.get('toMemberId'))
                or getattr(owner, '_native_sibling_transaction', None) is not None):
            return method(owner, map_id, floor, previous_floor, event)
        data = dict(event or {})
        token = data.get('workerToken')
        data.update(mapId=str(map_id), floor=str(floor), cycleId=(
            token[0] if isinstance(token, (tuple, list)) and token
            else getattr(owner, 'active_map_cycle_id', None)))
        if not owner._front_group_sibling_event_is_current(data,
                source_frame=data.get('structureSourceFrame')):
            return dict(handled=True, success=False, reason='native-sibling-result-stale')
        tx = NativeSiblingTransaction(owner)
        tx.begin()
        try:
            return method(owner, map_id, floor, previous_floor, event)
        finally:
            tx.rollback()
    return run
