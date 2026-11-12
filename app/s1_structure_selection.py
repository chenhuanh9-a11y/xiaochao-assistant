"""Shared structural proposals, not a new identity or live-display authority.

Partial walls can establish a current pose without identifying a whole layout.
A failed local solver is UNKNOWN, never negative identity evidence. This module
keeps that distinction through catalog scanning, temporal evidence and explicit
candidate choice. It does not invent similarity groups or semantic side labels.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import copy
import hashlib
import json
import math
import time
import uuid

import numpy as np

from entry_evidence_work import EntryEvidenceWork, FrameSnapshot, ObservationWorkScheduler
from entry_pose_proposals import current_entry_pose_seeds


@dataclass(frozen=True)
class StructureCatalog:
    season: str
    mode: str
    resource_key: str
    map_ids: tuple[str, ...]
    floors: tuple[str, ...]

    def __post_init__(self):
        if (self.season not in ("s0", "s1")
                or self.mode not in (("difficult", "nightmare") if self.season == "s0" else
                                     ("difficulty2", "difficult", "nightmare"))
                or not self.resource_key or not self.map_ids or len(set(self.map_ids)) != len(self.map_ids)
                or any(type(mid) is not str or not mid for mid in self.map_ids)
                or not self.floors or any(type(floor) is not str or not floor for floor in self.floors)
                or len(set(self.floors)) != len(self.floors)
                or self.season == "s1" and self.floors != ("-1", "-2", "-3", "-4")
                or self.season == "s0" and not set(self.floors) <= {"B1", "1", "2"}):
            raise ValueError("s1-structure-catalog-invalid")

    @classmethod
    def from_engine(cls, engine):
        if getattr(engine, "qt_season", None) == "s0":
            # Only the decoded base supplies S0 scope. Package presence grants
            # no ranking, identity, group or display qualification.
            base = getattr(engine, "qt_native_base", None)
            if (base is None or base.season != "s0" or base.mode != engine.pack.mode
                    or engine.pack is not base.pack):
                raise ValueError("s0-structure-catalog-context-mismatch")
            ref = base.component_ref  # Guarded read of the existing component identity.
            key = f"{ref.package_id}/{ref.resource_version}/{ref.component_id}/{ref.manifest_sha256}"
            return cls("s0", engine.pack.mode, key, tuple(sorted(base.pack.by_id)),
                       tuple(base.pack.floors))
        from s1_native_resources import MODE_LAYOUTS
        mode = engine.pack.mode
        if (getattr(engine, "qt_season", None) != "s1" or mode not in MODE_LAYOUTS
                or set(engine.pack.by_id) != set(MODE_LAYOUTS[mode])):
            raise ValueError("s1-structure-catalog-context-mismatch")
        native = engine.qt_native_context
        # Includes every product's content hash, not only mode or a filename.
        payload = json.dumps(native.manifest, sort_keys=True, separators=(",", ":")).encode()
        return cls("s1", mode, hashlib.sha256(payload).hexdigest(),
                   tuple(sorted(engine.pack.by_id)), tuple(engine.pack.floors))


def verified_pose(fit):
    if not isinstance(fit, dict) or fit.get("success") is not True:
        return False
    residual = fit.get("selectedRouteCurrentFrameResidual") or {}
    if (residual.get("verified") is not True
            or residual.get("poseVerifiedAgainstSharedStructure") is not True
            or fit.get("coordinateSpace") != "shared-structure-to-client"):
        return False
    try:
        matrix = np.asarray(fit.get("matrixSourceToClient"), dtype=float)
        return bool(matrix.shape == (3, 3) and np.isfinite(matrix).all()
                    and np.allclose(matrix[2], [0, 0, 1])
                    and matrix[0, 0] > 0 and abs(matrix[0, 0] - matrix[1, 1]) < 1e-8
                    and abs(matrix[0, 1]) < 1e-8 and abs(matrix[1, 0]) < 1e-8)
    except (ValueError, TypeError):
        return False


class _StructureFitWork(EntryEvidenceWork):
    """Canonical fit kernels using the same job lifecycle as entry evidence."""
    def __init__(self, snapshot, order, floor, fit, cancelled, bounds, preferred):
        self.order, self.floor, self.fit = order, floor, fit
        self.cancelled, self.bounds, self.preferred = cancelled, bounds, preferred
        self.last_fit = None
        self.entry_proposals = {}
        self.completed = set()
        self.preview_attempts = {}
        super().__init__(snapshot)

    def accept_entry_proposals(self, rows):
        proposals = current_entry_pose_seeds(rows or (), self.floor)
        self.entry_proposals = copy.deepcopy({mid: entry for mid, entry in proposals.items() if mid in self.order})
        priority = ([self.preferred] if self.preferred in self.order else [])
        priority.extend(mid for mid in self.entry_proposals if mid not in priority)
        self.order = tuple(priority) + tuple(mid for mid in self.order if mid not in priority)

    def _fit(self, map_id, *, proposal_only=False):
        proposal = self.entry_proposals.get(map_id)
        if proposal_only:
            self.preview_attempts[map_id] = copy.deepcopy(proposal)
        try:
            fit = self.fit(self.frame, map_id, self.floor, self.cancelled, observation_bounds=self.bounds,
                **(dict(entry_candidate=proposal) if proposal is not None else {}),
                **(dict(entry_observation=None) if proposal_only else {}))
        except MemoryError:
            raise
        except Exception as error:
            if proposal_only:
                fit = dict(success=False, reason='entry-preview-fit-unresolved',
                    error=type(error).__name__ + ':' + str(error))
            elif map_id != self.preferred:
                raise
            else:
                fit = dict(success=False, reason="preferred-fit-unresolved")
        if not isinstance(fit, dict) and proposal_only:
            fit = dict(success=False, reason='entry-preview-fit-unresolved')
        elif not isinstance(fit, dict) and map_id == self.preferred:
            fit = dict(success=False, reason="preferred-fit-unresolved")
        self.last_fit = fit
        # A prefix miss remains UNKNOWN. A later seed or the completed work's
        # qualified fixed-H path can still succeed; successful poses are reused.
        if not proposal_only or verified_pose(fit):
            self.completed.add(map_id)

    def needs_preview(self, map_id):
        return (map_id not in self.completed and
            (map_id not in self.preview_attempts or not _same_evidence(
                self.preview_attempts[map_id], self.entry_proposals.get(map_id))))

    def _jobs(self):
        for map_id in self.order:
            if map_id not in self.completed:
                yield self._job(map_id, 'canonical-fit', lambda map_id=map_id: self._fit(map_id))


class _ObservationWork(EntryEvidenceWork):
    """Dependencies and canonical poses share one driver, frame and deadline."""
    def __init__(self, snapshot, entry, fit, *, preview_enabled=False):
        if entry is not None and entry.frame_snapshot is not snapshot:
            entry.close()
            fit.close()
            raise ValueError('entry-frame-snapshot-mismatch')
        self.entry, self.fit, self.entry_rows = entry, fit, None
        self.preview_enabled, self.material_stop = preview_enabled, None
        super().__init__(snapshot)

    def _delegate(self, work):
        try:
            while (job := work.next_job()) is not None:
                yield self._job(job.map_id, job.phase, job.run)
        finally:
            work.close()

    def _jobs(self):
        try:
            if self.entry is not None:
                try:
                    while (job := self.entry.next_job()) is not None:
                        yield self._job(job.map_id, job.phase, job.run)
                        if (job.phase == 'entry-pose-proposals' and self.preview_enabled
                                and self.material_stop is None):
                            self.fit.accept_entry_proposals(self.entry.pose_proposal_rows)
                            for map_id in self.fit.order:
                                if self.material_stop is not None:
                                    break
                                if (self.fit.needs_preview(map_id)
                                        and (map_id in self.fit.entry_proposals or map_id == self.fit.preferred)):
                                    yield self._job(map_id, 'canonical-fit',
                                        lambda map_id=map_id: self.fit._fit(map_id, proposal_only=True))
                finally:
                    self.entry_rows = self.entry.finish()
                yield self._job(None, 'entry-evidence', lambda: None)
                self.fit.accept_entry_proposals(self.entry_rows)
            if self.material_stop is None:
                yield from self._delegate(self.fit)
        finally:
            self.fit.close()


def _same_evidence(left, right):
    """Exact nested evidence equality, including NumPy pose arrays."""
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        return bool(isinstance(left, np.ndarray) and isinstance(right, np.ndarray)
                    and left.dtype == right.dtype and np.array_equal(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_evidence(left[k], right[k]) for k in left)
    if isinstance(left, (list, tuple)) and isinstance(right, type(left)):
        return len(left) == len(right) and all(_same_evidence(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


class StructureCandidateScanner:
    """Exhaust the exact selected catalog, or explicitly report unscanned IDs.

    `fit` is the unchanged shared production solver, scoped to a full-client
    observation. The budget is cooperative: one in-flight fit may overrun it.
    Icons and entrance datasets are neither inputs nor prerequisite gates.
    """
    def __init__(self, catalog, fit, *, clock=time.monotonic, prepare_fit=None):
        self.catalog, self.fit, self.clock = catalog, fit, clock
        self.prepare_fit = prepare_fit

    def unresolved_report(self, snapshot, floor, floor_source, *, previous=None, elapsed_seconds=0.):
        """The completed race may stop a candidate before its next callback.

        Preserve only already offered canonical poses; unscanned members remain
        unknown. Entry identity has its independent owner and ledger.
        """
        report = copy.deepcopy(previous) if previous is not None else dict(
            schemaVersion=1, season=self.catalog.season, mode=self.catalog.mode,
            resourceKey=self.catalog.resource_key, floor=floor, floorSource=floor_source,
            observedFloor=floor if floor_source == 'current-frame-verified' else '',
            frameKey=snapshot.frame_key, updateSeq=0, expectedMapIds=list(self.catalog.map_ids),
            checkedMapIds=[], unscannedMapIds=list(self.catalog.map_ids), supportedMapIds=[],
            unknownMapIds=list(self.catalog.map_ids), firstVerifiedPoseElapsedSeconds=None,
            excludedMapIds=[], poses={}, outcomes=[], competitionComplete=False,
            uniqueIdentityVerified=False, identityHardGatePassed=False,
            automaticSelectionAuthorized=False, liveAuthorization=False,
            reason='supported-geometry-is-not-complete-identity-competition')
        report.update(status='subset-unresolved', scanComplete=False,
            updateSeq=report['updateSeq']+1, elapsedSeconds=elapsed_seconds,
            candidateReuse='none', scanSkipped=False)
        return report

    def scan(self, frame, *, floor, floor_source, cancelled=None, budget_seconds=None,
             on_progress=None, candidate_order=None, preferred_map_id=None,
             preferred_material_committed=None, planned_map_ids=None, first_material_committed=None,
             prepare_entry=None, on_entry_evidence=None, deadline=None, frame_snapshot=None):
        if floor not in self.catalog.floors or floor_source not in ("operator-declared", "current-frame-verified"):
            raise ValueError("s1-structure-floor-evidence-required")
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("s1-structure-frame-invalid")
        if budget_seconds is not None and (not math.isfinite(budget_seconds) or budget_seconds < 0):
            raise ValueError("s1-structure-budget-invalid")
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError('observation-deadline-invalid')
        cancelled = cancelled or (lambda: False)
        started = self.clock()
        # Own immutable bytes. Neither callers nor fit helpers can turn this
        # view writable and change the frame after a partial has been issued.
        if frame_snapshot is not None and (not isinstance(frame_snapshot, FrameSnapshot)
                or frame_snapshot.pixels is not frame):
            raise ValueError('structure-scan-frame-snapshot-mismatch')
        snapshot = frame_snapshot if frame_snapshot is not None else FrameSnapshot(frame)
        frame = snapshot.pixels
        frame_key = snapshot.frame_key
        bounds = (0, 0, frame.shape[1], frame.shape[0])
        fit_one = self.prepare_fit(frame, bounds) if self.prepare_fit else self.fit
        order = self.catalog.map_ids
        if candidate_order is not None:
            try:
                proposal = tuple(candidate_order)
            except TypeError:
                proposal = ()
            if (len(proposal) == len(order) and all(type(mid) is str for mid in proposal)
                    and len(set(proposal)) == len(order) and set(proposal) == set(order)):
                order = proposal
        # An ID is only a scheduling hint. Every call still fits this frame and
        # floor; only the runtime's current material commit can stop the scan.
        preferred = preferred_map_id if type(preferred_map_id) is str and preferred_map_id in order else None
        if preferred is not None:
            order = (preferred,) + tuple(mid for mid in order if mid != preferred)
        if planned_map_ids is not None:
            plan = tuple(planned_map_ids)
            if not plan or len(set(plan)) != len(plan) or not set(plan) <= set(order):
                raise ValueError("s1-structure-plan-invalid")
            order = tuple(mid for mid in order if mid in plan)
        supported, checked, outcomes = {}, [], []
        update_seq = 0
        first_verified_pose_elapsed = None

        def report(status):
            nonlocal update_seq
            update_seq += 1
            poses = {} if status == "cancelled" else copy.deepcopy(supported)
            return dict(schemaVersion=1, season=self.catalog.season, mode=self.catalog.mode,
                resourceKey=self.catalog.resource_key, floor=floor, floorSource=floor_source,
                observedFloor=floor if floor_source == "current-frame-verified" else "",
                frameKey=frame_key, status=status, scanComplete=status == "complete", updateSeq=update_seq,
                expectedMapIds=list(self.catalog.map_ids), checkedMapIds=list(checked),
                unscannedMapIds=[mid for mid in self.catalog.map_ids if mid not in checked],
                supportedMapIds=list(poses), unknownMapIds=[mid for mid in self.catalog.map_ids if mid not in poses],
                firstVerifiedPoseElapsedSeconds=first_verified_pose_elapsed,
                excludedMapIds=[], poses=poses, outcomes=copy.deepcopy(outcomes), elapsedSeconds=self.clock()-started,
                competitionComplete=False, uniqueIdentityVerified=False, identityHardGatePassed=False,
                automaticSelectionAuthorized=False, liveAuthorization=False,
                candidateReuse=("preferred-current-frame" if status == "preferred-material-committed" else
                    "first-current-frame" if status == "first-material-committed" else "none"),
                scanSkipped=status in ("preferred-material-committed", "first-material-committed"),
                reason="supported-geometry-is-not-complete-identity-competition")

        fit_work = _StructureFitWork(snapshot, order, floor, fit_one, cancelled, bounds, preferred)
        deadline = min(deadline if deadline is not None else float('inf'),
            started+budget_seconds if budget_seconds is not None else float('inf'))
        entry = prepare_entry(snapshot, floor, deadline=deadline) if prepare_entry is not None else None
        work = _ObservationWork(snapshot, entry, fit_work, preview_enabled=on_progress is not None)

        def entry_evidence():
            return dict(frameKey=frame_key, resourceKey=self.catalog.resource_key, floor=floor,
                role='identity-entry', rows=copy.deepcopy(work.entry_rows or []))

        def accept_fit(job):
            nonlocal first_verified_pose_elapsed
            if job.phase == 'entry-evidence':
                if on_entry_evidence is not None and on_entry_evidence(entry_evidence()) is True:
                    return 'subset-unresolved'
                return None
            if job.phase != 'canonical-fit':
                return None
            map_id, fit = job.map_id, fit_work.last_fit
            if "obsolete" in str(fit.get("reason", "")):
                return "cancelled"
            if map_id not in checked:
                checked.append(map_id)
            ok = verified_pose(fit)
            outcome = dict(mapId=map_id, outcome="supported" if ok else "unknown",
                           reason=str(fit.get("reason", "")))
            if any(row['mapId'] == map_id for row in outcomes):
                outcomes[next(i for i, row in enumerate(outcomes) if row['mapId'] == map_id)] = outcome
            else:
                outcomes.append(outcome)
            if ok:
                if on_progress is not None and first_verified_pose_elapsed is None:
                    first_verified_pose_elapsed = self.clock() - started
                supported[map_id] = copy.deepcopy(fit)
                if on_progress is not None:
                    # This executes on the scanning worker. Consumers must queue
                    # UI work, and own cancellation/exception render withdrawal.
                    on_progress(report("partial"))
                if callable(first_material_committed):
                    try:
                        material_committed = first_material_committed(map_id) is True
                    except MemoryError:
                        raise
                    except Exception:
                        material_committed = False
                    if material_committed:
                        if entry is not None and work.entry_rows is None:
                            work.material_stop = 'first-material-committed'
                            return None  # Only stop fitting; finish candidate identity jobs.
                        return "first-material-committed"
                if map_id == preferred and callable(preferred_material_committed):
                    # Not cancellation: preserve this ticket's accepted pose and
                    # pixels. Unknown/unscanned alternatives stay in the report.
                    try:
                        material_committed = preferred_material_committed(map_id) is True
                    except MemoryError:
                        raise
                    except Exception:
                        material_committed = False
                    if material_committed:
                        stopped = ('preferred-material-committed' if checked == [preferred]
                                   else 'first-material-committed')
                        if entry is not None and work.entry_rows is None:
                            work.material_stop = stopped
                            return None
                        return stopped
            return None

        status = ObservationWorkScheduler(clock=self.clock).drive(work,
            deadline=deadline,
            cancelled=cancelled, on_job_finished=accept_fit)
        if status == 'complete' and work.material_stop is not None:
            status = work.material_stop
        if status == "complete" and set(order) != set(self.catalog.map_ids):
            status = "subset-unresolved"
        result = report(status)
        if entry is not None and status != 'cancelled':
            result['entryEvidence'] = entry_evidence()
        return result

    @staticmethod
    def rank_rows(report, pack):
        rows = []
        for mid in report["supportedMapIds"]:
            fit = report["poses"][mid]
            residual = fit["selectedRouteCurrentFrameResidual"]
            score = residual.get("withinThreePixels", 0)
            score = float(score) if isinstance(score, (int, float)) and math.isfinite(score) else 0.
            rows.append(dict(mapId=mid, name=pack.by_id[mid].name, floor=report["floor"],
                score=score*100, strongSuggestion=False, identityHardGatePassed=False,
                identityEvidence=f"{report['season']}-current-structure-proposal", reason="结构匹配，地图身份待确认",
                structurePose=copy.deepcopy(fit), structureScan=dict(
                    scanComplete=report["scanComplete"], competitionComplete=False,
                    unknownMapIds=report["unknownMapIds"], unscannedMapIds=report["unscannedMapIds"])))
        return sorted(rows, key=lambda row: (-row["score"], row["mapId"]))


@dataclass(frozen=True)
class ObservationTicket:
    session_id: str
    round_key: str
    generation: int
    floor: str


class StructureEvidenceSession:
    """Round-owned bounded evidence; current poses are revoked before new work.

    `round_key` must identify a real round, not screenshot timestamp proximity.
    Support intersections are diagnostic corroboration only. They never remove
    unknown competitors or create a registered similarity group.
    """
    def __init__(self, catalog, round_key, *, max_observations=12):
        if not round_key or not 1 <= max_observations <= 64:
            raise ValueError("s1-structure-session-invalid")
        self.catalog, self.round_key, self.max_observations = catalog, round_key, max_observations
        self.generation, self._history, self._current, self._floor = 0, OrderedDict(), None, None
        self._ended = False
        # Positive scheduling hints are not observations/votes for identity.
        # Session construction already binds round, mode and resource generation.
        self._priority_history = OrderedDict()
        self._committed_generation = None
        self._session_id, self._ticket = uuid.uuid4().hex, None
        self._entry_evidence = {}

    def begin_observation(self, floor):
        if self._ended or floor not in self.catalog.floors:
            raise ValueError("s1-structure-session-unavailable")
        self.generation += 1
        self._current, self._floor = None, floor
        self._entry_evidence = {}
        self._ticket = ObservationTicket(self._session_id, self.round_key, self.generation, floor)
        return self._ticket

    def commit_entry_evidence(self, report, ticket, *, channel='candidate'):
        """Independent entry channel; a canonical terminal does not retire it.

        Only observation retirement invalidates its ticket. Storing source rows
        never revives canonical candidates or adjudicates their identity.
        """
        if (self._ended or ticket != self._ticket
                or self.catalog.season != 's0' or channel not in ('candidate', 'structure')
                or channel in self._entry_evidence
                or not isinstance(report, dict) or report.get('role') != 'identity-entry'
                or report.get('resourceKey') != self.catalog.resource_key
                or report.get('floor') != self._floor
                or not isinstance(report.get('frameKey'), str) or len(report['frameKey']) != 64
                or not isinstance(report.get('rows'), list)
                or any(not isinstance(row, dict) or row.get('mapId') not in self.catalog.map_ids
                       for row in report['rows'])
                or self._current is not None and self._current['frameKey'] != report['frameKey']
                or any(row['frameKey'] != report['frameKey'] for row in self._entry_evidence.values())):
            return False
        self._entry_evidence[channel] = copy.deepcopy(report)
        return True

    def commit(self, report, ticket):
        """Accept monotonic updates of ONE observation, never independent votes.

        A cancelled terminal update withdraws current candidates, even after a
        partial succeeded. The runtime must also revoke that ticket's pixels.
        Terminal generations cannot be revived by late progress or completion.
        """
        if not isinstance(report, dict):
            return False
        c = self.catalog
        status = report.get("status")
        seq = report.get("updateSeq", 1)  # Legacy one-shot reports remain valid.
        if (self._ended or not isinstance(ticket, ObservationTicket) or ticket != self._ticket or self.generation == self._committed_generation
                or report.get("schemaVersion") != 1 or report.get("floor") != self._floor
                or (report.get("season"), report.get("mode"), report.get("resourceKey")) !=
                   (c.season, c.mode, c.resource_key)
                or report.get("expectedMapIds") != list(c.map_ids)
                or self._floor not in c.floors
                or report.get("floorSource") not in ("operator-declared", "current-frame-verified")
                or report.get("observedFloor") != (self._floor if report.get("floorSource") == "current-frame-verified" else "")
                or status not in ("partial", "complete", "budget-unresolved", "cancelled", "preferred-material-committed", "first-material-committed", "subset-unresolved")
                or type(seq) is not int or seq < 1
                or status == "partial" and "updateSeq" not in report
                or report.get("scanComplete") is not (status == "complete")
                or not isinstance(report.get("frameKey"), str) or len(report["frameKey"]) != 64):
            return False
        supported = report.get("supportedMapIds", [])
        checked = report.get("checkedMapIds", [])
        if (not isinstance(supported, list) or not isinstance(checked, list)
                or any(type(mid) is not str for mid in supported + checked)
                or len(set(supported)) != len(supported) or len(set(checked)) != len(checked)
                or not set(supported) <= set(checked) <= set(c.map_ids)
                or report.get("unscannedMapIds") != [mid for mid in c.map_ids if mid not in checked]
                or report.get("unknownMapIds") != [mid for mid in c.map_ids if mid not in supported]
                or report.get("excludedMapIds") != []
                or any(report.get(key) is not False for key in ("competitionComplete",
                    "uniqueIdentityVerified", "identityHardGatePassed", "automaticSelectionAuthorized", "liveAuthorization"))
                or not isinstance(report.get("poses"), dict)
                or set(report["poses"]) != set(supported)
                or status == "complete" and set(checked) != set(c.map_ids)
                or status == "preferred-material-committed" and (
                    len(checked) != 1 or supported != checked or report.get("scanSkipped") is not True
                    or report.get("candidateReuse") != "preferred-current-frame")
                or status == "first-material-committed" and (
                    not supported or not checked or report.get("scanSkipped") is not True
                    or report.get("candidateReuse") != "first-current-frame")
                or status == "cancelled" and supported
                or any(not verified_pose(report.get("poses", {}).get(mid)) for mid in supported)):
            return False
        previous = self._current
        if any(row['frameKey'] != report['frameKey'] for row in self._entry_evidence.values()):
            return False
        if previous is not None:
            if (seq <= previous.get("updateSeq", 1) or report["frameKey"] != previous["frameKey"]
                    or report.get("floorSource") != previous.get("floorSource")
                    or report.get("observedFloor") != previous.get("observedFloor")
                    or checked[:len(previous["checkedMapIds"])] != previous["checkedMapIds"]
                    or status != "cancelled" and not set(previous["supportedMapIds"]) <= set(supported)):
                return False
            # An incremental list cannot replace previously authorized evidence.
            for mid in previous["supportedMapIds"] if status != "cancelled" else ():
                old, new = previous["poses"][mid], report["poses"][mid]
                if not _same_evidence(old, new):
                    return False
        self._current = copy.deepcopy(report)
        if status != "partial":
            self._committed_generation = self.generation
        # Diagnostic corroboration remains final-only. Independent bounded
        # scheduling hints survive early close/cancellation without becoming
        # identity evidence, a confidence vote, or a reusable pose.
        key = (report["floor"], report["frameKey"])
        if supported and status != "cancelled":
            previous_hints = self._priority_history.get(key, ())
            combined = set(previous_hints) | set(supported)
            self._priority_history[key] = tuple(mid for mid in c.map_ids if mid in combined)
            self._priority_history.move_to_end(key)
            while len(self._priority_history) > self.max_observations:
                self._priority_history.popitem(last=False)
        if supported and status in ("complete", "budget-unresolved", "preferred-material-committed", "first-material-committed", "subset-unresolved"):
            self._history[key] = dict(floor=report["floor"], frameKey=report["frameKey"],
                supportedMapIds=list(supported), scanComplete=report.get("scanComplete") is True)
            self._history.move_to_end(key)
            while len(self._history) > self.max_observations:
                self._history.popitem(last=False)
        return True

    def choose_candidate(self, map_id, ticket):
        current = self._current
        if (self._ended or ticket != self._ticket or current is None
                or map_id not in current["supportedMapIds"]):
            return dict(success=False, reason="s1-structure-candidate-not-current")
        # This is a reviewable candidate change, not an identity/group commit.
        return dict(success=True, mapId=map_id, floor=current["floor"],
            season=self.catalog.season, mode=self.catalog.mode, resourceKey=self.catalog.resource_key,
            frameKey=current["frameKey"], generation=self.generation,
            pose=copy.deepcopy(current["poses"][map_id]), identityKind="candidate",
            requiresManualConfirmation=True, groupId=None,
            identityVerified=False, displayAuthorized=False, liveAuthorization=False)

    def priority_supported(self, floor):
        """Newest positive hints first, scoped to this round/resource/floor.

        No poses or identity confidence escape this API. A failed or cancelled
        scan cannot negate earlier positives, and old tickets cannot add them.
        Caller must still fit every chosen candidate against its NEW frame.
        """
        if self._ended or floor not in self.catalog.floors:
            return ()
        result = []
        for (observed_floor, _frame), hints in reversed(self._priority_history.items()):
            if observed_floor == floor:
                result.extend(mid for mid in hints if mid not in result)
        return tuple(result)

    def priority_order(self, floor):
        """Return the full natural catalogue, with positive hints moved earlier."""
        hints = self.priority_supported(floor)
        return hints + tuple(mid for mid in self.catalog.map_ids if mid not in hints)

    def snapshot(self):
        observations = list(self._history.values())
        common = set.intersection(*(set(row["supportedMapIds"]) for row in observations)) if observations else set()
        counts = {mid: sum(mid in row["supportedMapIds"] for row in observations) for mid in self.catalog.map_ids}
        return dict(roundKey=self.round_key, generation=self.generation, observationCount=len(observations),
            floors=sorted({r["floor"] for r in observations}), supportedOnEveryObservation=sorted(common),
            priorityHintObservationCount=len(self._priority_history),
            priorityHintMapIds=list(self.priority_supported(self._floor)),
            priorityHintsOnly=True,
            supportCounts=counts, conflictingSupport=bool(observations and not common),
            currentSupportedMapIds=list(self._current["supportedMapIds"]) if self._current else [],
            currentScanComplete=bool(self._current and self._current["scanComplete"]),
            currentStatus=self._current["status"] if self._current else "",
            candidateReuse=self._current.get("candidateReuse", "none") if self._current else "none",
            scanSkipped=bool(self._current and self._current.get("scanSkipped")),
            currentUpdateSeq=self._current.get("updateSeq", 1) if self._current else 0,
            currentUnscannedMapIds=list(self._current["unscannedMapIds"]) if self._current else [],
            possibleMapIds=list(self.catalog.map_ids), excludedMapIds=[],
            registeredGroup=None, uniqueIdentityVerified=False, liveAuthorization=False)

    def withdraw_material(self, ticket):
        """Retire this observation's poses without cancelling its worker ticket.

        A confirmed pool contradiction may arrive while the foreground owns
        this ticket. It must finish normally, but cannot revive pre-conflict
        poses. Only begin_observation opens a new material generation.
        """
        if ticket != self._ticket or self._ended:
            return False
        self._current = None
        self._committed_generation = self.generation
        return True

    def revoke_current(self):
        """Withdraw current-frame choices without inventing negative evidence."""
        self.generation += 1
        self._current, self._floor, self._ticket = None, None, None
        self._entry_evidence = {}

    def end_round(self):
        self.generation += 1
        self._history.clear()
        self._priority_history.clear()
        self._ticket = None
        self._entry_evidence = {}
        self._current, self._floor, self._ended = None, None, True
