"""One current S1 common-coordinate observation, separate from identity state.

The owner consumes the complete coordinate report between the two jobs and
chooses whether its actual pool needs a fixed fit. Only the owner writes that
pool, round identity or material; this work retains the actual frame and source.
"""
import time
import threading

from entry_evidence_work import EntryEvidenceWork, FrameSnapshot


class CommonCoordinateObservationWork(EntryEvidenceWork):
    def __init__(self, prepared, snapshot, floor, pool_ids, pool_revision, *,
                 cancelled=None, source_cancelled=None):
        if not isinstance(snapshot, FrameSnapshot):
            raise TypeError('common-coordinate-frame-snapshot-required')
        self.prepared, self.catalog, self.floor = prepared, prepared.catalog, str(floor)
        self.pool_ids, self.source_pool_revision = tuple(pool_ids), pool_revision
        if (self.catalog.season != 's1' or self.floor not in self.catalog.floors
                or not self.pool_ids or len(set(self.pool_ids)) != len(self.pool_ids)
                or not set(self.pool_ids) <= set(self.catalog.map_ids)):
            raise ValueError('common-coordinate-observation-scope-invalid')
        self.cancelled = cancelled or (lambda: False)
        self.source_cancelled = source_cancelled or self.cancelled
        self.source_started = self.coordinate_complete = self.ledger_consumed = False
        self._source_lock, self._source_retired = threading.Lock(), False
        self.report, self.pose = None, None
        self.map_id, self.pool_revision = None, None
        self.coordinate_ms = self.fit_ms = None
        super().__init__(snapshot)

    def _stopped(self, source=False):
        return not self.prepared.current() or (self.source_cancelled if source else self.cancelled)()

    def _prepare(self):
        with self._source_lock:
            if self._source_retired:
                return
            self.source_started = True
        began = time.perf_counter()
        self.report = self.prepared.prepare(self.frame, self.pool_ids, self.floor,
            cancelled=lambda: self._stopped(source=True))
        self.coordinate_ms = (time.perf_counter()-began)*1000
        self.coordinate_complete = True

    def retire_unstarted_source(self):
        """Atomically choose fallback history or let the started source finish."""
        with self._source_lock:
            if self.source_started:
                return False
            self._source_retired = True
            return True

    def choose_owner_map(self, choice):
        """Scheduling reply from the actual pool owner, never pose authority."""
        if (not self.coordinate_complete or self._failed or self._stopped()
                or not isinstance(choice, dict) or choice.get('mapId') not in self.pool_ids
                or type(choice.get('poolRevision')) is not int):
            return False
        self.map_id, self.pool_revision = choice['mapId'], choice['poolRevision']
        return True

    def _fit(self):
        if self._stopped():
            return
        began = time.perf_counter()
        self.pose = self.prepared.fixed_fit(self.frame, self.map_id, self.floor,
            lambda: self._stopped(), report=self.report)
        self.fit_ms = (time.perf_counter()-began)*1000

    def _jobs(self):
        yield self._job(None, 'common-coordinate', self._prepare)
        # The scheduler's owner callback has consumed the first job and replied
        # before advancing this generator. No worker reads mutable pool state.
        if self.map_id is not None:
            yield self._job(self.map_id, 'common-coordinate-fixed-fit', self._fit)

    def finish(self):
        self.close()
        return self.report

    def current_fit(self, engine, frame, map_id, floor, pool_revision):
        """A completed producer's fixed fit on these exact immutable pixels."""
        from s1_structure_selection import verified_pose
        return bool(self._complete and not self._failed and self._pending is None
            and self.frame_snapshot.pixels is frame and self.frame is frame
            and self.prepared is getattr(engine, 'qt_s1_coordinate_recognition', None)
            and self.prepared.current() and self.coordinate_complete
            and self.map_id == map_id and self.floor == str(floor)
            and self.pool_revision == pool_revision and self.ledger_consumed
            and isinstance(self.report, dict)
            and (self.report.get('mode'), self.report.get('resourceKey'), self.report.get('floor'))
                == (self.catalog.mode, self.catalog.resource_key, self.floor)
            and verified_pose(self.pose))
