"""Bounded failure evidence on the existing Session worker; no capture/thread."""
from __future__ import annotations

import hashlib
import threading
import time

from failure_sampling import FailureSampling


def bounded_evidence(value):
    """Keep measured diagnostics, marking every omitted branch explicitly."""
    import numpy as np
    remaining = [20000]
    truncated = [False]
    def visit(item, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 12:
            truncated[0] = True
            return {"omitted": "diagnostic-bound"}
        if isinstance(item, np.ndarray):
            if item.size > 4096:
                truncated[0] = True
                return dict(omitted="array-bound", shape=list(item.shape), dtype=str(item.dtype))
            return visit(item.tolist(), depth + 1)
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, dict):
            if len(item) > 512:
                truncated[0] = True
            return {str(k): visit(v, depth+1) for k, v in list(item.items())[:512]}
        if isinstance(item, (tuple, list)):
            if len(item) > 512:
                truncated[0] = True
            return [visit(v, depth+1) for v in item[:512]]
        if isinstance(item, str):
            if len(item) > 8192:
                truncated[0] = True
            return item[:8192]
        return item if isinstance(item, (int, float, bool, type(None))) else str(item)[:512]
    result = visit(value)
    return result, truncated[0]


class FailureSampleRecorder:
    """Reuse store's lossless writer/disk budget and shared cooldown policy.

    A write is part of the existing Session job and therefore its drain. There
    is no daemon to outlive shutdown. Manual full frames keep the 12-per-process
    limit. Automatic cropped evidence uses bounded disk rotation and cooldown,
    so a long-running process can retain new failures after its first six images.
    """
    def __init__(self):
        self.enabled = False
        self.automatic_enabled = False
        self.metadata = {}
        self.policy = FailureSampling()
        # Missing frames/crops need bounded metadata, not an image cooldown.
        # Keep this separate so the same frame's valid control band (or the
        # next valid map crop) is not blocked by metadata-only evidence.
        self._automatic_metadata_policy = FailureSampling()
        self.lock = threading.Lock()
        self.saved_count = 0
        self.automatic_saved_count = 0
        self.last_error = ""

    def record_automatic(self, *, root, reason, frame, diagnostic, current,
                         crop_bounds=None, crop_provenance=None):
        if not self.automatic_enabled:
            return dict(saved=False, missingReason='automatic-sampling-disabled', sampling='automatic')
        if root is None:
            return dict(saved=False, missingReason='memory-root-unavailable', sampling='automatic')
        if not current():
            return dict(saved=False, missingReason='capture-not-current', sampling='automatic')
        from failure_sampling import automatic_storage_transaction
        try:
            with automatic_storage_transaction(root, create=True, namespace='automatic-v2') as status:
                if status != 'ready':
                    return dict(saved=False, missingReason=status, sampling='automatic')
                return self._record_automatic_locked(root=root, reason=reason, frame=frame,
                    diagnostic=diagnostic, current=current, crop_bounds=crop_bounds,
                    crop_provenance=crop_provenance)
        except OSError as error:
            return dict(saved=False, missingReason='sample-write-error', error=type(error).__name__, sampling='automatic')

    def _record_automatic_locked(self, *, root, reason, frame, diagnostic, current,
                                 crop_bounds=None, crop_provenance=None):
        """Reuse a guarded captured frame only; unproven map regions stay metadata-only.

        The caller owns binding/foreground/generation validation via current().
        This method never obtains frames, inputs, or network access.
        """
        from datetime import datetime, timezone
        import json
        from pathlib import Path
        import uuid
        import re
        import cv2
        import numpy as np
        result = dict(saved=False, missingReason='', sampling='automatic',
                      limits=dict(images=6, imageBytes=12*1024*1024, retentionDays=7))
        if not self.automatic_enabled:
            return dict(result, missingReason='automatic-sampling-disabled')
        if reason not in ('structure-current-geometry-failed', 'initial-structure-current-geometry-failed',
                          'current-geometry-failed', 'map-open-preparation-deadline',
                          'identity-not-uniquely-verified', 'similar-group-pending',
                          'candidate-awaiting-confirmation', 'current-floor-unavailable',
                          'map-open-recovery-exhausted'):
            return dict(result, missingReason='failure-reason-not-eligible')
        if not current():
            return dict(result, missingReason='capture-not-current')
        if root is None:
            return dict(result, missingReason='memory-root-unavailable')
        if not self.lock.acquire(blocking=False):
            return dict(result, missingReason='sample-writer-busy')
        key = ('automatic', diagnostic.get('mode'), reason, crop_provenance == 'detector-control-band')
        admitted = False
        committed_image = False
        failed = False
        metadata_key = None
        try:
            if not self.policy.eligible(key, time.monotonic()):
                return dict(result, missingReason=self.policy.last_status)
            admitted = True
            pixels = None
            missing = 'map-crop-unproven'
            # The production path derives bounds from the shared current-frame
            # map extractor, never a fixed ROI, source pose, or remembered map.
            if (crop_bounds is None and frame is not None
                    and diagnostic.get('evidenceKind') == 'live-client-capture'):
                from map_observation import structure_current_map_evidence
                evidence = structure_current_map_evidence(frame)
                if evidence is not None and evidence.get('visible') is not None:
                    component = evidence['visible'][2]
                    crop_bounds = tuple(int(component[k]) for k in ('x', 'y', 'width', 'height'))
                    crop_provenance = 'color-component-unvalidated'
                    diagnostic = dict(diagnostic, cropExtractor='structure_current_map_evidence',
                                      observationBounds=list(evidence['observationBounds']),
                                      cropPaddingRemoved=8)
            if frame is None:
                unavailable = diagnostic.get('frameUnavailableReason')
                missing = unavailable if isinstance(unavailable, str) and unavailable in (
                    'capture-attempt-unavailable', 'capture-attempt-mismatch', 'capture-scope-changed',
                    'capture-not-current', 'capture-frame-invalid', 'detector-control-band-unavailable',
                    'legal-captured-frame-unavailable') else 'legal-captured-frame-unavailable'
            elif diagnostic.get('evidenceKind') != 'live-client-capture':
                missing = 'not-live-bound-game-capture'
            elif (crop_provenance in ('color-component-unvalidated', 'detector-control-band') and isinstance(frame, np.ndarray)
                  and frame.ndim == 3 and frame.nbytes <= 64*1024*1024
                  and isinstance(crop_bounds, (tuple, list)) and len(crop_bounds) == 4
                  and all(type(v) is int for v in crop_bounds)):
                x, y, width, height = crop_bounds
                if x >= 0 and y >= 0 and width > 0 and height > 0 and x+width <= frame.shape[1] and y+height <= frame.shape[0]:
                    ok, encoded = cv2.imencode('.png', frame[y:y+height, x:x+width])
                    if not ok:
                        missing = 'sample-encode-failed'
                        failed = True
                    elif encoded.nbytes > 12*1024*1024:
                        missing = 'image-byte-limit'
                    else:
                        pixels = encoded.tobytes()
                        missing = ''
                else:
                    missing = 'sample-crop-invalid'
            elif crop_bounds is not None:
                missing = 'sample-crop-invalid'
            if pixels is None:
                metadata_key = (key, missing)
                if not self._automatic_metadata_policy.eligible(metadata_key, time.monotonic()):
                    # Preserve the actual missing-image reason even when its
                    # journal row is suppressed. A usable image bypasses this.
                    return dict(result, missingReason=missing)
            # Inventory validates retained PNG bytes and can hash up to 12 MiB.
            # Do it only for admitted evidence, never for a metadata cooldown.
            # The caller still holds the exclusive storage transaction here.
            base = Path(root).absolute()
            directory = base / 'failures' / 'automatic-v2'
            from diagnostic_storage import _safe
            try: _safe(directory)
            except OSError:
                failed = True
                return dict(result, missingReason='automatic-storage-redirected')
            directory.mkdir(parents=True, exist_ok=True)
            from failure_sampling import (_cleanup_automatic_failure_samples_locked,
                automatic_storage_inventory, replace_automatic_journal,
                ensure_automatic_capacity, create_automatic_image)
            cleanup = _cleanup_automatic_failure_samples_locked(base, namespace='automatic-v2')
            if not cleanup['complete']:
                failed = True
                return dict(result, missingReason=cleanup['status'], cleanup=cleanup)
            inventory = automatic_storage_inventory(base, namespace='automatic-v2')
            if inventory['status'] != 'ready':
                failed = True
                return dict(result, missingReason=inventory['status'])
            images = sorted(inventory['images'], key=lambda p: p.stat().st_mtime)
            safe_diagnostic = {k: diagnostic[k] for k in (
                'eventPath', 'sessionId', 'pid', 'buildFingerprint', 'sourceCommit', 'evidenceKind', 'mode',
                'frameCapturedAtUtc', 'mapId', 'floor', 'cycleId', 'frameId',
                'captureGeneration', 'sourceFrameGeneration', 'cropExtractor',
                'observationBounds', 'cropPaddingRemoved', 'sampleRole',
                'season', 'contextKey', 'attemptId', 'openingId', 'inputSequence',
                'requestGeneration', 'epoch', 'roundId', 'frameCapturedMonotonicAt') if k in diagnostic}
            row = dict(event='automatic-failure-sample', reason=reason,
                       recordedAt=datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                       capturedAt=diagnostic.get('frameCapturedAtUtc'), diagnostic=bounded_evidence(safe_diagnostic)[0],
                       samplePath=None, missingReason=missing, sampling='automatic')
            if pixels is not None and current() and self.automatic_enabled:
                capacity = ensure_automatic_capacity(base, len(pixels), namespace='automatic-v2')
                if not capacity['complete']:
                    failed = True
                    return dict(result, missingReason=capacity['status'], cleanup=capacity)
                latest = automatic_storage_inventory(base, namespace='automatic-v2')
                if latest['status'] != 'ready':
                    failed = True
                    return dict(result, missingReason=latest['status'])
                images = sorted(latest['images'], key=lambda p: p.stat().st_mtime)
                inventory = latest  # Rotation may retire rows; never resurrect its pre-rotation journal.
                path = directory / ('auto-' + uuid.uuid4().hex + '.png')
                row.update(samplePath=path.relative_to(base).as_posix(),
                           sampleSha256=hashlib.sha256(pixels).hexdigest(),
                           sampleBytes=len(pixels),
                           cropBounds=list(crop_bounds), cropProvenance=crop_provenance)
                # Durable prepare/commit: an interrupted image remains pending.
                # Recovery never adopts or deletes an uncommitted PNG.
                intent = dict(row, event='automatic-failure-image-intent',
                              imageTransactionSchema='automatic-image-intent-v1')
                intent_line = json.dumps(intent, ensure_ascii=False).encode('utf-8')
                prepared = [line for line, _ in inventory['rows']]
                retained = {p.relative_to(base).as_posix() for p in images if p.exists()}
                while len(prepared) >= 24 or sum(map(len, prepared))+len(intent_line)+len(prepared)+1 > 256*1024:
                    removable = next((i for i, line in enumerate(prepared)
                        if json.loads(line).get('samplePath') not in retained), None)
                    if removable is None:
                        failed = True
                        return dict(result, missingReason='automatic-image-intent-budget')
                    prepared.pop(removable)
                replace_automatic_journal(directory, b'\n'.join(prepared+[intent_line])+b'\n', namespace='automatic-v2')
                row.update(create_automatic_image(base, row['samplePath'], pixels, namespace='automatic-v2'))
                result['saved'] = True
            elif not missing:
                row['missingReason'] = 'capture-not-current'
            if not current() or not self.automatic_enabled:
                # Keep the uncommitted intent and image. A pathname deletion
                # could remove an externally substituted file; recovery cannot
                # assume this frame passed the final current/consent check.
                return dict(result, saved=False, missingReason='capture-not-current',
                            recoveryRequired=bool(result['saved']))
            journal = directory / 'automatic-failures.jsonl'
            # Rewrite a bounded recent tail; metadata contains no raw frame.
            rows = list(prepared) if result['saved'] else [line for line, _ in inventory['rows']]
            rows.append(json.dumps(row, ensure_ascii=False).encode('utf-8'))
            # Keep ownership evidence while its image remains retained, even
            # when subsequent metadata-only failures fill the recent tail.
            retained = {p.relative_to(base).as_posix() for p in images if p.exists()}
            pinned = [line for line, old in inventory['rows'] if old.get('samplePath') in retained]
            if result['saved']:
                pinned.append(rows[-1])
            unpinned = [line for line in rows if line not in pinned]
            rows = pinned + unpinned[-(24-len(pinned)):]
            while len(rows) > 1 and sum(map(len, rows)) + len(rows) > 256*1024:
                rows.pop(0)
            replace_automatic_journal(directory, b'\n'.join(rows) + b'\n', namespace='automatic-v2')
            if result['saved']:
                committed_image = True
                self.automatic_saved_count += 1
            elif metadata_key is not None:
                self._automatic_metadata_policy.completed(metadata_key, time.monotonic(), True)
            result['missingReason'] = row['missingReason']
            return result
        except Exception as error:
            failed = True
            self.last_error = type(error).__name__
            return dict(result, saved=False, missingReason='sample-write-error', error=self.last_error)
        finally:
            if admitted and (committed_image or failed):
                self.policy.completed(key, time.monotonic(), committed_image)
            self.lock.release()

    def configure(self, *, event_path, build_fingerprint, source_commit):
        from diagnostic_identity import process_scope
        self.metadata = dict(**process_scope(), eventPath=str(event_path), buildFingerprint=str(build_fingerprint),
                             sourceCommit=str(source_commit))

    def can_record(self, frame, key):
        """Avoid preparing resources/diagnostics for a rejected admission."""
        commit = self.metadata.get("sourceCommit", "")
        if (not self.enabled or self.saved_count >= 12 or frame.nbytes > 64 * 1024 * 1024
                or not self.metadata.get("eventPath")
                or self.metadata.get("buildFingerprint") in (None, "", "UNKNOWN")
                or len(commit) != 40 or any(c not in "0123456789abcdefABCDEF" for c in commit)
                or not self.lock.acquire(blocking=False)):
            return False
        try:
            return self.policy.eligible(key, time.monotonic())
        finally:
            self.lock.release()

    def record(self, store, *, map_id, floor, reason, fit, frame, client_bounds,
               cycle_id, diagnostic, current):
        metadata = dict(self.metadata)
        commit = metadata.get("sourceCommit", "")
        if (not self.enabled or self.saved_count >= 12 or store is None
                or not metadata.get("eventPath") or metadata.get("buildFingerprint") in (None, "", "UNKNOWN")
                or len(commit) != 40 or any(c not in "0123456789abcdefABCDEF" for c in commit)
                or frame.nbytes > 64 * 1024 * 1024 or not self.lock.acquire(blocking=False)):
            return False
        key = (diagnostic.get("mode"), map_id, diagnostic.get("profileId"), floor, reason)
        success = False
        admitted = False
        try:
            if not self.enabled or not current() or not self.policy.eligible(key, time.monotonic()):
                return False
            compact, truncated = bounded_evidence(dict(diagnostic, **metadata, fit=fit,
                framePixelsSha256=hashlib.sha256(frame.tobytes()).hexdigest()))
            compact["diagnosticsTruncated"] = truncated
            if not self.enabled or not current():
                return False
            admitted = True
            # No additional capture or mutable frame copy is taken here. The
            # synchronous owner retains the exact frame until the writer returns.
            store.record_alignment_failure(map_id, floor, reason, compact["fit"], frame,
                client_bounds, cycle_id=cycle_id, lossless=True, diagnostic=compact)
            success = True
            self.saved_count += 1
            self.last_error = ""
            return True
        except Exception as error:
            self.last_error = type(error).__name__
            return False  # Diagnostics must not alter the pose result.
        finally:
            if admitted:
                self.policy.completed(key, time.monotonic(), success)
            self.lock.release()
