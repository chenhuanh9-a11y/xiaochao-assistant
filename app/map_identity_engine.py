"""One entry recognition pipeline for all map resource families.

Mode adapters supply static resources and UI/floor rules; this class never
selects an old mode-specific recognition algorithm or owns a live pose.
"""
import time
from pathlib import Path
import cv2
import numpy as np
from nightmare_candidate import (NightmareCandidateEngine, nightmare_map_crop, map_color_mask,
                                SOURCE_STRUCTURE_PROFILE_ID)

def entry_rank_diagnostics(evidence, *, side_error=None):
    """Snapshot rejection facts on the ranked rows, before another frame ranks."""
    def subset(value, keys):
        return {key: value[key] for key in keys if key in value}

    def candidate_summary(row):
        checked = row.get('verification') or {}
        entry = row.get('entryEvidence') or {}
        residual = checked.get('residual') or {}
        summary = subset(row, ('mapId', 'floor', 'profileId', 'score', 'entryKind',
                              'sourceEntryId', 'competitionComplete', 'poseAmbiguous'))
        summary.update(verified=checked.get('success'), reason=checked.get('reason'),
            verificationEvaluated=checked.get('evaluated'),
            entry=subset(entry, ('localGeometryVerified', 'directionCompatible', 'sourceEntryId')),
            regionalEdgeVerified=(checked.get('regions') or {}).get('regionalEdgeVerified'),
            author=subset(residual, ('verified', 'reason', 'medianPixels', 'p90Pixels',
                                    'withinThreePixels', 'withinSixPixels', 'explainedPixelFraction')),
            wallsVerified=(checked.get('entryWalls') or {}).get('verified'),
            hypothesis=subset(row.get('hypothesis') or {}, ('chainCount', 'extentGrid')),
            exitContradicted=(row.get('entryCrossCheck') or entry.get('allObservedExits') or {}).get('contradicted'),
            structureVariants=[dict(name=variant.get('name'), verified=variant.get('verified'),
                regionalEdgeVerified=(variant.get('regions') or {}).get('regionalEdgeVerified'),
                walls=subset(variant.get('walls') or {}, ('verified', 'maxPixels', 'fragmentCount')))
                for variant in checked.get('structureVariants', [])])
        return summary

    fields = ('reason', 'error', 'complete', 'elapsedMs', 'exitObserved', 'observationStatus', 'entryObserved',
              'verificationComplete', 'coverageComplete', 'identityAmbiguous', 'poseAmbiguous',
              'competitiveHypotheses', 'selectionPolicy',
              'continuousRefinementMs', 'continuousRefinementReason',
              'poseRecoveryEvaluated', 'poseRecoveryCandidates', 'poseRecoveryEquivalent',
              'entryInventoryComplete', 'entryInventoryExpectedMapIds',
              'entryInventoryAvailableMapIds', 'entryInventoryMissingMapIds',
              'entryInventoryUnexpectedMapIds', 'entryInventoryProposalOnly',
              'sideDoorPath', 'sideDoorResourceError', 'seedCorrespondences',
              'sideLocalIndexError', 'localSearchMaps', 'localSeedTotal',
              'outsideSeedsScreened', 'outsideSeedsRejected', 'ringEvaluations',
              'sideOptionalDoorMs', 'sideOptionalDoorUsed', 'sideOptionalDoorError')
    result = {'sideResourceError': side_error, **evidence.get('recognitionPath', {})}
    for label, diagnostic_key, rows_key in (
        ('passport', 'entryPassport', 'entryPassportCandidates'),
        ('main', 'tilemapEntry', 'tilemapCandidates'),
        ('side', 'tilemapSide', 'tilemapSideCandidates'),
    ):
        diagnostic = evidence.get(diagnostic_key) or {}
        rows = evidence.get(rows_key) or []
        reasons = {}
        for row in rows:
            verification = row.get('verification') or {}
            reason = str(verification.get('reason') or row.get('reason') or 'not-recorded')
            reasons[reason] = reasons.get(reason, 0) + 1
        result[label] = {**{key: diagnostic[key] for key in fields if key in diagnostic},
            'candidateCount': len(rows), 'candidateReasons': reasons,
            'verifiedMapIds': sorted({str(row.get('mapId')) for row in rows
                if (row.get('verification') or {}).get('success') is True or row.get('identityVerified') is True}),
            'bestCandidates': [candidate_summary(row)
                for row in sorted(rows, key=lambda r: float(r.get('score', 0)), reverse=True)[:4]]}
        if label == 'side' and result.get('sideProducer') == 'passport':
            result[label].pop('bestCandidates')
            result[label]['candidateDetailsIn'] = 'passport'
    passport = evidence.get('entryPassport') or {}
    examined = passport.get('examined') or []
    result['passport']['examinedCount'] = len(examined)
    result['passport']['examinedReasons'] = {}
    for row in examined:
        key = str(row.get('reason') or 'not-recorded')
        counts = result['passport']['examinedReasons']
        counts[key] = counts.get(key, 0) + 1
    result['passport']['observations'] = [dict(reliable=row.get('reliable'),
        iconScore=(row.get('icon') or {}).get('score'),
        direction=(row.get('direction') or {}).get('direction'),
        directionReason=(row.get('direction') or {}).get('reason'))
        for row in passport.get('observations', [])]
    source=evidence.get('sourceStructureCandidates') or {}
    automatic=source.get('automaticCandidate') or {}
    result['sourceStructures']=dict(reason=source.get('reason'),complete=source.get('complete'),
        elapsedMs=source.get('elapsedMs'),evaluatedEntryCount=source.get('evaluatedEntryCount'),
        heldOutDecisionReason=source.get('heldOutDecisionReason'),
        variantExclusiveEvidence=source.get('variantExclusiveEvidence'),
        candidateCount=len(source.get('candidates') or []),
        candidates=[dict(row) for row in source.get('candidates') or []],
        automaticCandidate=({key:automatic.get(key) for key in (
            'runtimeMapId','sourceGroup','sourceCandidateId','producerVariantLabel',
            'producerSourceFloorLabel','producerVariantUncertain','gameFloor',
            'identityVerified','identityEvidence',
            'poseSubmissionAllowed','displayEligible','heldOutVerification')}
            if automatic else None),
        displayEligible=False,identityHardGatePassed=False)
    return result


def _expected_profile(profile_id, row):
    return profile_id(row['mapId']) if callable(profile_id) else profile_id


def equivalent_observed_poses(rows, points):
    """Merge only same-map poses agreeing on all currently measured corners."""
    if not rows or len({r['mapId'] for r in rows})!=1 or not len(points):return False
    points=np.asarray(points,dtype=float)
    first=np.asarray(rows[0]['verification']['matrixSourceToClient'])
    return all(np.max(np.linalg.norm(
        (points@(np.asarray(r['verification']['matrixSourceToClient'])@np.linalg.inv(first)).T-points)[:,:2],axis=1))<=3
        for r in rows[1:])


def verified_entry_candidate(rows, diagnostics, floor, *, profile_id='zhan10-speed'):
    """Consume complete exit-anchored evidence, independently of corner counts."""
    first_verified = diagnostics.get('selectionPolicy') == 'first-verified-entry'
    if not first_verified and not all(diagnostics.get(key) is True for key in (
            'exitObserved', 'entryObserved', 'complete', 'verificationComplete', 'coverageComplete')):
        return None
    if not first_verified and diagnostics.get('identityAmbiguous') is not False:
        return None
    accepted = []
    for row in rows:
        evidence = row.get('entryEvidence') or {}
        checked = row.get('verification') or {}
        if not (evidence.get('directionCompatible') is True
                and evidence.get('localGeometryVerified') is True
                and evidence.get('sourceEntryId')
                and checked.get('success') is True
                and ((checked.get('regions') or {}).get('regionalEdgeVerified') is True
                     or (first_verified and checked.get('validationPolicy')=='local-fit-and-author'))
                and (checked.get('residual') or {}).get('verified') is True):
            continue
        if str(row.get('floor')) != str(floor) or row.get('profileId') != _expected_profile(profile_id, row):
            return None
        try:
            matrix = np.asarray(checked.get('matrixSourceToClient'), dtype=float)
        except (ValueError, TypeError):
            return None
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            return None
        accepted.append(row)
        if first_verified:
            return dict(row,identityVerified=True,poseVerified=True,identityOnly=False,
                entryPoseCandidates=[row],locatorKind='side',competitionComplete=False,
                identityEvidence='first-verified-exit-anchored-entry')
    # No score-band shortcut: every verified entrance remains a competitor.
    if not accepted or len({(r.get('mapId'), str(r.get('floor'))) for r in accepted}) != 1:
        return None
    unique_pose = len(accepted) == 1 and diagnostics.get('poseAmbiguous') is False
    return dict(accepted[0], identityVerified=True, poseVerified=unique_pose,
                identityOnly=not unique_pose, entryPoseCandidates=accepted, locatorKind='side',
                identityEvidence='complete-unique-exit-anchored-entry')


