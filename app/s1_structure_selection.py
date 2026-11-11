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
        super().__init__(snapshot)

    def _fit(self, map_id):
        try:
            fit = self.fit(self.frame, map_id, self.floor, self.cancelled, observation_bounds=self.bounds)
        except MemoryError:
            raise
        except Exception:
            if map_id != self.preferred:
                raise
            fit = dict(success=False, reason="preferred-fit-unresolved")
        if not isinstance(fit, dict) and map_id == self.preferred:
            fit = dict(success=False, reason="preferred-fit-unresolved")
        self.last_fit = fit

    def _jobs(self):
        for map_id in self.order:
            yield self._job(map_id, 'canonical-fit', lambda map_id=map_id: self._fit(map_id))


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

    def scan(self, frame, *, floor, floor_source, cancelled=None, budget_seconds=None,
             on_progress=None, candidate_order=None, preferred_map_id=None,
             preferred_material_committed=None, planned_map_ids=None, first_material_committed=None):
        if floor not in self.catalog.floors or floor_source not in ("operator-declared", "current-frame-verified"):
            raise ValueError("s1-structure-floor-evidence-required")
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("s1-structure-frame-invalid")
        if budget_seconds is not None and (not math.isfinite(budget_seconds) or budget_seconds < 0):
            raise ValueError("s1-structure-budget-invalid")
        cancelled = cancelled or (lambda: False)
        started = self.clock()
        # Own immutable bytes. Neither callers nor fit helpers can turn this
        # view writable and change the frame after a partial has been issued.
        snapshot = FrameSnapshot(frame)
        frame = snapshot.pixels
        frame_key = hashlib.sha256(frame.tobytes()).hexdigest()
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

        work = _StructureFitWork(snapshot, order, floor, fit_one, cancelled, bounds, preferred)

        def accept_fit(job):
            nonlocal first_verified_pose_elapsed
            map_id, fit = job.map_id, work.last_fit
            if "obsolete" in str(fit.get("reason", "")):
                return "cancelled"
            checked.append(map_id)
            ok = verified_pose(fit)
            outcomes.append(dict(mapId=map_id, outcome="supported" if ok else "unknown",
                                 reason=str(fit.get("reason", ""))))
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
                        return "preferred-material-committed"
            return None

        status = ObservationWorkScheduler(clock=self.clock).drive(work,
            deadline=started+budget_seconds if budget_seconds is not None else None,
            cancelled=cancelled, on_job_finished=accept_fit)
        if status == "complete" and set(order) != set(self.catalog.map_ids):
            status = "subset-unresolved"
        return report(status)

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

    def begin_observation(self, floor):
        if self._ended or floor not in self.catalog.floors:
            raise ValueError("s1-structure-session-unavailable")
        self.generation += 1
        self._current, self._floor = None, floor
        self._ticket = ObservationTicket(self._session_id, self.round_key, self.generation, floor)
        return self._ticket

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

    def end_round(self):
        self.generation += 1
        self._history.clear()
        self._priority_history.clear()
        self._ticket = None
        self._current, self._floor, self._ended = None, None, True
