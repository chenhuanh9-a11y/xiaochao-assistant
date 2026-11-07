"""Pure diagnosis tests; no player resources or mutation authority."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from unittest.mock import Mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app'),
               str(Path(__file__).resolve().parents[1] / 'experiments/qt_quick')]
from resource_compatibility import diagnose_compatibility, safe_dependency_path, compatibility_message
from client_resource_errors import safe_error_details, error_fields
import generic_author_package


class ResourceCompatibilityTests(unittest.TestCase):
    def test_new_retired_mode_import_is_rejected_but_receipt_stays_readable(self):
        profile = 'author-history'
        def row(path):
            return dict(path=path, bytes=1, sha256='a' * 64)
        payload = [row(generic_author_package.bindings_path(profile)), row(
            generic_author_package.image_path(profile, 's1', 'difficulty2', '101001', '-1'))]
        deps = [row('_internal/seasons/s1/difficulty2/native-map-manifest.json')]
        receipt = dict(schema=generic_author_package.SCHEMA, revision=1, packageId=profile,
                       season='s1', files=payload, requiresFiles=deps)
        rows, dependencies = generic_author_package.receipt_rows(receipt)
        archive = Mock()
        with self.assertRaisesRegex(ValueError, 'author-v2-retired-mode') as raised:
            generic_author_package.validate_archive(receipt, archive, Path('unused'), rows, dependencies)
        archive.read.assert_not_called()
        self.assertIn('本次未导入', compatibility_message(raised.exception.code))

    def test_retired_closure_never_resolves_dormant_base_or_hides_active_dependencies(self):
        profile = 'author-history'
        root = Path('candidate')
        rows = {mode: dict(path=generic_author_package.image_path(profile, 's1', mode, 'map', '-1'))
                for mode in ('difficulty2', 'difficult')}
        dependencies = {'normal': dict(path='_internal/seasons/s1/difficulty2/native-map-manifest.json'),
                        'active': dict(path='active')}
        def resolve(selected, season, mode, *_):
            self.assertEqual(mode, 'difficult')
            self.assertEqual(selected, root)
            return dict(dependencies=['active'])
        with patch('generic_author_base.resolve_layer', side_effect=resolve):
            generic_author_package.validate_receipt_closure(root, dict(packageId=profile, season='s1'),
                rows, dependencies)
            with self.assertRaisesRegex(ValueError, 'receipt-dependency-closure-mismatch'):
                generic_author_package.validate_receipt_closure(root, dict(packageId=profile, season='s1'),
                    rows, {'normal': dependencies['normal']})

    def test_production_never_reactivates_residual_bundled_author_directory(self):
        import client_paths
        with patch.object(client_paths, 'FROZEN', True), patch.object(client_paths, 'PRODUCTION', True):
            self.assertIsNone(client_paths.bundled_author_root())
        with patch.object(client_paths, 'FROZEN', True), patch.object(client_paths, 'PRODUCTION', False):
            self.assertEqual(client_paths.bundled_author_root(), client_paths.RESOURCE_ROOT / 'qt-author-base')

    def test_missing_and_incompatible_base_are_distinct(self):
        for missing, state in ((True, 'base-missing'), (False, 'base-incompatible'), (None, 'base-unavailable')):
            diagnosis = diagnose_compatibility('missing-or-incompatible-dependency', {'missing': missing})
            self.assertEqual(diagnosis['state'], state)
            self.assertIs(diagnosis['authorizesMutation'], False)

    def test_untrusted_text_cannot_grant_a_known_classification(self):
        self.assertEqual(diagnose_compatibility('future-error', {'reason': 'retired-mode'})['state'], 'unknown')
        for value in (None, [], {'code': 'retired-mode'}):
            self.assertEqual(diagnose_compatibility(value)['state'], 'unknown')

    def test_v2_field_specific_failure_preserves_producer_code(self):
        with self.assertRaises(ValueError) as raised:
            generic_author_package._reject('reference-binding-mismatch:coordinateSpace')
        fields = error_fields(raised.exception)
        self.assertEqual(fields['code'], 'author-v2-reference-binding-mismatch')
        self.assertEqual(diagnose_compatibility(fields['code'])['state'], 'display-binding-incompatible')
        self.assertIn('坐标绑定', compatibility_message(fields['code']))

    def test_s1_dependencies_retain_bounded_relative_identity(self):
        for mode in ('difficulty2', 'difficult', 'nightmare'):
            for suffix in ('native-map-manifest.json', 'references/entries/106001--1-shared.npz',
                           'assets/game-native/106001/-1.png'):
                path = f'_internal/seasons/s1/{mode}/{suffix}'
                self.assertTrue(safe_dependency_path(path))
                self.assertEqual(safe_error_details({'path': path, 'missing': True}), {'path': path, 'missing': True})

    def test_diagnostics_never_accept_user_or_unsafe_locations(self):
        for path in ('C:/Users/player/file', '_internal/seasons/s1/difficult/../../secret',
                     '_internal/seasons/s1/difficult/work/settings.json',
                     '_internal/seasons/s2/difficult/native-map-manifest.json',
                     '_internal/seasons/s1/unknown/native-map-manifest.json',
                     '_internal/seasons/s1/difficult/references//secret',
                     '_internal/seasons/s1/difficult/references/file\x00.json',
                     '_internal/references/\ud800', '_internal/references/' + 'x' * 241):
            self.assertFalse(safe_dependency_path(path), repr(path))
            self.assertEqual(safe_error_details({'path': path}), {})


if __name__ == '__main__':
    unittest.main()
