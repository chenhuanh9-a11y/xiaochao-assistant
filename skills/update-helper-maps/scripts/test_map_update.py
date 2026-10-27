"""Isolated invariant/CLI tests; uses no game assets or application imports."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
import map_update as m


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='map-update-fixture-')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def asset(self, name, data=b'shared-fixture-bytes'):
        p = self.root / name
        p.write_bytes(data)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    def manifest(self, version='v1', base=None):
        h = hashlib.sha256(b'structure').hexdigest()
        source = self.asset('source.bin', b'layout+texture+metadata-fixture')
        products = {role: {**self.asset(role + '.bin'), 'structure_hash': h} for role in m.ROLES}
        products['rendered_display']['kind'] = 'game-rendered'
        return {'schema': 2, 'version': version, 'base_version': base, 'mode': 'nightmare',
                'tools': {'extractor': 'fixture-v1', 'opencv': '4.12.0'},
                'floors': [{'layout_id': 'L1', 'map_guid': 'G1', 'floor': 'B1',
                            'source_version': version, 'base_structure_version': version,
                            'coordinate_system': 'native:60:x-right,y-down',
                            'source': source, 'structure_hash': h, 'display_hash': h,
                            'artifacts': products, 'author_variants': []}]}

    def save(self, doc, name='manifest.json'):
        p = self.root / name
        p.write_text(json.dumps(doc), encoding='utf-8')
        return p

    def test_addition_revision_display_author_and_deletion(self):
        old = self.manifest()
        deleted = copy.deepcopy(old['floors'][0])
        deleted.update(layout_id='Lgone', map_guid='Ggone')
        old['floors'].append(deleted)
        new = self.manifest('v2', 'v1')
        row = new['floors'][0]
        row['structure_hash'] = m.digest('revision')
        row['display_hash'] = m.digest('new display')
        row['author_variants'] = [{'id': 'route-new'}]  # diff unit only, schema tested separately
        added = copy.deepcopy(row)
        added.update(layout_id='L2', map_guid='G2')
        new['floors'].append(added)
        p = m.plan(old, new)
        self.assertEqual(p['changes']['added'], [['L2', 'G2', 'B1']])
        for category in ('structure_changed', 'display_changed', 'author_variants_changed'):
            self.assertEqual(p['changes'][category], [['L1', 'G1', 'B1']])
        self.assertEqual(p['changes']['deletion_candidates'], [['Lgone', 'Ggone', 'B1']])
        self.assertEqual(len(p['changes']['cache_invalidated']), 2)

    def test_author_missing_uses_rendered_default_and_idempotent_resources(self):
        doc, root = m.load_manifest(self.save(self.manifest()))
        out = root / 'staged'
        a = m.stage(doc, root, out)
        before = {p.name: p.read_bytes() for p in out.rglob('*') if p.is_file()}
        b = m.stage(doc, root, out)
        after = {p.name: p.read_bytes() for p in out.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(a, b)
        self.assertEqual(a['unique_resources'], 1)
        entry = json.loads((out / 'index.json').read_text())['entries'][0]
        self.assertEqual(entry['author_variants'], [])
        self.assertEqual(entry['default_display'], entry['products']['rendered_display'])
        self.assertFalse(a['release_ready'])

    def test_wrong_baseline_rejected(self):
        with self.assertRaisesRegex(ValueError, 'baseline'):
            m.plan(self.manifest(), self.manifest('v2', 'wrong'))

    def author(self, doc):
        return {'id': 'author:route', 'base_version': doc['version'],
                'base_key': list(m.base_key(doc['floors'][0])), 'baked': True,
                'transform_applications': 0, 'asset': self.asset('author.bin', b'author-only')}

    def test_wrong_author_base_and_double_transform_rejected(self):
        for wrong in ({'base_version': 'old'}, {'base_key': ['other', 'G1', 'B1']},
                      {'transform_applications': 1}, {'embedded_base': 'bytes'}):
            doc = self.manifest()
            doc['floors'][0]['author_variants'] = [{**self.author(doc), **wrong}]
            with self.assertRaises(ValueError):
                m.load_manifest(self.save(doc))

    def test_author_references_shared_base_without_copying_it(self):
        doc = self.manifest()
        doc['floors'][0]['author_variants'] = [self.author(doc)]
        doc, root = m.load_manifest(self.save(doc))
        result = m.stage(doc, root, root / 'out')
        self.assertEqual(result['unique_resources'], 2)
        entry = json.loads((root / 'out/index.json').read_text())['entries'][0]
        self.assertEqual(entry['author_variants'][0]['base_key'], entry['base_key'])

    def test_opencv_and_coordinate_changes_invalidate_cache(self):
        doc = self.manifest()
        original = m.cache_key(doc, doc['floors'][0])
        doc['tools']['opencv'] = '5.0.0'
        self.assertNotEqual(original, m.cache_key(doc, doc['floors'][0]))
        doc['tools']['opencv'] = '4.12.0'
        doc['floors'][0]['coordinate_system'] = 'other'
        self.assertNotEqual(original, m.cache_key(doc, doc['floors'][0]))

    def test_missing_product_not_claimed_covered(self):
        doc = self.manifest()
        del doc['floors'][0]['artifacts']['recognition']
        doc, root = m.load_manifest(self.save(doc))
        result = m.stage(doc, root, root / 'out')
        self.assertEqual(result['staged_floors'], 0)
        self.assertEqual(result['coverage']['prepared_floors'], 0)
        self.assertEqual(result['coverage']['missing_products'][0]['roles'], ['recognition'])

    def test_duplicate_floor_and_conflicting_guid_rejected(self):
        for change in ({}, {'map_guid': 'wrong'}):
            doc = self.manifest()
            doc['floors'].append({**copy.deepcopy(doc['floors'][0]), **change})
            with self.assertRaises(ValueError):
                m.load_manifest(self.save(doc))

    def test_hash_and_artifact_structure_mismatch_rejected(self):
        doc = self.manifest()
        path = self.save(doc)
        (self.root / 'source.bin').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            m.load_manifest(path)
        doc = self.manifest()
        doc['floors'][0]['artifacts']['clean']['structure_hash'] = m.digest('wrong')
        with self.assertRaisesRegex(ValueError, 'artifact base'):
            m.load_manifest(self.save(doc))

    def test_resume_after_partial_copy_and_refuse_new_version(self):
        doc, root = m.load_manifest(self.save(self.manifest()))
        out = root / 'out'
        m.stage(doc, root, out)
        (out / 'index.json').unlink()
        self.assertEqual(m.stage(doc, root, out)['staged_floors'], 1)
        doc['version'] = 'different'
        with self.assertRaisesRegex(ValueError, 'another input'):
            m.stage(doc, root, out)

    def test_cli_unfinished_adapter_cannot_pass(self):
        path = self.save(self.manifest())
        for args in (['check', '--manifest', str(path), '--scope', 'release'],
                     ['stage', '--manifest', str(path), '--out', str(self.root / 'out')]):
            done = subprocess.run([sys.executable, str(Path(m.__file__)), *args],
                                  text=True, capture_output=True)
            self.assertEqual(done.returncode, 3, done.stderr)
            self.assertFalse(json.loads(done.stdout)['release_ready'])
            self.assertTrue(json.loads(done.stdout)['blocked'])

    def test_cli_inputs_and_plan(self):
        old = self.save(self.manifest(), 'old.json')
        new = self.save(self.manifest('v2', 'v1'), 'new.json')
        for args in (['check', '--manifest', str(new), '--scope', 'inputs'],
                     ['plan', '--old', str(old), '--new', str(new), '--out', str(self.root / 'plan.json')]):
            done = subprocess.run([sys.executable, str(Path(m.__file__)), *args],
                                  text=True, capture_output=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_cli_stage_requires_matching_old_for_incremental(self):
        old = self.save(self.manifest(), 'old.json')
        new_doc = self.manifest('v2', 'v1')
        new = self.save(new_doc, 'new.json')
        command = [sys.executable, str(Path(m.__file__)), 'stage', '--manifest', str(new),
                   '--out', str(self.root / 'out')]
        done = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(done.returncode, 2)
        self.assertFalse((self.root / 'out').exists())
        done = subprocess.run(command + ['--old', str(old)], text=True, capture_output=True)
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(json.loads(done.stdout)['staged_floors'], 1)

    def test_plan_cannot_overwrite_source_assets(self):
        doc = self.manifest()
        path = self.save(doc)
        target = self.root / 'source.bin'
        before = target.read_bytes()
        done = subprocess.run([sys.executable, str(Path(m.__file__)), 'plan', '--new', str(path),
                               '--out', str(target)], text=True, capture_output=True)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(target.read_bytes(), before)

    def test_stage_corrupt_resource_rejected_and_sources_preserved(self):
        doc, root = m.load_manifest(self.save(self.manifest()))
        snapshot = {p: p.read_bytes() for p in root.iterdir() if p.is_file()}
        out = root / 'out'
        m.stage(doc, root, out)
        next((out / 'resources').iterdir()).write_bytes(b'corrupt staged copy')
        with self.assertRaisesRegex(ValueError, 'corrupt staged'):
            m.stage(doc, root, out)
        self.assertEqual(snapshot, {p: p.read_bytes() for p in snapshot})

    def test_same_layout_old_and_new_base_coexist_and_mapping_invalidates(self):
        old = self.manifest()
        old['floors'][0]['author_variants'] = [self.author(old)]
        new = self.manifest('v2', 'v1')
        new['floors'][0]['structure_hash'] = m.digest('revised rooms same layout')
        new['floors'][0]['source'] = self.asset('revised-source.bin', b'revised layout+texture')
        for role, asset in new['floors'][0]['artifacts'].items():
            asset.update(self.asset('revised-' + role + '.bin', ('revised-' + role).encode()))
            asset['structure_hash'] = new['floors'][0]['structure_hash']
        new['retained_bases'] = copy.deepcopy(old['floors'])
        new['retained_bases'][0]['tools'] = copy.deepcopy(old['tools'])
        old_doc, root = m.load_manifest(self.save(old, 'old.json'))
        new_doc, _ = m.load_manifest(self.save(new, 'new.json'))
        result = m.plan(old_doc, new_doc)
        self.assertEqual(result['coverage']['target_maps'], 1)
        self.assertEqual(result['coverage']['retained_base_count'], 1)
        self.assertEqual([r['scope'] for r in result['coverage']['base_versions']], ['current', 'retained'])
        invalid = result['invalidated_mappings'][0]
        self.assertTrue(invalid['old_base_retained'])
        self.assertEqual(invalid['prior_author_ids'], ['author:route'])
        self.assertNotEqual(invalid['old_base'], invalid['new_base'])
        self.assertEqual(len(result['changes']['cache_invalidated']), 1)
        self.assertEqual(m.cache_key(old_doc, old_doc['floors'][0]),
                         m.cache_key(new_doc, new_doc['retained_bases'][0]))
        staged = m.stage(new_doc, root, root / 'out')
        index = json.loads((root / 'out/index.json').read_text())
        self.assertEqual(staged['staged_retained_bases'], 1)
        self.assertEqual(index['entries'][0]['author_variants'], [])
        historical = index['retained_bases'][0]
        self.assertEqual(historical['author_variants'][0]['base_key'], historical['base_key'])
        # Identical logical IDs cannot force an old author onto a revised current base.
        new_doc['floors'][0]['author_variants'] = copy.deepcopy(old_doc['floors'][0]['author_variants'])
        with self.assertRaisesRegex(ValueError, 'wrong author base'):
            m.load_manifest(self.save(new_doc, 'invalid-rebind.json'))

    def test_single_and_merged_cannot_both_be_canonical_for_same_author_base(self):
        doc = self.manifest()
        single = {**self.author(doc), 'id': 'single-layer', 'author_id': 'same-author'}
        merged = {**copy.deepcopy(single), 'id': 'merged-sheet',
                  'asset': self.asset('merged.bin', b'alternative merged source')}
        doc['floors'][0]['author_variants'] = [single, merged]
        with self.assertRaisesRegex(ValueError, 'multiple canonical displays'):
            m.load_manifest(self.save(doc))

    def test_only_selected_canonical_is_staged_alternative_stays_external(self):
        doc = self.manifest()
        selected = {**self.author(doc), 'author_id': 'same-author'}
        alternate = self.asset('external-alternative.bin', b'unselected source retained externally')
        doc['floors'][0]['author_variants'] = [selected]
        doc, root = m.load_manifest(self.save(doc))
        result = m.stage(doc, root, root / 'out')
        entry = json.loads((root / 'out/index.json').read_text())['entries'][0]
        self.assertEqual(len(entry['author_variants']), 1)
        self.assertEqual(entry['author_variants'][0]['author_id'], 'same-author')
        self.assertFalse((root / 'out/resources' / alternate['sha256']).exists())
        self.assertEqual(m.file_hash(root / alternate['path']), alternate['sha256'])
        self.assertEqual(result['author_resource_validation'], 'not_evaluated_by_this_collector')
        self.assertEqual(result['runtime_integration'], 'blocked_adapter')

    def test_same_author_can_keep_distinct_old_structure_canonical(self):
        doc = self.manifest()
        current = doc['floors'][0]
        current['author_variants'] = [{**self.author(doc), 'author_id': 'same-author'}]
        historical = copy.deepcopy(current)
        historical.update(source_version='old-source', base_structure_version='old-structure',
                          structure_hash=m.digest('old-structure'), tools=copy.deepcopy(doc['tools']))
        for artifact in historical['artifacts'].values():
            artifact['structure_hash'] = historical['structure_hash']
        author = historical['author_variants'][0]
        author.update(base_version='old-structure', base_key=list(m.base_key(historical)))
        doc['retained_bases'] = [historical]
        doc, root = m.load_manifest(self.save(doc))
        result = m.stage(doc, root, root / 'out')
        index = json.loads((root / 'out/index.json').read_text())
        self.assertEqual(result['staged_retained_bases'], 1)
        self.assertNotEqual(index['entries'][0]['base_key'], index['retained_bases'][0]['base_key'])
        self.assertEqual(len(index['retained_bases'][0]['author_variants']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
