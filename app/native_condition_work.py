"""Cooperative native similarity proposal jobs.

The similarity proposal is advisory.  This module only moves its existing
work kernels under the shared observation scheduler; it does not qualify an
identity or pose.
"""
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile

import numpy as np

from entry_evidence_work import EntryEvidenceWork


_RESOURCE_ERRORS = (OSError, ValueError, KeyError, TypeError, BadZipFile,
                    np.linalg.LinAlgError)


class NativeProposalWork(EntryEvidenceWork):
    """EntryEvidenceWork adapter whose finish value is the legacy proposal."""

    def __init__(self, result, *, frame=None):
        self.result = result
        self._stopped = False
        super().__init__(frame)

    def _jobs(self):
        try:
            yield from self._proposal_jobs()
        except _RESOURCE_ERRORS as error:
            self._fail(error)

    def _proposal_jobs(self):
        return
        yield  # pragma: no cover - keeps this a generator for subclasses

    def _fail(self, error):
        self._stopped = True
        if 'complete' in self.result:
            self.result['complete'] = False
        self.result['reason'] = str(error)

    def job(self, phase, function, map_id=None):
        def run():
            try:
                function()
            except _RESOURCE_ERRORS as error:
                self._fail(error)
        return self._job(map_id, phase, run)

    def finish(self):
        """Return the normal proposal shape, still incomplete after close/budget."""
        self.close()
        if self._failed:
            if 'complete' in self.result:
                self.result['complete'] = False
            self.result['reason'] = 'native-condition-work-failed'
        return self.result


