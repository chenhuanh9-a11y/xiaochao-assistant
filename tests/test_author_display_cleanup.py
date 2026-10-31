"""Display-only cleanup safety, provenance, cache and role agreement."""
from pathlib import Path
import copy
import hashlib
from io import BytesIO
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import author_display_cleanup as cleanup
from optional_author_displays import OptionalAuthorDisplays


def fixture():
    image = np.zeros((100, 140, 4), np.uint8)
    image[4:96, 4:136] = (1, 1, 1, 255)
    image[10:90, 10:130] = (100, 110, 120, 255)
    image[40:47, 40:47, :3] = 1  # Enclosed dark symbol must stay dark.
    image[7:11, 65:70] = (0, 0, 255, 255)  # Exterior route / marker.
    image[70:80, 100:110] = (240, 240, 240, 255)
    image[74:77, 103:107] = (1, 1, 1, 255)  # Text on its light label.
    return image


def binding(data):
    return dict(profileId='nightingale', assetSha256=hashlib.sha256(data).hexdigest(),
        transformApplications=1, matrixChanged=False, identityAuthority=False,
        structureValidationAuthority=False,
        registration=dict(sourceR3ManifestSha256='a' * 64, sourceR3RegistrationId='fixture'),
        coordinateBridge=dict(authorGeometryErrorUnchanged=True,
            proof='fixture: exact R4 base dependency'))


def paletted_fixture():
    indices = np.zeros((100, 140), np.uint8)
    indices[4:96, 4:136] = 1
    indices[10:90, 10:130] = 4
    indices[40:47, 40:47] = 1  # Enclosed black detail.
    indices[7:11, 65:70] = 5  # Exterior coloured route.
    indices[45:55, 4:8] = 6  # Near-black exterior annotation, never background.
    image = Image.fromarray(indices)
    image.putpalette([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                      120, 110, 100, 255, 0, 0, 7, 7, 7])
    target = BytesIO()
    image.save(target, format='PNG', transparency=bytes([0, 254, 48, 110, 254, 255, 255]))
    return target.getvalue()


def zhan10_binding(data):
    asset = np.array([[1., 0, 10], [0, 1., 20], [0, 0, 1]])
    native = np.array([[1.2, .01, 17], [-.02, 1.1, 31], [0, 0, 1]])
    canonical = (native @ asset).tolist()
    return dict(profileId='zhan10-nightmare', assetSha256=hashlib.sha256(data).hexdigest(),
        transformApplications=1, alreadyBaked=False, identityAuthority=False,
        structureValidationAuthority=False, currentFrameGeometryFitRequired=True,
        crossFloorProjectionAllowed=False, matrixAssetToCanonical=canonical,
        matrixAssetToBase=copy.deepcopy(canonical),
        registration=dict(status='confirmed', method='Existing affine SIFT/RANSAC author '
                          'registration against exact current renderer image; synthetic test'),
        coordinateBridge=dict(baseToNativeIsIdentity=True, authorGeometryErrorUnchanged=True),
        sourceDisplay=dict(relativePath='synthetic/map.jpg', sha256='a' * 64,
            cropBoxSource=[10, 20, 150, 120], assetToSource=asset.tolist(),
            sourceToNative=native.tolist()))


class AuthorDisplayCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'map.png'
        self.image = fixture()
        self.data = cv2.imencode('.png', self.image)[1].tobytes()
        self.path.write_bytes(self.data)
        self.declaration = binding(self.data)

    def test_exterior_alpha_only_preserves_dark_details_and_coloured_guard(self):
        original = self.image.copy()
        result, report = cleanup.derive_r4_display_alpha(self.image, 'nightingale')
        self.assertEqual(report['status'], 'cleaned')
        self.assertGreater(report['removedPixels'], 0)
        np.testing.assert_array_equal(self.image, original)
        np.testing.assert_array_equal(result[:, :, :3], original[:, :, :3])
        self.assertEqual(result.shape, original.shape)
        self.assertEqual(result[5, 5, 3], 0)
        np.testing.assert_array_equal(result[40:47, 40:47], original[40:47, 40:47])
        np.testing.assert_array_equal(result[70:80, 100:110], original[70:80, 100:110])
        np.testing.assert_array_equal(result[5:13, 63:72], original[5:13, 63:72])
        again, _ = cleanup.derive_r4_display_alpha(result, 'nightingale')
        np.testing.assert_array_equal(again, result)

    def test_unknown_or_opaque_source_is_unchanged(self):
        for profile in ('wang-qingxin', 'lianghapi-single', 'aichicu-difficult', 'zhan10-nightmare', 'unknown'):
            result, report = cleanup.derive_r4_display_alpha(self.image, profile)
            self.assertEqual(report['status'], 'unsupported-source')
            np.testing.assert_array_equal(result, self.image)
        opaque = self.image.copy()
        opaque[:, :, 3] = 255
        result, report = cleanup.derive_r4_display_alpha(opaque, 'nightingale')
        self.assertEqual(report['status'], 'missing-transparent-boundary')
        np.testing.assert_array_equal(result, opaque)

    def test_ambiguous_or_dominant_background_is_not_erased(self):
        textured = self.image.copy()
        textured[4:96:2, 4:136, :3] = (36, 28, 22)
        result, report = cleanup.derive_r4_display_alpha(textured, 'nightingale')
        self.assertEqual(report['status'], 'ambiguous-background')
        np.testing.assert_array_equal(result, textured)
        sparse = np.zeros_like(self.image)
        sparse[4:96, 4:136] = (1, 1, 1, 255)
        sparse[40:60, 50:90] = (100, 100, 100, 255)
        result, report = cleanup.derive_r4_display_alpha(sparse, 'nightingale')
        self.assertEqual(report['status'], 'ambiguous-coverage')
        np.testing.assert_array_equal(result, sparse)

    def test_family_requires_provenance_and_keeps_unverified_bytes(self):
        self.assertIsNotNone(cleanup.r4_source_family(self.declaration))
        for key, value in [('profileId', 'zhan10-nightmare'), ('transformApplications', 2),
                           ('registration', {}), ('coordinateBridge', {})]:
            declaration = dict(self.declaration, **{key: value})
            self.assertIsNone(cleanup.r4_source_family(declaration))
        cache = cleanup.AuthorDisplayCleanupCache()
        declaration = dict(self.declaration, assetSha256='b' * 64)
        np.testing.assert_array_equal(cache.pixels(self.path, declaration), self.image)
        self.assertEqual(cache.report(self.path)['status'], 'unverified-asset')

    def test_hash_cache_reuses_derivation_and_returns_independent_arrays(self):
        cache = cleanup.AuthorDisplayCleanupCache()
        duplicate = self.path.with_name('same.png')
        duplicate.write_bytes(self.data)
        with patch.object(cleanup, 'derive_r4_display_alpha', wraps=cleanup.derive_r4_display_alpha) as derive:
            first = cache.pixels(self.path, self.declaration)
            expected = first.copy()
            first[:, :, :] = 0
            second = cache.pixels(self.path, self.declaration)
            third = cache.pixels(duplicate, self.declaration)
            self.assertEqual(derive.call_count, 1)
        np.testing.assert_array_equal(second, expected)
        np.testing.assert_array_equal(third, expected)
        self.assertEqual(cache.report(self.path)['assetSha256'], self.declaration['assetSha256'])
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_replacement_cannot_reuse_old_hash_or_derivation(self):
        cache = cleanup.AuthorDisplayCleanupCache()
        cache.pixels(self.path, self.declaration)
        replacement = self.image.copy()
        replacement[20:30, 20:30] = (222, 111, 99, 255)
        self.path.write_bytes(cv2.imencode('.png', replacement)[1].tobytes())
        result = cache.pixels(self.path, self.declaration)
        np.testing.assert_array_equal(result, replacement)
        self.assertEqual(cache.report(self.path)['status'], 'unverified-asset')

    def test_cache_memory_is_bounded(self):
        cache = cleanup.AuthorDisplayCleanupCache(max_bytes=self.image.nbytes)
        cache.pixels(self.path, self.declaration)
        second = self.path.with_name('different.png')
        different = self.image.copy()
        different[50, 50, 0] += 1
        data = cv2.imencode('.png', different)[1].tobytes()
        second.write_bytes(data)
        cache.pixels(second, binding(data))
        self.assertLessEqual(cache._bytes, self.image.nbytes)
        self.assertEqual(len(cache._images), 1)
        small = cleanup.AuthorDisplayCleanupCache(max_bytes=1)
        small.pixels(self.path, self.declaration)
        self.assertFalse(small._images)

    def test_all_optional_roles_share_source_without_touching_matrix(self):
        matrix = np.array([[1.2, .01, 17], [-.02, 1.1, 31], [0, 0, 1]])
        row = dict(path=self.path.resolve(), declaration=self.declaration, usable=True, matrix=matrix)
        displays = OptionalAuthorDisplays.__new__(OptionalAuthorDisplays)
        displays.bindings = {('nightingale', 'map', '1'): row}
        displays._display_cleanup = cleanup.AuthorDisplayCleanupCache()
        before = copy.deepcopy(row)
        results = [displays.pixels(self.path, role) for role in
                   ('minimap', 'aligned-overlay', 'full-reference')]
        for image in results[1:]:
            np.testing.assert_array_equal(image, results[0])
        np.testing.assert_array_equal(row['matrix'], before['matrix'])
        self.assertEqual(row['declaration'], before['declaration'])
        self.assertEqual(displays.cleanup_report(self.path)['status'], 'cleaned')
        row['usable'] = False
        with self.assertRaisesRegex(ValueError, 'registration is unavailable'):
            displays.pixels(self.path, 'aligned-overlay')


    def test_zhan10_palette_exterior_keeps_near_black_and_enclosed_content(self):
        data = paletted_fixture()
        self.path.write_bytes(data)
        original = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        cache = cleanup.AuthorDisplayCleanupCache()
        result = cache.pixels(self.path, zhan10_binding(data))
        self.assertEqual(cache.report(self.path)['status'], 'cleaned')
        self.assertEqual(result.shape, original.shape)
        np.testing.assert_array_equal(result[:, :, :3], original[:, :, :3])
        self.assertEqual(result[5, 5, 3], 0)
        np.testing.assert_array_equal(result[40:47, 40:47], original[40:47, 40:47])
        np.testing.assert_array_equal(result[43:57, 2:10], original[43:57, 2:10])
        np.testing.assert_array_equal(result[5:13, 63:72], original[5:13, 63:72])
        again, _ = cleanup._derive_black_exterior_alpha(result)
        np.testing.assert_array_equal(result, again)
        self.assertEqual(self.path.read_bytes(), data)

    def test_zhan10_requires_complete_coordinate_provenance_and_matching_bytes(self):
        data = paletted_fixture()
        declaration = zhan10_binding(data)
        self.assertIsNotNone(cleanup.zhan10_source_family(declaration))
        self.assertIsNone(cleanup.r4_source_family(declaration))
        for key, value in [('sourceDisplay', {}), ('alreadyBaked', True),
                           ('transformApplications', 2), ('registration', {}),
                           ('identityAuthority', True), ('coordinateBridge', {})]:
            invalid = dict(declaration, **{key: value})
            self.assertIsNone(cleanup.zhan10_source_family(invalid), key)
        invalid = copy.deepcopy(declaration)
        invalid['sourceDisplay']['assetToSource'][0][2] += .5
        self.assertIsNone(cleanup.zhan10_source_family(invalid))
        invalid = copy.deepcopy(declaration)
        invalid['matrixAssetToCanonical'][0][2] += 1
        invalid['matrixAssetToBase'] = copy.deepcopy(invalid['matrixAssetToCanonical'])
        self.assertIsNone(cleanup.zhan10_source_family(invalid))
        self.path.write_bytes(data)
        cache = cleanup.AuthorDisplayCleanupCache()
        result = cache.pixels(self.path, dict(declaration, assetSha256='b' * 64))
        np.testing.assert_array_equal(result,
            cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED))
        self.assertEqual(cache.report(self.path)['status'], 'unverified-asset')

    def test_zhan10_rgba_or_changed_crop_cannot_reuse_verified_palette_result(self):
        data = paletted_fixture()
        self.path.write_bytes(data)
        declaration = zhan10_binding(data)
        original = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        cache = cleanup.AuthorDisplayCleanupCache()
        cache.pixels(self.path, declaration)
        changed = copy.deepcopy(declaration)
        asset = np.array([[2., 0, 10.5], [0, 2., 20.5], [0, 0, 1]])
        changed['sourceDisplay']['assetToSource'] = asset.tolist()
        canonical = np.asarray(changed['sourceDisplay']['sourceToNative']) @ asset
        changed['matrixAssetToCanonical'] = canonical.tolist()
        changed['matrixAssetToBase'] = canonical.tolist()
        self.assertIsNotNone(cleanup.zhan10_source_family(changed))
        np.testing.assert_array_equal(cache.pixels(self.path, changed), original)
        self.assertEqual(cache.report(self.path)['status'], 'unverified-palette-crop')
        rgba_data = cv2.imencode('.png', original)[1].tobytes()
        self.path.write_bytes(rgba_data)
        np.testing.assert_array_equal(cache.pixels(self.path, zhan10_binding(rgba_data)), original)
        self.assertEqual(cache.report(self.path)['status'], 'unverified-palette-crop')

    def test_malformed_source_declarations_and_unrecognised_palette_keep_pixels(self):
        data = paletted_fixture()
        self.path.write_bytes(data)
        original = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        declaration = zhan10_binding(data)
        invalids = [None, [], 'unknown', dict(declaration, profileId=[]),
                    dict(declaration, assetSha256=[])]
        for key in ('sourceDisplay', 'registration', 'coordinateBridge'):
            invalids.extend(dict(declaration, **{key: value}) for value in (['bad'], 'bad', 1))
        malformed_matrix = copy.deepcopy(declaration)
        malformed_matrix['sourceDisplay']['assetToSource'] = [[float('nan')]]
        invalids.append(malformed_matrix)
        for invalid in invalids:
            with self.subTest(declaration=invalid):
                cache = cleanup.AuthorDisplayCleanupCache()
                np.testing.assert_array_equal(cache.pixels(self.path, invalid), original)
        with Image.open(BytesIO(data)) as image:
            buffer = BytesIO()
            image.save(buffer, format='PNG', transparency=bytes([0, 255, 48, 110, 254, 255, 255]))
        unknown = buffer.getvalue()
        self.path.write_bytes(unknown)
        cache = cleanup.AuthorDisplayCleanupCache()
        np.testing.assert_array_equal(cache.pixels(self.path, zhan10_binding(unknown)),
            cv2.imdecode(np.frombuffer(unknown, np.uint8), cv2.IMREAD_UNCHANGED))
        self.assertEqual(cache.report(self.path)['status'], 'unverified-palette-crop')

    def test_zhan10_three_roles_use_one_derivation_and_keep_binding_matrix(self):
        data = paletted_fixture()
        self.path.write_bytes(data)
        declaration = zhan10_binding(data)
        matrix = np.asarray(declaration['matrixAssetToCanonical'])
        row = dict(path=self.path.resolve(), declaration=declaration, usable=True, matrix=matrix)
        original = copy.deepcopy(row)
        displays = OptionalAuthorDisplays.__new__(OptionalAuthorDisplays)
        displays.bindings = {('zhan10-nightmare', 'map', '1'): row}
        displays._display_cleanup = cleanup.AuthorDisplayCleanupCache()
        with patch.object(cleanup, '_derive_black_exterior_alpha',
                          wraps=cleanup._derive_black_exterior_alpha) as derive:
            results = [displays.pixels(self.path, role) for role in
                       ('minimap', 'aligned-overlay', 'full-reference')]
            self.assertEqual(derive.call_count, 1)
        for image in results[1:]:
            np.testing.assert_array_equal(image, results[0])
        np.testing.assert_array_equal(row['matrix'], original['matrix'])
        self.assertEqual(row['declaration'], original['declaration'])


if __name__ == '__main__':
    unittest.main()
