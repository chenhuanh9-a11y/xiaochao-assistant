"""Pure bounded ROI-preparation protocol; not an image/identity classifier.

There are no timers, threads, captures, queues, UI calls or trusted poses here.
The caller supplies observations from current pixels and its explicit stability
policy. Success means only: acquire a fresh FULL frame and run original checks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Hashable


@dataclass(frozen=True)
class ProbeTicket:
    window: int
    sequence: int
    generation: int
    issued_at: float


@dataclass(frozen=True)
class ReadyObservation:
    captured_at: float
    context: Hashable
    ready: bool | None
    stability_key: Hashable | None
    evidence_source: str


@dataclass(frozen=True)
class ReadyDecision:
    status: str
    reason: str
    generation: int | None
    deadline_at: float | None

    @property
    def needs_fresh_full_frame(self) -> bool:
        return self.status == 'need-fresh-full-frame'


class MapReadyWindow:
    def __init__(self, *, required_observations: int, stable_seconds: float):
        # No product default is inferred from synthetic data. A caller chooses
        # this temporal policy; it is separate from existing pixel thresholds.
        if isinstance(required_observations, bool) or not isinstance(required_observations, int) or required_observations < 1:
            raise ValueError('required_observations must be a positive integer')
        if not math.isfinite(stable_seconds) or stable_seconds < 0:
            raise ValueError('stable_seconds must be finite and nonnegative')
        self.required_observations = required_observations
        self.stable_seconds = stable_seconds
        self._window = 0
        self._sequence = 0
        self._ticket = None
        self._generation = None
        self._context = None
        self._deadline = None
        self._started_at = None
        self._status = 'idle'
        self._reason = 'not-started'
        self._reset_evidence()

    def _reset_evidence(self):
        self._key = None
        self._count = 0
        self._first_ready_at = None
        self._last_ready_at = None

    def decision(self) -> ReadyDecision:
        return ReadyDecision(self._status, self._reason, self._generation, self._deadline)

    def begin(self, *, generation: int, context: Hashable, now: float, deadline_at: float) -> ReadyDecision:
        if not math.isfinite(now) or not math.isfinite(deadline_at) or deadline_at <= now:
            raise ValueError('preparation requires a future finite absolute deadline')
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError('generation must be an integer owned by the caller')
        hash(context)
        self._window += 1
        # Retain an outstanding physical request across replacement/cancel.
        # Its completion releases the slot, but cannot contribute evidence.
        self._generation, self._context, self._deadline = generation, context, deadline_at
        self._started_at = now
        self._status, self._reason = 'pending', 'await-current-evidence'
        self._reset_evidence()
        return self.decision()

    def poll(self, now: float) -> ReadyDecision:
        if not math.isfinite(now):
            raise ValueError('time must use one finite monotonic domain')
        if self._status == 'pending' and now < self._started_at:
            raise ValueError('observation cannot precede the preparation window')
        if self._status == 'pending' and now >= self._deadline:
            self._status, self._reason = 'expired', 'preparation-deadline'
            self._reset_evidence()
        return self.decision()

    def request_roi(self, now: float) -> ProbeTicket | None:
        self.poll(now)
        if self._status != 'pending' or self._ticket is not None:
            return None
        self._sequence += 1
        self._ticket = ProbeTicket(self._window, self._sequence, self._generation, now)
        return self._ticket

    def observe(self, ticket: ProbeTicket, observation: ReadyObservation, *, now: float) -> ReadyDecision:
        self.poll(now)
        if ticket != self._ticket:
            # An old completion must not clear the new window's in-flight slot.
            return ReadyDecision(self._status, 'obsolete-ticket-ignored', self._generation, self._deadline)
        self._ticket = None
        if self._status != 'pending' or ticket.window != self._window or ticket.generation != self._generation:
            return ReadyDecision(self._status, 'obsolete-completion-drained', self._generation, self._deadline)
        if not math.isfinite(observation.captured_at) or not ticket.issued_at <= observation.captured_at <= now:
            self._reset_evidence()
            return ReadyDecision(self._status, 'stale-or-invalid-capture-time', self._generation, self._deadline)
        if observation.context != self._context:
            return self.cancel('capture-context-changed')
        if observation.ready is not True or observation.stability_key is None:
            self._reset_evidence()
            self._reason = 'uncertain' if observation.ready is None else 'not-ready'
            return self.decision()
        hash(observation.stability_key)
        if observation.stability_key != self._key:
            self._reset_evidence()
            self._key = observation.stability_key
            self._first_ready_at = observation.captured_at
        if self._last_ready_at is not None and observation.captured_at <= self._last_ready_at:
            return ReadyDecision(self._status, 'duplicate-capture-ignored', self._generation, self._deadline)
        self._count += 1
        self._last_ready_at = observation.captured_at
        if self._count >= self.required_observations and observation.captured_at - self._first_ready_at >= self.stable_seconds:
            self._status, self._reason = 'need-fresh-full-frame', 'roi-proposal-only-original-checks-required'
        else:
            self._reason = 'await-stable-current-evidence'
        return self.decision()

    def finish_roi_error(self, ticket: ProbeTicket, *, now: float) -> ReadyDecision:
        """Drain one actual failed request without pretending it supplied pixels."""
        self.poll(now)
        if ticket != self._ticket:
            return ReadyDecision(self._status, 'obsolete-ticket-ignored', self._generation, self._deadline)
        self._ticket = None
        if self._status == 'pending' and ticket.window == self._window:
            self._reset_evidence()
            self._reason = 'roi-capture-error'
        return self.decision()

    def cancel(self, reason: str = 'closed') -> ReadyDecision:
        self._window += 1
        self._status, self._reason = 'cancelled', reason
        self._reset_evidence()
        return self.decision()
