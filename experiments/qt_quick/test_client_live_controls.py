"""Event input and automatic binding contracts; no game or native listener starts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from client_input import NativeShortcutBackend
from client_coordinator import LiveImages
from client_settings import SettingsService
import test_client_input as native_seams
import test_client_coordinator as coordinator_seams

APP=QGuiApplication.instance() or QGuiApplication([])

class EventSource(QObject):
    eventReceived=Signal(object)
    started=Signal(object)
    failed=Signal(str)
    def __init__(self):
        super().__init__(); self.generation=0; self.running=False; self.held=set()
    def update_keys(self,keys): self.keys=set(keys);self.generation+=1
    def start(self): self.running=True;self.started.emit(frozenset(self.held));return True
    def stop(self): self.running=False;return True
    def send(self,vk,down=True,foreground=77,held=None,generation=None):
        self.eventReceived.emit(dict(vk=vk,down=down,foreground=foreground,
            held=frozenset(held if held is not None else ([vk] if down else [])),
            generation=self.generation if generation is None else generation))

class EventInputTests(unittest.TestCase):
    def setUp(self):
        self.native=native_seams.Native();self.source=EventSource()
        self.keys=NativeShortcutBackend(self.native,source=self.source)
        self.keys.configure({'toggle-panel':'F24','map':'G'})
        self.events=[];self.keys.actionTriggered.connect(self.events.append)
        self.addCleanup(self.keys.shutdown)
    def flush(self):APP.processEvents()
    def test_background_management_needs_no_bound_game_and_short_tap_is_not_lost(self):
        self.assertTrue(self.keys.enableManagement());self.flush()
        self.source.send(0x87);self.source.send(0x87,False);self.flush()
        self.assertEqual(self.events,['toggle-panel'])
        self.assertFalse(self.keys._timer.isActive())
        self.assertEqual(self.native.reads,[])
    def test_management_survives_bind_start_reconfigure_and_game_disable(self):
        self.keys.enableManagement();self.flush()
        self.keys.bindWindow(42);self.keys.configure({'toggle-panel':'F24','map':'G'})
        self.assertTrue(self.keys.enable());self.keys.disable()
        self.source.send(0x87);self.flush()
        self.assertEqual(self.events,['toggle-panel'])
        self.assertTrue(self.keys.snapshot['managementEnabled'])
    def test_game_keys_require_enabled_exact_foreground_and_pass_through(self):
        self.keys.enableManagement();self.keys.bindWindow(42);self.keys.enable();self.flush()
        self.source.send(ord('G'),foreground=77);self.source.send(ord('G'),False);self.flush()
        self.assertEqual(self.events,[])
        self.source.send(ord('G'),foreground=42);self.source.send(ord('G'),foreground=42);self.flush()
        self.assertEqual(self.events,['map'])
        self.assertFalse(self.keys.snapshot['globalRegistration'])
    def test_queued_old_revision_repeat_edit_and_shutdown_do_not_fire(self):
        self.keys.enableManagement();self.flush();old=self.source.generation
        self.keys.configure({'toggle-panel':'F24','map':'G'})
        self.source.send(0x87,generation=old);self.flush();self.assertEqual(self.events,[])
        self.keys.setEditingBindings(True);self.source.send(0x87);self.source.send(0x87,False)
        self.flush();self.keys.setEditingBindings(False)
        self.source.send(0x87);self.flush();self.assertEqual(self.events,['toggle-panel'])
        self.keys.shutdown();self.source.send(0x87,False);self.source.send(0x87);self.flush()
        self.assertEqual(self.events,['toggle-panel']);self.assertFalse(self.source.running)
    def test_held_at_listener_start_and_extra_modifiers_are_rejected(self):
        self.source.held={0x87};self.keys.enableManagement();self.flush()
        self.source.send(0x87);self.flush();self.assertEqual(self.events,[])
        self.source.send(0x87,False);self.source.send(0x87,held={0x87,0xa2});self.flush()
        self.assertEqual(self.events,[])
    def test_lost_game_native_error_does_not_disable_global_management(self):
        self.keys.enableManagement();self.keys.bindWindow(42);self.keys.enable();self.flush()
        self.native.failure=True
        self.source.send(0x87);self.flush()
        self.assertEqual(self.events,['toggle-panel'])
        self.assertTrue(self.keys.snapshot['managementEnabled'])

    def test_failed_binding_edit_cannot_revive_management_or_game(self):
        self.keys.enableManagement();self.flush()
        self.assertFalse(self.keys.configure({'toggle-panel':'G','map':'G'}))
        self.source.send(ord('G'));self.flush();self.assertEqual(self.events,[])
        self.assertFalse(self.keys.enableManagement())

class CoordinatorLiveTests(unittest.TestCase):
    def setUp(self):
        coordinator_seams.CoordinatorTests.setUpClass()
        self.f=coordinator_seams.CoordinatorTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.c=self.f.coordinator
        self.f.session.snapshot.update(roundActive=False,mapOpen=False)
        self.f.session.dispatchWithPayload=Mock();self.f.session.stop=Mock()
        self.f.input.bindWindow=Mock();self.f.input.enableManagement=Mock(return_value=True)
        self.f.input.shutdown=Mock();self.f.capture=SimpleNamespace(disable=Mock())
        self.c.capture=self.f.capture
        self.f.settings.setValue=Mock(side_effect=self.set_value)
        self.f.settings.saveFields=Mock(return_value=True)
        self.c._bound='';self.c._binding_identity=None
        self.c._catalog=Mock(return_value=[])
        self.addCleanup(self.c._discovery_timer.stop);self.addCleanup(self.c._startup_timer.stop)
    def set_value(self,key,value):self.f.settings.values[key]=value;return True
    def row(self,hwnd='42',created=1):return dict(hwnd=hwnd,pid=100,processCreateTime=created,executable='C:/fixture/dwrg.exe',width=100,height=80)
    def test_default_injected_probe_starts_no_catalog_or_native_observer(self):
        self.assertFalse(self.c._automatic_live);self.assertFalse(self.c._discovery_timer.isActive())
        self.f.input.enableManagement.assert_not_called()
    def test_unique_discovery_binds_without_focus_and_ambiguous_catalog_does_not_pick(self):
        self.c._catalog.return_value=[self.row(),self.row('43')]
        self.c.startLiveServices();self.f.session.dispatchWithPayload.assert_not_called()
        self.c._catalog.return_value=[self.row()];self.c._discover_game()
        self.f.session.dispatchWithPayload.assert_called_once_with('bind_capture',dict(hwnd=42,region=[0,0,100,80]))
        self.assertEqual(self.c.boundWindow,'42');self.f.windows.show.assert_not_called()
    def test_destroyed_and_reused_hwnd_cancels_then_rebinds_unique_new_process(self):
        self.c._catalog.return_value=[self.row()];self.c.startLiveServices()
        self.c._catalog.return_value=[self.row(created=2)];self.c._discover_game()
        self.f.session.stop.assert_called_once()
        self.f.runtime.invalidate.assert_any_call('game-window-lost')
        self.assertEqual(self.f.session.dispatchWithPayload.call_count,2)
        self.assertEqual(self.c._binding_identity[2],2)
    def test_mode_is_guarded_during_round_but_resource_browsing_is_independent(self):
        self.f.session.snapshot['roundActive']=True
        self.c.setMode('difficult');self.f.settings.setValue.assert_not_called()
        self.f.session.snapshot['roundActive']=False
        self.c.setMode('difficult');self.f.settings.setValue.assert_called_once_with('gameMode','difficult')
    def test_manual_selection_navigates_and_raises_only_on_explicit_request(self):
        window=Mock();self.f.windows._windows['main']=window
        self.c.requestManualMapSelection()
        self.f.session.dispatch.assert_called_once_with('reselect_map')
        window.showNormal.assert_called_once();window.raise_.assert_called_once();window.requestActivate.assert_called_once()
    def test_toggle_round_shares_pending_settings_start_guard_and_active_means_end(self):
        self.c._bound="42"
        self.f.settings.busy=True
        self.c._native_action("toggle-round")
        self.f.session.dispatch.assert_not_called()
        self.f.settings.busy=False
        self.c._pending_context=("difficult","new-profile")
        self.c._native_action("toggle-round")
        self.f.session.dispatch.assert_not_called()
        self.c._pending_context=None
        with patch("client_coordinator.another_assistant_running",return_value=False):
            self.c._native_action("toggle-round")
        self.f.session.dispatch.assert_called_once_with("start_round")
        self.f.session.dispatch.reset_mock()
        self.f.session.snapshot['roundActive']=True
        self.c._native_action("toggle-round")
        self.f.session.dispatch.assert_called_once_with("end_round")

    def test_visibility_toggles_persist_their_own_preferences(self):
        self.c.toggleAlignedVisibility()
        self.f.settings.saveFields.assert_called_with(['sheetVisibilityEnabled'])
        self.f.windows.hide.assert_called_with('aligned')
        self.c.toggleReferenceVisibility()
        self.f.settings.saveFields.assert_called_with(['referenceSheetVisible'])
        self.assertFalse(self.f.settings.values['referenceSheetVisible'])
    def test_notifications_are_once_per_round_identity_failure_and_end(self):
        notices=[];self.c.notificationRequested.connect(lambda *args:notices.append(args))
        state=dict(roundActive=True,roundId=1,cycleId=1,lastReceipt={'action':'map','success':False,'reason':'no-current-floor'})
        self.c._notify_session_transitions(state);self.c._notify_session_transitions(state)
        self.assertEqual(len(notices),2)
        self.c._notify_session_transitions(dict(roundActive=False));self.c._notify_session_transitions(dict(roundActive=False))
        self.assertEqual(len(notices),3)
    def test_reference_uses_runtime_pixels_not_browse_selection_and_is_independent(self):
        self.c.images=LiveImages();self.c._convert_client_geometry=lambda payload:None
        self.f.settings.persistedValues['referenceSheetVisible']=True
        self.f.settings.values['sheetVisibilityEnabled']=False
        urls=[]
        for token,floor,color in [('a','1',(255,0,0,255)),('b','2',(0,255,0,255))]:
            pixels=np.zeros((4,4,4),np.uint8);pixels[:]=color
            public=dict(token=token,liveAuthorization=True,mode='nightmare',roundId=1,mapId='current-map',floor=floor,profileId='current-author')
            self.f.session.snapshot.update(roundActive=True,alignedVisible=False,render=public)
            self.f.runtime.render_payload.return_value=dict(public,pixels=pixels,originX=0,originY=0,clientWidth=4,clientHeight=4)
            self.c._session_changed();urls.append(self.c.referenceData['imageUrl'])
            self.assertEqual(self.c.referenceData['floor'],floor)
            self.assertEqual(self.c.referenceData['mapId'],'current-map')
            self.assertLessEqual(len(self.c.images._images),2)
            self.f.windows.hide.assert_any_call('aligned')
            self.f.windows.show.assert_any_call('reference')
        self.assertNotEqual(*urls)
        self.c._invalidate_display()
        self.assertEqual(len(self.c.images._images),1)
        self.assertTrue(next(iter(self.c.images._images)).startswith('reference:'))
        self.c._clear_reference();self.assertEqual(self.c.images._images,{})
    def reference_fixture(self, token="same", floor="1"):
        pixels=np.zeros((4,4,4),np.uint8);pixels[:]=(255,0,0,255)
        payload=dict(token=token,liveAuthorization=True,referenceAuthorized=True,
            mode="nightmare",roundId=1,mapId="map",floor=floor,profileId="author",pixels=pixels,
            originX=0,originY=0,clientWidth=4,clientHeight=4)
        self.f.runtime.reference_payload=Mock(return_value=payload)
        self.f.runtime.render_payload.return_value=payload
        self.f.session.snapshot.update(roundActive=True,render={k:v for k,v in payload.items() if k!='pixels'})
        self.c._session_changed()
        return payload

    def test_real_auto_refresh_keeps_reference_editing_until_context_replaced(self):
        self.c.images=LiveImages();self.c._convert_client_geometry=lambda payload:None
        self.reference_fixture()
        window=Mock();window.x.return_value=10;window.y.return_value=20
        window.width.return_value=32;window.height.return_value=32
        self.f.windows._windows['reference']=window
        self.f.windows.snapshot['reference']=dict(visible=True,clickThrough=True)
        self.c.beginReferenceEdit();editing=self.c._edit
        self.assertIsNotNone(editing)
        self.f.windows.setClickThrough.reset_mock()
        self.c._session_changed()  # Same-token automatic refresh, not a static editor helper.
        self.assertIs(self.c._edit,editing)
        self.f.windows.setClickThrough.assert_not_called()
        self.reference_fixture(token='next-floor',floor='2')
        self.assertIsNone(self.c._edit)
        self.assertEqual(self.c.referenceData['floor'],'2')
        self.f.windows.setClickThrough.assert_any_call('reference',True)

    def test_reference_revocation_does_not_erase_same_token_aligned_pixels(self):
        self.c.images=LiveImages();self.c._convert_client_geometry=lambda payload:None
        self.reference_fixture()
        aligned_url=self.c.alignedData['routeUrl']
        self.f.runtime.reference_payload.return_value=None
        self.c._session_changed()
        self.assertEqual(self.c.referenceData,{})
        self.assertEqual(self.c.alignedData['routeUrl'],aligned_url)
        image=self.c.images.requestImage('same',None,None)
        self.assertFalse(image.isNull())
        self.assertEqual(image.pixelColor(0,0).getRgb(),(255,0,0,255))
        self.assertEqual(list(self.c.images._images),['same'])

    def test_qml_cache_reads_new_reference_pixels_after_floor_change(self):
        self.c.images=LiveImages();self.c._convert_client_geometry=lambda payload:None
        view=QQuickView();view.engine().addImageProvider('live',self.c.images)
        view.rootContext().setContextProperty('clientCoordinator',self.c)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'reference.qml';path.write_text('import QtQuick\nImage { width:32;height:32; cache:true;source:clientCoordinator.referenceData.imageUrl || "" }',encoding='utf8')
            view.setSource(QUrl.fromLocalFile(str(path)));view.resize(32,32);view.show()
            try:
                for token,floor,color in [('red','1',(255,0,0,255)),('green','2',(0,255,0,255))]:
                    pixels=np.zeros((4,4,4),np.uint8);pixels[:]=color
                    payload=dict(token=token,liveAuthorization=True,mode='nightmare',roundId=1,mapId='map',floor=floor,profileId='author',pixels=pixels,originX=0,originY=0,clientWidth=4,clientHeight=4)
                    self.f.session.snapshot.update(roundActive=True,alignedVisible=False,render={k:v for k,v in payload.items() if k!='pixels'})
                    self.f.runtime.render_payload.return_value=payload
                    self.c._session_changed();QTest.qWait(30)
                    self.assertEqual(view.grabWindow().pixelColor(16,16).getRgb(),color)
            finally:view.close()

class SharedRuntimeDeliveryTests(unittest.TestCase):
    """Real Session/LiveRuntime/core join, explicit synthetic capture/solver seams.

    Trusted live provenance is composed only for this in-memory fixture. No
    native capture/listener, user config, game image, or learned label is used.
    """
    def test_current_capture_delivers_independent_reference_on_pose_failure(self):
        import copy
        from client_live_runtime import LiveRuntime, _AlignmentHost
        from client_session import SessionService
        import test_client_live_runtime as runtime_seams
        capture = runtime_seams.FixtureCapture()
        capture.generation = 1
        capture.is_current = lambda context, generation: capture.enabled and generation == 1
        engine = runtime_seams.fixture_engine()
        runtime = LiveRuntime(engine_factories={"nightmare": lambda: engine},
            capture_provider=capture, evidence_kind="live-client-capture")
        session = SessionService(runtime)
        coordinator_seams.CoordinatorTests.setUpClass()
        fixture = coordinator_seams.CoordinatorTests();fixture.setUp()
        fixture.coordinator._closed = True
        fixture.coordinator._foreground_timer.stop();fixture.coordinator._tracking_timer.stop()
        from client_coordinator import ClientCoordinator
        fixture.input.enabled = False
        fixture.input.bindWindow = Mock()
        fixture.input.shutdown = Mock()
        fixture.input.enable = Mock(side_effect=lambda: setattr(fixture.input, "enabled", True) or True)
        fixture.settings.setValue = Mock(return_value=True)
        fixture.settings.saveFields = Mock(return_value=True)
        images = LiveImages()
        coordinator = ClientCoordinator(backend=fixture.backend, settings=fixture.settings,
            resources=fixture.resources, session=session, windows=fixture.windows,
            native_input=fixture.input, capture=capture, runtime=runtime, images=images,
            window_catalog=lambda: [])
        coordinator._foreground_timer.stop();coordinator._tracking_timer.stop()
        coordinator._convert_client_geometry = lambda payload: None
        fit = dict(success=True, matrixSourceToClient=np.eye(3),
            selectedRouteCurrentFrameResidual=dict(verified=True, poseVerifiedAgainstCanonicalGeometry=True,
                profileRenderDerivedFromCanonical=True, coordinatePackId="fixture-pack",
                validationBasis="shared-canonical-geometry-current-frame"))
        def complete():
            import time
            deadline=time.monotonic()+2
            while session.busy and time.monotonic()<deadline:
                QTest.qWait(5)
            APP.processEvents();APP.processEvents()
            self.assertFalse(session.busy,session.snapshot)
        try:
            with patch("map_interface_observation.detect_map_interface",return_value=dict(detected=True)), \
                 patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=1)), \
                 patch.object(_AlignmentHost,"fit",side_effect=lambda *args:copy.deepcopy(fit)), \
                 patch("client_coordinator.another_assistant_running",return_value=False):
                coordinator._window_rows=[dict(hwnd="42",width=1280,height=720)]
                coordinator.bindGameWindow("42");complete()
                coordinator.startRound();complete()
                self.assertTrue(session.snapshot["roundActive"])
                session.dispatch("capture_now");complete()
                self.assertEqual(coordinator.alignedData["mapId"],"fixture-map")
                self.assertEqual(coordinator.referenceData["mapId"],"fixture-map")
                self.assertEqual(coordinator.referenceData["floor"],"1")
                first_url=coordinator.referenceData["imageUrl"]
                fit.update(success=False,reason="fixture-pose-rejected")
                session.dispatch("capture_now");complete()
                self.assertEqual(coordinator.alignedData,{})
                self.assertEqual(coordinator.referenceData["mapId"],"fixture-map")
                self.assertTrue(runtime.reference_payload()["referenceAuthorized"])
                self.assertFalse(runtime.snapshot()["displayAuthorized"])
                session.dispatch("close_map");complete()
                self.assertEqual(coordinator.referenceData["imageUrl"],first_url)
                session.dispatch("end_round");complete()
                self.assertEqual(coordinator.referenceData,{})
                self.assertIsNone(runtime.reference_payload())
                self.assertLessEqual(len(images._images),2)
        finally:
            coordinator.shutdown();session.shutdown()
            self.assertTrue(session._pool.waitForDone(1000))
            fixture.tearDown()


class SettingsModeGuardTests(unittest.TestCase):
    def test_actual_save_and_direct_set_both_guard_real_mode_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.qt-lab-owner').write_text('qt-quick-isolated-v1')
            # Existing settings tests supply the same isolated sandbox seam.
            with patch.object(SettingsService,'_check_sandbox'),patch('client_settings.runtime_paths',return_value=SimpleNamespace(settings_path=root/'settings.json')):
                service=SettingsService(root)
            service.setValue('gameMode','difficult')
            service.setModeChangeGuard(lambda:False)
            self.assertFalse(service.saveFields(['gameMode']))
            self.assertFalse((root/'settings.json').exists())
            self.assertTrue(service.setValue('gameMode','nightmare'))
            self.assertFalse(service.setValue('gameMode','difficult'))
            self.assertTrue(service.setValue('sheetOpacity',.7))
            service.shutdown()

if __name__=='__main__':unittest.main()
