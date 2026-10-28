"""UI-owned position editing and a native-callback-only scalar mailbox."""
from dataclasses import dataclass, field
import threading


class ReferencePointerMailbox:
    """No Tk/controller objects: native callbacks only publish scalar samples."""
    def __init__(self, generation, hwnd, events):
        self.generation, self.hwnd = int(generation), int(hwnd)
        self._target = events
        self._lock = threading.Lock()
        self._events = []
        self._queued = False
        self._closed = False

    def post(self, phase, x=0, y=0):
        with self._lock:
            if self._closed:
                return False
            sample = (str(phase), int(x), int(y))
            if phase == 'move' and self._events and self._events[-1][0] == 'move':
                self._events[-1] = sample
            elif len(self._events) >= 64:
                self._events[:] = [('cancel', 0, 0)]
                phase = 'cancel'
            else:
                self._events.append(sample)
            notify = not self._queued
            self._queued = True
        if notify:
            self._target.put(dict(kind='reference-pointer', generation=self.generation, hwnd=self.hwnd))
        return phase != 'cancel'

    def take(self):
        with self._lock:
            samples, self._events = self._events, []
            self._queued = False
            return samples if not self._closed else []

    def close(self):
        with self._lock:
            self._closed = True
            self._events.clear()


@dataclass
class ReferencePositionEdit:
    generation: int
    context: tuple
    position: tuple[int, int]
    size: tuple[int, int]
    owner_thread: int = field(default_factory=threading.get_ident)
    active: bool = True
    offset: tuple[int, int] | None = None

    def _assert_owner(self):
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError('Position edits belong to the UI owner')

    def pointer(self, phase, x, y):
        self._assert_owner()
        if not self.active:
            return None
        if phase == 'down':
            self.offset = (int(x)-self.position[0], int(y)-self.position[1])
        elif phase in {'move', 'up'} and self.offset is not None:
            candidate = (int(x)-self.offset[0], int(y)-self.offset[1])
            if phase == 'up':
                self.offset = None
            return candidate
        return None

    def place(self, position):
        self._assert_owner()
        self.position = tuple(map(int, position))

    def cancel(self):
        self._assert_owner()
        self.active, self.offset = False, None
