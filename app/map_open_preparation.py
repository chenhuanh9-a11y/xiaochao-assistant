"""Shared original bounded map-open preparation cadence; capture is injected."""
import math
import os
import time

CAPTURE_VISUAL_DEADLINE_SECONDS = 1.05
CAPTURE_READY_PROBE_SECONDS = 0.05

def prepare_map_open_frame(self, hwnd, token, deadline_at, cancelled,
        *, silent_hint=True, round_identity_locked=False, capture_context_provider=None):
    """Check fresh full frames on a bounded start-to-start cadence.

    The former ROI stability check could only request this same full-frame
    check; it never authorized a floor or pose. Avoid its extra capture and
    color extraction. On success the caller retains the capture lane.
    """
    context = self._map_ready_context()
    from client_region_capture import client_capture_context
    capture_context = (capture_context_provider or client_capture_context)(hwnd)
    now = time.perf_counter()
    if now >= deadline_at:
        self._log('map-ready-stopped', cycleId=token, reason='preparation-deadline')
        return None
    full_probe_period = float(self.CAPTURE_READY_PROBE_SECONDS)
    # These cheap full checks replace BOTH the former full probes and ROI
    # captures. Keep the preparation time bounded independently of the
    # later ranking/retry count, without exhausting it during a long fade.
    max_full_probes = max(1, math.ceil(float(self.CAPTURE_VISUAL_DEADLINE_SECONDS) / full_probe_period))
    immediate = bool(round_identity_locked
        and os.environ.get('IDENTITYV_REOPEN_FAST_CAPTURE', '1') != '0')
    next_full_probe_at = now if immediate else now + full_probe_period
    self._log('map-ready-begin', cycleId=token, deadlineAt=deadline_at,
        fullProbePeriodSeconds=full_probe_period, maxFullProbes=max_full_probes,
        firstProbeImmediate=immediate, policy='bounded-full-frame-start-cadence')
    full_probes = 0
    while full_probes < max_full_probes and self._map_ready_current(token, context, cancelled):
        now = time.perf_counter()
        if now >= deadline_at:
            break
        if now < next_full_probe_at:
            cancelled.wait(min(.025, max(0., min(deadline_at, next_full_probe_at)-now)))
            continue
        probe_started = now
        full_probes += 1
        try:
            prepared, stopped = self._capture_map_open_full_probe(
                hwnd, token, context, capture_context, deadline_at, cancelled,
                silent_hint=silent_hint, round_identity_locked=round_identity_locked,
                proposal_source=('locked-first-full-frame' if immediate and full_probes == 1
                                 else 'bounded-full-probe'), full_probe=full_probes)
        except OSError as error:
            self._log('map-ready-full-unavailable', cycleId=token,
                fullProbe=full_probes, error=str(error))
            prepared, stopped = None, False
        if prepared is not None:
            return prepared
        if stopped:
            reason = ('obsolete' if not self._map_ready_current(token, context, cancelled)
                      else 'preparation-deadline' if time.perf_counter() >= deadline_at
                      else 'capture-context-changed')
            self._log('map-ready-stopped', cycleId=token, reason=reason)
            return None
        # Processing consumes part of the interval, rather than being
        # added to it. A slow probe never creates a backlog of catch-ups.
        next_full_probe_at = probe_started + full_probe_period
        if full_probes == max_full_probes - 1:
            # Keep the final allowed probe near the deadline: faster early
            # polling must not use up the budget before a long fade ends.
            work_seconds = max(0.001, time.perf_counter() - probe_started)
            next_full_probe_at = max(next_full_probe_at,
                deadline_at - min(full_probe_period, work_seconds))
    self._log('map-ready-stopped', cycleId=token,
        reason='obsolete' if not self._map_ready_current(token, context, cancelled) else 'preparation-deadline')
    return None