class ConditionProposalWork(NativeProposalWork):
    def __init__(self, index, rows, front_rows, front_diagnostics, side_rows,
                 side_diagnostics, floor, provider, geometry, deadline, prior,
                 required_map_ids):
        self.index = index
        self.rows = rows
        self.front_rows = front_rows
        self.front_diagnostics = front_diagnostics
        self.side_rows = side_rows
        self.side_diagnostics = side_diagnostics or {}
        self.floor = str(floor)
        self.provider = provider
        self.geometry = geometry
        self.deadline = deadline
        self.prior = prior
        self.required_map_ids = required_map_ids
        self.matches = {}
        self.established = {}
        self.witnesses = {}
        self.context = None
        self.outside = set()
        super().__init__(dict(status='conditions', mode=index.mode, floor=self.floor,
            groupId='conditions', resourceKey=index.owner.index_digest,
            conditionObservations=[], complete=False, candidateMemberId=None,
            memberIds=[], originalIdentityHardGatePassed=False,
            authorizesIdentity=False, authorizesPose=False,
            reason='condition-budget-incomplete'))

    def _proposal_jobs(self):
        index = self.index
        verified = [row for row in self.rows if row.get('identityHardGatePassed') is True]
        if len(verified) == 1:
            winner = verified[0]['mapId']
            rid = f'{index.mode}/{winner}/{self.floor}'
            if rid in index.references:
                self.result.update(complete=True, reason='original-identity-pending',
                    candidateMemberId=winner, memberIds=[winner],
                    originalIdentityHardGatePassed=True,
                    winnerReference=index.references[rid])
                return

        # Match each front hypothesis as a bounded work unit. The original
        # kernel keeps all of its verdict and source-feature rules; the shared
        # scheduler can now stop between rows, while continuation views and
        # later condition work remain separately scheduled below.
        for row in self.front_rows:
            matched = {'value': {}}

            def match_row(row=row, matched=matched):
                # Keep the legacy method's per-row semantics, while the
                # observation scheduler owns the absolute deadline between
                # those kernels.
                matched['value'], _ = index.match((row,), self.side_rows,
                    self.floor, float('inf'))

            yield self.job('condition-match-row', match_row, row.get('mapId'))
            if self._stopped:
                return
            for did, views in matched['value'].items():
                self.matches.setdefault(did, []).extend(views)

        if self.prior and tuple(self.prior[:3]) == (
                index.mode, self.floor, index.owner.index_digest):
            self.established = {did: set(cids) for did, cids in self.prior[3]}
            for did, condition_ids in self.established.items():
                if did not in index.domains:
                    continue
                for row in self.side_rows:
                    if (row.get('entryCrossCheck') or {}).get('contradicted') is True:
                        continue
                    checked = row.get('verification') or {}
                    if not (checked.get('success') is True
                            and (checked.get('residual') or {}).get('verified') is True):
                        continue
                    rid = f'{index.mode}/{row["mapId"]}/{self.floor}'
                    matrix = index._matrix(row)
                    if matrix is None:
                        continue
                    reference = index.references.get(rid)
                    if (reference is None or row.get('profileId') != reference['profile']
                            or row.get('sourceProduct') != reference['features'].get(
                                'side', {}).get('sourceProduct', reference['resourcePath'])):
                        continue
                    for pid, pose in index.poses.items():
                        if pose['domainId'] != did or pose['target'] != rid:
                            continue
                        if any(view['poseId'] == pid for view in self.matches.get(did, ())):
                            continue

                        def add_continuation(pid=pid, pose=pose, row=row, matrix=matrix,
                                             condition_ids=condition_ids):
                            view_matrix = matrix @ np.linalg.inv(index._matrices[pid])
                            self.matches.setdefault(did, []).append(dict(
                                poseId=pid, matrix=view_matrix, eligible=True,
                                matchedFeatureIds=set(), mapId=row['mapId'],
                                continuation=True, establishedConditions=condition_ids))

                        yield self.job('condition-continuation-pose', add_continuation,
                                       row['mapId'])
                        if self._stopped:
                            return

        # Local witness checks use the actual matched source feature points and
        # declared condition windows.  Each view/condition pair is its own job.
        for did, views in list(self.matches.items()):
            for view in views:
                if not view['eligible']:
                    continue
                for cid in index.domain_conditions.get(did, ()):
                    pose_ids = set(map(str, index.conditions[cid]['poseIds']))
                    if str(view['poseId']) not in pose_ids:
                        continue
                    key = (id(view), cid)
                    yield self.job('condition-local-witness',
                        lambda view=view, cid=cid, key=key:
                            self.witnesses.__setitem__(key, index.local_witness(
                                view, index.conditions[cid])), view['mapId'])
                    if self._stopped:
                        return
        self.matches = {did: views for did, views in self.matches.items()
            if any(self.witnesses.get((id(view), cid), False)
                for cid in index.domain_conditions.get(did, ()) for view in views)}

        if self.matches:
            geometry = {'value': None}
            yield self.job('condition-current-geometry',
                lambda: geometry.update(value=self.geometry()))
            if self._stopped:
                return
            self.context = geometry['value']
            if self.context is None:
                self.result['reason'] = 'condition-current-geometry-unavailable'
                self._stopped = True
                return
            yield self.job('condition-sample-bundle-load', lambda: index.samples(0))
            if self._stopped:
                return

        self.outside = ({row['mapId'] for row in [*self.front_rows, *self.side_rows]}
            if self.required_map_ids is None else set(self.required_map_ids))

        for did, views in self.matches.items():
            duplicated = any(sum(view['poseId'] == pose_id for view in views) > 1
                for pose_id in {view['poseId'] for view in views})
            for cid in index.domain_conditions[did]:
                condition = index.conditions[cid]
                pose_ids = set(map(str, condition['poseIds']))
                current = [view for view in views if str(view['poseId']) in pose_ids
                    and view['eligible'] and self.witnesses.get((id(view), cid), False)]
                if not current:
                    continue
                yield self.job('condition-domain-binding-validation',
                    lambda did=did: index.validate_domains((did,), self.provider))
                if self._stopped:
                    return
                members = sorted({index.references[index.poses[pose_id]['target']]['mapId']
                    for pose_id in pose_ids})
                observation = dict(domainId=did, conditionId=cid,
                    radiusUnits=condition['radiusUnits'], memberIds=members,
                    poseMaps={pose_id:index.references[index.poses[pose_id]['target']]['mapId']
                        for pose_id in pose_ids},
                    activeViews=sorted({view['poseId'] for view in current}), pairs=[],
                    expectedPoseIds=sorted(pose_ids),
                    entryEstablished=cid in self.established.get(did, set()),
                    completeForeignMapCompetition=condition['completeForeignMapCompetition'],
                    currentCompetitionComplete=(self.side_diagnostics.get('complete') is True
                        if condition['kind'] == 'side' else
                        self.front_diagnostics.get('complete') is True
                        and self.front_diagnostics.get('entryInventoryComplete') is True),
                    ambiguousViews=duplicated,
                    outsideCompetitors=sorted(self.outside - set(members)),
                    coverage='partial-positive-observation; unseen window pixels are unknown',
                    sameMapAlternativePoses=condition.get('sameMapAlternativePoses', 'UNKNOWN'))
                self.result['conditionObservations'].append(observation)
                if duplicated:
                    continue
                contradictory = False
                for pair in index.comparisons.get(cid, ()):
                    a, b = str(pair['poseA']), str(pair['poseB'])
                    winners = []
                    for view in current:
                        record = dict(poseA=a, poseB=b, viewerPoseId=view['poseId'])
                        supports = {}
                        for side in ('a', 'b'):
                            sample_id = int(pair[side + 'Only']['sampleSetId'])
                            cache_key = (index.sample_bundle[0], sample_id)
                            cached = {'value': index._sample_views.get(cache_key)}

                            def load_sample_view(sample_id=sample_id, cache_key=cache_key,
                                                 cached=cached):
                                if cached['value'] is not None:
                                    return
                                _, points, offsets = index.sample_bundle
                                if sample_id < 0 or sample_id + 1 >= len(offsets):
                                    raise ValueError('similarity-sample-id')
                                xy = points[offsets[sample_id]:offsets[sample_id + 1]]
                                if len(xy) > 512:
                                    raise ValueError('similarity-sample-count')
                                cached['value'] = (tuple(map(tuple, xy.tolist())),
                                    np.column_stack((xy, np.ones(len(xy)))))
                                index._sample_views[cache_key] = cached['value']

                            yield self.job(f'condition-comparison-{side}-sample-view',
                                           load_sample_view)
                            if self._stopped:
                                return

                            def support(cached=cached, view=view, supports=supports,
                                        side=side):
                                universe, points = cached['value']
                                reading = index.owner._support(points, view['matrix'], self.context)
                                supports[side] = dict(samples=universe,
                                    observed=tuple(universe[i] for i in reading['observedIndices']))

                            yield self.job(f'condition-comparison-{side}-view-support', support)
                            if self._stopped:
                                return
                        record.update(supports)
                        observation['pairs'].append(record)
                        fa = len(record['a']['observed']) / max(1, len(record['a']['samples']))
                        fb = len(record['b']['observed']) / max(1, len(record['b']['samples']))
                        if fa > fb:
                            winners.append(a)
                        if fb > fa:
                            winners.append(b)
                    if len(set(winners)) > 1:
                        contradictory = True
                observation['conflictingViews'] = contradictory

        self.result.update(complete=True, reason='finite-conditions-evaluated',
            memberIds=sorted({member for observation in self.result['conditionObservations']
                for member in observation['memberIds']}))


