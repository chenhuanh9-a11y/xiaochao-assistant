"""Ordered map intent and bounded pointer transport, without capture or poses.

An opening/closing key edge is a transition, never an idempotent request. Mouse
samples remain physical screen pixels; their consumer owns hit tests, current
context checks and the existing drag prediction/commit policy.
"""
from __future__ import annotations

from copy import deepcopy
import math
import threading
import time


class OrderedMapInput:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.sequence = 0
        self.open_intent = False

    def reset(self, open_intent=False):
        # Sequence never resets: late events cannot acquire a fresh identity.
        self.open_intent = bool(open_intent)

    def press(self, *, source="hotkey", created_at=None):
        self.open_intent = not self.open_intent
        return self._event("map-key", source, created_at)

    def close(self, *, source="escape", created_at=None):
        self.open_intent = False
        return self._event("map-close", source, created_at)

    def _event(self, kind, source, created_at):
        self.sequence += 1
        return dict(kind=kind, sequence=self.sequence, openIntent=self.open_intent,
                    source=str(source), createdAt=self._clock() if created_at is None else created_at)


class PointerEventMailbox:
    """One queued wake-up, bounded samples, adjacent motion coalescing.

    Like the existing ReferencePointerMailbox, down/up edges stay ordered and
    overflowing the bounded queue cancels instead of inventing missing motion.
    Coalesced motion keeps its final absolute position, so a consumer can derive
    the complete displacement since its last drained sample without losing it.
    """
    def __init__(self, maximum=64):
        if maximum < 3:
            raise ValueError("A pointer mailbox needs room for ordered edges")
        self.maximum = int(maximum)
        self._lock = threading.Lock()
        self._events = []
        self._queued = False

    @staticmethod
    def _gesture(event):
        return event.get("hwnd"), event.get("pointerGeneration"), event.get("gestureId")

    @staticmethod
    def _context(event):
        return {k: v for k, v in (event.get("context") or {}).items() if k != "createdAt"}

    @staticmethod
    def _sample(event):
        return dict(x=event.get("x"), y=event.get("y"), createdAt=event.get("createdAt"))

    def post(self, event):
        event = deepcopy(event)
        if event.get("phase") == "move":
            event.setdefault("motionSamples", [self._sample(event)])
        with self._lock:
            if (event.get("phase") == "move" and self._events
                    and self._events[-1].get("phase") == "move"
                    and self._gesture(event) == self._gesture(self._events[-1])
                    and self._context(event) == self._context(self._events[-1])):
                samples = self._events[-1]["motionSamples"] + event["motionSamples"]
                if len(samples) > self.maximum:
                    self._events[:] = [dict(event, phase="cancel", reason="pointer-motion-overflow", motionSamples=[])]
                else:
                    before = self._events[-1].get("delta", (0, 0))
                    after = event.get("delta", (0, 0))
                    event["delta"] = [before[0] + after[0], before[1] + after[1]]
                    event["motionSamples"] = samples
                    self._events[-1] = event
            elif len(self._events) >= self.maximum:
                self._events[:] = [dict(event, phase="cancel", reason="pointer-queue-overflow")]
            else:
                self._events.append(event)
            notify = not self._queued
            self._queued = True
            return notify

    def take(self):
        with self._lock:
            events, self._events = self._events, []
            self._queued = False
            return events

    def reset(self):
        with self._lock:
            self._events.clear()
            self._queued = False


class DragMotionAccumulator:
    """Apply original per-sample boundary policy to transient physical motion.

    The owner must first reject floor/X clicks and stale map/cycle/geometry.
    No matrix, identity, persistent pose or floor relation is produced here.
    """
    def __init__(self, cursor, client_bounds, normalized_boundary=None):
        from map_pointer_policy import cursor_can_drag_visible_map, normalize_drag_boundary
        self.last = self._point(cursor)
        self.bounds = tuple(client_bounds)
        self.boundary = normalize_drag_boundary(normalized_boundary)
        self.allowed = cursor_can_drag_visible_map(self.last, self.bounds, None, self.boundary)
        if not self.allowed:
            raise ValueError("Pointer press is outside the map drag boundary")
        self.total = [0., 0.]
        self.active = True

    @staticmethod
    def _point(cursor):
        if len(cursor) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                   or not math.isfinite(v) for v in cursor):
            raise ValueError("Finite physical pointer coordinates are required")
        return float(cursor[0]), float(cursor[1])

    def consume(self, event):
        from map_pointer_policy import calibrated_drag_motion_policy
        delta = [0., 0.]
        phase = event.get("phase")
        if phase == "cancel":
            self.active = False
        elif self.active and phase in {"move", "up"}:
            samples = event.get("motionSamples") if phase == "move" else None
            for sample in samples or [event]:
                point = self._point((sample["x"], sample["y"]))
                record, allowed = calibrated_drag_motion_policy(point, self.bounds, self.boundary, self.allowed)
                if record:
                    delta[0] += point[0] - self.last[0]
                    delta[1] += point[1] - self.last[1]
                self.last, self.allowed = point, allowed
            self.total[0] += delta[0]
            self.total[1] += delta[1]
            if phase == "up":
                self.active = False
        return dict(delta=delta, totalDelta=list(self.total), finished=not self.active)
