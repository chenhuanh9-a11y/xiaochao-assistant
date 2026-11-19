"""Exit-anchored passport evidence without runtime identity or display writes."""
import time

import numpy as np

from entry_evidence_work import EntryEvidenceWork


class PassportEntryEvidence(EntryEvidenceWork):
    def __init__(self, engine, frame, floor, *, deadline=None, candidate_map_ids=None):
        self.engine, self.floor = engine, None if floor is None else str(floor)
        self.deadline = float('inf') if deadline is None else deadline
        self.items = [p for p in engine.passports
                      if (self.floor is None or p['floor'] == self.floor)
                      and (candidate_map_ids is None or p['mapId'] in candidate_map_ids)]
        self.coverage = [c for c in engine.entry_coverage if self.floor is None or c['floor'] == self.floor]
        self.rows, self.examined = [], []
        self.meta = dict(exitObserved=False, entryObserved=False, complete=True, verificationComplete=False,
                         coverageComplete=False, identityAmbiguous=True, poseAmbiguous=True)
        self.reason = None
        self.context, self.qlines, self.exits = None, [], []
        self.route_cache = {}
        super().__init__(frame if self.floor is None or self.floor in engine.floors else None)

    def _observe(self):
        observations = self.engine._entry_observations(self.frame)
        self.meta['observations'] = observations
        self.exits = [o for o in observations if o['reliable']]
        self.meta['exitObserved'] = bool(self.exits)

    def _geometry(self):
        from single_map_structure import current_part
        self.context = self.engine._geometry(self.frame)
        if self.context is not None:
            self.qlines = current_part(self.frame, self.context, 'lines')

    def _probe(self, item):
        self.item = item
        self.report = dict(mapId=item['mapId'], floor=item['floor'], sourceEntryId=item['sourceEntryId'],
                           family=item['family'], observation=self.observation_id)
        self.examined.append(self.report)
        self.fits, self.fit_cache, self.verified_matrices = [], {}, {}
        if item['directionKnown'] and self.outward not in item['outwardDirections']:
            self.report['reason'] = 'source-exit-direction-incompatible'
            return
        slines = item['knownWallLines']
        self.arrays = self.engine._entry_wall_arrays(self.qlines, slines)
        self.center = np.asarray(item['center'])
        self.initial_scale = self.width / item['unit']
        for ratio in (1., .97, 1.03, .94, 1.06):
            for anchor in (self.local_center, self.icon_center):
                scale = self.initial_scale * ratio
                translation = np.asarray(anchor) - scale * self.center
                fit = self.engine._fit_entry_local_walls(self.qlines, slines, self.arrays,
                                                          scale, translation, self.fit_cache)
                if fit is None:
                    continue
                matrix = fit['matrix']
                anchor_error = np.linalg.norm(matrix[:2, :2] @ self.center + matrix[:2, 2] - self.icon_center)
                if anchor_error > max(6., self.width * .2) or abs(matrix[0, 0] / self.initial_scale - 1) > .08:
                    continue
                self.fits.append(fit)
        self.report['partialPoses'] = len(self.fits)
        self.fits = self.engine._refined_entry_fits(self.fits)
        self.report.update(reason='local-wall-pose-candidates' if self.fits else 'exit-local-wall-geometry-incompatible',
                           poses=len(self.fits))
        if self.fits:
            self.report['localWallCorrespondenceDomain'] = dict(queryLines=self.qlines, sourceLines=slines)
            self.report['initialPoses'] = [dict(matrixSourceToClient=f['matrix'].tolist(), planes=f['planes']) for f in self.fits]

    def _build_row(self, fit):
        item, matrix = self.item, fit['matrix']
        room = self.engine._entry_room_evidence(item, matrix, self.qlines, self.context['query'])
        row = dict(mapId=item['mapId'], floor=item['floor'], region=item['region'],
                   profileId=self.engine.profile_for_map(item['mapId']), sourceEntryId=item['sourceEntryId'],
                   entryKind='side-entry-passport' if item['family'] == 'side' else 'main-entry-passport',
                   score=float(len(fit['planes'])), matrixSourceToClient=matrix.tolist(), identityVerified=False,
                   sourceProduct=item['sourceProduct'], entryEvidence=dict(
                       sourceEntryId=item['sourceEntryId'], observationId=self.observation_id, exit=self.observation,
                       directionCompatible=True, sourceDirectionKnown=item['directionKnown'],
                       localGeometryVerified=room['verified'], roomGeometry=room,
                       refinement=dict(kind='continuous-local-wall-normal-fit', planes=fit['planes'],
                                       heldOutNormals=fit['heldOutNormals'])))
        # Exact matrix equality is reused only within this source item/frame.
        key = tuple(matrix.ravel())
        if key not in self.verified_matrices:
            self.verified_matrices[key] = self.engine.verify(self.frame, row, context=self.context,
                                                            _route_cache=self.route_cache)
        row['verification'] = dict(self.verified_matrices[key])
        cross_check = self.engine.check_entry_evidence(self.frame, row, self.meta)
        row['entryEvidence']['allObservedExits'] = cross_check
        if cross_check['contradicted']:
            row['verification']['success'] = False
            row['verification']['reason'] = 'entry-other-current-exit-contradiction'
        return row

    def _verify(self, fit):
        chain, converged = [fit], False
        for iteration in range(4):
            if time.perf_counter() >= self.deadline:
                self.meta['complete'], self.reason = False, 'entry-budget-incomplete'
                return
            matrix = chain[-1]['matrix']
            next_fit = self.engine._fit_entry_local_walls(self.qlines, self.item['knownWallLines'], self.arrays,
                matrix[0, 0], matrix[:2, 2], self.fit_cache)
            if next_fit is None:
                break
            next_matrix = next_fit['matrix']
            if np.array_equal(matrix, next_matrix):
                converged = True
                break
            if (np.linalg.norm(next_matrix[:2, :2] @ self.center + next_matrix[:2, 2] - self.icon_center) > max(6., self.width * .2)
                    or abs(next_matrix[0, 0] / self.initial_scale - 1) > .08):
                break
            if any(np.array_equal(next_matrix, previous['matrix']) for previous in chain):
                break
            same_correspondence = self.engine._entry_correspondence_extends(chain[-1], next_fit)
            chain.append(next_fit)
            if not same_correspondence:
                break
        verified, complete = self.engine._verify_entry_refinement_chain(chain, converged, self._build_row, self.deadline)
        self.rows.extend(verified)
        first = next((r for r in verified if r['verification'].get('success')
                      and r['entryEvidence'].get('localGeometryVerified')), None)
        if first is not None:
            # Retain the original early pose policy and explicitly unknown rivals.
            first['competitionComplete'] = False
            self.meta.update(complete=False, verificationComplete=False, entryObserved=True,
                             selectionPolicy='first-verified-entry')
            self.reason = 'entry-first-verified'
        elif not complete:
            self.meta['complete'], self.reason = False, 'entry-budget-incomplete'
        elif len(self.rows) > 64:
            self.meta['complete'], self.reason = False, 'entry-candidate-budget-incomplete'

    def _jobs(self):
        if self.floor is not None and self.floor not in self.engine.floors:
            self.reason = 'entry-unsupported-floor'
            return
        if time.perf_counter() >= self.deadline:
            self.meta['complete'], self.reason = False, 'entry-budget-incomplete'
            return
        if (self.floor is not None and not any(p['floor'] == self.floor for p in self.engine.passports)
                and not self.engine.passport_error and not any(c['complete'] for c in self.coverage)):
            self.meta.update(coverageGaps=self.coverage, exitObserved=None,
                             observationStatus='source-inventory-incomplete')
            self.reason = 'entry-coverage-incomplete'
            return
        yield self._job(None, 'observe', self._observe)
        if not self.exits:
            self.meta['verificationComplete'], self.reason = True, 'entry-no-reliable-exit'
            return
        self.meta['coverageGaps'] = [c for c in self.coverage if not c['complete']]
        self.meta['coverageComplete'] = bool(self.coverage and not self.meta['coverageGaps'] and not self.engine.passport_error)
        if self.engine.passport_error:
            self.reason = 'entry-passport-unavailable'
            return
        if not self.meta['coverageComplete']:
            self.reason = 'entry-coverage-incomplete'
            return
        yield self._job(None, 'geometry', self._geometry)
        if self.context is None:
            self.reason = 'entry-observation-unavailable'
            return
        if not self.qlines:
            self.reason = 'entry-observed-walls-insufficient'
            return
        cardinal = {'左': 'W', '右': 'E', '上': 'N', '下': 'S'}
        for self.observation_id, self.observation in enumerate(self.exits):
            rectangle, direction = self.observation['rectangle'], self.observation['direction']
            if not direction.get('reliable') or not rectangle or not rectangle.get('success'):
                self.meta['complete'] = False
                self.examined.append(dict(observation=self.observation_id, reason='exit-direction-or-local-width-unresolved'))
                continue
            self.outward = cardinal[direction['direction']]
            l, t, r, b = rectangle['bounds']
            self.width = b - t if self.outward in ('E', 'W') else r - l
            self.icon_center = np.asarray(self.observation['icon']['center'])
            self.local_center = {'W': [l + self.width/2, (t+b)/2], 'E': [r - self.width/2, (t+b)/2],
                                 'N': [(l+r)/2, t + self.width/2], 'S': [(l+r)/2, b - self.width/2]}[self.outward]
            for item in self.items:
                if time.perf_counter() >= self.deadline:
                    self.meta['complete'], self.reason = False, 'entry-budget-incomplete'
                    return
                yield self._job(item['mapId'], 'probe', lambda item=item: self._probe(item))
                for fit in self.fits:
                    yield self._job(item['mapId'], 'verify', lambda fit=fit: self._verify(fit))
                    if self.reason is not None:
                        return
        self._reduce()

    def _reduce(self):
        self.rows, groups = self.engine._verified_entry_pose_groups(self.rows, self.context['observationBounds'])
        self.meta['equivalentVerifiedPoseGroups'] = groups
        possible = [r for r in self.rows if r['verification'].get('success')]
        families = [r for r in self.rows if r['entryEvidence'].get('localGeometryVerified')
                    and r['entryEvidence'].get('directionCompatible') and r['entryEvidence'].get('exit', {}).get('reliable')]
        side = [r for r in families if r['entryKind'] == 'side-entry-passport']
        main = [r for r in families if r['entryKind'] == 'main-entry-passport']
        self.meta['entryObserved'] = bool(side and not main and self.meta['complete'])
        self.meta['verificationComplete'] = self.meta['complete']
        self.meta['identityAmbiguous'] = len({(r['mapId'], r['floor']) for r in possible}) != 1
        self.meta['poseAmbiguous'] = len(possible) != 1
        for row in self.rows:
            row['competitionComplete'] = self.meta['complete']
            row['poseAmbiguous'] = sum(r['mapId'] == row['mapId'] and r['floor'] == row['floor'] for r in possible) > 1
        self.reason = ('entry-family-unresolved' if main or not side
                       else 'entry-local-verification-failed' if not possible and self.meta['complete']
                       else 'entry-local-competition' if self.meta['complete'] else 'entry-local-observation-incomplete')

    def finish(self, *, elapsed_ms=0.):
        if self._result is not None:
            return self._result
        self._close()
        if self._failed or not self._complete and self.reason != 'entry-first-verified':
            self.meta.update(complete=False, verificationComplete=False, identityAmbiguous=True, poseAmbiguous=True)
            self.meta.pop('selectionPolicy', None)
            self.reason = 'entry-work-failed' if self._failed else 'entry-budget-incomplete'
        self.meta.update(reason=self.reason, elapsedMs=elapsed_ms, examined=self.examined)
        self._result = self.rows, self.meta
        return self._result