class LegacyGroupProposalWork(NativeProposalWork):
    def __init__(self, owner, rows, entry_rows, diagnostics, floor, provider,
                 geometry, deadline, side_rows, prior, required_map_ids):
        self.owner = owner
        self.rows = rows
        self.entry_rows = entry_rows
        self.diagnostics = diagnostics or {}
        self.floor = str(floor)
        self.provider = provider
        self.geometry = geometry
        self.deadline = deadline
        self.side_rows = side_rows
        self.prior = prior
        self.required_map_ids = required_map_ids
        self.group = None
        self.winner = None
        self.supported = {}
        self.declared = None
        self.poses = {}
        self.context = None
        self.readings = []
        self.champions = []
        self.wins = {}
        self.arrays = None
        self.samples = None
        self.best = None
        super().__init__(dict(status='unavailable', reason='similarity-budget-incomplete'))

    def _proposal_jobs(self):
        owner = self.owner
        verified = [row for row in self.rows if row.get('identityHardGatePassed') is True]
        self.winner = verified[0]['mapId'] if len(verified) == 1 else None
        self.group = owner.group_for(self.winner) if self.winner else None
        for row in self.entry_rows:
            fit = row.get('verification') or {}
            if ((fit.get('regions') or {}).get('regionalEdgeVerified') is True
                    and (fit.get('residual') or {}).get('verified') is True
                    and fit.get('matrixSourceToClient') is not None):
                self.supported[row['mapId']] = row
        if self.group is None:
            self.group = next((group for group in owner.groups
                if set(self.supported) == set(group['memberIds'])), None)
        if self.group is None:
            self.result.update(status='none', reason=owner.error or 'no-supported-native-group')
            return
        self.result['groupId'] = self.group['groupId']

        def validate_index():
            if self.provider is None:
                raise ValueError('similarity-shared-provider-unavailable')
            if owner._sha(owner.index_path) != owner.index_digest:
                raise ValueError('similarity-index-changed')
        yield self.job('similarity-index-validation', validate_index)
        if self._stopped:
            return
        for floor_record in self.group['floors']:
            for member in floor_record['members']:
                yield self.job('similarity-member-binding-validation',
                    lambda member=member: owner.validate_reference(member, self.provider),
                    member['mapId'])
                if self._stopped:
                    return
            def validate_difference(floor_record=floor_record):
                path = owner.local(floor_record['differenceResourcePath'])
                if owner._sha(path) != floor_record['differenceSha256']:
                    raise ValueError('similarity-differences-changed')
            yield self.job('similarity-difference-binding-validation', validate_difference)
            if self._stopped:
                return
        resource_key = hashlib.sha256(
            json.dumps(self.group, sort_keys=True).encode('utf8')).hexdigest()
        common = dict(mode=owner.mode, groupId=self.group['groupId'],
            memberIds=list(self.group['memberIds']), floor=self.floor,
            resourceKey=resource_key, authorizesIdentity=False, authorizesPose=False)

        if self.winner:
            reference = {'value': None}
            yield self.job('similarity-winning-reference-binding',
                lambda: reference.update(value=self.provider(
                    owner._member_profile(self.group, self.winner), self.winner, self.floor)),
                self.winner)
            if self._stopped:
                self.result.update(groupId=self.group['groupId'])
                return
            def finish_winner():
                ref = reference['value']
                if ref is None:
                    raise ValueError('similarity-winning-floor-unavailable')
                binding = {key:ref.metadata.get(key) for key in (
                    'binding', 'structureVersionKey', 'sourceVersion', 'baseSnapshot')}
                physical_path = Path(ref.path).resolve()
                if not physical_path.is_relative_to(owner.root):
                    raise ValueError('similarity-path-outside-root')
                binding.update(mapId=self.winner, floor=self.floor,
                    profile=owner._member_profile(self.group, self.winner),
                    resourcePath=physical_path.relative_to(owner.root).as_posix(),
                    resourceSha256=owner._sha(physical_path), shapeHW=list(ref.shape))
                self.result.update(common, status='identity-pending',
                    candidateMemberId=self.winner, originalIdentityHardGatePassed=True,
                    winnerReference=binding)
                self.result.pop('reason', None)
            yield self.job('similarity-winning-reference-evidence', finish_winner,
                           self.winner)
            return

        self.declared = next((record for record in self.group['floors']
            if str(record['floor']) == self.floor), None)
        if (self.declared is None or self.diagnostics.get('complete') is not True
                or self.diagnostics.get('entryInventoryComplete') is not True):
            self.result.update(status='none', reason='similarity-competition-incomplete')
            return
        self.poses = {mid:row['verification']['matrixSourceToClient']
            for mid, row in self.supported.items()}
        self.best = max(self.supported, key=lambda mid:float(self.supported[mid]['score']))
        geometry = {'value': None}
        yield self.job('similarity-current-geometry',
            lambda: geometry.update(value=self.geometry()))
        if self._stopped:
            return
        self.context = geometry['value']
        arrays = {'value': None}
        def load_arrays():
            arrays['value'] = owner.arrays(self.declared)
            path = owner.local(self.declared['differenceResourcePath'])
            self.samples = owner._arrays[(path, owner._sha(path))][1]
        yield self.job('similarity-difference-arrays-load', load_arrays)
        if self._stopped:
            return
        self.arrays = arrays['value']

        for member in self.declared['members']:
            map_id = member['mapId']
            if map_id not in self.poses:
                continue
            matrix_box = {'value': None}

            def make_matrix(member=member, map_id=map_id, matrix_box=matrix_box):
                matrix = (np.asarray(self.poses[map_id], float)
                    @ np.linalg.inv(np.asarray(member['matrixNativeToGroup'], float)))
                if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
                    return
                matrix_box['value'] = matrix
                self.wins[map_id] = {record['mapId']:set()
                    for record in self.declared['members']}

            yield self.job('similarity-difference-view-transform', make_matrix, map_id)
            if self._stopped:
                return
            matrix = matrix_box['value']
            if matrix is None:
                continue
            for pair in self.declared['pairs']:
                reading = {'poseMemberId':map_id,
                    'memberA':pair['memberA'], 'memberB':pair['memberB']}
                support = {}
                for side in ('a', 'b'):
                    array_name = pair[side + 'OnlyArray']

                    def compute_support(side=side, array_name=array_name,
                                        matrix=matrix, support=support):
                        support[side] = owner._support(self.samples[array_name],
                            matrix, self.context)

                    yield self.job(f'similarity-difference-{side}-view-support',
                                   compute_support, map_id)
                    if self._stopped:
                        return
                reading.update(support)
                self.readings.append(reading)
                a, b = pair['memberA'], pair['memberB']
                sa, sb = support['a'], support['b']
                if sa['observed'] and sa['fraction'] > sb['fraction']:
                    self.wins[map_id][a].add(b)
                if sb['observed'] and sb['fraction'] > sa['fraction']:
                    self.wins[map_id][b].add(a)
            self.champions.append(next((mid for mid, rivals in self.wins[map_id].items()
                if len(rivals) == len(self.wins[map_id]) - 1), None))

        winner = (self.champions[0] if self.champions and
            all(candidate == self.champions[0] for candidate in self.champions) else None)
        difference = dict(candidateMemberId=winner, pairs=self.readings,
            positiveEvidenceOnly=True, authorizesIdentity=False, authorizesPose=False)
        self.result.update(common, status='group-candidate',
            candidateMemberId=winner or self.best, differenceEvidence=difference,
            supportedMemberIds=list(self.supported), originalIdentityHardGatePassed=False)
        self.result.pop('reason', None)