def entry_passport_fallback_allowed(diagnostics):
    """Missing coverage is different from contradictory or ambiguous evidence."""
    if diagnostics is None:
        return True
    return diagnostics.get('reason') in (
        'entry-no-reliable-exit', 'entry-passport-unavailable',
        'entry-coverage-incomplete', 'entry-family-unresolved')


def verified_side_candidate(rows,diagnostics,floor,main_rows,frame_shape,*,speed_first=False,profile_id='zhan10-speed'):
    """Adjudicate current side evidence; the producer never owns identity."""
    if speed_first:
        # A complete current structure/author fit can seed the ordinary worker
        # even when an icon cross-check or another pose disagrees. Keep that
        # disagreement in the row for diagnostics; do not rewrite its verdict.
        for row in sorted(rows,key=lambda r:float(r['score']),reverse=True):
            checked=row.get('verification') or {}
            hypothesis=row.get('hypothesis') or {}
            if (str(row.get('floor'))!=str(floor) or row.get('profileId')!=_expected_profile(profile_id, row)
                    or int(hypothesis.get('chainCount',0))<6
                    or min(hypothesis.get('extentGrid',[0,0]))<2
                    or not checked.get('success') or not (checked.get('residual') or {}).get('verified')):
                continue
            matrix=np.asarray(checked.get('matrixSourceToClient'),dtype=float)
            if matrix.shape!=(3,3) or not np.isfinite(matrix).all():continue
            return dict(row,identityVerified=True,poseVerified=True,identityOnly=False,locatorKind='side',
                identityEvidence='first-verified-side-structure-and-current-author')
        return None
    if not (diagnostics.get('complete') is True and diagnostics.get('verificationComplete') is True):return None
    if not rows:return None
    top=max(float(r['score']) for r in rows)
    competitors=[r for r in rows if float(r['score'])>=max(4,top-1)]
    # The producer retains three poses per region. If a fourth could meet
    # this score band, its first three would already make this ambiguous.
    if len(competitors)!=diagnostics.get('competitiveHypotheses'):return None
    remaining=[r for r in competitors if (r.get('entryCrossCheck') or {}).get('contradicted') is not True]
    if len(remaining)==len(competitors):
        if diagnostics.get('identityAmbiguous') is True:return None
    else:
        # Keep the original score band; dropping an explicitly contradicted
        # pose must not promote lower hypotheses that were never verified.
        competitors=remaining
        if len({(r.get('mapId'),str(r.get('floor'))) for r in competitors
                if (r.get('verification') or {}).get('success')})>1:return None
    if len(competitors)>1:
        # Identity and pose have different uniqueness requirements. Several
        # fully checked poses of the same map cannot authorize a pose seed,
        # but they need not prevent the ordinary independent pose solver.
        identities={(r.get('mapId'),str(r.get('floor'))) for r in competitors}
        if len(identities)!=1:return None
        for row in competitors:
            hypothesis=row.get('hypothesis') or {}
            checked=row.get('verification') or {}
            if (row.get('competitionComplete') is not True
                    or str(row.get('floor'))!=str(floor)
                    or row.get('profileId')!=_expected_profile(profile_id, row)
                    or int(hypothesis.get('chainCount',0))<6
                    or min(hypothesis.get('extentGrid',[0,0]))<2
                    or not checked.get('success') or not (checked.get('residual') or {}).get('verified')):return None
            matrix=np.asarray(checked.get('matrixSourceToClient'),dtype=float)
            if matrix.shape!=(3,3) or not np.isfinite(matrix).all():return None
        if any((r.get('verification') or {}).get('success') and r.get('mapId')!=competitors[0]['mapId']
               and (r.get('entryCrossCheck') or {}).get('contradicted') is not True for r in main_rows):return None
        return dict(competitors[0],identityVerified=True,poseVerified=False,identityOnly=True,locatorKind='side',
            identityEvidence='complete-same-map-side-competition-pose-unresolved')
    if len(competitors)!=1:return None
    candidate=competitors[0];hypothesis=candidate.get('hypothesis') or {}
    verification=candidate.get('verification') or {}
    if (candidate.get('competitionComplete') is not True or candidate.get('poseAmbiguous')
            or str(candidate.get('floor'))!=str(floor)
            or candidate.get('profileId')!=_expected_profile(profile_id, candidate)
            or int(hypothesis.get('chainCount',0))<6
            or min(hypothesis.get('extentGrid',[0,0]))<2
            or not verification.get('success') or not (verification.get('residual') or {}).get('verified')):return None
    matrix=np.asarray(verification.get('matrixSourceToClient'),dtype=float)
    if matrix.shape!=(3,3) or not np.isfinite(matrix).all():return None
    height,width=frame_shape[:2]
    extent=np.array([[0,0,1],[width,0,1],[0,height,1],[width,height,1]])
    for main in main_rows:
        if (main.get('entryCrossCheck') or {}).get('contradicted') is True:continue
        checked=main.get('verification') or {}
        if not checked.get('success'):continue
        if main['mapId']!=candidate['mapId']:return None
        try:delta=matrix@np.linalg.inv(np.asarray(checked['matrixSourceToClient']))-np.eye(3)
        except (ValueError,np.linalg.LinAlgError):return None
        if np.linalg.norm((extent@delta.T)[:,:2],axis=1).max()>3:return None
    return dict(candidate,identityVerified=True,poseVerified=True,identityOnly=False,locatorKind='side',
        identityEvidence='complete-unique-side-structure-and-current-author')


