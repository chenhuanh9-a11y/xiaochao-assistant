"""Naming contract tests for typed resource-package filenames and producers."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools')]

from resource_package_types import (  # noqa: E402
    BASE_SUFFIX,
    ROUTE_SUFFIX,
    PackageTypeError,
    infer_package_suffix,
    preferred_download_suffix,
    validate_package_filename,
)
import export_author_display_component as route_exporter  # noqa: E402
import export_native_base_package as base_exporter  # noqa: E402


BASE_COMPONENTS = [
    dict(componentId='base', type='recognition-base', contexts=[dict(season='s0', mode='nightmare')]),
    dict(componentId='controls', type='controls-profile', contexts=[dict(season='s0', mode='nightmare')]),
    dict(componentId='hints', type='recognition-hints', contexts=[dict(season='s0', mode='nightmare')]),
]
ROUTE_COMPONENTS = [
    dict(componentId='display-s0-nightmare', type='display'),
    dict(componentId='display-s1-difficult', type='display'),
]


class ResourcePackageTypeTests(unittest.TestCase):
    def assert_package_error(self, code, callback, *args, **kwargs):
        with self.assertRaises(PackageTypeError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_infers_base_and_route_from_component_types(self):
        self.assertEqual(infer_package_suffix(BASE_COMPONENTS), BASE_SUFFIX)
        self.assertEqual(infer_package_suffix(ROUTE_COMPONENTS), ROUTE_SUFFIX)

    def test_validates_special_suffixes_case_insensitively(self):
        self.assertEqual(validate_package_filename('map.XINGTU-BASE', BASE_COMPONENTS), BASE_SUFFIX)
        self.assertEqual(validate_package_filename('author.XINGTU-ROUTE', ROUTE_COMPONENTS), ROUTE_SUFFIX)

    def test_rejects_suffix_mismatch_and_unknown_extensions(self):
        self.assert_package_error('resource-package-extension-mismatch',
            validate_package_filename, 'author.xingtu-base', ROUTE_COMPONENTS)
        self.assert_package_error('resource-package-extension-mismatch',
            validate_package_filename, 'map.xingtu-route', BASE_COMPONENTS)
        self.assert_package_error('resource-package-extension-unsupported',
            validate_package_filename, 'map.7z', BASE_COMPONENTS)
        self.assert_package_error('resource-package-extension-unsupported',
            validate_package_filename, 'map.zip', BASE_COMPONENTS, allow_legacy_zip=False)

    def test_rejects_mixed_unknown_and_incomplete_kinds(self):
        mixed = BASE_COMPONENTS[:2] + ROUTE_COMPONENTS[:1]
        self.assert_package_error('resource-package-kind-unsupported', infer_package_suffix, mixed)
        self.assert_package_error('resource-package-kind-unsupported', infer_package_suffix,
            [dict(componentId='base', type='recognition-base')])
        self.assert_package_error('resource-package-kind-unsupported', infer_package_suffix,
            [dict(componentId='other', type='future-component')])
        self.assert_package_error('resource-package-kind-unsupported', infer_package_suffix, [])

    def test_legacy_zip_is_untyped_and_does_not_grant_package_role(self):
        mixed = BASE_COMPONENTS[:2] + ROUTE_COMPONENTS[:1]
        self.assertIsNone(validate_package_filename('legacy.ZIP', mixed))

    def test_download_suffix_uses_only_known_url_path_suffixes(self):
        self.assertEqual(preferred_download_suffix(
            'https://example.invalid/download/Map.XINGTU-BASE?token=abc#part'), BASE_SUFFIX)
        self.assertEqual(preferred_download_suffix(
            'https://example.invalid/files/route.XINGTU-ROUTE?download=.zip'), ROUTE_SUFFIX)
        self.assertEqual(preferred_download_suffix('https://example.invalid/package.zip?x=1'), '.zip')
        self.assertEqual(preferred_download_suffix('https://example.invalid/package.tar'), '.zip')

    def test_base_cli_rejects_zip_before_source_processing_or_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'must-not-exist.zip'
            argv = [
                '--source-root', str(Path(temporary) / 'missing-source'),
                '--output', str(output),
                '--resource-version', 'v1',
            ]
            error = io.StringIO()
            with redirect_stderr(error), self.assertRaises(SystemExit) as caught:
                base_exporter.main(argv)
            self.assertEqual(caught.exception.code, 2)
            self.assertIn(BASE_SUFFIX, error.getvalue())
            self.assertIn('.zip', error.getvalue())
            self.assertFalse(output.exists())

    def test_route_cli_rejects_zip_before_base_processing_or_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'must-not-exist.zip'
            argv = [
                '--base-resource-root', str(Path(temporary) / 'missing-base'),
                '--base-package-id', 'base',
                '--base-resource-version', 'v1',
                '--base-component-id', 'base',
                '--base-manifest-sha256', 'a' * 64,
                '--registration', str(Path(temporary) / 'missing-registration.json'),
                '--image-root', str(Path(temporary) / 'missing-images'),
                '--output', str(output),
                '--package-id', 'route',
                '--resource-version', 'v1',
            ]
            error = io.StringIO()
            with redirect_stderr(error), self.assertRaises(SystemExit) as caught:
                route_exporter.main(argv)
            self.assertEqual(caught.exception.code, 2)
            self.assertIn(ROUTE_SUFFIX, error.getvalue())
            self.assertIn('.zip', error.getvalue())
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
