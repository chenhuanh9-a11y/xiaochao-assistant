"""Resumable side structure search; measured poses do not write runtime state."""
import time

import cv2
import numpy as np

from entry_evidence_work import EntryEvidenceWork


class SideEntryEvidence(EntryEvidenceWork):
    def __init__(self, engine, frame, floor, *, map_id=None, deadline=None, candidate_map_ids=None,
                 prepared_index=None):
        self.engine, self.floor, self.map_id = engine, None if floor is None else str(floor), map_id
        self.deadline = float('inf') if deadline is None else deadline
        self.entries = [entry for entry in engine.entries
                        if (map_id is None or entry[0]['mapId'] == map_id)
                        and (self.floor is None or entry[0]['floor'] == self.floor)
                        and (candidate_map_ids is None or entry[0]['mapId'] in candidate_map_ids)]
        self.local_search = engine.side_search if self.floor == '2' and map_id is None else None
        self.prepared_index = prepared_index
        actual_domain = {(entry[0]['mapId'], entry[0]['floor']) for entry in self.entries}
        # The prepared domain is a promise about every rival, including one
        # removed from the live locator after preparation. Reconcile the same
        # requested scope against actual work sources, not only old build gaps.
        self.index_directory_gaps = tuple(row for row in getattr(prepared_index, 'domain', ())
            if (self.floor is None or row[1] == self.floor) and (map_id is None or row[0] == map_id)
            and (candidate_map_ids is None or row[0] in candidate_map_ids) and row not in actual_domain)
        self.context, self.q, self.qr, self.qpairs = None, [], [], []
        self.rows, self.competitors, self.active = [], [], []
        self.scan_complete = self.probes_complete = self.verification_complete = False
        self.seed_count = 0
        self.search_counts = dict(localSearchMaps=0, localSeedTotal=0, outsideSeedsScreened=0,
                                  outsideSeedsRejected=0, ringEvaluations=[0, 0, 0, 0])
        if prepared_index is not None:
            self.search_counts.update(preparedIndexMaps=0, preparedIndexFallbacks=0,
                                      preparedSeedsScreened=0, preparedSeedsRejected=0)
        self.optional_door, self._early_result = None, None
        self._band_prepared = False
        self.route_cache = {}
        super().__init__(frame if self.floor is None or self.floor in engine.floors else None)

    def _observe(self):
        self.context, nodes, self.qpairs = self.engine._observe(self.frame)
        if len(nodes) < 4 or not len(self.qpairs):
            self._early_result = ([], dict(reason='side-visible-structure-insufficient', complete=True,
                queryTurns=len(nodes), minimumQueryTurns=4, queryPairs=len(self.qpairs)))
            return
        self.q = np.array([n['point'] for n in nodes])
        self.qr = np.array([n['rays'] for n in nodes])
        self.scan_complete = not self.index_directory_gaps

    def _probe(self, entry):
        item, source, rays, spairs = entry
        frame, deadline, local_search = self.frame, self.deadline, self.local_search
        q, qr, qpairs = self.q, self.qr, self.qpairs
        rows, complete, seed_count = [], self.scan_complete, 0
        search_counts = dict(localSearchMaps=0, localSeedTotal=0, outsideSeedsScreened=0,
                             outsideSeedsRejected=0, ringEvaluations=[0, 0, 0, 0])
        if not len(spairs):
            return
        prepared = (local_search or {}).get((item['mapId'], item['floor']))
        # Existing doorway scheduling/cross-checks retain their original path.
        # The explicit general index replaces only ordinary full-graph probing.
        indexed = (self.prepared_index.prepared_for(entry)
                   if self.prepared_index is not None and prepared is None else None)
        if indexed is not None:
            source, rays, spairs = indexed.points, indexed.rays, indexed.pairs
        compatible = np.min(np.sum(qr[:, None] * rays[None], axis=3), axis=2) >= .95
        # Incompatible turns can never be hits. Keep row-major pair order
        # while evaluating only the same orientation-compatible entries.
        oriented_qids, oriented_sids = np.nonzero(compatible)
        oriented_query, oriented_source = q[oriented_qids], source[oriented_sids]
        hypotheses = []
        seen = set()
        if prepared is not None:
            from side_entry_search import local_hypotheses
            hypotheses, searched, counts = local_hypotheses(q, qpairs, compatible, source, spairs,
                prepared, frame.shape, item['unit'], deadline)
            complete = complete and searched
            seed_count += counts['exactSeedEvaluations']
            search_counts['localSearchMaps'] += 1
            for name in ('localSeedTotal', 'outsideSeedsScreened', 'outsideSeedsRejected'):
                search_counts[name] += counts[name]
            search_counts['ringEvaluations'] = [a+b for a, b in zip(search_counts['ringEvaluations'], counts['ringEvaluations'])]
        else:
            if indexed is not None:
                self.search_counts['preparedIndexMaps'] += 1
            elif self.prepared_index is not None:
                self.search_counts['preparedIndexFallbacks'] += 1
            # Ordinary search remains available when optional local data is
            # absent. No current-frame icon is a prerequisite for either path.
            deltas = indexed.deltas if indexed is not None else source[spairs[:, 1]] - source[spairs[:, 0]]
            denoms = indexed.denoms if indexed is not None else np.sum(deltas*deltas, axis=1)
            long_enough = denoms >= (.6 * item['unit']) ** 2
        for a, b in (() if prepared is not None else qpairs):
            if time.perf_counter() >= deadline:
                complete = False
                break
            selected = compatible[a, spairs[:, 0]] & compatible[b, spairs[:, 1]] & long_enough
            delta = deltas[selected]
            scales = ((q[b]-q[a]) @ delta.T) / denoms[selected]
            translations = q[a] - scales[:, None]*source[spairs[selected, 0]]
            eligible = ((scales*item['unit'] >= 8) & (scales*item['unit'] <= min(frame.shape[:2])/4)
                        & (np.linalg.norm(q[b]-q[a]-scales[:, None]*delta, axis=1) <= 3))
            scales, translations = scales[eligible], translations[eligible]
            if indexed is not None:
                from local_structure_index import possible_turns
                upper = possible_turns(q, scales, translations, compatible, indexed)
            else:
                upper = np.full(len(scales), 4, np.int32)
            # Batch cheap residuals, preserve the original seed order and
            # deduplication order. Expensive full correspondences are sparse.
            for scale, translation, possible in zip(scales, translations, upper):
                if time.perf_counter() >= deadline:
                    complete = False
                    break
                key = tuple(np.rint(np.r_[scale * item['unit'] * 4, translation / 3]).astype(int))
                if key in seen:
                    continue
                seen.add(key)
                if indexed is not None:
                    self.search_counts['preparedSeedsScreened'] += 1
                    if possible < 4:
                        self.search_counts['preparedSeedsRejected'] += 1
                        continue
                seed_count += 1
                distances = np.linalg.norm(oriented_query - (oriented_source * scale + translation), axis=1)
                hits = distances <= 3
                hit_q, hit_s = oriented_qids[hits], oriented_sids[hits]
                if len(hit_q) < 4:
                    continue
                unique = (np.bincount(hit_q)[hit_q] == 1) & (np.bincount(hit_s)[hit_s] == 1)
                pairs = np.column_stack((hit_q[unique], hit_s[unique]))
                if len(pairs) < 4:
                    continue
                extent = np.ptp(source[pairs[:, 1]], axis=0) / item['unit']
                if min(extent) < 1:
                    continue
                if any(np.linalg.norm(translation-h['translation']) + abs(scale-h['scale'])*item['unit'] < 3 for h in hypotheses):
                    continue
                hypotheses.append(dict(scale=scale, translation=translation.tolist(), chainCount=len(pairs),
                                       extentGrid=extent.tolist(), pairs=pairs.tolist()))
        hypotheses.sort(key=lambda h: -h['chainCount'])
        finalists=hypotheses[:3]
        # Pair-generated scales inherit raster rounding. Refine the same
        # bounded finalist set for every registered map using measured
        # correspondences and the shared isotropic similarity model.
        continuous=[]
        for h in finalists:
            pairs=np.asarray(h['pairs'])
            for _ in range(3):
                src=source[pairs[:,1]];dst=q[pairs[:,0]]
                centered=src-src.mean(axis=0)
                s=float(np.sum(centered*(dst-dst.mean(axis=0)))/np.sum(centered**2))
                t=dst.mean(axis=0)-s*src.mean(axis=0)
                hits=np.linalg.norm(oriented_query-(oriented_source*s+t),axis=1)<=3
                hq,hs=oriented_qids[hits],oriented_sids[hits]
                unique=(np.bincount(hq)[hq]==1)&(np.bincount(hs)[hs]==1)
                updated=np.column_stack((hq[unique],hs[unique]))
                if len(updated)<4:break
                pairs=updated
            if not 8<=s*item['unit']<=min(frame.shape[:2])/4:continue
            continuous.append(dict(h,scale=s,translation=t.tolist(),pairs=pairs.tolist(),
                chainCount=len(pairs),extentGrid=(np.ptp(source[pairs[:,1]],axis=0)/item['unit']).tolist()))
        finalists=[]
        for h in sorted(continuous,key=lambda r:-r['chainCount']):
            s=h['scale'];t=np.asarray(h['translation'])
            if any(np.max(np.linalg.norm(source*(s-r['scale'])+t-r['translation'],axis=1))<=3
                for r in finalists):continue
            finalists.append(h)
        for h in finalists:
            matched = source[np.asarray(h['pairs'])[:, 1]]
            gates = [g for g in item['gates'] if np.min(np.linalg.norm(matched - g['center'], axis=1)) <= 12 * item['unit']]
            s = h['scale']; x, y = h['translation']
            rows.append(dict(mapId=item['mapId'], floor=item['floor'], region=item['region'], profileId=self.engine.profile_for_map(item['mapId']),
                             entryKind='side-neighborhood' if gates else 'local-structure', score=float(h['chainCount']),
                             hypothesis=h, matrixSourceToClient=[[s, 0, x], [0, s, y], [0, 0, 1]],
                             sideGates=gates, sourceProduct=item['sourceProduct'], identityVerified=False,
                             evidenceScope='oriented measured wall candidates; side icon itself is not detected'))
        self.rows.extend(rows)
        self.scan_complete = complete
        self.seed_count += seed_count
        for name in ('localSearchMaps', 'localSeedTotal', 'outsideSeedsScreened', 'outsideSeedsRejected'):
            self.search_counts[name] += search_counts[name]
        self.search_counts['ringEvaluations'] = [a+b for a, b in zip(
            self.search_counts['ringEvaluations'], search_counts['ringEvaluations'])]

    def _prepare_competition(self):
        if self._band_prepared:
            return
        self.rows.sort(key=lambda r: -r['score'])
        self.top = self.rows[0]['score'] if self.rows else 0
        self.competitors = [r for r in self.rows if r['score'] >= max(4, self.top-1)]
        self.active = self.competitors
        self._band_prepared = True

    def _door_cross_check(self):
        from side_entry_geometry import observed_side_boundary
        observed_at = time.perf_counter()
        try:
            observed = observed_side_boundary(self.engine._entry_observations(self.frame, context=self.context))
            if observed is not None:
                for row in self.rows:
                    row['sideDoorConstraint'] = dict(door=self.engine.side_doors[row['mapId']],
                        observed=observed, unit=float(self.local_search[(row['mapId'], row['floor'])]['cell'])*12)
                possible = [row for row in self.competitors if not self.engine._side_door_contradicted(row)]
                if possible and len(possible) < len(self.competitors):
                    self.optional_door = possible
                else:
                    for row in self.rows:
                        row.pop('sideDoorConstraint', None)
        except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
            for row in self.rows:
                row.pop('sideDoorConstraint', None)
            self.search_counts['sideOptionalDoorError'] = str(error)
        self.search_counts['sideOptionalDoorMs'] = (time.perf_counter()-observed_at)*1000

    def _verify(self, row):
        row['verification'] = self.engine.verify(self.frame, row, context=self.context, _route_cache=self.route_cache)

    def _verification_jobs(self):
        if self.scan_complete and len(self.active) <= 3:
            for row in self.active:
                if time.perf_counter() >= self.deadline:
                    self.verification_complete = False
                    break
                if 'verification' not in row:
                    yield self._job(row['mapId'], 'verify', lambda row=row: self._verify(row))

    def _restore_unusable_optional_door(self):
        if self.optional_door is None:
            return False
        usable = any(row.get('verification', {}).get('success')
                     and (row['verification'].get('residual') or {}).get('verified')
                     and row['hypothesis']['chainCount'] >= 6
                     and min(row['hypothesis']['extentGrid']) >= 2 for row in self.active)
        if usable:
            return False
        # Preserve the original band and every already measured validator.
        for row in self.rows:
            row.pop('sideDoorConstraint', None)
        self.active, self.optional_door = self.competitors, None
        return True

    def _jobs(self):
        if self.floor is not None and self.floor not in self.engine.floors:
            self._early_result = ([], dict(reason='unsupported-floor', complete=True))
            return
        if time.perf_counter() >= self.deadline:
            self._early_result = ([], dict(reason='side-budget-incomplete', complete=False))
            return
        yield self._job(None, 'observe', self._observe)
        if self._early_result is not None:
            return
        for entry in self.entries:
            if time.perf_counter() >= self.deadline:
                self.scan_complete = False
                break
            yield self._job(entry[0]['mapId'], 'probe', lambda entry=entry: self._probe(entry))
        self.probes_complete = True
        self._prepare_competition()
        if (self.local_search and self.scan_complete and self.top >= 6 and len(self.competitors) > 1
                and time.perf_counter() < self.deadline):
            yield self._job(None, 'door-cross-check', self._door_cross_check)
        self.active = self.optional_door if self.optional_door is not None else self.competitors
        self.verification_complete = self.scan_complete
        yield from self._verification_jobs()
        if self._restore_unusable_optional_door():
            yield from self._verification_jobs()

    def finish(self, *, elapsed_ms=0.):
        if self._result is not None:
            return self._result
        self._close()
        if self._early_result is not None:
            self._result = self._early_result
            return self._result
        self._prepare_competition()
        self._restore_unusable_optional_door()
        complete = self.scan_complete and self.probes_complete and not self._failed
        competitor_ids = {id(row) for row in self.competitors}
        for row in self.rows:
            if 'verification' not in row:
                contradicted = self.engine._side_door_contradicted(row)
                reason = ('current-side-door-geometry' if contradicted else
                          'side-search-incomplete-unverified' if not complete else
                          'side-outside-score-band-unverified' if id(row) not in competitor_ids else
                          'side-competition-limit-unverified' if len(self.competitors) > 3 else
                          'side-verification-budget-incomplete')
                row['verification'] = dict(success=False, evaluated=False, reason=reason)
            row['competitionComplete'] = complete
            row['poseAmbiguous'] = sum(r['mapId'] == row['mapId'] and r['floor'] == row['floor']
                                       for r in self.competitors) > 1
        self._result = self.rows, dict(
            reason=('side-work-failed' if self._failed else 'side-resource-directory-incomplete'
                    if self.index_directory_gaps else 'side-local-candidates' if complete else 'side-budget-incomplete'),
            complete=complete, queryTurns=len(self.q), competitiveHypotheses=len(self.competitors),
            seedCorrespondences=self.seed_count,
            verificationComplete=self._complete and not self._failed and self.verification_complete and len(self.active) <= 3,
            identityAmbiguous=len({(r['mapId'], r['floor']) for r in self.competitors}) != 1,
            elapsedMs=elapsed_ms, **self.search_counts, sideOptionalDoorUsed=self.optional_door is not None,
            sideDoorPath='door-local-index' if self.local_search else 'ordinary-structure',
            sideDoorResourceError=self.engine.side_door_error, sideLocalIndexError=self.engine.side_search_error)
        return self._result
