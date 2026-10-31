"""Conservative display alpha for the confirmed R4 author source family.

This is not a general dark-pixel remover. Only R4 Nightingale (爱吃醋 · 噩梦)
bindings with matching asset bytes are eligible. Textured or unrecognised
backgrounds retain their original alpha. RGB, canvas and all geometry stay put.
"""
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import re
from threading import RLock

import cv2
import numpy as np


RECIPE_VERSION = 'r4-exterior-background-v1'
_PROFILES = frozenset({'nightingale'})
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def r4_source_family(declaration):
    """Recognise the R4 conversion contract, not a list of sample image hashes."""
    profile = declaration.get('profileId')
    registration = declaration.get('registration') or {}
    bridge = declaration.get('coordinateBridge') or {}
    source_hash = registration.get('sourceR3ManifestSha256', '')
    if (profile not in _PROFILES or not isinstance(source_hash, str)
            or not _SHA256.fullmatch(source_hash)
            or not registration.get('sourceR3RegistrationId')
            or declaration.get('transformApplications') != 1
            or declaration.get('matrixChanged') is not False
            or declaration.get('identityAuthority') is not False
            or declaration.get('structureValidationAuthority') is not False
            or bridge.get('authorGeometryErrorUnchanged') is not True
            or 'exact R4 base dependency' not in str(bridge.get('proof', ''))):
        return None
    return profile, source_hash


def derive_r4_display_alpha(bgra, profile):
    """Return display pixels plus a decision; never crop, resample or edit RGB.

    The boundary must contain a dominant, near-uniform background in the known
    source's black backing. We erase only its four-connected exterior components, with
    two native pixels of protection around every different-colour pixel. Dark
    enclosed details, coloured annotations and their antialias fringe survive.
    A nonuniform boundary is a no-op. Wang's textured/watermarked background
    and other author sources are intentionally outside the accepted scope.
    """
    if (bgra.dtype != np.uint8 or bgra.ndim != 3 or bgra.shape[2] != 4):
        raise ValueError('author display cleanup requires uint8 BGRA pixels')
    result = bgra.copy()
    report = dict(recipe=RECIPE_VERSION, status='unsupported-source', removedPixels=0)
    if profile not in _PROFILES:
        return result, report
    rgb = bgra[:, :, 2::-1]
    visible = bgra[:, :, 3] > 0
    if not visible.any() or visible.all():
        report['status'] = 'missing-transparent-boundary'
        return result, report
    boundary = cv2.dilate((~visible).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    boundary[[0, -1], :] = True
    boundary[:, [0, -1]] = True
    samples = rgb[boundary & visible]
    if len(samples) < 64:
        report['status'] = 'insufficient-boundary'
        return result, report
    # Anchor the palette in unchanged RGB, not the evolving alpha edge. This
    # makes conversion followed by runtime loading idempotent. R4 Nightingale
    # uses a black backing. Do not extend this palette to other authors.
    background = np.zeros(3, np.int16)
    matching_boundary = np.max(np.abs(samples.astype(np.int16) - background), axis=1) <= 8
    confidence = float(matching_boundary.mean())
    report.update(backgroundRGB=background.tolist(), matchingBoundaryFraction=confidence)
    if confidence < .75:
        report['status'] = 'ambiguous-background'
        return result, report
    candidate = np.max(np.abs(rgb.astype(np.int16) - background), axis=2) <= 8
    foreground = visible & ~candidate
    protected = cv2.dilate(foreground.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    allowed = candidate & ~protected
    _, labels = cv2.connectedComponents((allowed | ~visible).astype(np.uint8), connectivity=4)
    seeds = np.unique(np.concatenate((labels[~visible], labels[0], labels[-1],
                                     labels[:, 0], labels[:, -1])))
    seeds = seeds[seeds != 0]  # Label zero is blocked foreground, never exterior.
    remove = allowed & visible & np.isin(labels, seeds)
    removed = int(remove.sum())
    # A clipped map should not mostly consist of the proposed background.
    if removed > int(visible.sum()) * .35:
        report['status'] = 'ambiguous-coverage'
        return result, report
    result[:, :, 3][remove] = 0
    report.update(status='cleaned' if removed else 'unchanged', removedPixels=removed)
    return result, report


class AuthorDisplayCleanupCache:
    """Bounded cache of derived pixels; segmentation runs only on a cold asset.

    A stat signature detects replacement, then bytes are hashed before checking
    the binding and decoding. Cache identity includes real SHA256 and provenance.
    Returned arrays are copies so a consumer cannot corrupt another view's source.
    """
    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._bytes = 0
        self._images = OrderedDict()
        self._paths = OrderedDict()
        self._lock = RLock()

    def pixels(self, path, declaration):
        path = Path(path).resolve()
        family = r4_source_family(declaration)
        expected = declaration.get('assetSha256', '')
        stamp = path.stat()
        signature = (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns, stamp.st_ino)
        lookup = (str(path), signature, family, expected)
        with self._lock:
            key = self._paths.get(lookup)
            if key in self._images:
                self._paths.move_to_end(lookup)
                self._images.move_to_end(key)
                return self._images[key][0].copy()
            with path.open('rb') as source:
                before = os.fstat(source.fileno())
                data = source.read()
                after = os.fstat(source.fileno())
            # Windows path.stat() and os.fstat() can expose different ctime
            # semantics. Compare file identity/mtime across those APIs; compare
            # ctime only between observations made by the same API.
            current = path.stat()
            identity = lambda value: (value.st_size, value.st_mtime_ns, value.st_ino)
            if (identity(before) != identity(stamp) or identity(after) != identity(before)
                    or before.st_ctime_ns != after.st_ctime_ns
                    or (current.st_size, current.st_mtime_ns, current.st_ctime_ns, current.st_ino) != signature):
                raise ValueError('author display changed while reading')
            digest = hashlib.sha256(data).hexdigest()
            key = (digest, RECIPE_VERSION, family, expected)
            if key not in self._images:
                image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
                if (image is None or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 4):
                    raise ValueError('optional author requires original BGRA display pixels')
                if family is not None and digest == expected:
                    image, report = derive_r4_display_alpha(image, family[0])
                else:
                    report = dict(recipe=RECIPE_VERSION, removedPixels=0,
                        status='unverified-asset' if family is not None else 'unsupported-source')
                report.update(assetSha256=digest, sourceFamily=family)
                image.setflags(write=False)
                while self._images and self._bytes + image.nbytes > self.max_bytes:
                    _, (old, _) = self._images.popitem(last=False)
                    self._bytes -= old.nbytes
                # Oversized images are returned but never retained.
                if image.nbytes > self.max_bytes:
                    return image.copy()
                self._images[key] = image, report
                self._bytes += image.nbytes
            self._images.move_to_end(key)
            self._paths[lookup] = key
            while len(self._paths) > 128:
                self._paths.popitem(last=False)
            return self._images[key][0].copy()

    def report(self, path):
        """Diagnostic provenance for the latest loaded path, without image data."""
        path = str(Path(path).resolve())
        with self._lock:
            for lookup, key in reversed(self._paths.items()):
                if lookup[0] == path and key in self._images:
                    return dict(self._images[key][1])
        return None
