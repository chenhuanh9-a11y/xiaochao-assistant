"""Compute-only direct structure recognition, independent of pose fitting.

Prepared index + current A/B observation in; fingerprint and fixed-pose
held-out proposals out. No file/catalog validation, per-map fullfit, resource
preparation, author/group evidence or runtime submission occurs here. B never
chooses A, changes a matrix, resolves competing maps or ranks pose families.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import math
import time

import numpy as np

from structure_identity_index import (Correspondence, FingerprintResult, PoseProposal,
    NumericPoseUnresolved, StructureIdentityIndex, _active_points, _ordered_rays, _unit_dot_upper)
from structure_identity_observation import (StructureIdentityObservation, _Cancelled,
    constellation_issue, independence_violations)


class _Unfinished(Exception):
    pass


class _Work:
    def __init__(self, maximum, cancelled, deadline):
        if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0):
            raise ValueError("b-work-budget-invalid")
        self.maximum, self.cancelled, self.deadline, self.done = maximum, cancelled, deadline, 0

    def check(self):
        if self.cancelled is not None and self.cancelled():
            raise _Unfinished("cancelled")
        if self.deadline is not None and time.perf_counter() >= self.deadline:
            raise _Unfinished("b-time-budget-exhausted")
        if self.maximum is not None and self.done >= self.maximum:
            raise _Unfinished("b-work-budget-exhausted")
        self.done += 1


@dataclass(frozen=True)
class HeldOutCheck:
    status: str
    reason: str
    correspondences: tuple[Correspondence, ...]
    maximum_residual: float | None
    arithmetic_error_bound: float | None
    epsilon: float
    roundoff_slack: float
    work_done: int
    elapsed_ms: float

    @property
    def verified(self):
        return self.status == "verified"


@dataclass(frozen=True)
class FixedPoseEvidence:
    proposal: PoseProposal
    held_out: HeldOutCheck


@dataclass(frozen=True)
class StructureIdentityRecognition:
    frame_key: str
    status: str
    fingerprint: FingerprintResult | None
    pose_checks: tuple[FixedPoseEvidence, ...]
    issues: tuple[str, ...]
    qualification_gaps: tuple[str, ...]
    extraction_ms: float
    retrieval_ms: float
    held_out_ms: float
    computation_ms: float
    authorizesIdentity: bool = field(default=False, init=False)
    lock: bool = field(default=False, init=False)

    @property
    def pose_proposals(self):
        # Preserve A's order and all verified families, never sort by B error.
        return tuple(item.proposal for item in self.pose_checks if item.held_out.verified)


@dataclass(frozen=True)
class _Pair:
    anchor: object
    points: tuple
    order: tuple
    residual: float
    arithmetic: float


def _arms(points):
    return ((points[1], points[0]), (points[1], points[2]))


def _exact_segments_overlap(first, second):
    # Only a centre ablation's support check needs this rare fallback. Full
    # endpoints are still required to prove independence, but their unused
    # lengths must not create floating overflow in the centre model.
    a, b, c, d = (tuple(Fraction(float(value)) for value in point)
                  for segment in (first, second) for point in segment)
    u, v, offset = (tuple(right[i] - left[i] for i in (0, 1))
                    for left, right in ((a, b), (c, d), (a, c)))
    if u[0] * v[1] - u[1] * v[0] != 0 or u[0] * offset[1] - u[1] * offset[0] != 0:
        return False
    axis = 0 if abs(u[0]) >= abs(u[1]) else 1
    return max(min(a[axis], b[axis]), min(c[axis], d[axis])) <= min(
        max(a[axis], b[axis]), max(c[axis], d[axis]))


def _segments_overlap(first, second, *, exact_on_overflow=False):
    a, b = np.asarray(first[0]), np.asarray(first[1])
    c, d = np.asarray(second[0]), np.asarray(second[1])
    try:
        with np.errstate(all="raise"):
            u, v, offset = b - a, d - c, c - a
            cross = u[0] * v[1] - u[1] * v[0]
            collinear = u[0] * offset[1] - u[1] * offset[0]
    except FloatingPointError:
        if exact_on_overflow:
            return _exact_segments_overlap(first, second)
        raise
    if not np.isfinite([*u, *v, *offset, cross, collinear]).all():
        raise _Unfinished("b-source-independence-numeric-unresolved")
    if cross != 0 or collinear != 0:
        return False
    axis = int(np.argmax(np.abs(u)))
    return max(min(a[axis], b[axis]), min(c[axis], d[axis])) <= min(
        max(a[axis], b[axis]), max(c[axis], d[axis]))


def _source_independent(anchor, vertices, arms, constraint_kind="full-chain"):
    if set(anchor.points) & vertices:
        return False
    return not any(_segments_overlap(left, right, exact_on_overflow=constraint_kind == "corner-centre")
                   for left in _arms(anchor.points) for right in arms)


def _connections(qi, pair, chosen, query):
    for other_qi, other in chosen.items():
        for endpoint in (0, 2):
            if (query[qi].points[endpoint] == query[other_qi].points[1]
                    and pair.points[endpoint] != other.points[1]):
                return False
            if (query[other_qi].points[endpoint] == query[qi].points[1]
                    and other.points[endpoint] != pair.points[1]):
                return False
    return True


def verify_fixed_b(proposal, source_entry, observation, *, max_work=100_000,
                   cancelled=None, deadline=None):
    """Existence of a complete one-to-one B match at A's unchanged matrix.

    All eligible source anchors and both arm orders compete at that fixed pose.
    Matched B source geometry cannot reuse A vertices or wall segments. Finding
    one full assignment certifies existence; no B residual is used for fitting.
    """
    began = time.perf_counter()
    work = _Work(max_work, cancelled, deadline)
    slack = 0.0

    def result(status, reason, pairs=()):
        correspondences = tuple(Correspondence(observation.b[qi].feature_id,
            observation.b[qi].center_id, pair.anchor.feature_id, pair.anchor.center_id,
            observation.b[qi].points, pair.points, pair.order) for qi, pair in pairs)
        values = tuple(pair for _, pair in pairs)
        return HeldOutCheck(status, reason, correspondences,
            max((pair.residual for pair in values), default=None),
            max((pair.arithmetic for pair in values), default=None),
            observation.epsilon, float(slack), work.done, (time.perf_counter() - began) * 1000)

    try:
        work.check()
        kind = observation.constraint_kind
        if kind not in ("full-chain", "corner-centre") or getattr(proposal, "constraint_kind", None) != kind:
            return result("unresolved", "constraint-kind-mismatch")
        ray_floor = None
        if kind == "corner-centre":
            ray_floor = getattr(proposal, "minimum_ray_dot", None)
            if ray_floor is None or not math.isfinite(ray_floor) or not -1 <= ray_floor <= 1:
                return result("unresolved", "b-ray-threshold-unavailable")
        slack = 16 * np.finfo(np.float64).eps * max(1.0, max(
            (abs(value) for anchor in observation.b for point in _active_points(anchor.points, kind)
             for value in point), default=1.0))
        slack = max(slack, proposal.pose_region.roundoff_slack)
        if observation.a.source_independent is not True or observation.issues:
            return result("unresolved", "observation-partition-not-ready")
        if source_entry.key != proposal.key or source_entry.resource_binding != proposal.resource_binding:
            return result("unresolved", "b-source-binding-mismatch")
        if (source_entry.search_allowed is not True or source_entry.eligibility_complete is not True
                or source_entry.anchors is None or source_entry.eligible_feature_ids is None):
            return result("unresolved", "b-source-eligibility-unavailable")
        for label, anchors, count in (("a", observation.a.anchors, 6), ("b", observation.b, 3)):
            issue = constellation_issue(anchors, count, observation.epsilon, label)
            if issue:
                return result("unresolved", issue)
        if independence_violations(observation.a.anchors, observation.b, observation.frame_shape,
                                   observation.support_halo, cancelled=cancelled):
            return result("unresolved", "a-b-shared-measurement-support")
        matrix = np.asarray(proposal.matrix_source_to_query)
        scale, translation = matrix[0, 0], matrix[:2, 2]
        source_vertices = {point for pair in proposal.correspondences for point in pair.source_points}
        source_arms = tuple(arm for pair in proposal.correspondences for arm in _arms(pair.source_points))
        source = []
        for anchor in source_entry.anchors:
            work.check()
            if (anchor.feature_id in source_entry.eligible_feature_ids
                    and _source_independent(anchor, source_vertices, source_arms, kind)):
                source.append(anchor)
        choices, numeric_pending = {}, False
        tolerance = observation.epsilon + slack
        for qi, query in enumerate(observation.b):
            choices[qi] = []
            active_query = _active_points(query.points, kind)
            query_rays = _ordered_rays(query, (0, 1, 2), kind) if kind == "corner-centre" else None
            for anchor in source:
                for order in ((0, 1, 2), (2, 1, 0)):
                    work.check()
                    points = tuple(anchor.points[index] for index in order)
                    with np.errstate(over="raise", invalid="raise", divide="raise"):
                        if (kind == "corner-centre" and
                                np.any(_unit_dot_upper(query_rays, _ordered_rays(anchor, order, kind)) < ray_floor)):
                            continue
                        product = scale * _active_points(points, kind)
                        predicted = product + translation
                        error = float(np.max(np.abs(predicted - active_query)))
                        # Dimensionless unit-roundoff allowance for evaluating
                        # this fixed global affine map, not extra pixel epsilon.
                        arithmetic = float(4 * np.finfo(np.float64).eps * (
                            np.max(np.abs(product)) + np.max(np.abs(translation))
                            + np.max(np.abs(active_query))))
                    if not math.isfinite(error) or not math.isfinite(arithmetic):
                        raise _Unfinished("b-fixed-matrix-numeric-unresolved")
                    if error + arithmetic <= tolerance:
                        choices[qi].append(_Pair(anchor, points, order, error, arithmetic))
                    elif error - arithmetic <= tolerance:
                        numeric_pending = True
        order = sorted(choices, key=lambda qi: (len(choices[qi]), qi))

        def visit(depth, chosen, used_centres):
            work.check()
            if depth == len(order):
                return tuple((qi, chosen[qi]) for qi in range(len(observation.b)))
            qi = order[depth]
            for pair in choices[qi]:
                work.check()
                old_query = used_centres.get(pair.anchor.center_id)
                if old_query is not None and old_query != observation.b[qi].center_id:
                    continue
                if any(observation.b[old_qi].center_id == observation.b[qi].center_id
                       and old_pair.anchor.center_id != pair.anchor.center_id
                       for old_qi, old_pair in chosen.items()):
                    continue
                if kind == "full-chain" and not _connections(qi, pair, chosen, observation.b):
                    continue
                chosen[qi] = pair
                found = visit(depth + 1, chosen, used_centres | {pair.anchor.center_id: observation.b[qi].center_id})
                del chosen[qi]
                if found is not None:
                    return found
            return None

        found = visit(0, {}, {})
        work.check()
        if found is not None:
            return result("verified", "fixed-a-seed-independent-b-geometry", found)
        if numeric_pending:
            return result("unresolved", "b-fixed-matrix-boundary-numeric-unresolved")
        return result("rejected", "fixed-a-seed-b-correspondence-rejected")
    except (_Unfinished, _Cancelled) as error:
        reason = str(error) or "cancelled"
        return result("cancelled" if reason == "cancelled" else "unresolved", reason)
    except (FloatingPointError, RecursionError, NumericPoseUnresolved):
        return result("unresolved", "b-geometry-numeric-or-depth-unresolved")


def recognize_structure_identity(index, observation, *, source_qualification_issues=None,
                                 index_max_work=100_000, b_max_work=100_000,
                                 time_budget_ms=None, cancelled=None):
    """Full A competition first; independent B only validates A-unique maps.

    This accepts prepared read-only data and an already extracted frame
    observation. Qualification strings are retained as gaps, never interpreted
    as an authorization flag. Current canonical residual and runtime identity
    qualification belong to the outer shared validator/owner.
    """
    if not isinstance(index, StructureIdentityIndex) or not isinstance(observation, StructureIdentityObservation):
        raise TypeError("structure-index-and-observation-required")
    if time_budget_ms is not None and (not math.isfinite(time_budget_ms) or time_budget_ms < 0):
        raise ValueError("recognition-time-budget-invalid")
    if source_qualification_issues is None:
        source_qualification_issues = ("footprint-source-qualification-not-supplied",)
    if (not isinstance(source_qualification_issues, (tuple, list))
            or not all(isinstance(value, str) for value in source_qualification_issues)):
        raise TypeError("source-qualification-issues-must-be-strings")
    began = time.perf_counter()
    deadline = began + time_budget_ms / 1000 if time_budget_ms is not None else None
    fingerprint, checks, issues = None, [], []
    retrieval_ms, b_ms = 0.0, 0.0
    gaps = tuple(dict.fromkeys((*source_qualification_issues, *observation.qualification_gaps,
        "current-frame-global-canonical-residual-required", "runtime-identity-qualification-required")))

    def result(status):
        return StructureIdentityRecognition(observation.frame_key, status, fingerprint, tuple(checks),
            tuple(issues), gaps, observation.extraction_ms, retrieval_ms, b_ms,
            (time.perf_counter() - began) * 1000)

    def stopped():
        return cancelled is not None and cancelled()

    if stopped() or "observation-cancelled" in observation.issues:
        issues.append("cancelled")
        return result("cancelled")
    if getattr(index, "constraint_kind", None) != observation.constraint_kind:
        issues.append("constraint-kind-mismatch")
        return result("unresolved")
    a_issues = tuple(issue for issue in observation.issues if not issue.startswith("b-"))
    if a_issues:
        issues.extend(a_issues)
        return result("insufficient-observation")
    issue = constellation_issue(observation.a.anchors, 6, observation.epsilon, "a")
    if issue:
        issues.append(issue)
        return result("insufficient-observation")
    retrieval_started = time.perf_counter()
    fingerprint = index.retrieve(observation.a, epsilon=observation.epsilon, max_work=index_max_work,
        time_budget_ms=max(0.0, (deadline - time.perf_counter()) * 1000) if deadline is not None else None,
        cancelled=cancelled)
    retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
    if not fingerprint.retrieval_complete:
        issues.extend(fingerprint.unresolved_reasons)
        if fingerprint.directory_gaps:
            issues.append("source-directory-gaps")
        return result("cancelled" if stopped() or "cancelled" in issues else "unresolved")
    if not fingerprint.fingerprint_unique:
        return result("ambiguous" if fingerprint.proposals else "no-compatible-fingerprint")
    # B availability cannot choose A or erase its map fingerprint. A-unique
    # evidence remains visible when independent pose confirmation is lacking.
    issue = constellation_issue(observation.b, 3, observation.epsilon, "b")
    if issue:
        issues.append(issue)
        return result("insufficient-heldout")
    try:
        if independence_violations(observation.a.anchors, observation.b, observation.frame_shape,
                                   observation.support_halo, cancelled=cancelled):
            issues.append("a-b-shared-measurement-support")
            return result("invalid-observation-partition")
    except _Cancelled:
        issues.append("cancelled")
        return result("cancelled")
    except ValueError:
        issues.append("measurement-support-invalid")
        return result("invalid-observation-partition")
    entries = {entry.key: entry for entry in index.entries}
    b_started = time.perf_counter()
    for proposal in fingerprint.proposals:
        check = verify_fixed_b(proposal, entries[proposal.key], observation,
            max_work=b_max_work, cancelled=cancelled, deadline=deadline)
        checks.append(FixedPoseEvidence(proposal, check))
        if check.status in ("unresolved", "cancelled"):
            issues.append(check.reason)
    b_ms = (time.perf_counter() - b_started) * 1000
    if issues:
        return result("cancelled" if stopped() or any(item.held_out.status == "cancelled" for item in checks)
                      else "unresolved")
    return result("pose-proposals" if any(item.held_out.verified for item in checks) else "heldout-rejected")
