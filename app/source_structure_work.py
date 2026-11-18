"""Source-sheet evidence jobs; source IDs never become canonical map poses."""
import time

import cv2
import numpy as np

from entry_evidence_work import EntryEvidenceWork
from nightmare_candidate import nightmare_map_crop, map_color_mask


class SourceStructureEvidence(EntryEvidenceWork):
    def __init__(self, engine, frame, floor, *, deadline):
        self.engine, self.floor, self.deadline = engine, str(floor), deadline
        self.started = time.perf_counter()
        self.store = getattr(engine.pack, 'source_candidates', None)
        self.features, self.status, self.query = {}, None, None
        self.rows, self.candidates, self.evaluated, self.expected = [], [], 0, 0
        self.automatic, self.variant_exclusive, self.selected = None, None, None
        self.reason = None
        self.decision_reason = 'source-structure-no-heldout-match'
        self.variant_uncertain = False
        super().__init__(frame)

    def _load_features(self):
        self.features, self.status = self.store.load_sift_features(
            max_edge=self.engine.max_edge, opencv_version=cv2.__version__)
        self.expected = sum(feature.descriptors is not None for feature in self.features.values())

    def _observe(self):
        crop, self.crop_offset = nightmare_map_crop(self.frame)
        self.query = self.engine._features(crop)

    def _match(self, key, row):
        reference = self.features[key]
        try:
            match = self.engine._match(reference, self.query, include_correspondences=True)
        except (ValueError, cv2.error):
            return
        self.evaluated += 1
        held = self.engine._verify_source_structure_match(reference, self.query, match, row, self.crop_offset)
        self.rows.append(dict(sourceGroup=str(row.get('sourceGroup')),
            sourceCandidateId=row.get('sourceCandidateId'), producerVariantLabel=str(row.get('variant')),
            producerSourceFloorLabel=str(row.get('sourceFloor')), score=float(match['score']),
            inliers=int(match['inliers']), goodMatches=int(match['goodMatches']),
            inlierRatio=float(match['inlierRatio']), medianError=float(match['medianError']),
            featureKeypointCount=len(reference.keypoints), heldOutVerification=held,
            sourceCandidateKey=[str(row.get('sourceGroup')), str(row.get('variant')), str(row.get('sourceFloor'))],
            identityVerified=False, poseVerified=False, displayEligible=False,
            runtimeMapId=self.store.runtime_map_id(row.get('sourceGroup'))))

    @staticmethod
    def _quality(row):
        held = row.get('heldOutVerification') or {}
        return (int(held.get('heldOutInliers', 0)), -float(held.get('heldOutP90Error', 99.)),
                int(row.get('inliers', 0)))

    def _rank(self):
        best = {}
        for row in self.rows:
            previous = best.get(row['sourceGroup'])
            if previous is None or self._quality(row) > self._quality(previous):
                best[row['sourceGroup']] = row
        self.candidates = sorted(best.values(), key=lambda row: (-row['score'], -row['inliers'], row['sourceGroup']))
        verified = sorted((row for row in self.rows if (row.get('heldOutVerification') or {}).get('verified')),
                          key=self._quality, reverse=True)
        return verified

    def _compare_variant(self, alternate):
        top = self.top
        top_row = self.store.entries[tuple(top['sourceCandidateKey'])]
        alternate_row = self.store.entries[tuple(alternate['sourceCandidateKey'])]
        top_image = self.store.load_rgba(top_row['sourceGroup'], top_row['variant'], top_row['sourceFloor'])
        alternate_image = self.store.load_rgba(alternate_row['sourceGroup'], alternate_row['variant'], alternate_row['sourceFloor'])
        if top_image is None or alternate_image is None:
            return
        size = self.frame.shape[1::-1]
        top_mask = cv2.warpAffine((top_image[:, :, 3] > 16).astype(np.uint8),
            np.asarray(top['heldOutVerification']['matrixSourceToClient'], dtype=np.float32)[:2], size) > 0
        alternate_mask = cv2.warpAffine((alternate_image[:, :, 3] > 16).astype(np.uint8),
            np.asarray(alternate['heldOutVerification']['matrixSourceToClient'], dtype=np.float32)[:2], size) > 0
        exclusive = top_mask & ~cv2.dilate(alternate_mask.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        count = int(exclusive.sum())
        observed = int((exclusive & (map_color_mask(self.frame, live=True) > 0)).sum())
        self.variant_exclusive = dict(top=top['sourceCandidateKey'], alternate=alternate['sourceCandidateKey'],
            exclusivePixels=count, observedExclusivePixels=observed)
        if count >= 500 and (observed < 500 or observed/count < .015):
            self.selected, self.variant_uncertain = alternate, True
            self.variant_exclusive['selected'] = alternate['sourceCandidateKey']

    def _jobs(self):
        if self.store is None:
            self.reason = 'source-candidate-store-unavailable'
            return
        if self.deadline - self.started < .04:
            self.reason = 'shared-request-budget-exhausted'
            return
        yield self._job(None, 'source-feature-store', self._load_features)
        if not self.status.get('ready'):
            self.reason = 'source-feature-cache-unavailable'
            return
        yield self._job(None, 'source-query-features', self._observe)
        if self.query.descriptors is None or len(self.query.keypoints) < 3:
            self.reason = 'query-has-insufficient-structure-features'
            return
        for key, row in sorted(self.store.entries.items()):
            reference = self.features.get(key)
            if reference is not None and reference.descriptors is not None:
                yield self._job(self.store.runtime_map_id(row.get('sourceGroup')), 'source-match-heldout',
                    lambda key=key, row=row: self._match(key, row))
        verified = self._rank()
        if self.evaluated != self.expected:
            self.decision_reason = 'source-structure-search-incomplete'
            return
        if not verified:
            return
        self.top = self.selected = verified[0]
        same = [row for row in verified if row['sourceGroup'] == self.top['sourceGroup']]
        other = [row for row in verified if row['sourceGroup'] != self.top['sourceGroup']]
        quality, top = self._quality, self.top
        sheet_clear = (len(same) == 1 or quality(top)[0] - quality(same[1])[0] >= 5
            or float((same[1].get('heldOutVerification') or {}).get('heldOutP90Error', 99))
               - float((top.get('heldOutVerification') or {}).get('heldOutP90Error', 99)) >= 1.25
            or int(top.get('inliers', 0)) - int(same[1].get('inliers', 0)) >= 12)
        rival_clear = (not other or quality(top)[0] - quality(other[0])[0] >= 5
            or float((other[0].get('heldOutVerification') or {}).get('heldOutP90Error', 99))
               - float((top.get('heldOutVerification') or {}).get('heldOutP90Error', 99)) >= 1.25)
        for alternate in self.rows:
            if (alternate is top or alternate['sourceGroup'] != top['sourceGroup']
                    or alternate['producerSourceFloorLabel'] != top['producerSourceFloorLabel']
                    or alternate['producerVariantLabel'] == top['producerVariantLabel']
                    or (alternate['heldOutVerification'] or {}).get('heldOutInliers', 0) < 5
                    or (alternate['heldOutVerification'] or {}).get('heldOutP90Error', 99) > 2.
                    or (alternate['heldOutVerification'] or {}).get('matrixSourceToClient') is None):
                continue
            top_row = self.store.entries[tuple(top['sourceCandidateKey'])]
            alternate_row = self.store.entries[tuple(alternate['sourceCandidateKey'])]
            if np.prod(top_row['canvasPx']) <= np.prod(alternate_row['canvasPx']) * 1.1:
                continue
            yield self._job(self.store.runtime_map_id(top['sourceGroup']), 'source-variant-visibility',
                lambda alternate=alternate: self._compare_variant(alternate))
            if self.variant_uncertain:
                break
        if sheet_clear and rival_clear:
            self.automatic = dict(self.selected, identityVerified=True, poseVerified=False, displayEligible=False,
                runtimeMapId=self.store.runtime_map_id(self.selected['sourceGroup']), gameFloor=self.floor,
                gameFloorFromCurrentObservation=True, sourceFloorIsProducerLabelOnly=True,
                identityEvidence=('source-structure-group-heldout-variant-provisional' if self.variant_uncertain
                    else 'source-structure-heldout-current-frame'), poseSubmissionAllowed=False,
                displayEligibleAfterSharedCommitOnly=True,
                sourceMatchMethod=('group-heldout-plus-visible-variant-selection' if self.variant_uncertain
                    else 'independent-spatial-heldout-sift'), producerVariantUncertain=self.variant_uncertain)
            self.decision_reason = ('source-structure-visible-variant-selected' if self.variant_uncertain
                                    else 'source-structure-heldout-unique')
        else:
            self.decision_reason = 'source-structure-competitor-ambiguous'

    def finish(self):
        if self._result is not None:
            return self._result
        self.close()
        elapsed = (time.perf_counter() - self.started) * 1000
        if self.reason is not None:
            self._result = dict(reason=self.reason, complete=self._complete and self.reason != 'shared-request-budget-exhausted',
                evaluatedEntryCount=0, candidates=[], elapsedMs=elapsed)
            if self.status is not None:
                self._result['featureStatus'] = self.status
            if self.query is not None:
                self._result['queryKeypointCount'] = len(self.query.keypoints)
        else:
            self._rank()
            complete = self._complete and not self._failed and self.evaluated == self.expected
            self._result = dict(reason='source-structure-work-failed' if self._failed else 'shared-sift-source-structure-advisory',
                complete=complete, elapsedMs=elapsed, featureStatus=self.status,
                queryKeypointCount=len(self.query.keypoints) if self.query is not None else 0,
                evaluatedEntryCount=self.evaluated, availableEntryCount=self.expected,
                candidateCount=len(self.candidates), candidates=self.candidates,
                sourceLabelsAreNotGameTruth=True, identityHardGatePassed=False,
                heldOutDecisionReason=self.decision_reason if complete else 'source-structure-search-incomplete',
                automaticCandidate=self.automatic if complete else None, variantExclusiveEvidence=self.variant_exclusive,
                poseSubmissionAllowed=False, authorRouteDisplayAllowed=False)
        return self._result
