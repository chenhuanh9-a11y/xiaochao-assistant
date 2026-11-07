"""Published S0 closure policy with synthetic artifact evidence, not history."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import build_qt_client as build
import managed_base_resources as managed


class PublishedS0ClosureTests(unittest.TestCase):
    @staticmethod
    def row(value):
        data = value.encode()
        return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def setUp(self):
        self.resources = {'references/published-s0.json': self.row('published-s0'),
                          'assets/native/s0.png': self.row('published-png')}
        self.manifest = managed.manifest_for_rows(
            [dict(path='_internal/' + name, **row) for name, row in self.resources.items()], 'published-fixture')
        self.release = dict(s0BaseFiles={'_internal/' + name: row for name, row in self.resources.items()},
                            s0Predecessors=[self.evidence(self.manifest)])
        item = patch.object(build, 'source_support_assets', return_value={})
        item.start(); self.addCleanup(item.stop)

    @staticmethod
    def evidence(manifest, setup='a'*64, crlf=False):
        raw = json.dumps(manifest, indent=2, sort_keys=True)
        if crlf:
            raw = raw.replace('\n', '\r\n')
        return dict(sourceCommit='b'*40, setupSha256=setup,
                    manifestSha256=hashlib.sha256(raw.encode()).hexdigest(), manifestUtf8=raw)

    def check(self, resources=None, release=None, **kwargs):
        return build.production_s0_predecessor_closure(
            self.resources if resources is None else resources,
            self.release if release is None else release, **kwargs)

    def test_exact_complete_predecessor_and_current_inventory_accept(self):
        result = self.check()
        self.assertEqual(result['currentBaseFiles'], 2)
        self.assertEqual(result['publishedPredecessors'][0]['retainedBaseFiles'], 2)
        self.assertNotIn('publishedVerified', result)

    def test_omitted_path_rejected_even_if_source_current_inventory_also_omits_it(self):
        current = dict(self.resources); current.pop('references/published-s0.json')
        with self.assertRaisesRegex(ValueError, 'Current S0 candidate plane'):
            self.check(current)
        release = copy.deepcopy(self.release)
        release['s0BaseFiles'].pop('_internal/references/published-s0.json')
        with self.assertRaisesRegex(ValueError, 'Unreviewed S0 omission'):
            self.check(current, release)

    def test_modified_or_renamed_published_bytes_reject_without_override(self):
        for kind in ('bytes', 'rename'):
            current = copy.deepcopy(self.resources)
            if kind == 'bytes':
                current['references/published-s0.json'] = self.row('new-unreviewed')
            else:
                current['references/renamed.json'] = current.pop('references/published-s0.json')
            release = copy.deepcopy(self.release)
            release['s0BaseFiles'] = {'_internal/' + n: r for n, r in current.items()}
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Unreviewed S0 omission'):
                self.check(current, release)

    def test_added_current_base_requires_exact_review_but_preserves_old_bytes(self):
        current = dict(self.resources, **{'references/new-s0.json': self.row('new')})
        with self.assertRaisesRegex(ValueError, 'Current S0 candidate plane'):
            self.check(current)
        release = copy.deepcopy(self.release)
        release['s0BaseFiles']['_internal/references/new-s0.json'] = current['references/new-s0.json']
        self.assertEqual(self.check(current, release)['currentBaseFiles'], 3)

    def test_absent_empty_malformed_or_duplicate_published_evidence_is_not_optional(self):
        for predecessors in (None, [], {}, [dict()], self.release['s0Predecessors'] * 2):
            release = copy.deepcopy(self.release); release['s0Predecessors'] = predecessors
            with self.subTest(predecessors=predecessors), self.assertRaises(ValueError):
                self.check(release=release)
        for field in ('sourceCommit', 'setupSha256', 'manifestSha256', 'manifestUtf8'):
            release = copy.deepcopy(self.release); release['s0Predecessors'][0].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check(release=release)

    def test_manifest_raw_bytes_and_bundle_identity_must_both_match(self):
        for mutation in ('raw', 'bundle', 'schema', 'files'):
            release = copy.deepcopy(self.release)
            if mutation == 'raw':
                release['s0Predecessors'][0]['manifestUtf8'] += '\n'
            else:
                manifest = copy.deepcopy(self.manifest)
                if mutation == 'bundle': manifest['bundleId'] = '0'*64
                if mutation == 'schema': manifest['schema'] = 'cloud-composite'
                if mutation == 'files': manifest['files'] = []
                release['s0Predecessors'] = [self.evidence(manifest)]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.check(release=release)

    def test_original_crlf_manifest_is_bound_without_transport_normalization(self):
        release = copy.deepcopy(self.release)
        release['s0Predecessors'] = [self.evidence(self.manifest, crlf=True)]
        self.assertEqual(self.check(release=release)['currentBaseFiles'], 2)
        release['s0Predecessors'][0]['manifestUtf8'] = release['s0Predecessors'][0]['manifestUtf8'].replace('\r\n', '\n')
        with self.assertRaisesRegex(ValueError, 'manifest bytes'):
            self.check(release=release)

    def test_all_predecessors_must_fit_not_only_the_latest_or_first(self):
        older = managed.manifest_for_rows(self.manifest['files'] + [
            dict(path='_internal/references/old-only.json', **self.row('old'))], 'older-fixture')
        release = copy.deepcopy(self.release)
        release['s0Predecessors'].append(self.evidence(older, setup='c'*64))
        with self.assertRaisesRegex(ValueError, 'old-only.json'):
            self.check(release=release)

    def test_registered_legacy_optional_author_is_relinquished_not_resurrected(self):
        optional = '_internal/references/author-packs/nightingale/catalog.json'
        predecessor = managed.manifest_for_rows(self.manifest['files'] + [dict(path=optional, **self.row('old author'))],
                                              'legacy-A066-fixture')
        release = copy.deepcopy(self.release); release['s0Predecessors'] = [self.evidence(predecessor)]
        result = self.check(release=release)
        self.assertEqual(result['publishedPredecessors'][0]['excludedLegacyOptionalAuthorFiles'], 1)
        only_optional = managed.manifest_for_rows([dict(path=optional, **self.row('old author'))], 'empty-base')
        release['s0Predecessors'] = [self.evidence(only_optional)]
        with self.assertRaisesRegex(ValueError, 'base ownership'):
            self.check(release=release)

    def test_bundled_author_relocation_cannot_satisfy_old_active_ownership(self):
        name = 'assets/zhan10-native/map/floor.png'
        current = dict(self.resources, **{name: self.row('old active author')})
        predecessor = managed.manifest_for_rows(self.manifest['files'] + [dict(path='_internal/' + name, **current[name])],
                                               'published-owned-author-fixture')
        release = copy.deepcopy(self.release); release['s0Predecessors'] = [self.evidence(predecessor)]
        # Bytes remain in the input, but bundling moves them out of candidate base.
        with self.assertRaisesRegex(ValueError, 'Unreviewed S0 omission'):
            self.check(current, release, bundled_packages=('difficult-zhan10',))
        with self.assertRaisesRegex(ValueError, 'Unreviewed S0 omission'):
            self.check(current, release)  # omitted author images are not base either

    def test_s1_composite_manifest_cannot_stand_in_for_published_s0(self):
        seasonal = [dict(path='_internal/seasons/s1/' + mode + '/native-map-manifest.json', **self.row(mode))
                    for mode in ('difficulty2', 'difficult', 'nightmare')]
        lock = ('composite-fixture', tuple((r['path'], r['bytes'], r['sha256']) for r in seasonal))
        with patch.object(managed, '_REVIEWED_S1_BASE_RELEASES', (lock,)):
            composite = managed.manifest_for_rows(self.manifest['files'] + seasonal, 'composite-fixture')
            release = copy.deepcopy(self.release); release['s0Predecessors'] = [self.evidence(composite)]
            with self.assertRaisesRegex(ValueError, 'S1 composite'):
                self.check(release=release)

    def test_program_editor_optional_author_and_fingerprint_do_not_pollute_base_closure(self):
        current = dict(self.resources, **{name: self.row(name) for name in (
            'version.json', 'references/build-fingerprint.txt', 'assets/custom-maps/manifest.json',
            'references/author-packs/nightingale/catalog.json')})
        self.assertEqual(self.check(current)['currentBaseFiles'], 2)


if __name__ == '__main__':
    unittest.main()
