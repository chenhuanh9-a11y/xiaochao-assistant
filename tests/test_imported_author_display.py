"""Independent display ZIPs use actual component readers and base references."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from client_providers import SharedReferenceProvider
from imported_author_display import SCHEMA, decode_author_display
from imported_native_base import decode_native_base
from imported_resource_archive import save_archive
from imported_resource_packages import PackageView
import star_map_import as files
import test_export_s1_native_base_package as base_fixture
import test_export_s0_difficult_native_base_package as difficult_fixture


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def image_bytes(format='PNG'):
    image = Image.new('RGBA' if format == 'PNG' else 'RGB', (5, 3), (11, 22, 33, 44) if format == 'PNG' else (64, 128, 192))
    if format == 'PNG':
        image.putpixel((2, 1), (1, 2, 3, 0))
    stream = BytesIO()
    image.save(stream, format=format)
    return stream.getvalue()


class _AuthorDisplayFixture(unittest.TestCase):
    def resource_root(self):
        return self.root/'resources'

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='display-base-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = base_fixture.S1NativeBaseSourceFixture(Path(temporary.name))
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='independent-display-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.resources = self.resource_root()
        saved = save_archive(self.source.output, self.resources)
        self.refs = {ref.component_id:ref for ref in saved.component_refs}
        ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        self.base_view = PackageView(self.resources, ref)
        self.base = decode_native_base(self.base_view, season='s1', mode='difficult')
        self.provider = SharedReferenceProvider(self.base_view.root, self.base.resources, self.base.versions,
            check_current=self.base.assert_current)
        self.counter = 0

    @staticmethod
    def wire(ref):
        return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
            componentId=ref.component_id, manifestSha256=ref.manifest_sha256)

    def layer(self, floor='-4', path='images/route.png'):
        mid = self.source.ids[0]
        reference = self.provider('shared-structure', mid, floor)
        relative = reference.path.relative_to(self.base_view.root).as_posix()
        return dict(mapId=mid, floor=floor, displayPath=path, displayShapeHW=[3,5],
            referenceResource=relative, referenceShapeHW=list(reference.shape),
            coordinateSpace=reference.metadata['canvas']['coordinateSpace'],
            matrixDisplayToReference=[[.8,.2,1],[.1,1.2,2],[0,0,1]],
            **{key:reference.metadata[key] for key in ('structureVersionKey','sourceVersion','baseSnapshot')})

    def package(self, edit=None, *, extra=None, payloads=None, dependency=None, context=None, layers=None,
                package_id=None, resource_version='v1', component_id='art', profile_id='author-fixture'):
        self.counter += 1
        document = dict(schema=SCHEMA, profileId=profile_id, authorName='测试作者', displayName='独立路线',
            season=self.base.season, mode=self.base.mode, baseDependency='base',
            layers=deepcopy(layers) if layers is not None else [self.layer()],
            identityAuthority=False, structureValidationAuthority=False, crossFloorProjectionAllowed=False,
            currentFrameGeometryFitRequired=True, provenance=dict(reviewed=True))
        members = {'images/route.png':image_bytes()}
        if payloads:
            members.update(payloads)
        if edit:
            edit(document)
        members['display.json'] = encoded(document)
        if extra:
            members.update(extra)
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId=package_id or f'author-display-fixture-{self.counter}', resourceVersion=resource_version,
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId=component_id, type='display', schemaVersion=1,
                contexts=[context or dict(season=self.base.season,mode=self.base.mode)], entrypoint='display.json',
                dependencies={'base':dependency or self.wire(self.base.component_ref)})],
            files=[dict(componentId=component_id, path=path, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                for path,raw in sorted(members.items())])
        archive_path = self.root/f'author-{self.counter}.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', encoded(manifest))
            for path,raw in members.items():
                archive.writestr('data/'+component_id+'/'+path, raw)
        saved = save_archive(archive_path, self.resources)
        return PackageView(self.resources, saved.component_refs[0]), document, members

    def decode(self, view):
        return decode_author_display(view, self.base, self.provider)


class ImportedAuthorDisplayTests(_AuthorDisplayFixture):
    def test_png_alpha_jpeg_negative_floor_and_unreviewed_metadata_are_preserved(self):
        def add_jpeg(document):
            document['layers'].append(self.layer('-3', 'images/route.jpg'))
        view, document, members = self.package(add_jpeg, payloads={'images/route.jpg':image_bytes('JPEG')})
        display = self.decode(view)
        self.assertEqual(display.profile_id, 'author-fixture')
        self.assertEqual(display.qualification, 'user-provided-unreviewed')
        self.assertEqual(len(display.layers), 2)
        self.assertEqual(display.gaps, ())
        for floor,path in (('-4','images/route.png'),('-3','images/route.jpg')):
            layer = display.layer(self.source.ids[0], floor)
            self.assertEqual(layer.image.component, view.component_ref)
            self.assertEqual(layer.reference.component, self.base.component_ref)
            self.assertEqual(layer.reference_shape_hw, (8,8))
            self.assertEqual(layer.matrix_display_to_reference, ((.8,.2,1),(.1,1.2,2),(0,0,1)))
            self.assertEqual(display.image_bytes(self.source.ids[0], floor), members[path])
        with Image.open(BytesIO(display.image_bytes(self.source.ids[0], '-4'))) as image:
            self.assertEqual(image.getpixel((2,1)), (1,2,3,0))
        self.assertIsNone(display.layer('unknown-map', '-4'))

    def test_declaration_cannot_rebind_reference_or_gain_authority(self):
        cases = [
            lambda d:d.update(baseDependency='other'),
            lambda d:d.update(identityAuthority=True),
            lambda d:d.update(structureValidationAuthority=0),
            lambda d:d['layers'][0].update(coordinateSpace='wrong-space'),
            lambda d:d['layers'][0].update(sourceVersion='wrong-version'),
            lambda d:d['layers'][0].update(referenceShapeHW=[4,4]),
            lambda d:d['layers'][0].update(displayPath='../outside.png'),
            lambda d:d['layers'].append(deepcopy(d['layers'][0])),
            lambda d:d['layers'][0].update(matrixDisplayToReference=[[1,0,0],[0,0,0],[0,0,1]]),
        ]
        for edit in cases:
            with self.subTest(edit=edit):
                view,_,_ = self.package(edit)
                with self.assertRaises(files.ImportRejected):
                    self.decode(view)
        wrong = dict(self.wire(self.base.component_ref), componentId='controls')
        view,_,_ = self.package(dependency=wrong)
        with self.assertRaisesRegex(files.ImportRejected, 'base-dependency-mismatch'):
            self.decode(view)

    def test_bad_pixels_extra_file_and_component_context_are_rejected(self):
        view,_,_ = self.package(payloads={'images/route.png':b'not a PNG'})
        with self.assertRaises(files.ImportRejected):
            self.decode(view)
        view,_,_ = self.package(extra={'images/unbound.png':image_bytes()})
        with self.assertRaisesRegex(files.ImportRejected, 'unlisted-display-file'):
            self.decode(view)
        view,_,_ = self.package(context=dict(season='s1',mode='nightmare'))
        with self.assertRaisesRegex(files.ImportRejected, 'component-context-invalid'):
            self.decode(view)

    def test_actual_manifest_replacement_and_changed_image_do_not_revive_cached_declarations(self):
        view,_,_ = self.package()
        display = self.decode(view)
        mid = self.source.ids[0]
        self.assertTrue(display.image_bytes(mid, '-4'))
        (view.root/'images/route.png').write_bytes(b'changed pixels')
        with self.assertRaises(files.ImportRejected):
            display.image_bytes(mid, '-4')

        view,_,_ = self.package()
        display = self.decode(view)
        manifest = view.root.parent.parent/'manifest.json'
        replacement = manifest.with_suffix('.replacement')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        for call in (lambda:display.layers, lambda:display.profile_id, lambda:display.image_bytes(mid,'-4')):
            with self.subTest(call=call), self.assertRaises(files.ImportRejected):
                call()
        self.base.assert_current()

    def test_same_component_reference_from_another_installation_is_not_the_current_base(self):
        other_root = self.root/'other-resources'
        saved = save_archive(self.source.output, other_root)
        ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        other_view = PackageView(other_root, ref)
        other = decode_native_base(other_view, season='s1', mode='difficult')
        wrong_provider = SharedReferenceProvider(other_view.root, other.resources, other.versions,
            check_current=other.assert_current)
        view,_,_ = self.package()
        with self.assertRaisesRegex(files.ImportRejected, 'reference-file-mismatch'):
            decode_author_display(view, self.base, wrong_provider)

    def test_actual_legacy_reference_stays_a_role_gap_without_native_layout_or_display_binding(self):
        from imported_controls_profile import S0ControlsProfile
        from imported_difficult_native_context import ImportedDifficultNativeContext
        source = difficult_fixture.S0DifficultSourceFixture(self.root/'legacy-source')
        source.export()
        saved = save_archive(source.output, self.resources)
        refs = {ref.component_id:ref for ref in saved.component_refs}
        self.base_view = PackageView(self.resources, refs['base'])
        self.base = decode_native_base(self.base_view, season='s0', mode='difficult')
        controls = S0ControlsProfile(PackageView(self.resources, refs['controls']))
        native = ImportedDifficultNativeContext(self.base_view, self.base, controls)
        self.provider = SharedReferenceProvider(self.base_view.root, self.base.resources, self.base.versions,
            legacy_reference_loader=native.legacy_reference, check_current=native.assert_current)
        reference = self.provider('shared-structure', difficult_fixture.LEGACY, '1')
        row = dict(mapId=difficult_fixture.LEGACY, floor='1', displayPath='images/route.png',
            displayShapeHW=[3,5], referenceShapeHW=list(reference.shape),
            referenceResource=reference.path.relative_to(self.base_view.root).as_posix(),
            coordinateSpace=reference.metadata['canvas']['coordinateSpace'],
            matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]],
            **{key:reference.metadata[key] for key in ('structureVersionKey','sourceVersion','baseSnapshot')})
        view,_,_ = self.package(layers=[row])
        display = self.decode(view)
        self.assertEqual(display.layers, ())
        self.assertEqual(len(display.gaps), 1)
        self.assertEqual(display.gaps[0].reason, 'reference-role-unavailable')
        self.assertEqual(display.qualification, 'user-provided-unreviewed')
        self.assertIsNone(reference.metadata['sourceLayout'])
        self.assertIs(reference.metadata['authorizesMapIdentity'], False)
        self.assertIs(reference.metadata['authorizesStructureValidation'], False)
        with self.assertRaisesRegex(files.ImportRejected, 'layer-unavailable'):
            display.image_bytes(difficult_fixture.LEGACY, '1')


if __name__ == '__main__':
    unittest.main()
