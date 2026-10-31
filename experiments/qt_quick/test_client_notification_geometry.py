"""Fake native monitor rectangles and Qt screens; no WinAPI or windows."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from client_coordinator import ClientCoordinator
from client_input import NativeWindowAPI
from test_client_screen_geometry import screen


def workarea(item, rectangle):
    x, y, width, height = rectangle
    item.availableGeometry = lambda: SimpleNamespace(x=lambda:x, y=lambda:y,
        width=lambda:width, height=lambda:height)
    return item


class NotificationGeometryTests(unittest.TestCase):
    def geometry(self, rows, primary, info=None, bound="42", query=None):
        query = query or Mock(return_value=info)
        owner = SimpleNamespace(_bound=bound, _native_windows=SimpleNamespace(monitor_info=query))
        app = SimpleNamespace(screens=lambda:rows, primaryScreen=lambda:primary)
        with patch("client_coordinator.QGuiApplication", app):
            return ClientCoordinator._notification_geometry(owner)

    def test_friendly_names_choose_game_monitor_geometry_instead_of_primary(self):
        primary = workarea(screen("Q27G4S",0,0,2560,1440), (0,0,2560,1400))
        target = workarea(screen("Q27G2S",2560,0,2560,1440), (2560,0,2560,1390))
        result = self.geometry([primary,target], primary,
            dict(device=r"\\.\DISPLAY2", monitorRect=(2560,0,5120,1440)))
        self.assertEqual(tuple(result[k] for k in ("x","y","width","height")), (2560,0,2560,1390))
        self.assertTrue(result["diagnostics"]["gameMonitorMatched"])
        self.assertEqual(result["diagnostics"]["projection"]["match"], "unique-physical-geometry")

    def test_negative_origin_fractional_dpr_returns_actual_logical_workarea(self):
        target = workarea(screen("left-friendly",-2560,-120,1707,960,1.5), (-2540,-100,1687,920))
        result = self.geometry([target], None,
            dict(device=r"\\.\DISPLAY3", monitorRect=(-2560,-120,0,1320)))
        self.assertEqual(tuple(result[k] for k in ("x","y","width","height")), (-2540,-100,1687,920))
        self.assertEqual(result["diagnostics"]["projection"]["qtScreens"][0]["deviceScale"], 1.5)

    def test_missing_unreadable_or_ambiguous_binding_fallback_is_never_claimed_matched(self):
        primary = workarea(screen("primary",0,0,1920,1080), (0,0,1920,1040))
        unreadable = Mock(side_effect=OSError("fake monitor unreadable"))
        no_binding = Mock(side_effect=AssertionError("unbound must not query native"))
        cases = [(dict(rows=[],primary=primary,bound="",query=no_binding), "no-bound-game"),
                 (dict(rows=[],primary=primary,query=unreadable), "native-monitor-unavailable"),
                 (dict(rows=[screen("same",0,0,1920,1080),screen("same",0,0,1920,1080)],
                       primary=primary, info=dict(device=r"\\.\DISPLAY1",monitorRect=(0,0,1920,1080))),
                  "screen-projection-unverified")]
        for arguments, reason in cases:
            with self.subTest(reason=reason):
                result = self.geometry(**arguments)
                self.assertEqual(result["width"],1920)
                self.assertEqual(result["diagnostics"]["placement"],"primary-fallback")
                self.assertFalse(result["diagnostics"]["gameMonitorMatched"])
                self.assertEqual(result["diagnostics"]["reason"],reason)
        no_binding.assert_not_called()
        result = self.geometry([],None,bound="")
        self.assertEqual(result["diagnostics"]["placement"],"default-fallback")

    def test_legacy_device_query_delegates_monitor_info_without_native_access(self):
        native = NativeWindowAPI()
        native.monitor_info = Mock(return_value=dict(device=r"\\.\DISPLAY2",monitorRect=(0,0,2560,1440)))
        self.assertEqual(native.monitor_device(42),r"\\.\DISPLAY2")
        native.monitor_info.assert_called_once_with(42)
        self.assertIsNone(native._user)


if __name__ == "__main__": unittest.main()
