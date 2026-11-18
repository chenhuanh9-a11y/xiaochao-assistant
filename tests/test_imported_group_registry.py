"""Real base-package declarations/bindings; fixture updates are not real reviews."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from imported_group_registry import INTERFACE, RESOURCE_PATH, SCHEMA
from imported_native_base import decode_native_base
from imported_resource_archive import save_archive
from imported_resource_packages import PackageView
import star_map_import as files
from test_export_s0_difficult_native_base_package import S0DifficultSourceFixture


class ImportedGroupDeclarationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='embedded-group-source-')
        cls.addClassCleanup(temporary.cleanup)
        source = S0DifficultSourceFixture(Path(temporary.name))
        source.export()
        with zipfile.ZipFile(source.output) as archive:
            cls.members = {name:archive.read(name) for name in archive.namelist()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='embedded-group-declarations-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def bundle(self, change=None, *, remove=False):
        members = dict(self.members)
        context_path, groups_path = 'data/base/resource-context.json', 'data/base/' + RESOURCE_PATH
        if remove:
            context = json.loads(members[context_path])
            context['runtimeInterfaces'].pop(INTERFACE)
            members[context_path] = json.dumps(context).encode('utf-8')
            members.pop(groups_path)
        elif change:
            document = json.loads(members[groups_path])
            change(document)
            members[groups_path] = json.dumps(document).encode('utf-8')
        manifest = json.loads(members['manifest.json'])
        manifest['files'] = [row for row in manifest['files']
                            if f"data/{row['componentId']}/{row['path']}" in members]
        for row in manifest['files']:
            raw = members[f"data/{row['componentId']}/{row['path']}"]
            row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        members['manifest.json'] = json.dumps(manifest).encode('utf-8')
        archive = self.root / 'base.zip'
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as output:
            for name, raw in members.items():
                output.writestr(name, raw)
        saved = save_archive(archive, self.root / 'resources')
        ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        view = PackageView(self.root / 'resources', ref)
        return decode_native_base(view, season='s0', mode='difficult')

    @staticmethod
    def provider(bundle):
        def load(_profile, mid, floor):
            keys = [key for key in bundle.versions if key[1] == mid and key[3] == floor]
            if len(keys) != 1:
                return None
            profile, mid, variant, floor = keys[0]
            return bundle.resources.load(profile, mid, floor, variant=variant)
        return load

    def test_missing_binding_keeps_complete_family_unavailable(self):
        def change(document):
            group = document['groups'][-1]
            group['bindings'] = [row for row in group['bindings'] if row['mapId'] != 'difficult-map-09-2']
        bundle = self.bundle(change)
        registry = bundle.reviewed_front_groups
        group = registry.group_for('difficult-map-05', floor='1', entrance_kind='front')
        self.assertEqual(group['memberIds'], ('difficult-map-05', 'difficult-map-09-2'))
        self.assertEqual(len(registry.groups), 5)
        self.assertIn('member-typed-binding-unavailable:difficult-map-09-2', registry.group_gaps[group['groupId']])
        with self.assertRaisesRegex(ValueError, 'reviewed-front-group-unavailable'):
            registry.validate(group, self.provider(bundle))
        self.assertTrue(bundle.ready)
        self.assertFalse(bundle.rank_ready)

    def test_old_base_does_not_fall_back_to_program_members(self):
        bundle = self.bundle(remove=True)
        self.assertIsNone(bundle.reviewed_front_groups)
        self.assertTrue(bundle.ready)
        self.assertFalse(bundle.rank_ready)

    def test_cross_context_cannot_borrow_s0_table(self):
        with self.assertRaises(files.ImportRejected):
            self.bundle(lambda document:document.update(season='s1'))

    def test_repeated_members_are_rejected_by_consumer_and_producer(self):
        def change(document):
            group = document['groups'][0]
            group['memberIds'] = [group['memberIds'][0]] * 2
        with self.assertRaisesRegex(files.ImportRejected, 'members-invalid'):
            self.bundle(change)
        source = S0DifficultSourceFixture(self.root / 'invalid-source')
        declaration = dict(schema=SCHEMA, season='s0', mode='difficult', groups=[dict(
            groupId='invalid-repeated-members', floor='1', entranceKind='front',
            memberIds=['difficult-map-01', 'difficult-map-01'])])
        registration = self.root / 'repeated-members.json'
        registration.write_text(json.dumps(declaration), encoding='utf-8')
        with self.assertRaisesRegex(files.ImportRejected, 'members-invalid'):
            source.export(group_registration=registration)
        self.assertFalse(source.output.exists())

    def test_member_binding_cannot_point_at_another_real_reference(self):
        def change(document):
            rows = document['groups'][0]['bindings']
            rows[0]['referenceResource'] = rows[1]['referenceResource']
        with self.assertRaisesRegex(files.ImportRejected, 'structure-binding-mismatch'):
            self.bundle(change)

    def test_table_changes_invalidate_cached_registration(self):
        bundle = self.bundle()
        registry = bundle.reviewed_front_groups
        group = registry.group_for('difficult-map-05', floor='1', entrance_kind='front')
        registry.validate(group, self.provider(bundle))
        path = bundle.reader.view.root / RESOURCE_PATH
        path.write_bytes(b'broken-registration')
        with self.assertRaises(files.ImportRejected):
            registry.group_for('difficult-map-05', floor='1', entrance_kind='front')
        with self.assertRaises(files.ImportRejected):
            registry.validate(group, self.provider(bundle))

    def test_same_program_exports_explicit_data_update_without_changing_member_code(self):
        # Deliberately synthetic registration exercises update mechanics; it
        # is not a newly reviewed production relationship between these maps.
        source = S0DifficultSourceFixture(self.root / 'updated-source')
        declaration = dict(schema=SCHEMA, season='s0', mode='difficult', groups=[dict(
            groupId='synthetic-reviewed-update', floor='1', entranceKind='front',
            memberIds=['difficult-map-01','difficult-map-02'])])
        registration = self.root / 'reviewed-fixture.json'
        registration.write_text(json.dumps(declaration), encoding='utf-8')
        report = source.export(group_registration=registration)
        self.assertEqual(report['reviewedGroupCount'], 1)
        saved = save_archive(source.output, self.root / 'updated-resources')
        ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        bundle = decode_native_base(PackageView(self.root / 'updated-resources', ref), season='s0', mode='difficult')
        registry = bundle.reviewed_front_groups
        self.assertEqual(tuple(row['memberIds'] for row in registry.groups),
                         (('difficult-map-01','difficult-map-02'),))
        registry.validate(registry.groups[0], self.provider(bundle))
        self.assertFalse(bundle.rank_ready)


if __name__ == '__main__':
    unittest.main()
