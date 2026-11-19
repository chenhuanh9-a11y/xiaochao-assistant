"""Default main -> real QML shell with an empty imported-resource root.

Native input, capture, single-instance IPC and networking are isolated. The
actual client, resource services, providers, workers and QML are constructed;
the resource lifecycle uses exported synthetic S0/S1 structure packages.
there is no successful replacement engine. This is an offscreen GUI regression,
not installed-product, physical-pixel, binding or game acceptance.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QTimer, QUrl, QObject, QMetaObject, QThread, Qt
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQuick import QQuickItem
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

import client
from client_paths import WORK_ROOT
from original_qt_resource_fixtures import OriginalS0DifficultSourceFixture, OriginalS1SourceFixture


class ImportedResourceStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        if os.environ.get('XINGTU_TEST_RESOURCE_UI_EVIDENCE') and os.name == 'nt':
            for name in ('msyh.ttc', 'msyhbd.ttc'):
                QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / name))

    def test_default_main_opens_real_shell_without_repairing_or_seeding_resources(self):
        from client_route_plaza import RoutePlazaService
        from client_input import NativeShortcutBackend
        from client_resource_startup import repair_managed_base_resources
        from test_client_input import Native
        from test_client_live_runtime import FixtureCapture
        WORK_ROOT.mkdir(exist_ok=True)
        constructor = client.QtClient
        with tempfile.TemporaryDirectory(prefix='qt-empty-resources-', dir=WORK_ROOT) as directory:
            root = Path(directory)
            (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
            legacy = root / '_internal/references/nightmare-map-pack.json'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'{"legacy":"must remain untouched"}')
            import zipfile
            source = OriginalS0DifficultSourceFixture(root / 'source')
            source.export()
            s1_source = OriginalS1SourceFixture(root / 's1-source')
            s1_source.export()
            bad_zip, good_zip = root / 'old.zip', source.output
            with zipfile.ZipFile(bad_zip, 'w') as archive:
                archive.writestr('map-package.json', '{}')
            capture = FixtureCapture()
            capture.is_current = lambda *args, **kwargs: True
            holder = dict(errors=[], phase='empty', visited=[], availability_on_ui_thread=[])
            def construct(*args, **kwargs):
                created = kwargs.get('on_created')
                def retain(partial):
                    holder['client'] = partial
                    if created is not None:
                        created(partial)
                kwargs['on_created'] = retain
                holder['client'] = constructor(*args, **kwargs)
                return holder['client']
            guard = Mock()
            guard.start.return_value = True
            def offline_plaza(*args, **kwargs):
                return RoutePlazaService(*args, **kwargs, listing=lambda query: [])
            def no_network(*args, **kwargs):
                raise AssertionError('Empty resource startup attempted networking')
            poll, watchdog = QTimer(), QTimer()
            poll.setInterval(10)
            watchdog.setSingleShot(True)
            def loaded(instance, page, name):
                loader = instance.main.findChild(QQuickItem, name)
                if instance.main.property('page') != page or loader is None:
                    return False
                expression = QQmlExpression(QQmlEngine.contextForObject(loader), loader,
                                           'status === Loader.Ready && item !== null')
                value = expression.evaluate()
                if expression.hasError():
                    raise AssertionError(expression.error().toString())
                return value[0] if isinstance(value, tuple) else value

            def check_picker(instance, contexts, seasons, selected, name='homeGameContextSeason'):
                self.assertEqual(instance.resources.availableContexts, contexts)
                picker = instance.main.findChild(QQuickItem, name)
                self.assertIsNotNone(picker, name)
                self.assertIs(picker, holder.setdefault(name, picker))
                self.assertEqual(picker.property('count'), len(seasons), name)
                for index, season in enumerate(seasons):
                    expression = QQmlExpression(QQmlEngine.contextForObject(picker), picker, f'valueAt({index})')
                    value, undefined = expression.evaluate()
                    self.assertFalse(expression.hasError(), expression.error().toString())
                    self.assertFalse(undefined)
                    self.assertEqual(value, season)
                self.assertEqual(picker.property('currentIndex'), seasons.index(selected) if selected else -1)
                if not selected:
                    self.assertEqual(picker.property('displayText'), '选择赛季')
                if not seasons:
                    self.assertFalse(picker.isEnabled())
                return picker

            def import_archive(instance, archive):
                instance.resources.importArchive(QUrl.fromLocalFile(str(archive)).toString())

            def dismiss_import_success(instance):
                success = instance.main.findChild(QObject, 'resourceImportSuccessDialog')
                if success is not None:
                    QMetaObject.invokeMethod(success, 'accept')

            def click(instance, item):
                self.assertTrue(item.isEnabled() and item.isVisible())
                point = item.mapToScene(item.boundingRect().center()).toPoint()
                QTest.mouseClick(instance.main, Qt.LeftButton, Qt.NoModifier, point)

            def inspect_removal(instance, context):
                row = next(row for row in instance.resources.packageRows if context in row['selectedContexts'])
                self.assertTrue(instance.resources.requestRemoval(row['key']))

            def collect():
                instance = holder.get('client')
                if (instance is None or not getattr(instance, '_initialized', False) or instance.session.busy
                        or instance.backend.busy or instance.route_choices.busy or instance.resources.busy
                        or instance.settings.busy):
                    return
                if holder['phase'] == 'empty':
                    if not instance.resources.inventoryLoaded:
                        return
                    check_picker(instance, [], [], '')
                    holder['state'] = dict(capabilities=instance.providers.capabilities(),
                        seasonOptions=instance.settings.seasonOptions,
                        rows=list(instance.resources.packageRows), warnings=list(instance.warnings),
                        mainVisible=instance.main.isVisible(), resourceRootCreated=instance.resource_resolver.root.exists(),
                        sameResolver=all(owner is instance.resource_resolver for owner in (
                            instance.providers.resolver, instance.resources.resolver,
                            instance.backend.resource_resolver, instance.route_choices.resource_resolver,
                            instance.info.resource_resolver)),
                        previews=list(instance.info.homePreviews), profiles=list(instance.backend.profiles))
                    instance.resources.errorReported.connect(holder['errors'].append)
                    instance.settings.seasonOptionsChanged.connect(lambda: holder['availability_on_ui_thread'].append(
                        QThread.currentThread() == self.app.thread()))
                    instance.coordinator.navigate('gallery')
                    holder['phase'] = 'gallery'
                    return
                if holder['phase'] == 'gallery':
                    if not loaded(instance, 'gallery', 'lazyGalleryPage'):
                        return
                    check_picker(instance, [], [], '', 'galleryContextSeason')
                    holder['visited'].append('gallery')
                    instance.coordinator.navigate('downloads')
                    holder['phase'] = 'downloads'
                    return
                if holder['phase'] == 'downloads':
                    if not loaded(instance, 'downloads', 'lazyDownloadsPage'):
                        return
                    holder['visited'].append('downloads')
                    instance.coordinator.navigate('resources')
                    import_archive(instance, bad_zip)
                    holder['phase'] = 'legacy'
                    return
                if holder['phase'] == 'legacy':
                    if not holder['errors']:
                        return
                    check_picker(instance, [], [], '')
                    import_archive(instance, good_zip)
                    holder['phase'] = 'saved'
                    return
                if holder['phase'] == 'multi':
                    picker = check_picker(instance, ['s0:difficult', 's1:difficult'], ['s0', 's1'], 's0')
                    if not picker.isEnabled():
                        return
                    dismiss_import_success(instance)
                    click(instance, picker)
                    QTest.keyClick(instance.main, Qt.Key_Down)
                    QTest.keyClick(instance.main, Qt.Key_Return)
                    holder['phase'] = 'switched'
                    return
                if holder['phase'] == 'switched':
                    if instance.session.appliedSeason != 's1':
                        return
                    check_picker(instance, ['s0:difficult', 's1:difficult'], ['s0', 's1'], 's1')
                    self.assertEqual(instance.settings.persistedValues['gameSeason'], 's1')
                    self.assertEqual(instance.session.appliedMode, 'difficult')
                    inspect_removal(instance, 's1:difficult')
                    holder['phase'] = 'confirm-s1'
                    return
                if holder['phase'] in {'confirm-s0', 'confirm-s1'}:
                    self.assertTrue(instance.resources.removalPlanOpen, instance.resources.message)
                    self.assertTrue(instance.resources.confirmRemoval(instance.resources.removalPlanId))
                    holder['phase'] = 'removed-' + holder['phase'][-2:]
                    return
                if holder['phase'] == 'removed-s1':
                    check_picker(instance, ['s0:difficult'], ['s0'], '')
                    self.assertEqual(instance.session.appliedSeason, 's1')
                    inspect_removal(instance, 's0:difficult')
                    holder['phase'] = 'confirm-s0'
                    return
                if holder['phase'] == 'removed-s0':
                    check_picker(instance, [], [], '')
                    import_archive(instance, s1_source.output)
                    holder['phase'] = 'restored'
                    return
                if holder['phase'] == 'restored':
                    check_picker(instance, ['s1:difficult'], ['s1'], 's1')
                    check_picker(instance, ['s1:difficult'], ['s1'], '', 'galleryContextSeason')
                    self.assertEqual(instance.session.appliedSeason, 's1')
                    self.assertEqual(instance.settings.persistedValues['gameSeason'], 's1')
                    dismiss_import_success(instance)
                    holder['finalWarnings'] = list(instance.warnings)
                    poll.stop()
                    instance.coordinator.requestClose()
                    return
                listing = instance.main.findChild(QObject, 'managedPackageList')
                if listing is None or listing.property('count') != 1:
                    return
                if holder['phase'] == 'saved':
                    check_picker(instance, ['s0:difficult'], ['s0'], 's0')
                    check_picker(instance, ['s0:difficult'], ['s0'], 's0', 'galleryContextSeason')
                    holder['savedRows'] = instance.resources.packageRows
                    holder['qmlCount'] = listing.property('count')
                    holder['phase'], holder['renderAfter'] = 'render', time.monotonic() + .1
                    dismiss_import_success(instance)
                    return
                if time.monotonic() < holder['renderAfter']:
                    return
                evidence_dir = os.environ.get('XINGTU_TEST_RESOURCE_UI_EVIDENCE')
                if evidence_dir and 'renderIndex' not in holder:
                    holder['renderIndex'] = 0
                if evidence_dir and holder['renderIndex'] < 2:
                    sizes = ((650, 470), (1000, 720))
                    index = holder['renderIndex']
                    width, height = sizes[index]
                    if instance.main.width() != width or instance.main.height() != height:
                        instance.main.resize(width, height)
                        holder['renderAfter'] = time.monotonic() + .15
                        return
                    output = Path(evidence_dir)
                    output.mkdir(parents=True, exist_ok=True)
                    holder.setdefault('renders', []).append(instance.main.grabWindow().save(str(output / f'resources-{width}x{height}.png')))
                    holder['renderIndex'] += 1
                    if holder['renderIndex'] < 2:
                        return
                if not holder.get('contextRequested'):
                    if instance.main.property('page') != 'home':
                        instance.coordinator.navigate('home')
                        return
                    button = instance.main.findChild(QQuickItem, 'homeGameContextDifficult')
                    if button is None or not button.isVisible() or button.width() <= 0:
                        return
                    self.assertTrue(button.isEnabled(), 'imported S0 difficult base must enable its real home mode button')
                    holder['contextRequested'] = True
                    click(instance, button)
                    return
                if instance.session.appliedMode != 'difficult':
                    return
                holder['appliedMode'] = instance.session.appliedMode
                import_archive(instance, s1_source.output)
                holder['phase'] = 'multi'
            def collect_result():
                try:
                    collect()
                except Exception as error:
                    holder['collectorError'] = str(error)
                    poll.stop()
                    self.app.exit(89)
            poll.timeout.connect(collect_result)
            watchdog.timeout.connect(lambda: self.app.exit(88))
            with ExitStack() as stack:
                for name in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto',
                             'socket.create_connection', 'socket.getaddrinfo'):
                    stack.enter_context(patch(name, side_effect=no_network))
                stack.enter_context(patch.object(client, 'PRODUCTION', True))
                stack.enter_context(patch.object(client, 'owned_sandbox', side_effect=lambda path: Path(path)))
                stack.enter_context(patch.object(client, 'QGuiApplication', return_value=self.app))
                stack.enter_context(patch.object(client, 'QtClient', side_effect=construct))
                stack.enter_context(patch.object(__import__('sys'), 'argv',
                    ['client', '--sandbox', str(root), '--probe-output', str(root / 'unused-probe.json')]))
                stack.enter_context(patch('client_single_instance.SingleInstanceGuard', return_value=guard))
                stack.enter_context(patch('client_input.NativeShortcutBackend',
                                         side_effect=lambda: NativeShortcutBackend(Native())))
                stack.enter_context(patch('client_capture.ClientCaptureProvider', return_value=capture))
                stack.enter_context(patch('client_coordinator.another_assistant_running', return_value=False))
                stack.enter_context(patch('client_route_plaza.RoutePlazaService', side_effect=offline_plaza))
                stack.enter_context(patch('probe_client.run_client_probe'))
                stack.enter_context(patch('client_resource_startup_recovery.offer_startup_recovery', return_value=None))
                repair = stack.enter_context(patch('client_resource_startup.repair_managed_base_resources',
                                                   wraps=repair_managed_base_resources))
                for name in ('backend.load_browse_context', 'client_info.load_browse_context',
                             'client_downloads.verified_installed_package'):
                    stack.enter_context(patch(name, side_effect=AssertionError('Legacy resource reader used')))
                if os.name == 'nt':
                    stack.enter_context(patch('ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID', return_value=0))
                try:
                    poll.start()
                    watchdog.start(90000)
                    result = client.main()
                    instance = holder.get('client')
                    details = dict(constructed=instance is not None,
                        phase=holder['phase'],
                        collectorError=holder.get('collectorError'),
                        initialized=getattr(instance, '_initialized', None),
                        session={key: getattr(getattr(instance, 'session', None), 'snapshot', {}).get(key)
                                 for key in ('mode', 'season', 'busy', 'closed', 'resourceLocked')},
                        resourcesBusy=getattr(getattr(instance, 'resources', None), 'busy', None))
                    self.assertEqual(result, 0, details)
                finally:
                    poll.stop()
                    watchdog.stop()
                    if holder.get('client') is not None:
                        holder['client'].shutdown()
            guard.close.assert_called_once()
            repair.assert_not_called()
            state = holder['state']
            self.assertTrue(state['mainVisible'])
            self.assertEqual(state['warnings'], [])
            self.assertEqual(state['rows'], [])
            self.assertTrue(state['sameResolver'])
            self.assertEqual(state['previews'], [])
            self.assertEqual(state['profiles'], [])
            self.assertFalse(state['resourceRootCreated'])
            self.assertTrue(all(not capability['available'] for row in state['seasonOptions']
                                for capability in row['capabilities'].values()))
            self.assertEqual([error['errorCode'] for error in holder['errors']], ['legacy-format-unsupported'])
            self.assertEqual(holder['availability_on_ui_thread'], [True] * 5)
            self.assertEqual(holder['savedRows'][0]['status'], 'selected')
            self.assertEqual(holder['savedRows'][0]['selectedContexts'], ['s0:difficult'])
            self.assertEqual(holder['qmlCount'], 1)
            self.assertEqual(holder['visited'], ['gallery', 'downloads'])
            self.assertEqual(holder['finalWarnings'], [])
            if holder.get('renders'):
                self.assertTrue(all(holder['renders']))
            capability = state['capabilities']['nightmare']
            self.assertFalse(capability['available'])
            self.assertIn('resourceGaps', capability)
            self.assertEqual(set(capability['resourceGaps']),
                             {'recognition-base-missing', 'controls-profile-missing'})
            self.assertEqual(legacy.read_bytes(), b'{"legacy":"must remain untouched"}')
            self.assertTrue((root / 'work/imported-resources/catalog.json').exists())
            self.assertEqual(instance.resources.availableContexts, ['s1:difficult'])
            available = [row['id'] + ':' + mode for row in instance.settings.seasonOptions
                         for mode, capability in row['capabilities'].items() if capability['available']]
            self.assertEqual(available, ['s1:difficult'])
            self.assertEqual(holder['appliedMode'], 'difficult')
            self.assertEqual(instance.settings.persistedValues['gameMode'], 'difficult')

    def test_readonly_selfcheck_can_report_an_uninitialized_explicit_source_root(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            output = StringIO()
            with patch.object(client, 'PRODUCTION', False), patch.object(client, 'FROZEN', False), \
                 patch.object(__import__('sys'), 'argv', ['client', '--sandbox', str(root), '--self-check', '--verify-resources']), \
                 patch.object(client, 'QGuiApplication', side_effect=AssertionError('self-check created GUI')), \
                 redirect_stdout(output):
                self.assertEqual(client.main(), 1)
            report = json.loads(output.getvalue())
            self.assertTrue(report['ready'])
            self.assertFalse(report['resourcesReady'])
            self.assertEqual(set(report['resourceContexts']), {'s0:nightmare', 's0:difficult', 's1:nightmare', 's1:difficult'})
            self.assertFalse(report['nativeInputStarted'])
            self.assertFalse(report['captureStarted'])
            self.assertEqual(list(root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
