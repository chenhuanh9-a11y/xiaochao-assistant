"""Qt owner bridge for the unchanged default-enabled 0059 steady proposal.

Keep one adapter per runtime. The solver still receives the real typed shared
reference, real round group and original validation-spec producer. This module
does not prepare resources, choose a pose, or grant live/display authority.
"""
import copy
import threading
import numpy as np

from client_paths import APP_ROOT
import sys
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
import idvb0059_runtime as original
from author_display_policy import shared_route_validation_spec


def _copy_state(state):
    if state is None:
        return None
    # Preserve original group weakref and typed-reference object identity.
    # Coverage is a staged value: a cancelled remember cannot mutate a prior.
    return dict(group=state["group"], floors={key: dict(row, coverage=copy.deepcopy(row.get("coverage")))
                for key, row in state["floors"].items()})


class _Owner:
    def __init__(self, host, state, display_signature):
        self._host = host
        self.pack = host.pack
        self.author_coordinate_adapters = host.author_coordinate_adapters
        if display_signature is None:
            display_signature = getattr(host, "current_frame_display_signature", None)
        if display_signature is None:
            raise ValueError("0059 requires a current display-environment signature")
        self.current_frame_display_signature = tuple(display_signature)
        hash(self.current_frame_display_signature)
        self._idvb0059_scales = state

    def __getattr__(self, name):
        return getattr(self._host, name)

    def _route_profile_validation_spec(self, profile, map_id, floor, matrix, route):
        # Same shared branch extracted from the Tk owner, not a fabricated
        # profileBridgeVerified flag or a replacement residual verifier.
        matrix = np.asarray(matrix, dtype=np.float64)
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            return None  # Tk's outer validation-spec guard, before shared branch.
        return shared_route_validation_spec(self, profile, map_id, floor, matrix, route)


class Idvb0059Adapter:
    def __init__(self):
        self._lock = threading.RLock()
        self._generation = self._revision = 0
        self._state = None

    def reset(self):
        """Round/resource/binding replacement discards every remembered prior."""
        with self._lock:
            self._generation += 1
            self._revision += 1
            self._state = None

    def _capture(self, host, display_signature):
        with self._lock:
            return self._generation, self._revision, _Owner(host, _copy_state(self._state), display_signature)

    def _obsolete(self, generation, context, is_current, cancelled=None):
        if cancelled is not None and cancelled():
            return True
        with self._lock:
            if generation != self._generation:
                return True
        return not is_current(context)

    def try_alignment(self, host, frame, map_id, floor, *, context, is_current,
                      cancelled=None, display_signature=None, enabled=True):
        """Run before original fast/recovery; None retains that original fallback.

        ``context`` identifies this immutable worker frame/round/resource epoch.
        ``is_current`` must validate that captured context without changing it.
        The separate stable display signature scopes cached same-floor scales.
        The returned fit is only a proposal for the original residual/core path.
        """
        if not enabled or not original.enabled():
            return None
        generation, _, owner = self._capture(host, display_signature)
        obsolete = lambda: self._obsolete(generation, context, is_current, cancelled)
        if obsolete():
            return dict(success=False, reason="structure-work-obsolete")
        result = original.try_alignment(owner, frame, map_id, str(floor), cancelled=obsolete)
        if obsolete():
            return dict(success=False, reason="structure-work-obsolete")
        return result

    def remember_committed(self, host, frame, map_id, floor, fit=None, *, context,
                           is_current, material_accepted, transient=False,
                           display_signature=None, enabled=True):
        """Call only after actual current core/group AND material acceptance.

        Tk calls remember after its compositor/native transaction succeeds and
        excludes transient display. Qt must therefore call this after a pending
        material ACK commits, not when a proposed fit/render is merely prepared.
        Original per-floor/reference/group checks and coverage measurement run
        unchanged. This cache is never an identity, pose, or live grant.
        """
        if not enabled or not original.enabled() or material_accepted is not True or transient:
            return False
        generation, revision, owner = self._capture(host, display_signature)
        if self._obsolete(generation, context, is_current):
            return False
        group = owner._active_round_layer_group(map_id)
        reference = owner._shared_structure_reference("shared-structure", map_id, str(floor))
        key = original._floor_key(owner, map_id, str(floor), frame)
        previous_row = owner._idvb0059_scales["floors"].get(key) if owner._idvb0059_scales else None
        original.remember_committed(owner, map_id, str(floor), frame, fit)
        state = owner._idvb0059_scales
        if (state is None or key not in state["floors"] or state["group"]() is not group
                or state["floors"][key] is previous_row
                or owner._active_round_layer_group(map_id) is not group
                or owner._shared_structure_reference("shared-structure", map_id, str(floor)) is not reference
                or self._obsolete(generation, context, is_current)):
            return False
        with self._lock:
            if generation != self._generation or revision != self._revision:
                return False
            self._state = state
            self._revision += 1
        return True
