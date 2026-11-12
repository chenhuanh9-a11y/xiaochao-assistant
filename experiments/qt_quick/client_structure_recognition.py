"""Imported direct-recognition preparation and a bounded compute-only lane.

The engine preparation worker reads the full declared pool once. Per-frame
workers consume its immutable floor indexes and one FrameSnapshot. Results are
collected by the scanner's owning thread; this module never publishes identity,
candidate previews or runtime state, and never treats A/B as lock authority.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import threading
import time

from entry_evidence_work import FrameSnapshot
from structure_identity_index import StructureIdentityIndex
from structure_identity_observation import observe_structure_identity
from structure_identity_recognition import recognize_structure_identity
from structure_identity_resources import build_structure_identity_resources


class _Run:
    def __init__(self, report):
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.report = report
        self.thread = None
        self.error = None

    def collect(self):
        with self.lock:
            if self.error is not None:
                raise self.error
            return copy.deepcopy(self.report)

    def cancel(self):
        self.stopped.set()


class DirectStructureRecognition:
    """One in-flight frame per engine, no executor queue or late callbacks.

    Cancellation is cooperative. An old kernel may drain in the background;
    another frame explicitly reports busy until that single worker finishes.
    Static preparation and resource checks stay outside numerical computation.
    """
    def __init__(self, engine, catalog):
        self.engine, self.catalog = engine, catalog
        self._binding = self._owner()
        # Factories install their revision/context guard after engine assembly.
        self._revision = getattr(engine, 'qt_resource_revision', None)
        self._lock, self._active = threading.Lock(), None
        self.resources, self.indexes = None, {}
        self.preparation = dict(status='unavailable', reason='native-base-unavailable', elapsedMs=0.)

    def _owner(self):
        engine = self.engine
        return (id(getattr(engine, 'qt_native_base', None)),
                id(getattr(engine, 'qt_shared_provider', None)),
                id(getattr(engine, 'qt_fast_structure_source', None)))

    def current(self):
        check = getattr(self.engine, 'qt_resource_context_current', None)
        return (self._binding == self._owner() and (check is None or check())
            and (self._revision is None or self._revision == getattr(self.engine, 'qt_resource_revision', 0)))

    def prepare(self):
        """Called by attach_structure_scanner on the engine preparation worker."""
        if getattr(self.engine, 'qt_native_base', None) is None:
            return
        began = time.perf_counter()
        try:
            if not self.current():
                raise ValueError('resource-context-superseded')
            resources = build_structure_identity_resources(self.engine.qt_native_base,
                                                          self.engine.qt_shared_provider)
            indexes = {}
            for floor in self.catalog.floors:
                # All declared map IDs remain competitors, including absent or
                # legacy layers. A candidate/pruned pool never defines this domain.
                domain = tuple(key for key in resources.index.domain if key.floor == floor)
                entries = tuple(row for row in resources.index.entries if row.key.floor == floor)
                if {key.map_id for key in domain} != set(self.catalog.map_ids):
                    raise ValueError('direct-recognition-catalog-domain-mismatch')
                indexes[floor] = StructureIdentityIndex(domain, entries, directory_complete=True)
            if not self.current():
                raise ValueError('resource-context-superseded')
            self.resources, self.indexes = resources, indexes
            self.preparation = dict(status='prepared', domainCount=len(resources.index.domain),
                searchableCount=sum(info.geometry_searchable for info in resources.sources),
                geometryComplete=resources.geometry_complete,
                directoryGaps=[asdict(row) for row in resources.index.directory_gaps],
                qualificationGaps=list(resources.identity_issues))
        except (OSError, ValueError, TypeError, KeyError) as error:
            # Optional recognition cannot disable the original scanner. Keep the
            # actual preparation failure visible rather than silently falling back.
            self.preparation = dict(status='unavailable', reason=str(error), errorType=type(error).__name__)
        finally:
            self.preparation['elapsedMs'] = (time.perf_counter()-began)*1000

    def _report(self, snapshot, floor, status, **values):
        return dict(status=status, season=self.catalog.season, mode=self.catalog.mode,
            resourceKey=self.catalog.resource_key, floor=floor, frameKey=snapshot.frame_key,
            shapeSnapshotId=snapshot.sha256, authorizesIdentity=False, lock=False,
            preparation=copy.deepcopy(self.preparation), **values)

    def start(self, snapshot, floor, *, cancelled=None, deadline=None):
        if not isinstance(snapshot, FrameSnapshot):
            raise TypeError('direct-recognition-frame-snapshot-required')
        run = _Run(self._report(snapshot, floor, 'pending'))
        if self._revision is None:
            self._revision = getattr(self.engine, 'qt_resource_revision', 0)
        if floor not in self.catalog.floors:
            run.report = self._report(snapshot, floor, 'unavailable', reason='direct-recognition-floor-not-declared')
            return run
        if not self.current():
            run.report = self._report(snapshot, floor, 'cancelled', reason='resource-context-superseded')
            return run
        if self.resources is None:
            run.report = self._report(snapshot, floor, 'unavailable', reason=self.preparation.get('reason'))
            return run
        with self._lock:
            if self._active is not None and self._active.thread.is_alive():
                run.report = self._report(snapshot, floor, 'busy', reason='previous-observation-draining')
                return run
            self._active = run
            run.thread = threading.Thread(target=self._compute,
                args=(run, snapshot, floor, cancelled, deadline),
                name='direct-structure-recognition', daemon=True)
            run.thread.start()
        return run

    def _compute(self, run, snapshot, floor, cancelled, deadline):
        began = time.perf_counter()
        # A call without an observation deadline still has bounded retrieval.
        deadline = min(deadline if deadline is not None else float('inf'), began+1.)

        def obsolete():
            return (run.stopped.is_set() or not self.current()
                or (cancelled is not None and cancelled()) or time.perf_counter() >= deadline)

        try:
            bounds = (0, 0, snapshot.shape[1], snapshot.shape[0])
            observed = observe_structure_identity(snapshot, observation_bounds=bounds, cancelled=obsolete)
            issues = tuple(dict.fromkeys((*self.resources.identity_issues,
                *(issue for row in self.resources.sources if row.key.floor == floor
                  for issue in row.qualification_issues))))
            result = recognize_structure_identity(self.indexes[floor], observed,
                source_qualification_issues=issues, cancelled=obsolete,
                time_budget_ms=max(0., (deadline-time.perf_counter())*1000))
            fingerprint = result.fingerprint
            report = self._report(snapshot, floor, result.status,
                issues=list(result.issues), qualificationGaps=list(result.qualification_gaps),
                measuredChainCount=observed.measured_chain_count,
                aCount=len(observed.a.anchors), bCount=len(observed.b),
                fingerprintUnique=bool(fingerprint and fingerprint.fingerprint_unique),
                retrievalComplete=bool(fingerprint and fingerprint.retrieval_complete),
                directoryGaps=[asdict(row) for row in self.indexes[floor].directory_gaps],
                fingerprintProposals=[asdict(row) for row in fingerprint.proposals] if fingerprint else [],
                poseChecks=[asdict(row) for row in result.pose_checks],
                poseProposals=[asdict(row) for row in result.pose_proposals],
                extractionMs=result.extraction_ms, retrievalMs=result.retrieval_ms,
                heldOutMs=result.held_out_ms,
                computationMs=result.extraction_ms+result.computation_ms,
                elapsedMs=(time.perf_counter()-began)*1000)
            if obsolete():
                report.update(status='cancelled', reason=('time-budget-exhausted'
                    if time.perf_counter() >= deadline else 'cancelled-or-stale'), poseProposals=[])
        except (MemoryError, KeyboardInterrupt, SystemExit) as error:
            with run.lock:
                run.error = error
            return
        except Exception as error:
            report = self._report(snapshot, floor, 'failed', reason=str(error), errorType=type(error).__name__,
                                  elapsedMs=(time.perf_counter()-began)*1000)
        with run.lock:
            run.report = report

    def close(self):
        with self._lock:
            if self._active is not None:
                self._active.cancel()
