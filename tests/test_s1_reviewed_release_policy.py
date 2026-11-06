"""Check the actual formal release policies outside disabled synthetic fixtures."""
from pathlib import Path
import json
import unittest

import managed_base_resources as managed
import s1_candidate_purpose as purpose
import s1_author_package as author
from s1_reviewed_base_releases import REVIEWED_S1_BASE_RELEASES
from s0_legacy_base_releases import REVIEWED_LEGACY_S0_BASE_RELEASES
from star_map_import import SUPPORTED_DISPLAY_VERSIONS

ROOT = Path(__file__).resolve().parents[1]


class ReviewedReleasePolicyTests(unittest.TestCase):
    def test_actual_release_policies_agree_on_complete_exact_native_inventory(self):
        lock = json.loads((ROOT / 'tools/s1-production-release-lock.json').read_text(encoding='utf8'))
        self.assertIs(lock['enabled'], True)
        self.assertIs(purpose._PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED, True)
        self.assertEqual(managed._REVIEWED_S1_BASE_RELEASES, REVIEWED_S1_BASE_RELEASES)
        version, rows = REVIEWED_S1_BASE_RELEASES[0]
        self.assertEqual(lock['release']['resourceVersion'], version)
        self.assertEqual(len(rows), 564)
        self.assertEqual(lock['release']['files'], {path.removeprefix('_internal/'): dict(bytes=size, sha256=sha)
                                                   for path, size, sha in rows})
        result = managed.manifest_for_rows([dict(path=p, bytes=n, sha256=h) for p, n, h in rows], version)
        self.assertEqual(len(result['files']), 564)
        self.assertEqual(len(lock['release']['s0BaseFiles']), 325)

    def test_actual_version_author_registration_and_legacy_adoption_are_source_pinned(self):
        version = json.loads((ROOT / 'version.json').read_text(encoding='utf8'))
        self.assertEqual((version['displayVersion'], version['numericVersion']), ('1.1.0', '1.0.0.22'))
        self.assertIn(version['displayVersion'], SUPPORTED_DISPLAY_VERSIONS)
        self.assertIn('8e5a11d41c2bdd5c4fdc55a19089e1005e8fd47434bde75affa40f8928ac4b9d', author.APPROVED_BINDINGS)
        self.assertEqual(len(REVIEWED_LEGACY_S0_BASE_RELEASES[0][2]), 307)
        self.assertFalse(purpose.private_native_candidate_purpose_is_valid(purpose._NATIVE_S1_PRODUCTION_CANDIDATE))


if __name__ == '__main__':
    unittest.main()