class MapIdentityEngine(NightmareCandidateEngine):
    def __init__(self, *args, identity_root, identity_profile_id='zhan10-speed',
                 identity_floors=('1','2'), exit_icon_path=None, entry_advisory=None,
                 entry_structure_store=None, entry_route_loader=None,
                 entry_shared_reference_provider=None,
                 native_similarity_root=None, native_similarity_priority_provider=None,
                 native_similarity_binding_provider=None,native_similarity_context_provider=None,
                 entry_context_is_current=None, **kwargs):
        super().__init__(*args, **kwargs)
        from native_similarity_groups import NativeSimilarityGroups
        self.native_similarity_index = (NativeSimilarityGroups(native_similarity_root,self.pack,
            binding_provider=native_similarity_binding_provider)
            if native_similarity_root is not None else None)
        self.native_similarity_priority_provider = native_similarity_priority_provider
        self.native_similarity_context_provider = native_similarity_context_provider
        self.identity_profile_id = identity_profile_id
        self.identity_floors = tuple(identity_floors)
        self.entry_advisory = entry_advisory
        self.entry_shared_reference_provider = entry_shared_reference_provider
        self.last_difficult_evidence = None
        from difficult_entry_identity import MapEntryGeometryIdentity
        from difficult_side_entry_identity import MapLocalEntryIdentity
        self.entry_identity = MapEntryGeometryIdentity(identity_root,
            structure_store=entry_structure_store, route_loader=entry_route_loader,
            shared_reference_provider=entry_shared_reference_provider,
            profile_id=identity_profile_id,profile_for_map=self.identity_profile_for_map)
        def geometry_provider(frame):
            if entry_context_is_current is not None and not entry_context_is_current():
                return None
            return self.entry_identity.geometry(frame)
        def author_visible_provider(frame, *, context=None):
            if entry_context_is_current is not None and not entry_context_is_current():
                return None
            return self.entry_identity.author_visible(frame, context=context)
        self.side_entry_identity = None
        self.side_entry_error = None
        try:
            self.side_entry_identity = MapLocalEntryIdentity(identity_root,
                mode=self.pack.mode, profile_id=identity_profile_id,
                floors=self.identity_floors, exit_icon_path=exit_icon_path,
                route_loader=entry_route_loader, geometry_provider=geometry_provider,
                shared_reference_provider=entry_shared_reference_provider,
                author_visible_provider=author_visible_provider,
                structure_store=self.entry_identity.structures,profile_for_map=self.identity_profile_for_map)
        except (ImportError,OSError,ValueError,KeyError,TypeError) as error:
            self.side_entry_error = str(error)
        from front_gate_observation import FrontGatePrefilter
        self.front_gate_prefilter=FrontGatePrefilter(self.pack,self.entry_identity.entries)

    def _native_similarity_prioritize(self, proposals, floor):
        """Only reorder the existing bounded recovery set, never omit a rival."""
        hint = self.native_similarity_priority_provider() if self.native_similarity_priority_provider else None
        index = self.native_similarity_index
        if not hint or index is None:
            return proposals
        if index.condition_index is not None:
            try:
                if str(hint[2])!=str(floor):return proposals
                return index.condition_index.prioritize(proposals,hint,self.entry_shared_reference_provider)
            except (OSError,ValueError,KeyError,TypeError):return proposals
        mode,key,hint_floor,group_id,members=hint
        group=next((g for g in index.groups if g['groupId']==group_id),None)
        try:
            if (mode!=self.pack.mode or str(hint_floor)!=str(floor) or group is None
                    or not any(str(f['floor'])==str(floor) for f in group['floors'])
                    or index.validate(group,self.entry_shared_reference_provider)!=key):
                return proposals
        except (OSError,ValueError,KeyError,TypeError):
            return proposals
        order={mid:i for i,mid in enumerate(members)}
        return sorted(proposals,key=lambda row:order.get(row['mapId'],len(order)))

    @staticmethod
    def _side_recovery_proposals(rows):
        """The existing three-slot/profile/repeated-room selection, unchanged."""
        top=max((float(r['score']) for r in rows),default=0.)
        band=max(6,top*.65)
        proposals=[];represented=set()
        for row in rows:
            if float(row['score'])<6:continue
            profile=row.get('profileId')
            if profile not in represented:
                represented.add(profile)
                if not (row.get('continuousRefinementReason')
                    and not (row.get('verification') or {}).get('success')):
                    proposals.append(row)
        proposals=proposals[:3]
        for first in sorted(proposals,key=lambda row:float(row['score'])):
            if len(proposals)>=3:break
            second=next((row for row in rows
                if row['mapId']==first['mapId'] and row['floor']==first['floor']
                and row is not first),None)
            if second is not None:proposals.append(second)
        for row in rows:
            if len(proposals)>=3:break
            if (float(row['score'])>=band and not row.get('continuousRefinementReason')
                    and not any(row is picked for picked in proposals)):
                proposals.append(row)
        return proposals

    def _shared_entry_inventory(self, floor):
        """Describe whether shared entry proposals cover this pack floor.

        ``MapEntryGeometryIdentity`` only knows about the entry rows present in
        its typed index.  In shared mode that is a proposal inventory, not the
        pack's complete search space.  Keep this check at the engine boundary
        so legacy author-backed packs retain their old behavior and so a
        partial proposal can still flow into the independent local search.
        """
        if self.entry_shared_reference_provider is None:
            return None
        floor = str(floor)
        expected = {
            str(item.id) for item in self.pack.maps
            if floor in {str(key) for key in getattr(item, 'layers', {})}
        }
        available = {
            str(item.get('mapId')) for item, *_ in getattr(self.entry_identity, 'entries', [])
            if str(item.get('floor')) == floor and item.get('mapId') is not None
        }
        applicable_available = sorted(expected & available)
        missing = sorted(expected - available)
        unexpected = sorted(available - expected)
        return dict(
            entryInventoryComplete=not missing,
            entryInventoryExpectedMapIds=sorted(expected),
            entryInventoryAvailableMapIds=applicable_available,
            entryInventoryMissingMapIds=missing,
            entryInventoryUnexpectedMapIds=unexpected,
            entryInventoryScope='shared-pack-floor-manifest',
        )

    @staticmethod
    def _apply_shared_entry_inventory(rows, diagnostics, inventory):
        if inventory is None:
            return
        diagnostics.update(inventory)
        if inventory['entryInventoryComplete']:
            return
        # Keep the row's geometry and current-frame verification intact as a
        # proposal. Only its identity authorization is withheld until the
        # other manifest maps have had the same entry search opportunity.
        for row in rows:
            row.update(
                entryInventoryComplete=False,
                entryInventoryMissingMapIds=list(inventory['entryInventoryMissingMapIds']),
                entryInventoryIdentitySuppressed=bool(row.get('identityVerified')),
            )
            row['identityVerified'] = False
            row['poseVerified'] = False
        diagnostics.setdefault('reason', 'entry-inventory-incomplete')
        diagnostics['entryInventoryProposalOnly'] = True

    def pose_index_candidates(self,frame,map_id,floor,*,cancelled=None,context=None):
        """Optional structural index proposals; the shared pose solver verifies them."""
        return [],dict(reason='pose-index-unavailable',candidateCount=0)

    def _verify_source_structure_match(self, reference, query, match, row, crop_offset, *, include_points=False):
        """Fit on spatial support points and independently check held-out cells."""
        source=np.asarray(match.get('referencePointsScaled',[]),dtype=np.float32)
        target=np.asarray(match.get('queryPointsScaled',[]),dtype=np.float32)
        empty=dict(verified=False,reason='source-structure-heldout-insufficient',
            supportInliers=0,heldOutCount=0,heldOutInliers=0,heldOutRatio=0.0,
            heldOutMedianError=99.0,heldOutP90Error=99.0,heldOutCellCount=0,
            matrixSourceToClient=None)
        if source.ndim!=2 or source.shape[1:]!=(2,) or target.shape!=source.shape or len(source)<14:
            return empty
        cell_size=48.0
        cells=np.floor(source/cell_size).astype(np.int32)
        held=((cells[:,0]+2*cells[:,1])%3)==0
        if int((~held).sum())<8 or int(held.sum())<6:
            return empty
        support_matrix,support_mask=cv2.estimateAffinePartial2D(
            source[~held],target[~held],method=cv2.RANSAC,ransacReprojThreshold=3.5,
            maxIters=1800,confidence=0.995)
        if support_matrix is None or support_mask is None or not np.isfinite(support_matrix).all():
            return empty
        support_keep=support_mask.ravel().astype(bool)
        support_inliers=int(support_keep.sum())
        if support_inliers<7 or np.linalg.matrix_rank(source[~held][support_keep]-source[~held][support_keep].mean(axis=0))<2:
            return dict(empty,reason='source-structure-support-degenerate',supportInliers=support_inliers)
        held_source=source[held];held_target=target[held]
        predicted=cv2.transform(held_source[:,None,:],support_matrix)[:,0,:]
        errors=np.linalg.norm(predicted-held_target,axis=1)
        held_inliers=errors<=4.5
        held_count=int(len(errors));held_inlier_count=int(held_inliers.sum())
        held_ratio=held_inlier_count/max(1,held_count)
        accepted_errors=errors[held_inliers]
        median=float(np.median(accepted_errors)) if len(accepted_errors) else 99.0
        p90=float(np.percentile(accepted_errors,90)) if len(accepted_errors) else 99.0
        held_cells={tuple(point) for point in cells[held][held_inliers]}
        selected_source=held_source[held_inliers]
        source_span=(np.ptp(selected_source,axis=0) if len(selected_source)>1 else np.zeros(2))
        target_span=(np.ptp(held_target[held_inliers],axis=0) if int(held_inliers.sum())>1 else np.zeros(2))
        spread_ok=bool(np.min(source_span)>=80.0 and np.min(target_span)>=45.0)
        verified=bool(held_inlier_count>=5 and held_ratio>=0.55 and median<=3.5
            and p90<=7.5 and len(held_cells)>=4 and spread_ok)
        fit=np.vstack([support_matrix,[0.0,0.0,1.0]])
        ref_scale=float(reference.scale);query_scale=float(query.scale)
        if not (np.isfinite([ref_scale,query_scale]).all() and ref_scale>0 and query_scale>0):
            return dict(empty,reason='source-structure-feature-scale-invalid',supportInliers=support_inliers)
        x0,y0=(float(value) for value in crop_offset)
        query_to_frame=np.asarray([[1/query_scale,0,x0],[0,1/query_scale,y0],[0,0,1]],dtype=np.float64)
        source_to_scaled=np.asarray([[ref_scale,0,0],[0,ref_scale,0],[0,0,1]],dtype=np.float64)
        matrix_source_to_client=query_to_frame@fit@source_to_scaled
        if matrix_source_to_client.shape!=(3,3) or not np.isfinite(matrix_source_to_client).all():
            return dict(empty,reason='source-structure-transform-invalid',supportInliers=support_inliers)
        point_evidence=(dict(
            heldOutSourcePoints=(held_source/float(ref_scale)).tolist(),
            heldOutClientPoints=(held_target/float(query_scale)
                +np.asarray(crop_offset,dtype=np.float32)).tolist(),
            heldOutCellIds=cells[held].tolist()) if include_points else {})
        return dict(verified=verified,reason='source-structure-heldout-current-frame' if verified else 'source-structure-heldout-rejected',
            supportInliers=support_inliers,heldOutCount=held_count,heldOutInliers=held_inlier_count,
            heldOutRatio=held_ratio,heldOutMedianError=median,heldOutP90Error=p90,
            heldOutCellCount=len(held_cells),spatialSupportPixels=[float(source_span[0]),float(source_span[1])],
            spatialQueryPixels=[float(target_span[0]),float(target_span[1])],
            matrixSourceToClient=matrix_source_to_client.tolist(),**point_evidence)

    def source_structure_pose_candidate(self, frame, map_id, floor, *, expected_group=None,
                                        expected_candidate_key=None,allow_provisional_variant=False):
        """Find one source sheet for this frame, without inferring game floor."""
        store=getattr(self.pack,'source_candidates',None)
        if store is None or not store.source_group_for_runtime_map_id(map_id):
            return dict(success=False,reason='source-structure-identity-unavailable')
        group=store.source_group_for_runtime_map_id(map_id)
        if expected_group is not None and str(expected_group)!=group:
            return dict(success=False,reason='source-structure-group-mismatch')
        if str(floor) not in {'1','2','B1'}:
            return dict(success=False,reason='source-structure-game-floor-unsupported',gameFloor=str(floor))
        features,status=store.load_sift_features(max_edge=self.max_edge,opencv_version=cv2.__version__)
        if not status.get('ready'):
            return dict(success=False,reason='source-feature-cache-unavailable',featureStatus=status)
        query_crop,crop_offset=nightmare_map_crop(frame)
        query=self._features(query_crop)
        if query.descriptors is None:return dict(success=False,reason='query-has-insufficient-structure-features')
        rows=[]
        for key,row in sorted(store.entries.items()):
            if str(row.get('sourceGroup'))!=group:continue
            if expected_candidate_key is not None and tuple(map(str,expected_candidate_key))!=tuple(map(str,key)):continue
            reference=features.get(key)
            if reference is None or reference.descriptors is None:continue
            match=self._match(reference,query,include_correspondences=True)
            held=self._verify_source_structure_match(reference,query,match,row,crop_offset,
                include_points=True)
            rows.append((held.get('heldOutInliers',0),-held.get('heldOutP90Error',99),
                match.get('inliers',0),row,held,match))
        def provisional(item):
            held=item[4]
            return bool(allow_provisional_variant and expected_candidate_key is not None
                and held.get('heldOutInliers',0)>=5
                and held.get('heldOutRatio',0)>=.5
                and held.get('heldOutCellCount',0)>=4
                and held.get('heldOutP90Error',99)<=2.0)
        verified=sorted((item for item in rows if item[4].get('verified') or provisional(item)),
            key=lambda item:(item[0],item[1],item[2]),reverse=True)
        if not verified:return dict(success=False,reason='source-structure-current-frame-unverified',
            candidateCount=len(rows),verifiedCandidateCount=0,gameFloor=str(floor),sourceGroup=group,
            rejectedCandidates=[dict(variant=x[3].get('variant'),sourceFloor=x[3].get('sourceFloor'),
                inliers=x[5].get('inliers'),goodMatches=x[5].get('goodMatches'),
                heldOutVerification=x[4]) for x in rows[:6]])
        if len(verified)>1:
            first,second=verified[:2]
            same_quality=(first[0]-second[0]<5 and abs(first[1]-second[1])<1.25
                and first[2]-second[2]<12)
            if same_quality:
                return dict(success=False,reason='source-structure-sheet-ambiguous',
                    candidateCount=len(rows),verifiedCandidateCount=len(verified),gameFloor=str(floor),
                    sourceGroup=group,competitors=[dict(sourceFloor=x[3].get('sourceFloor'),
                        variant=x[3].get('variant'),heldOutInliers=x[4].get('heldOutInliers'),
                        heldOutP90Error=x[4].get('heldOutP90Error')) for x in verified[:3]])
        _,_,_,row,held,match=verified[0]
        return dict(success=True,reason='source-structure-current-frame-verified',
            mapId=str(map_id),sourceProfileId=SOURCE_STRUCTURE_PROFILE_ID,gameFloor=str(floor),
            sourceGroup=group,sourceCandidateId=row.get('sourceCandidateId'),
            producerVariantLabel=str(row.get('variant')),producerSourceFloorLabel=str(row.get('sourceFloor')),
            sourceCandidateKey=[str(row.get('sourceGroup')),str(row.get('variant')),str(row.get('sourceFloor'))],
            route=store.load_rgba(row['sourceGroup'],row['variant'],row['sourceFloor']),
            matrixSourceToClient=held['matrixSourceToClient'],heldOutVerification=held,
            provisionalVariantAccepted=bool(not held.get('verified') and provisional(verified[0])),
            inliers=int(match.get('inliers',0)),goodMatches=int(match.get('goodMatches',0)),
            sourceFloorIsProducerLabelOnly=True,gameFloorInferredFromSourceFloor=False,
            authorRoute=False,crossFloorRelation=None)

    def verify_source_structure_pose(self, frame, map_id, floor, matrix_source_to_client, *,
                                     candidate=None,fit_result=None):
        """Check a proposed source-sheet pose against independent current-frame points."""
        result=(fit_result if isinstance(fit_result,dict) and fit_result.get('success')
            and str(fit_result.get('mapId'))==str(map_id)
            and str(fit_result.get('gameFloor'))==str(floor)
            and str(fit_result.get('sourceGroup'))==str((candidate or {}).get('sourceGroup'))
            and tuple(map(str,fit_result.get('sourceCandidateKey') or ()))
                ==tuple(map(str,(candidate or {}).get('sourceCandidateKey') or ()))
            else self.source_structure_pose_candidate(frame,map_id,floor,
                expected_group=(candidate or {}).get('sourceGroup'),
                expected_candidate_key=(candidate or {}).get('sourceCandidateKey'),
                allow_provisional_variant=bool((candidate or {}).get('producerVariantUncertain'))))
        if not result.get('success'):return dict(verified=False,reason=result.get('reason'),
            validationBasis='source-structure-heldout-current-frame',poseVerifiedAgainstSourceStructure=False,
            sourceGroup=result.get('sourceGroup'),gameFloor=str(floor))
        proposed=np.asarray(matrix_source_to_client,dtype=np.float64)
        if proposed.shape!=(3,3) or not np.isfinite(proposed).all():
            return dict(verified=False,reason='source-structure-proposed-pose-invalid',
                validationBasis='source-structure-heldout-current-frame',poseVerifiedAgainstSourceStructure=False)
        fit_evidence=result['heldOutVerification']
        # The common solver may correct scale as well as translation. Evaluate
        # its proposed pose on SIFT points withheld from the seed fit, rather
        # than forcing it to reproduce the seed matrix itself.
        source=np.asarray(fit_evidence.get('heldOutSourcePoints',[]),dtype=np.float64).reshape(-1,2)
        observed=np.asarray(fit_evidence.get('heldOutClientPoints',[]),dtype=np.float64).reshape(-1,2)
        cells=np.asarray(fit_evidence.get('heldOutCellIds',[]),dtype=np.int32).reshape(-1,2)
        if len(source)<6 or source.shape!=observed.shape or cells.shape!=source.shape:
            return dict(verified=False,reason='source-structure-heldout-points-unavailable',
                validationBasis='source-structure-heldout-current-frame',poseVerifiedAgainstSourceStructure=False)
        predicted=source@proposed[:2,:2].T+proposed[:2,2]
        errors=np.linalg.norm(predicted-observed,axis=1)
        inliers=errors<=4.5
        good=errors[inliers]
        median=float(np.median(good)) if len(good) else float('inf')
        p90=float(np.percentile(good,90)) if len(good) else float('inf')
        cell_count=len({tuple(cell) for cell in cells[inliers]})
        ratio=float(inliers.mean())
        spread=bool(int(inliers.sum())>=5 and np.min(np.ptp(source[inliers],axis=0))>=80
            and np.min(np.ptp(observed[inliers],axis=0))>=45)
        required_ratio=.5 if result.get('provisionalVariantAccepted') else .55
        verified=bool((fit_evidence.get('verified') or result.get('provisionalVariantAccepted'))
            and spread and int(inliers.sum())>=5 and cell_count>=4
            and ratio>=required_ratio and median<=3.5 and p90<=7.5)
        return dict(verified=verified,reason='source-structure-heldout-current-frame' if verified else 'source-structure-pose-disagrees-with-current-frame',
            validationBasis='source-structure-heldout-current-frame',
            poseVerifiedAgainstSourceStructure=verified,sourceCandidateId=result.get('sourceCandidateId'),
            sourceGroup=result.get('sourceGroup'),producerVariantLabel=result.get('producerVariantLabel'),
            producerSourceFloorLabel=result.get('producerSourceFloorLabel'),gameFloor=str(floor),
            heldOutInliers=int(inliers.sum()),heldOutCellCount=cell_count,
            producerVariantUncertain=bool(result.get('provisionalVariantAccepted')),
            heldOutRatio=ratio,heldOutMedianError=median,heldOutP90Error=p90,
            seedHeldOutInliers=fit_evidence.get('heldOutInliers'),
            proposedTransformMedianError=median,proposedTransformP90Error=p90,
            sourceCoordinatesVerified=True,sourceFloorIsProducerLabelOnly=True,
            sourceCoordinateSpace='producer-source-png-pixels',sourcePngIsDisplayImage=True,
            authorRouteEligible=False,crossFloorRelationAvailable=False)

    def _rank_source_structure_candidates(self,frame,*,budget_seconds,floor):
        """Rank source sheets and expose a separate held-out identity result."""
        started=time.perf_counter()
        store=getattr(self.pack,'source_candidates',None)
        if store is None:
            return dict(reason='source-candidate-store-unavailable',complete=True,
                evaluatedEntryCount=0,candidates=[],elapsedMs=(time.perf_counter()-started)*1000)
        remaining=max(0.0,float(budget_seconds or 0.0))
        if remaining<0.04:
            return dict(reason='shared-request-budget-exhausted',complete=False,
                evaluatedEntryCount=0,candidates=[],elapsedMs=(time.perf_counter()-started)*1000)
        features,status=store.load_sift_features(max_edge=self.max_edge,opencv_version=cv2.__version__)
        if not status.get('ready'):
            return dict(reason='source-feature-cache-unavailable',complete=True,
                featureStatus=status,evaluatedEntryCount=0,candidates=[],
                elapsedMs=(time.perf_counter()-started)*1000)
        query_crop,crop_offset=nightmare_map_crop(frame)
        query=self._features(query_crop)
        query_count=len(query.keypoints)
        if query.descriptors is None or query_count<3:
            return dict(reason='query-has-insufficient-structure-features',complete=True,
                featureStatus=status,queryKeypointCount=query_count,evaluatedEntryCount=0,
                candidates=[],elapsedMs=(time.perf_counter()-started)*1000)
        deadline=started+remaining
        rows=[]
        evaluated=0
        for key,row in sorted(store.entries.items()):
            if time.perf_counter()>=deadline:break
            reference=features.get(key)
            if reference is None or reference.descriptors is None:continue
            try:match=self._match(reference,query,include_correspondences=True)
            except (ValueError,cv2.error):continue
            evaluated+=1
            held=self._verify_source_structure_match(reference,query,match,row,crop_offset)
            candidate=dict(sourceGroup=str(row.get('sourceGroup')),
                sourceCandidateId=row.get('sourceCandidateId'),
                producerVariantLabel=str(row.get('variant')),
                producerSourceFloorLabel=str(row.get('sourceFloor')),
                score=float(match['score']),inliers=int(match['inliers']),
                goodMatches=int(match['goodMatches']),inlierRatio=float(match['inlierRatio']),
                medianError=float(match['medianError']),
                featureKeypointCount=len(reference.keypoints),heldOutVerification=held,
                sourceCandidateKey=[str(row.get('sourceGroup')),str(row.get('variant')),str(row.get('sourceFloor'))],
                identityVerified=False,poseVerified=False,displayEligible=False,
                runtimeMapId=store.runtime_map_id(row.get('sourceGroup')))
            rows.append(candidate)
        expected=sum(1 for feature in features.values() if feature.descriptors is not None)
        complete=evaluated==expected
        best_by_group={}
        quality=lambda value:(int((value.get('heldOutVerification') or {}).get('heldOutInliers',0)),
            -float((value.get('heldOutVerification') or {}).get('heldOutP90Error',99.0)),
            int(value.get('inliers',0)))
        for candidate in rows:
            prior=best_by_group.get(candidate['sourceGroup'])
            if prior is None or quality(candidate)>quality(prior):best_by_group[candidate['sourceGroup']]=candidate
        candidates=sorted(best_by_group.values(),key=lambda row:(-row['score'],-row['inliers'],row['sourceGroup']))
        verified_rows=sorted((r for r in rows if (r.get('heldOutVerification') or {}).get('verified')),
            key=quality,reverse=True)
        automatic=None;decision_reason='source-structure-no-heldout-match'
        variant_exclusive=None
        if complete and verified_rows:
            top=verified_rows[0]
            selected=top
            variant_uncertain=False
            same_group=[r for r in verified_rows if r['sourceGroup']==top['sourceGroup']]
            other_group=[r for r in verified_rows if r['sourceGroup']!=top['sourceGroup']]
            same_group.sort(key=quality,reverse=True);other_group.sort(key=quality,reverse=True)
            sheet_clear=(len(same_group)==1 or quality(top)[0]-quality(same_group[1])[0]>=5
                or float((same_group[1].get('heldOutVerification') or {}).get('heldOutP90Error',99))
                   -float((top.get('heldOutVerification') or {}).get('heldOutP90Error',99))>=1.25
                or int(top.get('inliers',0))-int(same_group[1].get('inliers',0))>=12)
            rival_clear=(not other_group or quality(top)[0]-quality(other_group[0])[0]>=5
                or float((other_group[0].get('heldOutVerification') or {}).get('heldOutP90Error',99))
                   -float((top.get('heldOutVerification') or {}).get('heldOutP90Error',99))>=1.25)
            # A larger sheet can match all the visible rooms of a smaller one.
            # Prefer the independently verified smaller sheet until the extra
            # rooms become visible. This is a provisional variant choice within
            # an already identified group, not negative evidence from fog.
            alternates=[row for row in rows if row is not top
                and row['sourceGroup']==top['sourceGroup']
                and row['producerSourceFloorLabel']==top['producerSourceFloorLabel']
                and row['producerVariantLabel']!=top['producerVariantLabel']
                and (row['heldOutVerification'] or {}).get('heldOutInliers',0)>=5
                and (row['heldOutVerification'] or {}).get('heldOutP90Error',99)<=2.0
                and (row['heldOutVerification'] or {}).get('matrixSourceToClient') is not None]
            for alternate in alternates:
                top_row=store.entries[tuple(top['sourceCandidateKey'])]
                alternate_row=store.entries[tuple(alternate['sourceCandidateKey'])]
                if np.prod(top_row['canvasPx'])<=np.prod(alternate_row['canvasPx'])*1.1:
                    continue
                top_image=store.load_rgba(top_row['sourceGroup'],top_row['variant'],top_row['sourceFloor'])
                alternate_image=store.load_rgba(alternate_row['sourceGroup'],alternate_row['variant'],alternate_row['sourceFloor'])
                if top_image is None or alternate_image is None:continue
                size=frame.shape[1::-1]
                top_mask=cv2.warpAffine((top_image[:,:,3]>16).astype(np.uint8),
                    np.asarray(top['heldOutVerification']['matrixSourceToClient'],dtype=np.float32)[:2],size)>0
                alternate_mask=cv2.warpAffine((alternate_image[:,:,3]>16).astype(np.uint8),
                    np.asarray(alternate['heldOutVerification']['matrixSourceToClient'],dtype=np.float32)[:2],size)>0
                exclusive=top_mask & ~cv2.dilate(alternate_mask.astype(np.uint8),
                    np.ones((7,7),np.uint8)).astype(bool)
                exclusive_count=int(exclusive.sum())
                observed_count=int((exclusive & (map_color_mask(frame,live=True)>0)).sum())
                variant_exclusive=dict(top=top['sourceCandidateKey'],alternate=alternate['sourceCandidateKey'],
                    exclusivePixels=exclusive_count,observedExclusivePixels=observed_count)
                if exclusive_count>=500 and (observed_count<500
                    or observed_count/exclusive_count<0.015):
                    selected=alternate
                    variant_uncertain=True
                    variant_exclusive['selected']=alternate['sourceCandidateKey']
                    break
            if sheet_clear and rival_clear:
                group=selected['sourceGroup']
                map_id=store.runtime_map_id(group)
                automatic=dict(selected,identityVerified=True,poseVerified=False,displayEligible=False,
                    runtimeMapId=map_id,gameFloor=str(floor),gameFloorFromCurrentObservation=True,
                    sourceFloorIsProducerLabelOnly=True,
                    identityEvidence=('source-structure-group-heldout-variant-provisional'
                        if variant_uncertain else 'source-structure-heldout-current-frame'),
                    poseSubmissionAllowed=False,displayEligibleAfterSharedCommitOnly=True,
                    sourceMatchMethod=('group-heldout-plus-visible-variant-selection'
                        if variant_uncertain else 'independent-spatial-heldout-sift'),
                    producerVariantUncertain=variant_uncertain)
                decision_reason=('source-structure-visible-variant-selected' if variant_uncertain
                    else 'source-structure-heldout-unique')
            else:
                decision_reason='source-structure-competitor-ambiguous'
        elif not complete:decision_reason='source-structure-search-incomplete'
        return dict(reason='shared-sift-source-structure-advisory',complete=evaluated==expected,
            elapsedMs=(time.perf_counter()-started)*1000,featureStatus=status,
            queryKeypointCount=query_count,evaluatedEntryCount=evaluated,availableEntryCount=expected,
            candidateCount=len(candidates),candidates=candidates,
            sourceLabelsAreNotGameTruth=True,identityHardGatePassed=False,
            heldOutDecisionReason=decision_reason,automaticCandidate=automatic,
            variantExclusiveEvidence=variant_exclusive,
            poseSubmissionAllowed=False,authorRouteDisplayAllowed=False)

    def rank(self,frame,floor='1',limit=30,*,budget_seconds=None):
        if floor not in self.identity_floors:return []
        started=time.perf_counter()
        allowed=None;gate_report=None
        prefilter=getattr(self,'front_gate_prefilter',None)
        if prefilter is not None:
            allowed,gate_report=prefilter.select(frame,floor,self.entry_identity.geometry)
        gate_args=dict(candidate_map_ids=allowed) if allowed is not None else {}
        budget=1.0 if budget_seconds is None else max(0.,float(budget_seconds))
        remaining=lambda:max(0.,budget-(time.perf_counter()-started))
        passport_rows=[];passport_diagnostics=None
        rank_entry=getattr(self.side_entry_identity,'rank_entry',None)
        if rank_entry is not None:
            try:
                passport_rows,passport_diagnostics=rank_entry(frame,floor,budget_seconds=remaining(),**gate_args)
            except (ValueError,TypeError,KeyError,OSError,cv2.error) as error:
                # A failed exit observation cannot authorize a generic identity.
                passport_diagnostics=dict(reason='entry-resource-unavailable',error=str(error),
                    exitObserved=None,entryObserved=False,complete=False)
        entry_rows=[];entry_diagnostics=dict(reason='side-entry-path-selected',complete=True)
        fallback_allowed=entry_passport_fallback_allowed(passport_diagnostics)
        passport_selected=bool(passport_diagnostics and not fallback_allowed
            and passport_diagnostics.get('entryObserved') is True)
        entry_unresolved=passport_diagnostics is not None and not fallback_allowed and not passport_selected
        if entry_unresolved:
            entry_diagnostics=dict(reason=passport_diagnostics['reason'],complete=False)
        if not passport_selected and not entry_unresolved:
            entry_rows,entry_diagnostics=self.entry_identity.rank(frame,floor,budget_seconds=remaining(),**gate_args)
            self._apply_shared_entry_inventory(
                entry_rows, entry_diagnostics, self._shared_entry_inventory(floor))
            check_entry=getattr(self.side_entry_identity,'check_entry_evidence',None)
            if check_entry is not None and passport_diagnostics is not None:
                for row in entry_rows:
                    if not row.get('identityVerified'):continue
                    candidate=dict(row,floor=str(floor),profileId=self.identity_profile_for_map(row['mapId']),
                        matrixSourceToClient=row['verification']['matrixSourceToClient'])
                    try:
                        row['entryCrossCheck']=check_entry(frame,candidate,observation=passport_diagnostics)
                    except (ValueError,TypeError,KeyError,OSError,cv2.error):
                        row['identityVerified']=False;row['poseVerified']=False
                        row['entryCrossCheck']=dict(reason='entry-cross-check-unavailable')
                        continue
                    if row['entryCrossCheck'].get('contradicted') is True:
                        row['identityVerified']=False;row['poseVerified']=False
        side_producer='not-run'
        original_group_recovery=[]
        side_rows=[];side_diagnostics=(dict(reason='side-resource-unavailable',error=self.side_entry_error,complete=False)
            if self.side_entry_identity is None else None);side_winner=None
        if passport_selected:
            side_producer='passport'
            side_rows,side_diagnostics=passport_rows,passport_diagnostics
            side_winner=verified_entry_candidate(side_rows,side_diagnostics,floor,profile_id=self.identity_profile_for_map)
        if (not passport_selected and fallback_allowed
                and not any(row['identityVerified'] for row in entry_rows) and self.side_entry_identity is not None):
            side_producer='local-structure'
            try:
                side_rows,side_diagnostics=self.side_entry_identity.rank(frame,floor,budget_seconds=remaining(),**gate_args)
                # Refine one fully competed local hypothesis for any map. The
                # bound, request budget and normal author/identity checks remain
                # identical across resource families.
                if (side_rows and side_diagnostics.get('complete')
                    and side_diagnostics.get('competitiveHypotheses')==1 and remaining()>0
                    and not (side_rows[0].get('verification') or {}).get('success')):
                    side_rows[0]=self.side_entry_identity.refine_candidate(frame,side_rows[0])
                    side_diagnostics=dict(side_diagnostics,
                        continuousRefinementMs=side_rows[0].get('continuousRefinementMs'),
                        continuousRefinementReason=side_rows[0].get('continuousRefinementReason'))
                check_entry=getattr(self.side_entry_identity,'check_entry_evidence',None)
                if check_entry is not None and passport_diagnostics is not None and side_rows:
                    band=max(4,max(float(row['score']) for row in side_rows)-1)
                    for row in side_rows:
                        if float(row['score'])>=band:
                            row['entryCrossCheck']=check_entry(frame,row,observation=passport_diagnostics)
                side_winner=verified_side_candidate(side_rows,side_diagnostics,floor,entry_rows,frame.shape,profile_id=self.identity_profile_for_map)
                if side_winner is None and side_diagnostics.get('complete') and remaining()>0:
                    # Raw chain counts are not comparable across author
                    # profiles with different drawing density. Reserve one
                    # recovery slot per profile before filling the remaining
                    # three-slot budget from the strongest other poses.
                    # Every proposal still takes the identical current-frame
                    # wall/author/identity verification below.
                    top=max((float(r['score']) for r in side_rows),default=0.)
                    original_proposals=self._side_recovery_proposals(side_rows)
                    original_group_recovery=original_proposals
                    recovery_rows=self._native_similarity_prioritize(side_rows,floor)
                    group_priority_applied=any(a is not b for a,b in zip(recovery_rows,side_rows))
                    proposals=(self._side_recovery_proposals(recovery_rows)
                        if group_priority_applied else original_proposals)
                    accepted=[];evaluated=0
                    completed=set()
                    for candidate in proposals[:3]:
                        if remaining()<=0:break
                        if not (candidate.get('verification') or {}).get('evaluated',True):
                            candidate['verification']=self.side_entry_identity.verify(frame,candidate)
                        if (not candidate['verification'].get('success')
                            and not candidate.get('continuousRefinementReason')):
                            refined=self.side_entry_identity.refine_candidate(frame,candidate)
                            candidate.update(refined)
                        if check_entry is not None and passport_diagnostics is not None:
                            candidate['entryCrossCheck']=check_entry(frame,candidate,observation=passport_diagnostics)
                        evaluated+=1
                        completed.add(id(candidate))
                        if (candidate['verification'].get('success')
                            and (candidate.get('entryCrossCheck') or {}).get('contradicted') is not True):
                            accepted.append(candidate)
                    side_diagnostics=dict(side_diagnostics,poseRecoveryEvaluated=evaluated,
                        poseRecoveryCandidates=len(proposals))
                    unresolved=[]
                    if group_priority_applied:
                        # Reordering work cannot erase original required
                        # recovery paths or any already successful rival.
                        # An initial failed verify is not a finished rejection
                        # when the original path still owes refinement.
                        required=list(original_proposals)
                        for row in side_rows:
                            if ((row.get('verification') or {}).get('success')
                                    or float(row['score'])>=max(4,top-1)):
                                if not any(row is old for old in required):required.append(row)
                        unresolved=[row['mapId'] for row in required
                            if id(row) not in completed
                            and not (row.get('verification') or {}).get('success')
                            and (not row.get('continuousRefinementReason')
                                 or (row.get('verification') or {}).get('evaluated',True) is False)]
                        for row in required:
                            if ((row.get('verification') or {}).get('success')
                                    and (row.get('entryCrossCheck') or {}).get('contradicted') is not True
                                    and not any(row is old for old in accepted)):
                                accepted.append(row)
                        side_diagnostics['groupPriorityUnverifiedCompetitors']=unresolved
                    equivalent=len(accepted)==1
                    if len(accepted)>1 and len({r['mapId'] for r in accepted})==1:
                        # Compare actual observed client positions, not remote
                        # unobserved floor extents. One-cell shifts remain
                        # distinct; only fits within the existing three-pixel
                        # retrieval tolerance are merged.
                        _,nodes,_=self.side_entry_identity._observe(frame)
                        points=np.array([[*n['point'],1.] for n in nodes])
                        equivalent=equivalent_observed_poses(accepted,points)
                    if unresolved:equivalent=False
                    side_diagnostics['poseRecoveryEquivalent']=equivalent
                    if evaluated==len(proposals) and accepted and equivalent:
                        candidate=dict(accepted[0],poseAmbiguous=False)
                        side_winner=verified_side_candidate([candidate],dict(complete=True,
                            verificationComplete=True,competitiveHypotheses=1,identityAmbiguous=False),
                            floor,entry_rows,frame.shape,profile_id=self.identity_profile_for_map)
            except (ValueError,TypeError,KeyError,OSError,cv2.error) as error:
                side_diagnostics=dict(reason='side-resource-unavailable',error=str(error),complete=False)
        registered_identity_candidates=[dict(mapId=str(row.get('mapId')),
            score=float(row.get('score',0.0)),identityEvidence=row.get('identityEvidence'),
            identityVerified=bool(row.get('identityVerified')))
            for row in entry_rows if row.get('identityVerified')]
        if side_winner is not None:
            registered_identity_candidates.append(dict(mapId=str(side_winner.get('mapId')),
                score=float(side_winner.get('score',0.0)),
                identityEvidence=side_winner.get('identityEvidence'),identityVerified=True))
        # A registered identity does not suppress the new source-family scan.
        # Both searches share the original request budget; source rows remain
        # advisory and cannot replace the registered identity or authorize pose/display.
        source_candidates=self._rank_source_structure_candidates(
            frame,budget_seconds=remaining(),floor=str(floor))
        source_candidates=dict(source_candidates,
            registeredIdentityCandidates=registered_identity_candidates,
            evaluatedAlongsideRegisteredIdentity=bool(registered_identity_candidates),
            crossDomainScoresComparable=False,
            identityHardGatePassed=False,poseSubmissionAllowed=False,
            authorRouteDisplayAllowed=False)
        # A uniquely verified current-frame identity no longer needs the old
        # icon-only advisory pass. Unresolved inputs keep that fallback intact.
        evidence=(dict(candidates=[],entryKind='main',reason='tilemap-entry-verified')
                  if any(row['identityVerified'] for row in entry_rows) or side_winner is not None
                  else self.entry_advisory(frame) if self.entry_advisory is not None else dict(candidates=[],entryKind='unknown'))
        evidence['tilemapEntry']=entry_diagnostics
        evidence['tilemapCandidates']=entry_rows
        evidence['tilemapSide']=side_diagnostics
        evidence['tilemapSideCandidates']=side_rows
        evidence['entryPassport']=passport_diagnostics
        evidence['entryPassportCandidates']=passport_rows
        evidence['sourceStructureCandidates']=source_candidates
        evidence['recognitionPath']=dict(passportSelected=passport_selected,
            fallbackAllowed=fallback_allowed, passportUnresolved=entry_unresolved,
            sideProducer=side_producer, sideWinnerMapId=(side_winner or {}).get('mapId'),
            sourceStructureCandidateCount=source_candidates.get('candidateCount',0),
            sourceStructureIdentityAuthorized=False,
            sourceStructureIdentityVerified=bool(source_candidates.get('automaticCandidate')),
            sourceStructureConsumer='separate-structure-reference-branch',
            frameSize=[int(frame.shape[1]),int(frame.shape[0])])
        self.last_difficult_evidence=evidence
        suggestions={r['mapId']:r for r in evidence.get('candidates',[])}
        rows=[]
        for item in self.pack.maps:
            if allowed is not None and item.id not in allowed:continue
            suggested=suggestions.get(item.id,{})
            rows.append(dict(mapId=item.id,name=item.name,score=float(suggested.get('score',0))*100,
                strongSuggestion=False,identityHardGatePassed=False,inliers=0,
                reason='difficult-entry-advisory-manual-confirmation-required',mode=self.pack.mode,
                entryKind=evidence.get('entryKind','unknown'),direction=evidence.get('direction')))
        rows.sort(key=lambda row:row['score'],reverse=True)
        by_id={row['mapId']:row for row in rows}
        for candidate in entry_rows:
            row=by_id[candidate['mapId']]
            verified=candidate['identityVerified']
            row.update(score=200+candidate['score'],entryKind='main',
                strongSuggestion=verified,identityHardGatePassed=verified,
                identityEvidence='tilemap-entry-neighborhood-current-frame' if verified else 'tilemap-entry-candidates',
                reason=('entry-current-exit-contradicted' if (candidate.get('entryCrossCheck') or {}).get('contradicted') is True
                        else entry_diagnostics['reason']),difficultEntry=candidate,
                identityElapsedMs=entry_diagnostics['elapsedMs'],identityBudgetExceeded=not entry_diagnostics.get('complete',True))
            if verified:row['score']+=1000
        for candidate in side_rows:
            row=by_id.get(candidate['mapId'])
            if row is None or row.get('identityHardGatePassed') or row['score']>=200+candidate['score']:continue
            row.update(score=200+candidate['score'],entryKind=candidate['entryKind'],
                strongSuggestion=False,identityHardGatePassed=False,identityEvidence='side-structure-candidates',
                reason=('entry-current-exit-contradicted' if (candidate.get('entryCrossCheck') or {}).get('contradicted') is True
                        else side_diagnostics['reason']),difficultEntry=dict(candidate,locatorKind='side',identityVerified=False),
                identityElapsedMs=(time.perf_counter()-started)*1000,
                identityBudgetExceeded=not side_diagnostics.get('complete',False))
        if side_winner is not None and side_winner['mapId'] in by_id:
            row=by_id[side_winner['mapId']]
            row.update(score=1200+side_winner['score'],entryKind=side_winner['entryKind'],
                strongSuggestion=True,identityHardGatePassed=True,
                identityEvidence=side_winner['identityEvidence'],reason='side-current-identity-verified',
                difficultEntry=side_winner,identityElapsedMs=(time.perf_counter()-started)*1000,
                identityBudgetExceeded=False)
        rows.sort(key=lambda row:row['score'],reverse=True)
        diagnostics = entry_rank_diagnostics(evidence, side_error=self.side_entry_error)
        diagnostics['elapsedMs'] = (time.perf_counter()-started)*1000
        if gate_report is not None:
            from front_gate_observation import record
            gate_report.update(actualFloor=str(floor),rankElapsedMs=diagnostics['elapsedMs'],
                evaluatedMainMapIds=entry_diagnostics.get('evaluatedMapIds',[]),
                acceptedMapIds=[r['mapId'] for r in rows if r.get('identityHardGatePassed')],
                mainComplete=entry_diagnostics.get('complete'),
                sideComplete=(side_diagnostics or {}).get('complete'))
            diagnostics['frontGateObservation']=gate_report
            record(gate_report)
        for row in rows:
            row['difficultRecognition'] = diagnostics
        if rows:
            # Reuse the existing entry competitive band and original side
            # band/recovery set. Raw weak hypotheses remain on all rows, but
            # do not become new mandatory group competitors merely by existing.
            front_top=max((float(r['score']) for r in entry_rows),default=0.)
            side_top=max((float(r['score']) for r in side_rows),default=0.)
            group_required={r['mapId'] for r in entry_rows if float(r['score'])>=max(10,front_top*.65)}
            group_required.update(r['mapId'] for r in side_rows if float(r['score'])>=max(4,side_top-1)
                or (r.get('verification') or {}).get('success'))
            group_required.update(r['mapId'] for r in original_group_recovery)
            rows[0]['nativeSimilarity'] = self.native_similarity_index.propose(
                rows, entry_rows, entry_diagnostics, str(floor),
                self.entry_shared_reference_provider, lambda:self.entry_identity.geometry(frame),
                side_rows=side_rows,side_diagnostics=side_diagnostics,
                deadline=started+budget,prior=(self.native_similarity_context_provider()
                    if getattr(self,'native_similarity_context_provider',None) else None),
                required_map_ids=tuple(sorted(group_required))) if self.native_similarity_index is not None else dict(status='none',reason='similarity-root-not-configured')
        diagnostics['elapsedMs'] = (time.perf_counter()-started)*1000
        return rows[:limit]

    def identity_profile_for_map(self, map_id):
        return getattr(self.pack, 'identity_profiles', {}).get(map_id, self.identity_profile_id)

    def native_similarity_pose_observation(self, frame, map_id, floor, matrix, *, prior, deadline):
        """Continue an established entrance using one independently checked pose.

        This does not retrieve a new group or authorize identity/route pixels.
        The caller owns the current-frame/global validation of this matrix.
        """
        index=getattr(self,'native_similarity_index',None)
        conditions=getattr(index,'condition_index',None)
        if conditions is None or str(floor)!='1' or not prior:
            return None
        rid=f'{self.pack.mode}/{map_id}/{floor}'
        reference=conditions.references.get(rid)
        if reference is None:return None
        side=reference['features'].get('side') or {}
        row=dict(mapId=map_id,profileId=reference['profile'],
            sourceProduct=side.get('sourceProduct',reference['resourcePath']),
            verification=dict(success=True,residual=dict(verified=True),
                matrixSourceToClient=np.asarray(matrix,dtype=float).tolist()))
        cohorts=[{conditions.references[conditions.poses[p]['target']]['mapId']
            for p in map(str,conditions.conditions[cid]['poseIds'])}
            for did,cids in prior[3] if did in conditions.domains
            for cid in cids if cid in conditions.conditions]
        members=set.intersection(*cohorts) if cohorts else set()
        if map_id not in members:return None
        return index.propose([],[],dict(complete=True,entryInventoryComplete=True),str(floor),
            self.entry_shared_reference_provider,lambda:self.entry_identity.geometry(frame),
            side_rows=[row],side_diagnostics=dict(complete=True),deadline=deadline,
            prior=prior,required_map_ids=tuple(sorted(members)))

    def native_similarity_recover_observation(self, frame, floor, *, prior, deadline):
        """Ask the existing local locator only within the established family."""
        index=getattr(self,'native_similarity_index',None)
        conditions=getattr(index,'condition_index',None)
        if conditions is None or str(floor)!='1' or not prior:return None
        cohorts=[{conditions.references[conditions.poses[p]['target']]['mapId']
            for p in map(str,conditions.conditions[cid]['poseIds'])}
            for did,cids in prior[3] if did in conditions.domains
            for cid in cids if cid in conditions.conditions]
        members=set.intersection(*cohorts) if cohorts else set()
        current=[]
        for member in sorted(members):
            remaining=deadline-time.perf_counter()
            if remaining<=0:return None
            rows,diagnostic=self.side_entry_identity.rank(frame,str(floor),map_id=member,
                budget_seconds=remaining)
            if diagnostic.get('complete') is not True or diagnostic.get('verificationComplete') is not True:
                return None
            current.extend(r for r in rows if not r.get('poseAmbiguous')
                and (r.get('verification') or {}).get('success') is True
                and ((r.get('verification') or {}).get('residual') or {}).get('verified') is True)
        if not current or time.perf_counter()>=deadline:return None
        return index.propose([],[],dict(complete=True,entryInventoryComplete=True),str(floor),
            self.entry_shared_reference_provider,lambda:self.entry_identity.geometry(frame),
            side_rows=current,side_diagnostics=dict(complete=True),deadline=deadline,
            prior=prior,required_map_ids=tuple(sorted(members)))

    def preload(self,floor='1'):
        if floor not in self.identity_floors:return 0
        source_store=getattr(self.pack,'source_candidates',None)
        self._source_candidate_preload_status=(source_store.load_sift_features(
            max_edge=self.max_edge,opencv_version=cv2.__version__)[1]
            if source_store is not None else dict(ready=False,reason='source-candidate-store-unavailable'))
        if self.entry_shared_reference_provider is not None:
            return sum(self.entry_shared_reference_provider(
                self.identity_profile_for_map(m.id), m.id, floor) is not None
                for m in self.pack.maps)
        return sum(self.entry_identity.structures.load(self.identity_profile_for_map(m.id),m.id,floor) is not None for m in self.pack.maps)

    def preload_status(self,floor='1'):
        return dict(floor=floor,ready=floor in self.identity_floors,loaded=0,total=len(self.pack.maps),mode=self.pack.mode,
            sourceStructureFeatures=getattr(self,'_source_candidate_preload_status',dict(ready=False,reason='not-preloaded')))

    def preload_fast_exit_references(self):return 0
    def preload_memory(self,floor='1'):return 0
