"""UI-owned resource grants; a bounded worker alone reads the resource files."""
import copy
import json
import time
from types import SimpleNamespace

from resource_validation_lane import ResourceValidationLane


class NativeResourceValidationMixin:
    # Continuously recheck, with a finite fail-closed lease if the disk stalls.
    # A new scope never inherits this lease from a previous group or resource.
    NATIVE_RESOURCE_GRANT_SECONDS = 0.5

    def _native_resource_binding(self):
        state = getattr(self, 'native_similarity_state', None)
        if state is None or not state.group_id:
            return None
        engine = getattr(self, 'engine', None)
        index = getattr(engine, 'native_similarity_index', None)
        provider = getattr(engine, 'entry_shared_reference_provider', None)
        reference = copy.deepcopy(state.member_reference)
        snapshot = SimpleNamespace(group_id=state.group_id, resource_key=state.resource_key,
            condition_domains=tuple(sorted(state.condition_domains)), member_reference=reference)
        resources = (snapshot.group_id, snapshot.resource_key, snapshot.condition_domains,
                     json.dumps(reference, sort_keys=True, separators=(',', ':')))
        provider_id = (id(getattr(provider, '__self__', None)), id(getattr(provider, '__func__', provider)))
        key = (id(engine), id(index), provider_id, getattr(self, '_native_resource_epoch', 0),
               getattr(self, 'game_mode', None), getattr(self, 'capture_generation', None),
               getattr(self, 'active_map_cycle_id', None), str(getattr(self, 'current_floor', '')),
               id(state), state.generation, resources,
               (tuple(getattr(state, 'member_ids', ())), getattr(state, 'floor', None)))
        return key, snapshot, index, provider

    def _native_similarity_resources_ready(self):
        binding = self._native_resource_binding()
        if binding is None:
            return not getattr(self, '_native_resource_rejected', False)
        grant = getattr(self, '_native_resource_grant', None)
        return bool(not getattr(self, 'closed', False) and getattr(self, 'map_is_open', False)
            and getattr(self, '_map_package_live_request', None) is None
            and grant and grant[0] == binding[0]
            and time.monotonic() - grant[1] <= self.NATIVE_RESOURCE_GRANT_SECONDS)

    def _cancel_native_resource_validation(self):
        lane = getattr(self, '_native_resource_lane', None)
        if lane is not None:
            lane.cancel()
        self._native_resource_epoch = getattr(self, '_native_resource_epoch', 0) + 1
        self._native_resource_grant = None
        self._native_resource_suspended = None
        self._pending_native_similarity_ranked = None
        self._native_resource_recovery = None

    def _queue_native_resource_recovery(self, key):
        frame = getattr(self, 'current_frame', None)
        map_id = getattr(getattr(self, 'gate', None), 'confirmed_id', None)
        if frame is None or not map_id or not getattr(self, 'map_is_open', False):
            return
        self._native_resource_recovery = (key, frame, map_id, str(self.current_floor),
            getattr(self, 'current_frame_generation', None),
            getattr(self, 'surface_visibility_generation', None))

    def _resume_native_resource_recovery(self):
        intent = getattr(self, '_native_resource_recovery', None)
        if not intent or not self._native_similarity_resources_ready():
            return
        key, frame, map_id, floor, generation, surface = intent
        binding = self._native_resource_binding()
        if (not binding or key != binding[0] or frame is not getattr(self, 'current_frame', None)
                or generation != getattr(self, 'current_frame_generation', None)
                or surface != getattr(self, 'surface_visibility_generation', None)
                or map_id != getattr(getattr(self, 'gate', None), 'confirmed_id', None)
                or floor != str(self.current_floor)):
            self._native_resource_recovery = None
            return
        if (getattr(self, 'floor_required', True) or not getattr(self, 'game_foreground', True)
                or getattr(self, 'map_visual_suppressed', False)):
            return
        # This is a new current-frame fit, never a restoration of cached pixels.
        result = self._start_alignment_v2_first_pose_worker(map_id, floor, floor, mode='first-pose')
        if result.get('reason') != 'v2-sensor-start-failed':
            self._native_resource_recovery = None

    def _suspend_native_resource_display(self, key):
        self._queue_native_resource_recovery(key)
        if getattr(self, '_native_resource_suspended', None) == key:
            return
        self._native_resource_suspended = key
        self._invalidate_alignment_work()
        self._alignment_v2_close('similar-group-resource-pending')
        self._hide_spatial_route('similar-group-resource-pending')
        self.current_floor_authorization = None
        self.current_alignment_candidate = None
        self.background_alignment_cache = {}
        self.progressive_pose_seed_cache = {}

    def _invalidate_native_resource_context(self, reason):
        """Known resource mutation revokes even a previously verified member."""
        state = getattr(self, 'native_similarity_state', None)
        affected = bool(state is not None and state.group_id)
        self._cancel_native_resource_validation()
        if not affected:
            return
        self._native_resource_rejected = True
        self._suspend_native_resource_display(('invalid', self._native_resource_epoch))
        self._alignment_v2_reset_round(reason)
        self._clear_round_layer_group(reason)
        self.active_confirmation = None
        self.auto_lock_pending = None
        self.map_identity_pose_pending = False
        hide = getattr(getattr(self, 'gate', None), 'hide_for_new_frame', None)
        if callable(hide):
            hide()
        state.reset()
        self._native_similarity_priority = None
        self._native_similarity_condition_context = None
        self._native_similarity_family = None
        self._native_group_review_floor = None
        self._log('similar-group-reset', reason=reason)

    def _carry_native_resource_grant(self, previous, *, replace_state=False):
        """Only a UI-only member choice/confirmation may retain identical files.

        Worker results still require exact scope equality; this never adopts a
        result from an older generation or extends the validation timestamp.
        """
        binding = self._native_resource_binding()
        grant = getattr(self, '_native_resource_grant', None)
        boundary_length = 8 if replace_state else 9
        if (binding and previous and grant and grant[0] == previous
                and previous[:boundary_length] == binding[0][:boundary_length]
                and previous[10:] == binding[0][10:]):
            self._native_resource_grant = (binding[0], grant[1])

    def _check_native_similarity_resources(self):
        state = getattr(self, 'native_similarity_state', None)
        if state is None:
            return True
        self._native_similarity_floor_changed(self.current_floor)
        binding = self._native_resource_binding()
        if binding is None:
            lane = getattr(self, '_native_resource_lane', None)
            if lane is not None:
                lane.cancel()
            self._native_resource_grant = None
            self._pending_native_similarity_ranked = None
            return not getattr(self, '_native_resource_rejected', False)
        if (getattr(self, 'closed', False) or not getattr(self, 'map_is_open', False)
                or getattr(self, '_map_package_live_request', None) is not None):
            self._cancel_native_resource_validation()
            return False
        key, snapshot, index, provider = binding
        lane = getattr(self, '_native_resource_lane', None)
        if lane is None:
            lane = self._native_resource_lane = ResourceValidationLane()
        result = lane.take(key)
        if result is not None:
            if not result['valid']:
                self._log('similar-group-resource-validation-failed', error=result['error'])
                self._invalidate_native_resource_context('resource-binding-changed')
                return False
            self._native_resource_grant = (key, result['completedAt'])
            self._native_resource_rejected = False
            self._native_resource_suspended = None
        ready = self._native_similarity_resources_ready()
        if not ready:
            self._suspend_native_resource_display(key)

        def validate():
            if index is None or provider is None:
                raise ValueError('similarity-context-changed')
            index.validate_state(snapshot, provider)
        lane.submit(key, validate)

        pending = getattr(self, '_pending_native_similarity_ranked', None)
        if pending and ready:
            self._pending_native_similarity_ranked = None
            pending_key, event, previous = pending
            if (pending_key == key and event.get('token') == self.capture_generation
                    and event.get('frame') is self.current_frame
                    and event.get('rankSourceFrameGeneration') == self.current_frame_generation
                    and event.get('rankSourceSurfaceGeneration') == self.surface_visibility_generation
                    and not getattr(self, 'floor_required', True)):
                self._finish_native_similarity_ranked(event, previous)
                current = self._native_resource_binding()
                if current:
                    self._queue_native_resource_recovery(current[0])
        if ready:
            self._resume_native_resource_recovery()
        return self._native_similarity_resources_ready()
