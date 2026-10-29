"""Real ZIP validation/OS lock/additive rollback in temporary installations."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import star_map_import as importer


CORE = '_internal/references/difficult-native-coordinate-pack.json'
SUJU = '_internal/assets/suju/nightmare-03/floor-2.png'
NATIVE = '_internal/assets/zhan10-native/difficult-map-01/floor-1.lossless.webp'
LEGACY = '_internal/assets/zhan10-legacy-clean/difficult-map-29/floor-1.png'
WANG = '_internal/assets/authors/wang-qingxin/map-03-2.png'
NIGHTINGALE = '_internal/assets/authors/nightingale/map-03-2.png'


def row(path, data):
    return dict(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


class LiveAuthorImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='live-author-import-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / 'install'
        self.root.mkdir()
        self.write('_internal/version.json', json.dumps(dict(
            displayVersion='1.0.2', numericVersion='1.0.0.6')).encode())
        self.dependencies = []
        for index, path in enumerate(sorted(importer.BASE_DEPENDENCIES)):
            data = f'base-{index}'.encode()
            self.write(path, data)
            self.dependencies.append(row(path, data))
        self.core_bytes = b'unchanged-native-coordinate-pack'
        self.write(CORE, self.core_bytes)
        self.write('player-custom-route.json', b'keep-custom')
        self.write('第五人格小抄助手.exe', b'not-a-running-executable')

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def package(self, package_id, payload, name='package.zip', *, corrupt_path=None):
        path = self.folder / name
        manifest = dict(schemaVersion=1, revision=1, packageId=package_id,
            clientCompatibility=importer.CLIENT_COMPATIBILITY,
            requiresFiles=self.dependencies, files=[row(p, data) for p, data in payload.items()])
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(importer.MANIFEST, json.dumps(manifest))
            for target, data in payload.items():
                archive.writestr('data/' + target, b'bad' if target == corrupt_path else data)
        return path

    def live(self, package, **kwargs):
        return importer.install_author_package_live(package, self.root, application_idle=True, **kwargs)

    def receipt(self, package_id='nightmare-suju'):
        return self.root / f'work/map-packages/{package_id}.json'

    def assert_base_preserved(self):
        self.assertEqual((self.root / CORE).read_bytes(), self.core_bytes)
        self.assertEqual((self.root / 'player-custom-route.json').read_bytes(), b'keep-custom')
        self.assertEqual((self.root / '第五人格小抄助手.exe').read_bytes(), b'not-a-running-executable')
        for record in self.dependencies:
            self.assertEqual(importer._sha(self.root / record['path']), record['sha256'])

    def test_103_main_and_helper_share_strict_import_contract(self):
        import star_map_import_helper as helper
        self.write('_internal/version.json', json.dumps(dict(
            displayVersion='1.0.3', numericVersion='1.0.0.7')).encode())
        package = self.package('nightmare-suju', {SUJU: b'image'})
        self.assertEqual(importer.inspect_package(package, self.root)['packageId'], 'nightmare-suju')
        self.assertEqual(self.live(package)['status'], 'author-installed-live')
        # Only parent-process waiting is substituted; archive validation, locks,
        # closed-file checks, installation and helper receipt use production code.
        with patch.object(helper, 'wait_for_application_exit', return_value={'exited': True}):
            code, receipt = helper.run_helper(archive=package, install_root=self.root,
                parent_pid=1, parent_created='fixture', archive_sha256=importer._sha(package),
                request_id='version-103')
        self.assertEqual(code, 0, receipt)
        self.assertTrue(receipt['success'])
        self.assert_base_preserved()
        dependency = self.dependencies[0]['path']
        self.write(dependency, b'changed-core')
        with self.assertRaisesRegex(importer.ImportRejected, 'missing-or-incompatible-dependency'):
            importer.inspect_package(package, self.root)
        with patch.object(helper, 'wait_for_application_exit', return_value={'exited': True}):
            code, receipt = helper.run_helper(archive=package, install_root=self.root,
                parent_pid=1, parent_created='fixture', archive_sha256=importer._sha(package),
                request_id='version-103-tampered')
        self.assertEqual(code, 2)
        self.assertIn('missing-or-incompatible-dependency', receipt['reason'])

    def test_unknown_client_version_remains_rejected(self):
        package = self.package('nightmare-suju', {SUJU: b'image'})
        self.write('_internal/version.json', json.dumps(dict(
            displayVersion='99.0', numericVersion='1.0.0.7')).encode())
        with self.assertRaisesRegex(importer.ImportRejected, 'unsupported-client-version'):
            importer.inspect_package(package, self.root)

    def test_readonly_support_hint_classifies_four_roles_without_filesystem_access(self):
        paths = {'nightmare-suju': SUJU, 'difficult-zhan10': NATIVE,
                 'nightmare-wang-qingxin': WANG, 'nightmare-nightingale': NIGHTINGALE}
        with patch.object(importer, '_target', side_effect=AssertionError('hint read filesystem')):
            with patch.object(importer, '_validate', side_effect=AssertionError('hint revalidated archive')):
                for package_id, path in paths.items():
                    with self.subTest(package_id=package_id):
                        snapshot = dict(packageId=package_id, replaceFiles=[], missingFiles=[{'path': path}])
                        before = json.dumps(snapshot, sort_keys=True)
                        self.assertTrue(importer.supports_live_author_import(snapshot))
                        self.assertEqual(json.dumps(snapshot, sort_keys=True), before)
                        self.assertTrue(importer.supports_live_author_import(dict(
                            packageId=package_id, replaceFiles=[], missingFiles=[])))

    def test_support_hint_rejects_missing_core_unknown_ids_replacements_and_bad_rows(self):
        valid = dict(packageId='difficult-zhan10', replaceFiles=[], missingFiles=[])
        cases = [None, [], {}, dict(valid, packageId='unknown'), dict(valid, packageId=[]),
                 dict(valid, replaceFiles=[{'path': NATIVE}]), dict(valid, replaceFiles=None),
                 dict(valid, missingFiles=None), dict(valid, missingFiles='not-rows'),
                 dict(valid, missingFiles=[None]), dict(valid, missingFiles=[NATIVE]),
                 dict(valid, missingFiles=[{}]), dict(valid, missingFiles=[{'path': []}]),
                 dict(valid, missingFiles=[{'path': CORE}]),
                 dict(valid, missingFiles=[{'path': '_internal/recognition/native/entries.json'}])]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                self.assertFalse(importer.supports_live_author_import(snapshot))

    def test_support_hint_accepts_only_the_authors_exact_sidecars(self):
        for package_id, author in importer.AUTHOR_PACKAGES.items():
            for name in importer.AUTHOR_SIDECARS:
                snapshot = dict(packageId=package_id, replaceFiles=[], missingFiles=[{
                    'path': f'_internal/references/author-packs/{author}/{name}'}])
                self.assertTrue(importer.supports_live_author_import(snapshot))
                self.assertFalse(importer.supports_live_author_import(dict(snapshot, packageId='nightmare-suju')))
            for path in (f'_internal/references/author-packs/{author}/extra.json',
                         f'_internal/references/author-packs/{author}/nested/catalog.json',
                         f'_internal/assets/authors/{author}/metadata.json'):
                self.assertFalse(importer.supports_live_author_import(dict(
                    packageId=package_id, replaceFiles=[], missingFiles=[{'path': path}])))

    def test_first_and_repeat_four_package_roles_without_exclusive_exe_check(self):
        payloads = {
            'nightmare-suju': {SUJU: b'suju-image'},
            'difficult-zhan10': {CORE: self.core_bytes, NATIVE: b'native-image', LEGACY: b'legacy-image'},
            'nightmare-wang-qingxin': {WANG: b'wang-image'},
            'nightmare-nightingale': {NIGHTINGALE: b'nightingale-image'},
        }
        for package_id, author in importer.AUTHOR_PACKAGES.items():
            for name in importer.AUTHOR_SIDECARS:
                payloads[package_id][f'_internal/references/author-packs/{author}/{name}'] = b'{}'
        with patch.object(importer, '_check_closed', side_effect=AssertionError('live must not open EXE exclusively')):
            for package_id, payload in payloads.items():
                with self.subTest(package_id=package_id):
                    package = self.package(package_id, payload, package_id + '.zip')
                    result = self.live(package)
                    expected = sum(importer._live_author_path(package_id, p) for p in payload)
                    self.assertEqual(result['status'], 'author-installed-live')
                    self.assertFalse(result['restartRequired'])
                    self.assertTrue(result['liveImport'])
                    self.assertEqual(result['added'], expected)
                    self.assertEqual(result['authorFilesAdded'], expected)
                    self.assertEqual(result['authorFilesAlreadyPresent'], 0)
                    receipt = self.receipt(package_id)
                    receipt_bytes, inode = receipt.read_bytes(), receipt.stat().st_ino
                    again = self.live(package)
                    self.assertEqual(again['status'], 'author-already-installed')
                    self.assertEqual(again['authorFilesAdded'], 0)
                    self.assertEqual(again['authorFilesAlreadyPresent'], expected)
                    self.assertTrue(again['receiptPreserved'])
                    self.assertEqual(receipt.read_bytes(), receipt_bytes)
                    self.assertEqual(receipt.stat().st_ino, inode)
        self.assert_base_preserved()

    def test_explicit_idle_true_is_required_and_old_api_remains_closed_only(self):
        package = self.package('nightmare-suju', {SUJU: b'image'})
        for idle in (False, None, 1, 'yes'):
            with self.subTest(idle=idle):
                with self.assertRaisesRegex(importer.ImportRejected, 'live-import-requires-idle'):
                    importer.install_author_package_live(package, self.root, application_idle=idle)
        with self.assertRaisesRegex(importer.ImportRejected, 'close-application-first'):
            importer.install_package(package, self.root)
        with patch.object(importer, '_check_closed', side_effect=importer.ImportRejected('application-running')) as closed:
            with self.assertRaisesRegex(importer.ImportRejected, 'application-running'):
                importer.install_package(package, self.root, application_stopped=True)
            closed.assert_called_once()
        self.assertFalse((self.root / SUJU).exists())

    def test_missing_core_and_non_image_payload_require_restart(self):
        for payload in ({NATIVE: b'image', '_internal/difficult-native-manifest.json': b'{}'},
                        {SUJU: b'image', '_internal/assets/suju/recognition.npz': b'core'}):
            with self.subTest(paths=tuple(payload)):
                package_id = 'difficult-zhan10' if NATIVE in payload else 'nightmare-suju'
                package = self.package(package_id, payload)
                with self.assertRaisesRegex(importer.ImportRejected, 'live-import-requires-restart'):
                    self.live(package)
                for path in payload:
                    self.assertFalse((self.root / path).exists())
                self.assertFalse(self.receipt(package_id).exists())
        self.assert_base_preserved()

    def test_owned_replacement_still_requires_restart_and_preserves_receipt(self):
        old = self.package('nightmare-suju', {SUJU: b'old-image'}, 'old.zip')
        self.live(old)
        receipt_bytes = self.receipt().read_bytes()
        newer = self.package('nightmare-suju', {SUJU: b'new-image'}, 'new.zip')
        with self.assertRaisesRegex(importer.ImportRejected, 'live-import-requires-restart'):
            self.live(newer)
        self.assertEqual((self.root / SUJU).read_bytes(), b'old-image')
        self.assertEqual(self.receipt().read_bytes(), receipt_bytes)

    def test_package_roles_and_exact_sidecars_are_not_broadened(self):
        cases = (
            ('nightmare-suju', WANG), ('difficult-zhan10', SUJU),
            ('nightmare-wang-qingxin', NIGHTINGALE), ('nightmare-nightingale', WANG),
            ('nightmare-wang-qingxin', '_internal/references/author-packs/wang-qingxin/extra.json'),
        )
        for package_id, path in cases:
            with self.subTest(package_id=package_id, path=path):
                package = self.package(package_id, {path: b'not-permitted'})
                with self.assertRaisesRegex(importer.ImportRejected, 'path-not-in-data-allowlist'):
                    self.live(package)
                self.assertFalse((self.root / path).exists())

    def test_archive_sha_and_complete_staged_payload_integrity_precede_publication(self):
        second = '_internal/assets/suju/nightmare-03/floor-1.png'
        package = self.package('nightmare-suju', {SUJU: b'good', second: b'expected'})
        with self.assertRaisesRegex(importer.ImportRejected, 'selected-archive-changed'):
            self.live(package, expected_archive_sha256='0' * 64)
        corrupt = self.package('nightmare-suju', {SUJU: b'good', second: b'expected'},
                               'corrupt.zip', corrupt_path=second)
        with patch.object(importer.os, 'link', side_effect=AssertionError('all staging must finish first')):
            with self.assertRaises(importer.ImportRejected):
                self.live(corrupt)
        self.assertFalse((self.root / SUJU).exists())
        self.assertFalse((self.root / second).exists())
        self.assertFalse(self.receipt().exists())

    def test_dependency_validation_is_unchanged(self):
        package = self.package('nightmare-suju', {SUJU: b'image'})
        self.write(self.dependencies[0]['path'], b'incompatible-base')
        with self.assertRaisesRegex(importer.ImportRejected, 'missing-or-incompatible-dependency'):
            self.live(package)
        self.assertFalse((self.root / SUJU).exists())

    def test_cancel_before_publication_leaves_no_author_files_or_receipt(self):
        package = self.package('nightmare-suju', {SUJU: b'image'})
        with self.assertRaisesRegex(importer.ImportRejected, 'import-cancelled'):
            self.live(package, cancelled=lambda: True)
        self.assertFalse((self.root / SUJU).exists())
        self.assertFalse(self.receipt().exists())
        self.assert_base_preserved()

    def test_caller_generation_invalidated_during_staging_prevents_publication(self):
        package = self.package('nightmare-suju', {SUJU: b'image'})
        state = {'cancelled': False}
        real_validate = importer._validate
        def validated(*args, **kwargs):
            result = real_validate(*args, **kwargs)
            state['cancelled'] = True  # Controlled idle/generation-token invalidation.
            return result
        with patch.object(importer, '_validate', side_effect=validated):
            with patch.object(importer.os, 'link', side_effect=AssertionError('stale caller published files')):
                with self.assertRaisesRegex(importer.ImportRejected, 'import-cancelled'):
                    self.live(package, cancelled=lambda: state['cancelled'])
        self.assertFalse((self.root / SUJU).exists())
        self.assertFalse(self.receipt().exists())
        self.assert_base_preserved()

    def test_cancel_during_publication_rolls_back_even_after_the_last_image(self):
        second = '_internal/assets/suju/nightmare-03/floor-1.png'
        package = self.package('nightmare-suju', {SUJU: b'first', second: b'second'})
        real_link = importer.os.link
        for stop_after in (1, 2):
            with self.subTest(stop_after=stop_after):
                state = {'published': 0}
                def publish(source, target, *args, **kwargs):
                    real_link(source, target, *args, **kwargs)
                    state['published'] += 1
                with patch.object(importer.os, 'link', side_effect=publish):
                    with self.assertRaisesRegex(importer.ImportRejected, 'import-cancelled'):
                        self.live(package, cancelled=lambda: state['published'] >= stop_after)
                self.assertEqual(state['published'], stop_after)
                self.assertFalse((self.root / SUJU).exists())
                self.assertFalse((self.root / second).exists())
                self.assertFalse(self.receipt().exists())
        self.assert_base_preserved()

    def test_cancellation_after_receipt_commit_cannot_report_a_rolled_back_import(self):
        package = self.package('nightmare-suju', {SUJU: b'committed-image'})
        state = {'cancelled': False}
        real_replace = importer.os.replace
        def commit_then_cancel(source, target, *args, **kwargs):
            real_replace(source, target, *args, **kwargs)
            state['cancelled'] = True  # Cancellation arrives after the transaction commit point.
        with patch.object(importer.os, 'replace', side_effect=commit_then_cancel):
            result = self.live(package, cancelled=lambda: state['cancelled'])
        self.assertTrue(state['cancelled'])
        self.assertEqual(result['status'], 'author-installed-live')
        self.assertEqual((self.root / SUJU).read_bytes(), b'committed-image')
        self.assertEqual(json.loads(self.receipt().read_text(encoding='utf-8'))['archiveSha256'],
                         result['archiveSha256'])
        self.assert_base_preserved()

    def test_publish_and_receipt_failures_roll_back_only_new_files(self):
        first = self.package('nightmare-suju', {SUJU: b'preserved'}, 'first.zip')
        self.live(first)
        previous_receipt = self.receipt().read_bytes()
        second, third = '_internal/assets/suju/second.png', '_internal/assets/suju/third.png'
        package = self.package('nightmare-suju', {SUJU: b'preserved', second: b'new-2', third: b'new-3'})
        real_link = importer.os.link
        def fail_link(source, target, *args, **kwargs):
            if Path(target) == self.root / third:
                raise OSError('controlled publication failure')
            return real_link(source, target, *args, **kwargs)
        for operation, replacement in (('link', fail_link),
                                       ('replace', OSError('controlled receipt failure'))):
            with self.subTest(operation=operation):
                with patch.object(importer.os, operation, side_effect=replacement):
                    with self.assertRaises(OSError):
                        self.live(package)
                self.assertEqual((self.root / SUJU).read_bytes(), b'preserved')
                self.assertFalse((self.root / second).exists())
                self.assertFalse((self.root / third).exists())
                self.assertEqual(self.receipt().read_bytes(), previous_receipt)
        self.assert_base_preserved()

    def test_shared_os_lock_blocks_another_live_or_closed_writer(self):
        package = self.package('nightmare-suju', {SUJU: b'image'})
        with importer._writer_locked_installation(self.root, require_closed=False):
            with self.assertRaisesRegex(importer.ImportRejected, 'another-import-is-running'):
                self.live(package)
            with patch.object(importer, '_check_closed'):
                with self.assertRaisesRegex(importer.ImportRejected, 'another-import-is-running'):
                    importer.install_package(package, self.root, application_stopped=True)
        self.assertFalse((self.root / SUJU).exists())
        self.assertEqual(self.live(package)['status'], 'author-installed-live')

    def test_core_disappearing_after_staging_is_not_repaired_live(self):
        package = self.package('difficult-zhan10', {NATIVE: b'image', CORE: self.core_bytes})
        real_commit = importer._commit_additions
        def changed_core(root, result, stage, **kwargs):
            (self.root / CORE).unlink()  # Controlled external mutation of this temporary root.
            return real_commit(root, result, stage, **kwargs)
        with patch.object(importer, '_commit_additions', side_effect=changed_core):
            with self.assertRaisesRegex(importer.ImportRejected, 'live-import-requires-restart'):
                self.live(package)
        self.assertFalse((self.root / CORE).exists())
        self.assertFalse((self.root / NATIVE).exists())
        self.assertFalse(self.receipt('difficult-zhan10').exists())


if __name__ == '__main__':
    unittest.main()
