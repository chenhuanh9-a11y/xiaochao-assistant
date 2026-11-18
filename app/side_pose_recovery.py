"""Recover source poses without changing original scope or runtime state."""
import time

import numpy as np

from entry_evidence_work import EntryEvidenceWork


class SidePoseRecovery(EntryEvidenceWork):
    def __init__(self, engine, frame, floor, rows, diagnostics, *, entry_rows, passport_diagnostics,
                 deadline, adjudicate, equivalent_poses):
        self.engine, self.floor = engine, str(floor)
        self.rows, self.diagnostics = rows, diagnostics
        self.entry_rows, self.passport = entry_rows, passport_diagnostics
        self.deadline, self.adjudicate, self.equivalent_poses = deadline, adjudicate, equivalent_poses
        self.locator = engine.side_entry_identity
        self.check_entry = getattr(self.locator, 'check_entry_evidence', None)
        self.winner = None
        self.original_proposals, self.proposals, self.accepted, self.unresolved = [], [], [], []
        self.evaluated, self.completed = 0, set()
        self.priority_applied = self.plan_ready = self.budget_incomplete = False
        self.equivalent = False
        super().__init__(frame)

    def _initial_refine(self):
        self.rows[0] = self.locator.refine_candidate(self.frame, self.rows[0])
        self.diagnostics = dict(self.diagnostics,
            continuousRefinementMs=self.rows[0].get('continuousRefinementMs'),
            continuousRefinementReason=self.rows[0].get('continuousRefinementReason'))

    def _cross_check(self, row):
        row['entryCrossCheck'] = self.check_entry(self.frame, row, observation=self.passport)

    def _plan_recovery(self):
        self.top = max((float(r['score']) for r in self.rows), default=0.)
        self.original_proposals = self.engine._side_recovery_proposals(self.rows)
        reordered = self.engine._native_similarity_prioritize(self.rows, self.floor)
        self.priority_applied = any(a is not b for a, b in zip(reordered, self.rows))
        self.proposals = (self.engine._side_recovery_proposals(reordered)
                          if self.priority_applied else self.original_proposals)
        self.plan_ready = True

    def _recover(self, candidate):
        if not (candidate.get('verification') or {}).get('evaluated', True):
            candidate['verification'] = self.locator.verify(self.frame, candidate)
        if not candidate['verification'].get('success') and not candidate.get('continuousRefinementReason'):
            candidate.update(self.locator.refine_candidate(self.frame, candidate))
        if self.check_entry is not None and self.passport is not None:
            self._cross_check(candidate)
        self.evaluated += 1
        self.completed.add(id(candidate))
        if (candidate['verification'].get('success')
                and (candidate.get('entryCrossCheck') or {}).get('contradicted') is not True):
            self.accepted.append(candidate)

    def _collect_required(self):
        self.diagnostics = dict(self.diagnostics, poseRecoveryEvaluated=self.evaluated,
                                poseRecoveryCandidates=len(self.proposals))
        if not self.priority_applied:
            return
        required = list(self.original_proposals)
        for row in self.rows:
            if (row.get('verification') or {}).get('success') or float(row['score']) >= max(4, self.top-1):
                if not any(row is old for old in required):
                    required.append(row)
        self.unresolved = [row['mapId'] for row in required
                           if id(row) not in self.completed
                           and not (row.get('verification') or {}).get('success')
                           and (not row.get('continuousRefinementReason')
                                or (row.get('verification') or {}).get('evaluated', True) is False)]
        for row in required:
            if ((row.get('verification') or {}).get('success')
                    and (row.get('entryCrossCheck') or {}).get('contradicted') is not True
                    and not any(row is old for old in self.accepted)):
                self.accepted.append(row)
        self.diagnostics['groupPriorityUnverifiedCompetitors'] = self.unresolved

    def _observe_equivalence(self):
        _, nodes, _ = self.locator._observe(self.frame)
        points = np.array([[*n['point'], 1.] for n in nodes])
        self.equivalent = self.equivalent_poses(self.accepted, points)

    def _jobs(self):
        if (self.rows and self.diagnostics.get('complete')
                and self.diagnostics.get('competitiveHypotheses') == 1
                and time.perf_counter() < self.deadline
                and not (self.rows[0].get('verification') or {}).get('success')):
            yield self._job(self.rows[0]['mapId'], 'initial-refine', self._initial_refine)
        if self.check_entry is not None and self.passport is not None and self.rows:
            band = max(4, max(float(row['score']) for row in self.rows)-1)
            for row in self.rows:
                if float(row['score']) >= band:
                    yield self._job(row['mapId'], 'entry-cross-check', lambda row=row: self._cross_check(row))
        self.winner = self.adjudicate(self.rows, self.diagnostics, self.floor, self.entry_rows,
                                     self.frame.shape, profile_id=self.engine.identity_profile_for_map)
        if self.winner is not None or not self.diagnostics.get('complete') or time.perf_counter() >= self.deadline:
            return
        yield self._job(None, 'plan-recovery', self._plan_recovery)
        for candidate in self.proposals[:3]:
            if time.perf_counter() >= self.deadline:
                self.budget_incomplete = True
                break
            yield self._job(candidate['mapId'], 'recover-pose', lambda candidate=candidate: self._recover(candidate))
        self._collect_required()
        if self.budget_incomplete:
            return
        self.equivalent = len(self.accepted) == 1
        if len(self.accepted) > 1 and len({r['mapId'] for r in self.accepted}) == 1:
            yield self._job(self.accepted[0]['mapId'], 'observe-equivalence', self._observe_equivalence)
        if self.unresolved:
            self.equivalent = False
        self.diagnostics['poseRecoveryEquivalent'] = self.equivalent
        if self.evaluated == len(self.proposals) and self.accepted and self.equivalent:
            candidate = dict(self.accepted[0], poseAmbiguous=False)
            self.winner = self.adjudicate([candidate], dict(complete=True, verificationComplete=True,
                competitiveHypotheses=1, identityAmbiguous=False), self.floor, self.entry_rows,
                self.frame.shape, profile_id=self.engine.identity_profile_for_map)

    def finish(self):
        if self._result is not None:
            return self._result
        self._close()
        if self._failed or not self._complete or self.budget_incomplete:
            if self.plan_ready:
                self._collect_required()
                self.diagnostics['poseRecoveryEquivalent'] = False
            self.winner = None
            self.diagnostics = dict(self.diagnostics, complete=False, verificationComplete=False,
                reason='side-recovery-work-failed' if self._failed else 'side-recovery-budget-incomplete')
        self._result = self.rows, self.diagnostics, self.winner, self.original_proposals
        return self._result
