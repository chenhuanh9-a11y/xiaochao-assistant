"""Imported direct-recognition preparation and a bounded compute-only lane.

The engine preparation worker reads the full declared pool once. Per-frame
workers consume its immutable floor indexes and one FrameSnapshot. Results are
collected by the scanner's owning thread; this module never publishes identity,
candidate previews or runtime state, and never treats A/B as lock authority.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, replace
import threading
import time

from entry_evidence_work import FrameSnapshot
from structure_identity_index import StructureIdentityIndex
from structure_identity_observation import StructureIdentityObservation, observe_structure_identity
from structure_identity_recognition import StructureIdentityRecognition, recognize_structure_identity
from structure_identity_resources import build_structure_identity_resources
from structure_pose_inputs import PoseInputBinding


@dataclass(frozen=True)
class DirectStructureRecognitionInputs:
    """The already prepared floor index and its existing resource owner."""
    binding: PoseInputBinding
    index: StructureIdentityIndex
    source_qualification_issues: tuple[str, ...]


@dataclass(frozen=True)
class DirectStructureRecognitionComputation:
    recognition: StructureIdentityRecognition
    observation: StructureIdentityObservation
    binding: PoseInputBinding
    shape_snapshot_id: str
    elapsed_ms: float
    cancellation_reason: str | None = None


def compute_structure_identity(snapshot, inputs, *, cancelled=None, deadline=None):
    """Observe and recognize synchronously, using only prepared RAM inputs.

    The caller owns the worker, resource-current check and result publication.
    This entry never creates another thread or grants identity authority.
    """
    if not isinstance(snapshot, FrameSnapshot):
        raise TypeError('direct-recognition-frame-snapshot-required')
    if not isinstance(inputs, DirectStructureRecognitionInputs):
        raise TypeError('direct-recognition-prepared-inputs-required')
    began = time.perf_counter()
    deadline = min(deadline if deadline is not None else float('inf'), began+1.)

    def obsolete():
        return ((cancelled is not None and cancelled()) or time.perf_counter() >= deadline)

    observed = observe_structure_identity(snapshot,
        observation_bounds=(0, 0, snapshot.shape[1], snapshot.shape[0]), cancelled=obsolete)
    result = recognize_structure_identity(inputs.index, observed,
        source_qualification_issues=inputs.source_qualification_issues, cancelled=obsolete,
        time_budget_ms=max(0., (deadline-time.perf_counter())*1000))
    reason = None
    if obsolete():
        reason = 'time-budget-exhausted' if time.perf_counter() >= deadline else 'cancelled-or-stale'
        # A late successful kernel is still revoked. Typed consumers must not
        # find usable proposals behind a cancelled status.
        result = replace(result, status='cancelled', pose_checks=(),
                         issues=tuple(dict.fromkeys((*result.issues, 'cancelled'))))
    return DirectStructureRecognitionComputation(result, observed, inputs.binding,
        snapshot.sha256, (time.perf_counter()-began)*1000, reason)


class _Run:
    def __init__(self, report):
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.report = report
        self.thread = None
        self.error = None
        self.revocation_reason = None
        self.deadline = None

    def _revoke_if_needed(self, report):
        reason = ('cancelled-or-stale' if self.stopped.is_set() else
                  self.revocation_reason() if self.revocation_reason is not None else None)
        if reason is not None:
            report.update(status='cancelled', reason=reason, poseProposals=[],
                          authorizesIdentity=False, lock=False)
        return report

    def collect(self):
        with self.lock:
            if self.error is not None:
                raise self.error
            # Revocation may happen during a large deepcopy, after the worker
            # has already published its diagnostic. Check the returned copy.
            return self._revoke_if_needed(copy.deepcopy(self.report))

    def publish(self, report):
        with self.lock:
            # asdict/deepcopy report construction is outside this critical
            # section; cancellation/current/deadline must be checked after it.
            self.report = self._revoke_if_needed(report)

    def cancel(self):
        # Keep close/cancel nonblocking even while collect deep-copies a large
        # report under its lock. Both publication and collection recheck this.
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
        self._close_generation = 0
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

    def computation_inputs(self, floor):
        """Capture the prepared index without resource I/O or numerical work."""
        if self._revision is None:
            self._revision = getattr(self.engine, 'qt_resource_revision', 0)
        if floor not in self.catalog.floors:
            raise ValueError('direct-recognition-floor-not-declared')
        if not self.current():
            raise ValueError('resource-context-superseded')
        if self.resources is None:
            raise ValueError(self.preparation.get('reason') or 'direct-recognition-not-prepared')
        issues = tuple(dict.fromkeys((*self.resources.identity_issues,
            *(issue for row in self.resources.sources if row.key.floor == floor
              for issue in row.qualification_issues))))
        binding = PoseInputBinding(self.catalog.season, self.catalog.mode,
            self.resources.component, self._revision, id(self.engine), *self._binding)
        return DirectStructureRecognitionInputs(binding, self.indexes[floor], issues)

    def compute(self, snapshot, floor, *, cancelled=None, deadline=None):
        """Use the caller's worker; retain the original resource-current boundary."""
        close_generation = self._close_generation
        inputs = self.computation_inputs(floor)
        return compute_structure_identity(snapshot, inputs,
            cancelled=lambda: (close_generation != self._close_generation or not self.current()
                or (cancelled is not None and cancelled())),
            deadline=deadline)

    def report_computation(self, snapshot, floor, computation):
        result, observed = computation.recognition, computation.observation
        if (result.frame_key != snapshot.frame_key
                or computation.shape_snapshot_id != snapshot.sha256):
            raise ValueError('direct-recognition-computation-frame-mismatch')
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
            elapsedMs=computation.elapsed_ms)
        if computation.cancellation_reason is not None:
            report['reason'] = computation.cancellation_reason
        return report

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
            close_generation = self._close_generation
            def revocation_reason():
                if (close_generation != self._close_generation or not self.current()
                        or (cancelled is not None and cancelled())):
                    return 'cancelled-or-stale'
                if run.deadline is not None and time.perf_counter() >= run.deadline:
                    return 'time-budget-exhausted'
                return None
            run.revocation_reason = revocation_reason
            run.thread = threading.Thread(target=self._compute,
                args=(run, snapshot, floor, cancelled, deadline),
                name='direct-structure-recognition', daemon=True)
            run.thread.start()
        return run

    def _compute(self, run, snapshot, floor, cancelled, deadline):
        began = time.perf_counter()
        run.deadline = min(deadline if deadline is not None else float('inf'), began+1.)
        def obsolete():
            return run.stopped.is_set() or (cancelled is not None and cancelled())

        try:
            computation = self.compute(snapshot, floor, cancelled=obsolete, deadline=run.deadline)
            report = self.report_computation(snapshot, floor, computation)
        except (MemoryError, KeyboardInterrupt, SystemExit) as error:
            with run.lock:
                run.error = error
            return
        except Exception as error:
            # A resource switch between start() and the worker's input capture
            # is cancellation, just as a switch inside a numerical kernel is.
            if obsolete() or not self.current():
                report = self._report(snapshot, floor, 'cancelled', reason='cancelled-or-stale',
                                      poseProposals=[], elapsedMs=(time.perf_counter()-began)*1000)
            else:
                report = self._report(snapshot, floor, 'failed', reason=str(error), errorType=type(error).__name__,
                                      elapsedMs=(time.perf_counter()-began)*1000)
        run.publish(report)

    def close(self):
        with self._lock:
            self._close_generation += 1
            if self._active is not None:
                self._active.cancel()
