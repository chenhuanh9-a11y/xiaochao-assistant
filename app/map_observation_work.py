"""Entry evidence dependencies, driven one kernel at a time by the scanner.

The plan produces qualified source evidence. It never writes runtime identity,
selects a display or treats an unfinished competitor as a rejection.
"""
import time

import cv2

from entry_evidence_work import EntryEvidenceWork


class MapObservationWork(EntryEvidenceWork):
    def __init__(self, engine, frame, floor, *, deadline, prior=None):
        self.engine, self.floor = engine, str(floor)
        self.started, self.deadline = time.perf_counter(), deadline
        self.allowed = None
        self.prior, self.established, self.scope_invalid = prior, prior is not None, False
        self.entry_complete, self.rows, self.proposal = False, None, None
        self.advisory = dict(candidates=[], entryKind='unknown')
        self.gate_report = None
        self.passport_rows, self.passport_diagnostics = [], None
        self.entry_rows = []
        self.entry_diagnostics = dict(reason='side-entry-path-selected', complete=True)
        self.side_rows, self.side_winner, self.original_group_recovery = [], None, []
        self.side_diagnostics = (dict(reason='side-resource-unavailable', error=engine.side_entry_error,
                                      complete=False) if engine.side_entry_identity is None else None)
        self.passport_selected = self.entry_unresolved = False
        self.fallback_allowed, self.side_producer = True, 'not-run'
        self.errors = {}
        self.source_candidates = dict(candidates=[], reason='source-candidate-store-unavailable',
            complete=True, evaluatedEntryCount=0)
        super().__init__(frame)

    def _prefilter(self):
        if self.established:
            members = self.engine.established_candidate_scope(prior=self.prior)
            self.scope_invalid = members is None
            self.allowed = set(members) if members is not None else set()
            return
        prefilter = getattr(self.engine, 'front_gate_prefilter', None)
        if prefilter is not None and self.allowed is None:
            self.allowed, self.gate_report = prefilter.select(self.frame, self.floor, self.engine.entry_identity.geometry)

    def _delegate(self, kind, work, accept, *, elapsed=True):
        """Close and collect even when the common driver stops between jobs."""
        try:
            while (job := work.next_job()) is not None:
                def run(job=job):
                    try:
                        job.run()
                    except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
                        if kind in ('main', 'source'):
                            raise
                        self.errors[kind] = str(error)
                yield self._job(job.map_id, kind + ':' + job.phase, run)
        finally:
            result = work.finish() if not elapsed else work.finish(
                elapsed_ms=(time.perf_counter() - self.started) * 1000)
            accept(result)

    def _passport(self, result):
        self.passport_rows, self.passport_diagnostics = result
        if 'passport' in self.errors:
            self.passport_diagnostics = dict(reason='entry-resource-unavailable', error=self.errors['passport'],
                exitObserved=None, entryObserved=False, complete=False)

    def _main(self, result):
        self.entry_rows, self.entry_diagnostics = result
        self.engine._apply_shared_entry_inventory(self.entry_rows, self.entry_diagnostics,
            self.engine._shared_entry_inventory(self.floor))

    def _cross_check(self, row):
        candidate = dict(row, floor=self.floor, profileId=self.engine.identity_profile_for_map(row['mapId']),
            matrixSourceToClient=row['verification']['matrixSourceToClient'])
        try:
            checked = self.engine.side_entry_identity.check_entry_evidence(
                self.frame, candidate, observation=self.passport_diagnostics)
        except (ValueError, TypeError, KeyError, OSError, cv2.error):
            checked = dict(reason='entry-cross-check-unavailable')
            row['identityVerified'] = row['poseVerified'] = False
        row['entryCrossCheck'] = checked
        if checked.get('contradicted') is True:
            row['identityVerified'] = row['poseVerified'] = False

    def _side(self, result):
        self.side_rows, self.side_diagnostics = result
        if 'side' in self.errors:
            self.side_diagnostics = dict(reason='side-resource-unavailable', error=self.errors['side'], complete=False)

    def _recovery(self, result):
        self.side_rows, self.side_diagnostics, self.side_winner, self.original_group_recovery = result
        if 'recovery' in self.errors:
            self.side_diagnostics = dict(reason='side-resource-unavailable', error=self.errors['recovery'], complete=False)

    def _source(self, result):
        self.source_candidates = result

    def _advisory(self):
        self.advisory = self.engine.entry_advisory(self.frame)

    def _proposal(self, result):
        self.proposal = result

    def _required_members(self):
        front_top = max((float(row['score']) for row in self.entry_rows), default=0.)
        side_top = max((float(row['score']) for row in self.side_rows), default=0.)
        members = {row['mapId'] for row in self.entry_rows if float(row['score']) >= max(10, front_top*.65)}
        members.update(row['mapId'] for row in self.side_rows if float(row['score']) >= max(4, side_top-1)
                       or (row.get('verification') or {}).get('success'))
        members.update(row['mapId'] for row in self.original_group_recovery)
        return tuple(sorted(members))

    def _jobs(self):
        from map_identity_engine import entry_passport_fallback_allowed, verified_entry_candidate
        if self.floor not in self.engine.identity_floors:
            return
        yield self._job(None, 'entry-scope', self._prefilter)
        if self.scope_invalid:
            return
        options = dict(candidate_map_ids=self.allowed) if self.allowed is not None else {}
        side = self.engine.side_entry_identity
        if callable(getattr(side, 'prepare_rank_entry', None)):
            yield from self._delegate('passport', side.prepare_rank_entry(self.frame_snapshot,
                self.floor, deadline=self.deadline, **options), self._passport)
        self.fallback_allowed = entry_passport_fallback_allowed(self.passport_diagnostics)
        self.passport_selected = bool(self.passport_diagnostics and not self.fallback_allowed
            and self.passport_diagnostics.get('entryObserved') is True)
        self.entry_unresolved = self.passport_diagnostics is not None and not self.fallback_allowed and not self.passport_selected
        if self.entry_unresolved:
            self.entry_diagnostics = dict(reason=self.passport_diagnostics['reason'], complete=False)
        if not self.passport_selected and not self.entry_unresolved:
            yield from self._delegate('main', self.engine.entry_identity.prepare_rank(
                self.frame_snapshot, self.floor, **options), self._main)
            if callable(getattr(side, 'check_entry_evidence', None)) and self.passport_diagnostics is not None:
                for row in self.entry_rows:
                    if row.get('identityVerified'):
                        yield self._job(row['mapId'], 'main:exit-cross-check', lambda row=row: self._cross_check(row))
        if self.passport_selected:
            self.side_producer = 'passport'
            self.side_rows, self.side_diagnostics = self.passport_rows, self.passport_diagnostics
            self.side_winner = verified_entry_candidate(self.side_rows, self.side_diagnostics,
                self.floor, profile_id=self.engine.identity_profile_for_map)
        if (not self.passport_selected and self.fallback_allowed
                and not any(row['identityVerified'] for row in self.entry_rows) and side is not None):
            self.side_producer = 'local-structure'
            yield from self._delegate('side', side.prepare_rank(self.frame_snapshot, self.floor,
                deadline=self.deadline, **options), self._side)
            if 'side' not in self.errors:
                yield from self._delegate('recovery', self.engine.prepare_side_recovery(
                    self.frame_snapshot, self.floor, self.side_rows, self.side_diagnostics,
                    entry_rows=self.entry_rows, passport_diagnostics=self.passport_diagnostics,
                    deadline=self.deadline), self._recovery, elapsed=False)
        self.entry_complete = True
        if self.established:
            self.source_candidates = dict(candidates=[], candidateCount=0, reason='established-group-only')
        else:
            yield from self._delegate('source', self.engine.prepare_source_structure_candidates(
                self.frame_snapshot, floor=self.floor, deadline=self.deadline), self._source, elapsed=False)
            if not any(row['identityVerified'] for row in self.entry_rows) and self.side_winner is None and self.engine.entry_advisory is not None:
                yield self._job(None, 'entry-icon-advisory', self._advisory)
        self.rows = self.engine.collect_observation_rows(self)
        index = self.engine.native_similarity_index
        if self.rows and index is not None:
            context = getattr(self.engine, 'native_similarity_context_provider', None)
            proposal = index.prepare_proposal(self.rows, self.entry_rows, self.entry_diagnostics,
                self.floor, self.engine.entry_shared_reference_provider, lambda: self.engine.entry_identity.geometry(self.frame),
                side_rows=self.side_rows, side_diagnostics=self.side_diagnostics, deadline=self.deadline,
                prior=self.prior if self.established else context() if context is not None else None,
                required_map_ids=self._required_members())
            yield from self._delegate('condition', proposal, self._proposal, elapsed=False)

    def finish(self):
        if self._result is None:
            self._close()
            if self.floor not in self.engine.identity_floors or self.scope_invalid:
                self._result = []
                return self._result
            if not self.entry_complete or self._failed:
                for row in self.entry_rows:
                    row['identityVerified'] = row['poseVerified'] = False
                self.side_winner = None
                self.entry_diagnostics = dict(self.entry_diagnostics, complete=False,
                    verificationComplete=False, reason='entry-observation-incomplete')
                self.rows = None
            self._result = self.rows if self.rows is not None else self.engine.collect_observation_rows(self)
            if self._result and self.proposal is not None:
                self._result[0]['nativeSimilarity'] = self.proposal
            for row in self._result:
                row['difficultRecognition']['elapsedMs'] = (time.perf_counter() - self.started) * 1000
        return self._result
