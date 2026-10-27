from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Any, Callable, Hashable


DEFAULT_UI_FRAME_BUDGET_MS = 6.0


class UiActionPhase(str, Enum):
    """Lifecycle of one user intent as observed by the UI thread."""

    VISUAL_CONFIRMED = "visual-confirmed"
    BACKGROUND_ENQUEUED = "background-enqueued"
    SETTLED = "settled"
    FAILED = "failed"
    CANCELLED = "cancelled"


PENDING_PHASES = frozenset({
    UiActionPhase.VISUAL_CONFIRMED,
    UiActionPhase.BACKGROUND_ENQUEUED,
})


@dataclass(frozen=True)
class UiActionToken:
    action: str
    generation: int


@dataclass(frozen=True)
class UiActionSnapshot:
    token: UiActionToken
    phase: UiActionPhase
    target_state: Any
    visible_state: Any
    input_at_ms: float
    visual_at_ms: float
    enqueued_at_ms: float | None
    settled_at_ms: float | None
    coalesced_inputs: int
    reason: str | None

    @property
    def visual_latency_ms(self) -> float:
        return max(0.0, self.visual_at_ms - self.input_at_ms)

    @property
    def enqueue_latency_ms(self) -> float | None:
        if self.enqueued_at_ms is None:
            return None
        return max(0.0, self.enqueued_at_ms - self.input_at_ms)

    @property
    def settle_latency_ms(self) -> float | None:
        if self.settled_at_ms is None:
            return None
        return max(0.0, self.settled_at_ms - self.input_at_ms)

    @property
    def background_latency_ms(self) -> float | None:
        if self.enqueued_at_ms is None or self.settled_at_ms is None:
            return None
        return max(0.0, self.settled_at_ms - self.enqueued_at_ms)


@dataclass(frozen=True)
class UiActionDecision:
    snapshot: UiActionSnapshot
    accepted: bool
    coalesced: bool = False
    stale: bool = False
    should_enqueue_background: bool = False
    attempted_token: UiActionToken | None = None

    @property
    def token(self) -> UiActionToken:
        # A rejected completion returns the current snapshot so callers can
        # render the authoritative state, but its decision token must still
        # identify the stale request that was rejected.
        return self.attempted_token or self.snapshot.token


@dataclass
class _MutableAction:
    token: UiActionToken
    phase: UiActionPhase
    target_state: Any
    visible_state: Any
    input_at_ms: float
    visual_at_ms: float
    enqueued_at_ms: float | None = None
    settled_at_ms: float | None = None
    coalesced_inputs: int = 0
    reason: str | None = None


