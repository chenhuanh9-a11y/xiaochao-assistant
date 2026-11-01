"""Headless production boundary for Qt workers, with no implicit live resources.

``dispatch``/``execute`` are synchronous: SessionService runs them on its worker.
Constructing this object never loads a pack, imports Tk, captures, or polls keys.
Saved inputs are diagnostics/replays and can never authorize a live overlay.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Callable, Mapping
from client_paths import APP_ROOT, RESOURCE_ROOT, WORK_ROOT

ROOT = RESOURCE_ROOT
APP = str(APP_ROOT)
if APP not in sys.path:
    sys.path.insert(0, APP)

from alignment_core_v2 import AlignmentCoreV2


INITIAL_IDENTITY_BUDGET_SECONDS = 1.85
GROUP_IDENTITY_REVIEW_BUDGET_SECONDS = .2
GROUP_IDENTITY_REVIEW_INTERVAL_SECONDS = 1.0


def clone_similarity_state(previous=None):
    """New worker-owned transaction; the published original is never mutated."""
    from dataclasses import fields
    from native_similarity_groups import NativeSimilarityState
    values = ({field.name: copy.deepcopy(getattr(previous, field.name))
               for field in fields(NativeSimilarityState) if field.init and field.name != "owner"}
              if previous is not None else {})
    return NativeSimilarityState(**values)


def stage_similarity_rank(previous, rows, context, observation_id, display_members=(), *, group_only=False):
    """Replay the legacy native state consumer without altering rank verdicts."""
    proposal = copy.deepcopy((rows or [{}])[0].get("nativeSimilarity") or {"status": "none"})
    state = clone_similarity_state(previous)
    if proposal.get("mode") not in (None, context[0]) or proposal.get("floor") not in (None, context[2]):
        state.reset()
        return state, dict(status="none", reason="similarity-context-mismatch")
    if proposal.get("originalIdentityHardGatePassed"):
        verified = [row for row in rows if row.get("identityHardGatePassed") is True]
        if len(verified) != 1 or verified[0]["mapId"] != proposal.get("candidateMemberId"):
            proposal["originalIdentityHardGatePassed"] = False
    # Every current explicit rank is an observation. The original consumer
    # calls accept even for status=none, whose existing reset revokes the group.
    # Passive group review does not call this complete-rank consumer.
    state.accept(proposal, context, observation_id)
    if group_only and len(state.member_ids)>1:
        state.pending_identity=state.verified_member_id=state.member_reference=None
        if state.group_id:
            state.state="GROUP_LOCKED"
            state.provisional_member_id=initial_group_display_member(state, display_members)
    if not group_only and state.state == "GROUP_LOCKED" and state.context_snapshot() and context[2] == "1":
        state.prefer_display_member(display_members)
    return state, proposal


def similarity_evidence_summary(proposal, state):
    """Bounded identity diagnostics; never retain frames or packed samples."""
    observations = proposal.get("conditionObservations") or ()
    return dict(status=str(proposal.get("status", "none"))[:64],
        complete=proposal.get("complete") is True, observationCount=len(observations),
        foreignCompetitionCompleteCount=sum(o.get("completeForeignMapCompetition") is True for o in observations),
        currentCompetitionCompleteCount=sum(o.get("currentCompetitionComplete") is True for o in observations),
        ambiguousCount=sum(bool(o.get("ambiguousViews")) for o in observations),
        conflictCount=sum(bool(o.get("conflictingViews")) for o in observations),
        outsideCount=sum(bool(o.get("outsideCompetitors")) for o in observations),
        eligibleMembers=list(state.member_ids)[:16])


def initial_group_display_member(state, verified_members=()):
    """Choose an initial geometry witness, never a unique member verdict."""
    members=set(state.member_ids)
    seeded=members.intersection(verified_members)
    current=state.provisional_member_id
    if current in seeded:
        return current
    if seeded:
        return min(seeded)
    return current if current in members else min(members) if members else None


def is_registered_identity_group(state):
    return bool(state is not None and state.state == "GROUP_LOCKED"
                and state.condition_reason == "registered-logical-family"
                and state.group_id and len(state.member_ids) > 1)


def ranked_entrance_kind(engine, rows, floor):
    """Current producer evidence, never an advisory label or a prior group."""
    top = (rows or [{}])[0]
    for position, row in enumerate(rows):
        entry = row.get("difficultEntry") or {}
        # Main-entry rank(frame, floor=...) omits floor. Reject a supplied
        # mismatch; the invocation is authoritative when the field is absent.
        if entry.get("mapId") != row.get("mapId") or str(entry.get("floor", floor)) != str(floor):
            continue
        kind = entry.get("entryKind")
        front = kind in ("main", "main-entry-passport")
        # Passport solving uses locatorKind='side' even for main passports.
        # The producer's semantic entryKind takes precedence over that solver.
        if position == 0 and not front and (entry.get("locatorKind") == "side" or kind in (
                "side-entry-passport", "side-neighborhood", "local-structure")):
            return "side"
        checked = entry.get("verification") or {}
        # This is the original native front-witness gate, not a new full-fit
        # requirement: a provisional group can precede verification.success.
        front_geometry = (checked.get("success") is True or
            ((checked.get("regions") or {}).get("regionalEdgeVerified") is True
             and (checked.get("residual") or {}).get("verified") is True))
        if (front and front_geometry and not entry.get("poseAmbiguous")
                and (entry.get("entryCrossCheck") or {}).get("contradicted") is not True):
            return "front"
    # A fresh native condition is also real front-entry evidence. Established
    # conditions can be continued by side poses, so never use those as an entry.
    proposal = top.get("nativeSimilarity") or {}
    index = getattr(engine, "native_similarity_index", None)
    conditions = getattr(index, "condition_index", None)
    if (conditions is not None and proposal.get("mode") == engine.pack.mode
            and str(proposal.get("floor")) == str(floor)
            and proposal.get("resourceKey") == getattr(index, "index_digest", None)):
        for observed in proposal.get("conditionObservations", ()):
            condition = conditions.conditions.get(str(observed.get("conditionId")), {})
            if (condition.get("kind") == "front" and condition.get("mode") == engine.pack.mode
                    and str(condition.get("floor")) == str(floor)
                    and observed.get("domainId") == condition.get("domainId")
                    and observed.get("entryEstablished") is False
                    and observed.get("completeForeignMapCompetition") is True
                    and observed.get("currentCompetitionComplete") is True
                    and observed.get("activeViews") and not observed.get("ambiguousViews")
                    and not observed.get("outsideCompetitors")):
                return "front"
    return "unknown"


def registered_logical_family(engine, map_id, floor, *, entrance_kind="unknown", required_members=()):
    """Only schema-1 explicit map-level registrations can define a family.

    Schema-2 finite local observation cohorts are not map-level registrations.
    Their original current-frame consumer may establish a group; this adapter
    neither expands that group nor promotes an unobserved compatibility cohort.
    Current-floor resources and pose remain independently verified by Runtime.
    """
    if str(floor) != "1" or entrance_kind != "front":
        return dict(status="none",reason="outside-reviewed-front-entry-scope")
    index=getattr(engine,"qt_logical_group_registry",None)
    if index is None:return dict(status="none",reason="no-authoritative-map-level-registration")
    try:
        if getattr(index,"error",None):raise ValueError("similarity-registration-invalid")
        if getattr(index,"mode",None)!=engine.pack.mode:raise ValueError("similarity-registration-mode")
        if getattr(index,"condition_index",None) is not None:
            return dict(status="none",reason="no-authoritative-map-level-registration")
        group=index.group_for(map_id, floor=str(floor), entrance_kind=entrance_kind)
        if group is None:return dict(status="none")
        members=tuple(sorted(group["memberIds"]))
        if len(members)<2 or not set(members)<=set(engine.pack.by_id):
            raise ValueError("similarity-registration-members")
        if required_members and not set(required_members)<=set(members):
            raise ValueError("similarity-registration-observation-mismatch")
        index.validate(group,engine.qt_shared_provider)
        return dict(status="group",candidates=[dict(groupId=str(group["groupId"]),
                    memberIds=list(members),resourceKey=index.index_digest)])
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
        return dict(status="unavailable",reason=str(error))


def automatic_identity_candidates(rows, state):
    """Expose an established family once, never member-vs-member verdicts.

    The unchanged engine may measure member features to discover a family.
    These are evidence for one logical candidate, not unique member identity.
    """
    members=tuple(sorted(set(state.member_ids))) if state is not None else ()
    grouped=bool(state is not None and state.state=="GROUP_LOCKED" and len(members)>1)
    if not grouped:
        return [dict(row,identityKind="map",key=str(row["mapId"])) for row in rows]
    key="group:"+hashlib.sha256("\0".join(members).encode()).hexdigest()[:16]
    evidence=[row for row in rows if row.get("mapId") in members]
    scores=[r["score"] for r in evidence if isinstance(r.get("score"),(int,float))]
    group=dict(key=key,mapId="",identityKind="group",groupId=state.group_id,memberIds=list(members),
               name="相似地图组",score=max(scores) if scores else None,
               identityHardGatePassed=False,strongSuggestion=False)
    return [group,*[dict(row,identityKind="map",key=str(row["mapId"]))
                   for row in rows if row.get("mapId") not in members]]


class EngineAdapter:
    """Inject ``{mode: factory}`` of configured MapIdentityEngine instances.

    A factory is called on the requesting worker, once per mode. It must bind
    the real pack and shared resource providers; no fallback mode is inferred.
    The factory/engine must not own UI, capture, listeners, or installed config.
    """

    MODES = ("nightmare", "difficult")
    OFFLINE = ("load_saved_result", "saved_result", "load_saved_pose",
               "set_game_mode", "set_route_profile", "set_context", "cancel",
               "stop_round", "reset_round")
    ALIASES = {"saved_frame": "rank_saved_frame", "recognize": "rank_saved_frame",
               "saved_result": "load_saved_result", "replay": "load_saved_result"}

    def __init__(self, *, engine_factories: Mapping[str, Callable] | None = None,
                 allowed_read_root: Path | None = None, clock=None):
        self._provider_registry = (engine_factories if callable(getattr(engine_factories, "capabilities", None))
                                   and callable(getattr(engine_factories, "prepare", None)) else None)
        self._factories = dict(engine_factories or {})
        if set(self._factories) - set(self.MODES):
            raise ValueError("engine factories must name nightmare or difficult")
        self._read_root = Path(allowed_read_root or WORK_ROOT).resolve()
        self._engines = {}
        self._engine_locks = {mode: threading.Lock() for mode in self.MODES}
        self._lock = threading.RLock()
        self._core = AlignmentCoreV2(clock=clock)
        self._revision = 0
        self._context = dict(mode="nightmare", profileId="", mapId="", floor="1",
                             roundId=0, cycleId=0, frameId="", generation=0)

    def snapshot(self):
        with self._lock:
            commands = list(self.OFFLINE)
            capabilities = (self._provider_registry.capabilities() if self._provider_registry is not None
                            else {mode: dict(available=mode in self._factories,
                                             reason="configured-factory" if mode in self._factories else "factory-unavailable")
                                  for mode in self.MODES})
            if self._provider_registry is not None:
                commands.append("inspect_engine_resources")
            if capabilities[self._context["mode"]]["available"]:
                commands += ["rank_saved_frame", "saved_frame", "recognize"]
            return dict(**copy.deepcopy(self._context), engineConnected=True,
                        adapterConnected=True, liveEngineAvailable=False,
                        savedFrameEngineAvailable=capabilities[self._context["mode"]]["available"],
                        savedFrameCapabilities=capabilities,
                        roundActive=False, roundPreparing=False,
                        availableCommands=commands, liveAuthorization=False,
                        core=self._core.snapshot(),
                        message="Saved replay is available. Live recognition is not attached.",
                        unavailableReason="live-recognition-orchestration-not-extracted")

    @staticmethod
    def _cancelled(cancelled):
        if cancelled is None:
            return False
        return bool(cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled())

    def _request_context(self, payload):
        context = copy.deepcopy(self._context)
        supplied = dict(payload.get("context") or {})
        supplied.update({key: payload[key] for key in context if key in payload})
        context.update({key: supplied[key] for key in context if key in supplied})
        if "roundId" in supplied and "cycleId" not in supplied:
            context["cycleId"] = supplied["roundId"]
        if "cycleId" in supplied and "roundId" not in supplied:
            context["roundId"] = supplied["cycleId"]
        if context["mode"] not in self.MODES:
            raise ValueError("unsupported mode")
        context["floor"] = str(context["floor"])
        if context["floor"] not in ("B1", "1", "2"):
            raise ValueError("unsupported floor")
        return context

    @staticmethod
    def _receipt(action, context, *, success=False, reason="", scope="none", **extra):
        return dict(action=action, **copy.deepcopy(context), success=success,
                    reason=reason, message=reason, liveAuthorization=False,
                    authorization=dict(live=False, scope=scope), **extra)

    def _path(self, value, *, maximum=512 * 1024):
        path = Path(value).resolve()
        if not path.is_relative_to(self._read_root):
            raise ValueError("saved input must be beneath allowed_read_root")
        if not path.is_file() or path.stat().st_size > maximum:
            raise ValueError("saved input missing or exceeds size bound")
        return path

    def execute(self, action, context=None, cancel_event=None):
        """SessionService compatibility facade; ``context`` is the full payload."""
        return self.dispatch(action, context or {}, cancel_event)

    def invalidate_resources(self):
        with self._lock:
            self._revision += 1
            self._engines.clear()
            self._core.reset_round(reason="resources-changed")
            if self._provider_registry is not None:
                self._provider_registry.invalidate()

    def dispatch(self, action, payload=None, cancelled=None):
        payload = dict(payload or {})
        action = self.ALIASES.get(str(action), str(action))
        with self._lock:
            try:
                context = self._request_context(payload)
            except (TypeError, ValueError) as error:
                return self._receipt(action, self._context, reason=str(error), error=True)
            revision = self._revision
        result = lambda **values: self._receipt(action, context, **values)
        if self._cancelled(cancelled):
            return result(reason="cancelled", cancelled=True)
        if action in ("set_game_mode", "set_route_profile", "set_context"):
            with self._lock:
                self._context = context
                self._revision += 1
                self._core.reset_round(reason=action)
                return result(success=True, reason="context-updated", snapshot=self.snapshot())
        if action in ("cancel", "stop_round", "reset_round"):
            with self._lock:
                self._revision += 1
                if action == "reset_round":
                    self._core.reset_round(reason=action)
                else:
                    self._core.close(reason=action)
                return result(success=True, reason=action, snapshot=self.snapshot())
        try:
            if action == "inspect_engine_resources" and self._provider_registry is not None:
                data = dict(capabilities=self._provider_registry.prepare(cancelled), diagnosticOnly=True)
                scope = "resource-inventory"
            elif action == "rank_saved_frame":
                data = self._rank_saved_frame(payload, context, cancelled)
                scope = "saved-frame-diagnostic"
            elif action == "load_saved_result":
                from replay_adapter import load_render_replay
                data = dict(replay=load_render_replay(self._path(payload["path"]), self._read_root),
                            diagnosticOnly=True, recordedMode=None)
                scope = "historical-replay"
            elif action == "load_saved_pose":
                from replay_adapter import load_pose
                data = dict(pose=load_pose(self._path(payload["path"]), context["floor"]),
                            diagnosticOnly=True, evidenceKind="pose-snapshot-input-unverified")
                scope = "pose-replay"
            else:
                return result(reason="live-recognition-orchestration-not-extracted"
                              if action in ("start_round", "prepare_round", "recognize_live", "start")
                              else "unsupported-action", unavailable=True)
            with self._lock:
                if self._cancelled(cancelled):
                    return result(reason="cancelled", cancelled=True)
                if self._revision != revision:
                    return result(reason="stale-context", stale=True)
            # SessionService compares the echoed generation/frame/mode context
            # before publishing. Historical IDs live inside replay/pose only.
            return result(success=True, reason="completed", scope=scope, **data)
        except EngineUnavailable as error:
            return result(reason=str(error), unavailable=True)
        except Exception as error:
            return result(reason=f"{type(error).__name__}: {error}", error=True)

    def _rank_saved_frame(self, payload, context, cancelled):
        mode = context["mode"]
        if mode not in self._factories:
            raise EngineUnavailable(f"saved-frame-engine-unavailable:{mode}")
        from PIL import Image
        import cv2
        import numpy as np

        path = self._path(payload["path"], maximum=64 * 1024 * 1024)
        # PIL checks dimensions before OpenCV allocates the decoded BGR frame.
        with Image.open(path) as opened:
            if opened.width * opened.height > 16_000_000:
                raise ValueError("saved frame exceeds pixel bound")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        expected = payload.get("sha256")
        if expected is not None and str(expected).lower() != digest:
            raise ValueError("saved frame checksum mismatch")
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("unable to decode saved frame")
        budget = float(payload.get("budgetSeconds", 1.0))
        if not np.isfinite(budget) or not 0 < budget <= 10:
            raise ValueError("budgetSeconds must be finite and in (0, 10]")
        with self._engine_locks[mode]:
            if self._cancelled(cancelled):
                return {}
            engine = self._engines.get(mode)
            if engine is None:
                try:
                    engine = self._factories[mode]()
                except Exception as error:
                    if getattr(error, "unavailable", False):
                        raise EngineUnavailable(str(error)) from error
                    raise
                if getattr(getattr(engine, "pack", None), "mode", None) != mode:
                    raise ValueError("engine pack mode does not match requested mode")
                self._engines[mode] = engine
            if context["floor"] not in tuple(map(str, engine.identity_floors)):
                raise EngineUnavailable(f"saved-frame-floor-unavailable:{mode}/{context['floor']}")
            if self._cancelled(cancelled):
                return {}
            # This is the existing shared production entry, not an adapter solve.
            # Its budget is cooperative; it has no cancellation callback. Cancel
            # is checked before and after rank, and obsolete output is discarded.
            rows = engine.rank(frame, floor=context["floor"], limit=len(engine.pack.maps),
                               budget_seconds=budget)
            diagnostics = copy.deepcopy(getattr(engine, "last_difficult_evidence", None))
            candidates = copy.deepcopy(rows)
        # Shared rank output may contain NumPy matrices/scalars. Keep the
        # boundary JSON/QVariant compatible without copying solver logic.
        def native(value):
            if isinstance(value, np.ndarray):
                return value.tolist()
            if isinstance(value, np.generic):
                return value.item()
            raise TypeError(f"non-serializable engine result: {type(value).__name__}")
        serial = json.loads(json.dumps(dict(candidates=candidates, diagnostics=diagnostics),
                                     default=native, allow_nan=False))
        return dict(**serial,
                    diagnosticOnly=True, identityOrPoseAuthorized=False,
                    evidenceKind={"synthetic": "saved-synthetic-frame",
                                  "recorded": "saved-recorded-frame"}.get(
                                      payload.get("inputKind"), "saved-frame-input-unverified"),
                    inputSha256=digest, frameSize=[frame.shape[1], frame.shape[0]])


class EngineUnavailable(RuntimeError):
    """A missing resource/provider seam, not recognition success."""
