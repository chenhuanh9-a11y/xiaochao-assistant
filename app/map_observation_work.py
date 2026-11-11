"""Entry evidence dependencies, driven one kernel at a time by the scanner.

The plan produces qualified source evidence. It never writes runtime identity,
selects a display or treats an unfinished competitor as a rejection.
"""
import time

import cv2

from entry_evidence_work import EntryEvidenceWork


class MapObservationWork(EntryEvidenceWork):
    def __init__(self, engine, frame, floor, *, deadline):
        self.engine, self.floor = engine, str(floor)
        self.started, self.deadline = time.perf_counter(), deadline
        self.allowed = None
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
        prefilter = getattr(self.engine, 'front_gate_prefilter', None)
        if prefilter is not None and self.allowed is None:
            self.allowed, self.gate_report = prefilter.select(self.frame, self.floor, self.engine.entry_identity.geometry)

    def _delegate(self, kind, work, accept, *, recovery=False):
        """Close and collect even when the common driver stops between jobs."""
        try:
            while (job := work.next_job()) is not None:
                def run(job=job):
                    try:
                        job.run()
                    except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
                        if kind == 'main':
                            raise
                        self.errors[kind] = str(error)
                yield self._job(job.map_id, kind + ':' + job.phase, run)
        finally:
            result = work.finish() if recovery else work.finish(
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

    def _recovery(self, result):
        self.side_rows, self.side_diagnostics, self.side_winner, self.original_group_recovery = result

    def _jobs(self):
        from map_identity_engine import entry_passport_fallback_allowed, verified_entry_candidate
        if self.floor not in self.engine.identity_floors:
            return
        yield self._job(None, 'entry-scope', self._prefilter)
        options = dict(candidate_map_ids=self.allowed) if self.allowed is not None else {}
        side = self.engine.side_entry_identity
        if side is not None:
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
            yield from self._delegate('recovery', self.engine.prepare_side_recovery(
                self.frame_snapshot, self.floor, self.side_rows, self.side_diagnostics,
                entry_rows=self.entry_rows, passport_diagnostics=self.passport_diagnostics,
                deadline=self.deadline), self._recovery, recovery=True)

    def finish(self):
        if self._result is None:
            self._close()
            if self.floor not in self.engine.identity_floors:
                self._result = []
                return self._result
            if not self._complete:
                for row in self.entry_rows:
                    row['identityVerified'] = row['poseVerified'] = False
                self.side_winner = None
                self.entry_diagnostics = dict(self.entry_diagnostics, complete=False,
                    verificationComplete=False, reason='entry-observation-incomplete')
            self._result = self.engine.collect_observation_rows(self)
        return self._result
