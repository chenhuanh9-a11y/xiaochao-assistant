"""Prepared registered corner/ray competition, with independent query state.

The engine preparation worker owns the static snapshot. A recognition worker
returns the existing entry evidence; only the runtime may submit its identity.
This adapter does not upgrade S1 entry purposes or use author images as input.
"""
from copy import copy, deepcopy
import time

from entry_evidence_work import FrameSnapshot, ObservationWorkScheduler
from local_structure_index import LocalStructureIndex
from map_observation_work import LocalStructureObservationWork
from structure_pose_inputs import _freeze, _LocatorTemplates


class PreparedLocalStructureRecognition:
    def __init__(self, engine, catalog, *, cancelled=None):
        from s1_structure_selection import StructureCatalog
        if catalog != StructureCatalog.from_engine(engine):
            raise ValueError('local-structure-catalog-context-mismatch')
        self.engine, self.catalog = engine, catalog
        self._binding = self._owner()
        self._revision = getattr(engine, 'qt_resource_revision', None)
        self._common = getattr(engine, 'qt_pose_common_models', None)
        started = time.perf_counter()
        check = getattr(engine, 'qt_assert_resource_current', None)
        if callable(check):
            check()
        if not self.current() or cancelled is not None and cancelled():
            raise ValueError('resource-context-superseded')
        if self._common is not None:
            self._common.assert_current(engine, getattr(engine, 'qt_resource_revision', 0), cancelled)
            main, side = self._common.locators.new_queries()
            # Common pose templates intentionally contain no per-target images.
            # Recognition resolves a winner through the actual source provider.
            for locator in (main, side):
                locator.structures = locator.shared_reference_provider = engine.qt_shared_provider
        else:
            main, side = copy(engine.entry_identity), copy(engine.side_entry_identity)
            main.entries, side.entries = _freeze(main.entries), _freeze(side.entries)
            for name in ('passports', 'entry_coverage', 'side_doors', 'side_search', 'semantic_gaps'):
                setattr(side, name, _freeze(getattr(side, name)))
        # Finish the original optional lazy icon decoder during preparation.
        if side.semantic_side_entries and side._exit_recognizer is None:
            from side_door_recognition import SideDoorRecognizer
            side._exit_recognizer = SideDoorRecognizer(side.exit_icon_path, read_resource=side.read_resource)
        self._locators = _LocatorTemplates(main, side)
        domain = tuple((mid, floor) for mid in catalog.map_ids for floor in catalog.floors)
        self.index = LocalStructureIndex(side.entries, competition_domain=domain, cancelled=cancelled)
        if callable(check):
            check()
        if not self.current() or cancelled is not None and cancelled():
            raise ValueError('resource-context-superseded')
        self.preparation = dict(algorithm='local-corner-ray-competition',
            domainCount=len(domain), sourceCount=len(self.index.members),
            directoryGaps=list(self.index.directory_gaps), indexBytes=self.index.nbytes,
            lowerBytes=self.index.lower_bytes, elapsedMs=(time.perf_counter()-started)*1000)

    def _owner(self):
        engine = self.engine
        main, side = engine.entry_identity, engine.side_entry_identity
        return (id(getattr(engine, 'qt_native_base', None)),
                id(getattr(engine, 'qt_shared_provider', None)),
                id(getattr(engine, 'qt_fast_structure_source', None)),
                id(main), id(main.entries), id(side), id(side.entries))

    def current(self):
        check = getattr(self.engine, 'qt_resource_context_current', None)
        if self._common is not None:
            from structure_pose_inputs import PoseInputsUnavailable
            try:
                self._common.assert_current(self.engine,
                    getattr(self.engine, 'qt_resource_revision', 0), None)
            except PoseInputsUnavailable:
                return False
        return bool(self._binding == self._owner() and (check is None or check())
            and (self._revision is None or self._revision == getattr(self.engine, 'qt_resource_revision', 0)))

    def _new_query_engine(self):
        # Capture current runtime-bound callbacks, while sharing only immutable
        # prepared geometry and giving each lane its own observation caches.
        query = copy(self.engine)
        query.entry_identity, query.side_entry_identity = self._locators.new_queries()
        recognizer = query.side_entry_identity._exit_recognizer
        if recognizer is not None:
            query.side_entry_identity._exit_recognizer = copy(recognizer)
        return query

    def capture_identity_context(self):
        """Called by the runtime owner before handing either lane to a worker."""
        values = {}
        for name in ('native_similarity_priority_provider', 'native_similarity_context_provider'):
            provider = getattr(self.engine, name, None)
            values[name] = deepcopy(provider()) if callable(provider) else None
        return values

    @staticmethod
    def bind_query_context(query, values):
        for name, value in values.items():
            # Original proposal code may consume mutable context data. Give it
            # its own copy without calling back into the runtime from a worker.
            setattr(query, name, lambda value=value: deepcopy(value))

    def run(self, snapshot, floor, *, cancelled=None, deadline=None, on_entry=None,
            identity_context=None):
        if not isinstance(snapshot, FrameSnapshot):
            raise TypeError('local-structure-frame-snapshot-required')
        if floor not in self.catalog.floors:
            raise ValueError('local-structure-floor-not-declared')
        if self._revision is None:
            self._revision = getattr(self.engine, 'qt_resource_revision', 0)
        started = time.perf_counter()

        def obsolete():
            return not self.current() or cancelled is not None and cancelled()

        report = dict(role='identity-entry', frameKey=snapshot.frame_key,
            resourceKey=self.catalog.resource_key, floor=floor, rows=[],
            algorithm='local-corner-ray-competition')
        if obsolete():
            return dict(report, status='cancelled', elapsedMs=0.)
        if self.catalog.season == 's1':
            return dict(report, status='unavailable', reason='proposal-only-entry-evidence', elapsedMs=0.)
        query = self._new_query_engine()
        self.bind_query_context(query, self.capture_identity_context()
            if identity_context is None else identity_context)
        work = LocalStructureObservationWork(query, snapshot, floor,
            deadline=deadline if deadline is not None else float('inf'),
            prepared_index=self.index.for_floor(floor))
        status = ObservationWorkScheduler(clock=time.perf_counter).drive(
            work, deadline=deadline, cancelled=obsolete)
        rows = work.finish()
        # An unfinished/obsolete lane cannot offer identity behind its status.
        if status == 'complete' and not obsolete():
            report['rows'] = rows
            if on_entry is not None:
                on_entry(report, entry_observation=work)
        else:
            status = 'cancelled' if obsolete() else status
        return dict(report, status=status, elapsedMs=(time.perf_counter()-started)*1000,
                    diagnostics=(getattr(query, 'last_difficult_evidence', None)
                                 if status == 'complete' else None))