class ImmediateUiActionStateMachine:
    """Separate immediate feedback from asynchronous final state.

    The object is intentionally UI-toolkit agnostic. ``begin`` is called on
    the UI thread and records the visible acknowledgement in the same turn.
    Workers return only tokens; their completion must be marshalled back to
    the UI thread before calling ``settle`` or ``fail``.

    A repeated intent for the same target is coalesced. A different target
    advances the generation, so a late result from the superseded intent can
    never overwrite what the player currently sees.
    """

    def __init__(self, *, clock_ms: Callable[[], float] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: time.perf_counter() * 1000.0)
        self._generations: dict[str, int] = {}
        self._actions: dict[str, _MutableAction] = {}

    def _time(self, now_ms: float | None) -> float:
        return float(self._clock_ms() if now_ms is None else now_ms)

    @staticmethod
    def _snapshot(record: _MutableAction) -> UiActionSnapshot:
        return UiActionSnapshot(
            token=record.token,
            phase=record.phase,
            target_state=record.target_state,
            visible_state=record.visible_state,
            input_at_ms=record.input_at_ms,
            visual_at_ms=record.visual_at_ms,
            enqueued_at_ms=record.enqueued_at_ms,
            settled_at_ms=record.settled_at_ms,
            coalesced_inputs=record.coalesced_inputs,
            reason=record.reason,
        )

    def snapshot(self, action: str) -> UiActionSnapshot | None:
        record = self._actions.get(action)
        return self._snapshot(record) if record is not None else None

    def begin(
        self,
        action: str,
        *,
        target_state: Any,
        visual_state: Any,
        now_ms: float | None = None,
    ) -> UiActionDecision:
        action = str(action).strip()
        if not action:
            raise ValueError("action must not be empty")
        now = self._time(now_ms)
        current = self._actions.get(action)
        if (
            current is not None
            and current.phase in PENDING_PHASES
            and current.target_state == target_state
        ):
            current.coalesced_inputs += 1
            return UiActionDecision(
                snapshot=self._snapshot(current),
                accepted=True,
                coalesced=True,
                should_enqueue_background=False,
            )

        generation = self._generations.get(action, 0) + 1
        self._generations[action] = generation
        record = _MutableAction(
            token=UiActionToken(action=action, generation=generation),
            phase=UiActionPhase.VISUAL_CONFIRMED,
            target_state=target_state,
            visible_state=visual_state,
            input_at_ms=now,
            visual_at_ms=now,
        )
        self._actions[action] = record
        return UiActionDecision(
            snapshot=self._snapshot(record),
            accepted=True,
            should_enqueue_background=True,
        )

    def _current_pending(self, token: UiActionToken) -> _MutableAction | None:
        current = self._actions.get(token.action)
        if (
            current is None
            or current.token != token
            or current.phase not in PENDING_PHASES
        ):
            return None
        return current

    def accepts_background_result(self, token: UiActionToken) -> bool:
        return self._current_pending(token) is not None

    def mark_enqueued(
        self,
        token: UiActionToken,
        *,
        now_ms: float | None = None,
    ) -> UiActionDecision:
        current = self._current_pending(token)
        if current is None:
            return self._rejected(token)
        if current.phase == UiActionPhase.BACKGROUND_ENQUEUED:
            return UiActionDecision(
                snapshot=self._snapshot(current),
                accepted=True,
                coalesced=True,
            )
        now = self._time(now_ms)
        if now < current.input_at_ms:
            raise ValueError("enqueue timestamp precedes input")
        current.phase = UiActionPhase.BACKGROUND_ENQUEUED
        current.enqueued_at_ms = now
        return UiActionDecision(snapshot=self._snapshot(current), accepted=True)

    def settle(
        self,
        token: UiActionToken,
        *,
        final_state: Any | None = None,
        now_ms: float | None = None,
    ) -> UiActionDecision:
        current = self._current_pending(token)
        if current is None:
            return self._rejected(token)
        now = self._time(now_ms)
        if now < current.input_at_ms:
            raise ValueError("settle timestamp precedes input")
        if current.enqueued_at_ms is not None and now < current.enqueued_at_ms:
            raise ValueError("settle timestamp precedes background enqueue")
        current.phase = UiActionPhase.SETTLED
        current.visible_state = (
            current.target_state if final_state is None else final_state
        )
        current.settled_at_ms = now
        current.reason = None
        return UiActionDecision(snapshot=self._snapshot(current), accepted=True)

    def fail(
        self,
        token: UiActionToken,
        *,
        fallback_state: Any,
        reason: str,
        now_ms: float | None = None,
    ) -> UiActionDecision:
        current = self._current_pending(token)
        if current is None:
            return self._rejected(token)
        now = self._time(now_ms)
        if now < current.input_at_ms:
            raise ValueError("failure timestamp precedes input")
        if current.enqueued_at_ms is not None and now < current.enqueued_at_ms:
            raise ValueError("failure timestamp precedes background enqueue")
        current.phase = UiActionPhase.FAILED
        current.visible_state = fallback_state
        current.settled_at_ms = now
        current.reason = str(reason)
        return UiActionDecision(snapshot=self._snapshot(current), accepted=True)

    def cancel(
        self,
        token: UiActionToken,
        *,
        visible_state: Any | None = None,
        reason: str = "cancelled",
        now_ms: float | None = None,
    ) -> UiActionDecision:
        current = self._current_pending(token)
        if current is None:
            return self._rejected(token)
        now = self._time(now_ms)
        if now < current.input_at_ms:
            raise ValueError("cancel timestamp precedes input")
        if current.enqueued_at_ms is not None and now < current.enqueued_at_ms:
            raise ValueError("cancel timestamp precedes background enqueue")
        current.phase = UiActionPhase.CANCELLED
        if visible_state is not None:
            current.visible_state = visible_state
        current.settled_at_ms = now
        current.reason = str(reason)
        return UiActionDecision(snapshot=self._snapshot(current), accepted=True)

    def _rejected(self, token: UiActionToken) -> UiActionDecision:
        current = self._actions.get(token.action)
        if current is not None:
            snapshot = self._snapshot(current)
        else:
            snapshot = UiActionSnapshot(
                token=token,
                phase=UiActionPhase.CANCELLED,
                target_state=None,
                visible_state=None,
                input_at_ms=0.0,
                visual_at_ms=0.0,
                enqueued_at_ms=None,
                settled_at_ms=None,
                coalesced_inputs=0,
                reason="unknown-action",
            )
        return UiActionDecision(
            snapshot=snapshot,
            accepted=False,
            stale=True,
            attempted_token=token,
        )


