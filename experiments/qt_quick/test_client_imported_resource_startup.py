"""Default main -> real QML shell with an empty imported-resource root.

Native input, capture, single-instance IPC and networking are isolated. The
actual client, resource services, providers, workers and QML are constructed;
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
from PySide6.QtCore import QTimer, QUrl, QObject, QMetaObject, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQuick import QQuickItem
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

import client
from client_paths import WORK_ROOT


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
            from test_export_s0_difficult_native_base_package import S0DifficultSourceFixture
            source = S0DifficultSourceFixture(root / 'source')
            source.export()
            bad_zip, good_zip = root / 'old.zip', source.output
            with zipfile.ZipFile(bad_zip, 'w') as archive:
                archive.writestr('map-package.json', '{}')
            capture = FixtureCapture()
            capture.is_current = lambda *args, **kwargs: True
            holder = dict(errors=[], phase='empty', visited=[])
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
            def collect():
                instance = holder.get('client')
                if (instance is None or not getattr(instance, '_initialized', False) or instance.session.busy
                        or instance.backend.busy or instance.route_choices.busy or instance.resources.busy
                        or instance.settings.busy):
                    return
                if holder['phase'] == 'empty':
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
                    instance.coordinator.navigate('gallery')
                    holder['phase'] = 'gallery'
                    return
                if holder['phase'] == 'gallery':
                    if not loaded(instance, 'gallery', 'lazyGalleryPage'):
                        return
                    holder['visited'].append('gallery')
                    instance.coordinator.navigate('downloads')
                    holder['phase'] = 'downloads'
                    return
                if holder['phase'] == 'downloads':
                    if not loaded(instance, 'downloads', 'lazyDownloadsPage'):
                        return
                    holder['visited'].append('downloads')
                    instance.coordinator.navigate('resources')
                    instance.resources.importArchive(QUrl.fromLocalFile(str(bad_zip)).toString())
                    holder['phase'] = 'legacy'
                    return
                if holder['phase'] == 'legacy':
                    if not holder['errors']:
                        return
                    instance.resources.importArchive(QUrl.fromLocalFile(str(good_zip)).toString())
                    holder['phase'] = 'saved'
                    return
                listing = instance.main.findChild(QObject, 'managedPackageList')
                if listing is None or listing.property('count') != 1:
                    return
                if holder['phase'] == 'saved':
                    holder['savedRows'] = instance.resources.packageRows
                    holder['qmlCount'] = listing.property('count')
                    holder['phase'], holder['renderAfter'] = 'render', time.monotonic() + .1
                    success = instance.main.findChild(QObject, 'resourceImportSuccessDialog')
                    if success is not None:
                        QMetaObject.invokeMethod(success, 'accept')
                    return
                if time.monotonic() < holder['renderAfter']:
                    return
                holder['finalWarnings'] = list(instance.warnings)
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
                    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
                    holder['contextRequested'] = True
                    QTest.mouseClick(instance.main, Qt.LeftButton, Qt.NoModifier, point)
                    return
                if instance.session.appliedMode != 'difficult':
                    return
                holder['appliedMode'] = instance.session.appliedMode
                poll.stop()
                instance.coordinator.requestClose()
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
            self.assertEqual(holder['errors'][0]['errorCode'], 'legacy-format-unsupported')
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
            self.assertEqual(instance.resources.availableContexts, ['s0:difficult'])
            available = [row['id'] + ':' + mode for row in instance.settings.seasonOptions
                         for mode, capability in row['capabilities'].items() if capability['available']]
            self.assertEqual(available, ['s0:difficult'])
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
