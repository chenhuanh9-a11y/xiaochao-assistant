"""Two independent opacity preferences and bounded UI update scheduling (no Tk)."""
import math


def opacity_percent(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('opacity must be finite')
    return max(0, min(100, round(number)))


def load_opacities(settings, default=0.55):
    """Return overlay/reference alpha; old sheetOpacity remains overlay's key."""
    def alpha(value, fallback):
        try:
            number = float(value)
            return max(0.0, min(1.0, number)) if math.isfinite(number) else fallback
        except (TypeError, ValueError):
            return fallback
    overlay = alpha(settings.get('sheetOpacity'), default)
    return overlay, alpha(settings.get('referenceOpacity'), overlay)


class OpacityUpdates:
    """One latest-only preview timer per surface; one debounced disk-save timer."""
    def __init__(self, schedule, cancel, previews, save):
        self.schedule, self.cancel = schedule, cancel
        self.previews, self.save = previews, save
        self.values, self.pending, self.timers = {}, {}, {}
        self.dirty = set()
        self.save_timer = None
        self.closed = False
        self._flushing_surface = None

    def sync(self, surface, value):
        value = opacity_percent(value)
        if surface not in self.dirty:
            self.values[surface] = value
        return self.values.get(surface, value)

    def change(self, surface, value):
        value = opacity_percent(value)
        if self.closed or surface not in self.previews or self.values.get(surface) == value:
            return
        self.values[surface] = self.pending[surface] = value
        self.dirty.add(surface)
        if surface not in self.timers:
            self.timers[surface] = self.schedule(16, lambda: self.flush(surface))
        if self.save_timer is not None:
            self.cancel(self.save_timer)
        self.save_timer = self.schedule(300, self.commit)

    def external(self, surface, value):
        """Reconcile a controller write without replaying an older queued value.

        A callback from our own flush retains its dirty/save lease. A direct
        controller write supersedes just that surface; the other lane survives.
        """
        value = opacity_percent(value)
        if self.closed or surface not in self.previews:
            return
        self.values[surface] = value
        if self._flushing_surface == surface:
            return
        timer = self.timers.pop(surface, None)
        if timer is not None:
            self.cancel(timer)
        self.pending.pop(surface, None)
        self.dirty.discard(surface)
        if not self.dirty and self.save_timer is not None:
            self.cancel(self.save_timer)
            self.save_timer = None

    def flush(self, surface):
        timer = self.timers.pop(surface, None)
        if timer is not None:
            self.cancel(timer)
        value = self.pending.pop(surface, None)
        if not self.closed and value is not None:
            previous = self._flushing_surface
            self._flushing_surface = surface
            try:
                self.previews[surface](value)
            finally:
                self._flushing_surface = previous

    def commit(self):
        if self.save_timer is not None:
            self.cancel(self.save_timer)
            self.save_timer = None
        if self.closed or not self.dirty:
            return
        for surface in tuple(self.pending):
            self.flush(surface)
        self.save()
        self.dirty.clear()

    def close(self):
        self.closed = True
        for timer in self.timers.values():
            self.cancel(timer)
        if self.save_timer is not None:
            self.cancel(self.save_timer)
        self.timers.clear()
        self.pending.clear()
        self.dirty.clear()
        self.save_timer = None
