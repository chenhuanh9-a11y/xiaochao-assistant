"""A single physical resource validator with one replaceable pending request."""
import threading
import time


class ResourceValidationLane:
    def __init__(self):
        self._lock = threading.Lock()
        self._desired = None
        self._pending = None
        self._result = None
        self._running = False
        self._serial = 0

    def submit(self, key, check):
        with self._lock:
            if self._desired is not None and self._desired[1] == key:
                return False
            self._serial += 1
            ticket = (self._serial, key)
            self._desired = ticket
            self._pending = (ticket, check)
            self._result = None
            if self._running:
                return True
            self._running = True
            # Starting under this short lock makes publication atomic with the
            # owner flag; check() always runs outside the scheduling lock.
            try:
                threading.Thread(target=self._run, daemon=True,
                                 name='resource-validation').start()
            except Exception as error:
                self._running = False
                self._pending = None
                self._result = (ticket, self._outcome(key, False, error))
            return True

    @staticmethod
    def _outcome(key, valid, error=None):
        return dict(key=key, valid=valid,
                    error=None if error is None else f'{type(error).__name__}: {error}',
                    completedAt=time.monotonic())

    def _run(self):
        while True:
            with self._lock:
                request = self._pending
                self._pending = None
                if request is None:
                    self._running = False
                    return
            ticket, check = request
            try:
                result = self._outcome(ticket[1], check() is not False)
            except Exception as error:
                result = self._outcome(ticket[1], False, error)
            with self._lock:
                if ticket == self._desired:
                    self._result = (ticket, result)

    def cancel(self):
        with self._lock:
            self._desired = None
            self._pending = None
            self._result = None

    def take(self, key):
        with self._lock:
            if (self._result is None or self._desired is None
                    or self._desired[1] != key or self._result[0] != self._desired):
                return None
            result = self._result[1]
            self._result = None
            # Consuming a completed check permits the scheduler's next recheck.
            self._desired = None
            return result

    def snapshot(self):
        with self._lock:
            return dict(running=self._running, pending=int(self._pending is not None),
                        results=int(self._result is not None))
