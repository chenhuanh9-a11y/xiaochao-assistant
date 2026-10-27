"""Bounded sampling admission; called under the store's short submit lock."""
from collections import OrderedDict


class FailureSampling:
    interval_seconds = 60.0
    max_keys = 512
    max_disk_bytes = 512 * 1024 * 1024

    def __init__(self):
        self.saved = OrderedDict()
        self.pending = set()
        self.retry_at = 0.0
        self.errors = 0
        self.last_status = 'ready'

    def eligible(self, key, now):
        if now < self.retry_at:
            self.last_status = 'write-error-backoff'
            return False
        if key is not None and key in self.pending:
            self.last_status = 'pending'
            return False
        if key is not None and now - self.saved.get(key, float('-inf')) < self.interval_seconds:
            self.last_status = 'sample-cooldown'
            return False
        return True

    def completed(self, key, now, success):
        self.pending.discard(key)
        if success:
            self.saved[key] = now
            self.saved.move_to_end(key)
            while len(self.saved) > self.max_keys:
                self.saved.popitem(last=False)
            self.errors = 0
            self.retry_at = 0.0
            self.last_status = 'saved'
        else:
            self.errors = min(self.errors + 1, 5)
            self.retry_at = now + min(60.0, 2.0 ** self.errors)
            self.last_status = 'write-error-backoff'