@dataclass(frozen=True)
class UiFrameDrainReport:
    processed: int
    stale_discarded: int
    failures: tuple[str, ...]
    remaining: int
    elapsed_ms: float
    budget_ms: float
    budget_exhausted: bool
    budget_overrun_ms: float


@dataclass(frozen=True)
class _ScheduledUiWork:
    key: Hashable
    token: UiActionToken
    callback: Callable[[UiActionToken], None]


class UiFrameBudgetQueue:
    """Thread-safe ingress with a cooperative UI-frame start budget.

    Immediate feedback never enters this queue. Only background completions
    and other deferrable UI work do. Work with the same coalescing key keeps
    only its newest callback, and the consumer can discard stale generations
    before any callback is allowed to mutate the UI. The queue never starts a
    second callback once the budget is spent, but it cannot pre-empt a callback
    already running; integrations must therefore keep callbacks bounded and
    observe ``budget_overrun_ms``.
    """

    def __init__(
        self,
        *,
        budget_ms: float = DEFAULT_UI_FRAME_BUDGET_MS,
        clock_ms: Callable[[], float] | None = None,
        max_items_per_frame: int = 256,
    ) -> None:
        if budget_ms <= 0:
            raise ValueError("budget_ms must be positive")
        if max_items_per_frame <= 0:
            raise ValueError("max_items_per_frame must be positive")
        self.budget_ms = float(budget_ms)
        self.max_items_per_frame = int(max_items_per_frame)
        self._clock_ms = clock_ms or (lambda: time.perf_counter() * 1000.0)
        self._lock = threading.Lock()
        self._pending: OrderedDict[Hashable, _ScheduledUiWork] = OrderedDict()

    def submit(
        self,
        token: UiActionToken,
        callback: Callable[[UiActionToken], None],
        *,
        coalesce_key: Hashable | None = None,
    ) -> bool:
        if not callable(callback):
            raise TypeError("callback must be callable")
        key = coalesce_key if coalesce_key is not None else (
            token.action,
            token.generation,
        )
        item = _ScheduledUiWork(key=key, token=token, callback=callback)
        with self._lock:
            replaced = key in self._pending
            if replaced:
                self._pending.pop(key)
            self._pending[key] = item
        return replaced

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def drain(
        self,
        *,
        accepts: Callable[[UiActionToken], bool] | None = None,
    ) -> UiFrameDrainReport:
        accepts = accepts or (lambda _token: True)
        started = float(self._clock_ms())
        processed = 0
        inspected = 0
        stale_discarded = 0
        failures: list[str] = []

        while inspected < self.max_items_per_frame:
            elapsed = max(0.0, float(self._clock_ms()) - started)
            if inspected > 0 and elapsed >= self.budget_ms:
                break
            with self._lock:
                if not self._pending:
                    break
                _key, item = self._pending.popitem(last=False)
            inspected += 1
            if not accepts(item.token):
                stale_discarded += 1
                continue
            try:
                item.callback(item.token)
                processed += 1
            except Exception as error:  # UI loop reports and continues.
                failures.append(f"{type(error).__name__}: {error}")

        elapsed = max(0.0, float(self._clock_ms()) - started)
        remaining = self.pending_count()
        return UiFrameDrainReport(
            processed=processed,
            stale_discarded=stale_discarded,
            failures=tuple(failures),
            remaining=remaining,
            elapsed_ms=elapsed,
            budget_ms=self.budget_ms,
            budget_exhausted=elapsed >= self.budget_ms,
            budget_overrun_ms=max(0.0, elapsed - self.budget_ms),
        )
