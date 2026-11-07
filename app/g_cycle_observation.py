"""Opt-in, thread-local observation of existing algorithm inputs. Never captures."""
from contextlib import contextmanager
from contextvars import ContextVar

_observer = ContextVar("g_cycle_observer", default=None)


def observing():
    """Only the current dispatch owns collection; no inherited worker scope."""
    return _observer.get() is not None


@contextmanager
def observe_inputs(observer):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def emit(kind, *values):
    observer = _observer.get()
    if observer is not None:
        try:
            observer(kind, *values)
        except Exception:
            # Evidence must never replace a capture/solver result or exception.
            pass


def copy_input(frame):
    """Preserve existing copy semantics and carry same-capture provenance."""
    copied = frame.copy()
    emit("input-alias", frame, copied)
    return copied
