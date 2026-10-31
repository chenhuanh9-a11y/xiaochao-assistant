"""Physical-to-logical monitor correspondence; no native calls or windows."""
from types import SimpleNamespace
import unittest
from client_screen_geometry import project_client_geometry, ScreenGeometryError


def screen(name, x, y, width, height, dpr=1.):
    rectangle = SimpleNamespace(x=lambda:x, y=lambda:y, width=lambda:width, height=lambda:height)
    return SimpleNamespace(name=lambda:name, geometry=lambda:rectangle, devicePixelRatio=lambda:dpr)


class ScreenProjectionTests(unittest.TestCase):
    def project(self, origin=(100, 120), size=(800, 600), name=r"\\.\DISPLAY1",
                native=(0, 0, 2560, 1440), screens=None):
        return project_client_geometry(origin, size, name, native,
            [screen("Q27G4S", 0, 0, 2560, 1440)] if screens is None else screens)

    def test_actual_friendly_name_layout_maps_uniquely_without_primary_fallback(self):
        screens = [screen("Q27G4S",0,0,2560,1440), screen(r"\\.\DISPLAY3",-1200,-116,1200,1920),
                   screen("Q27G2S",2560,0,2560,1440)]
        first = self.project(screens=screens)
        self.assertIs(first.screen, screens[0])
        self.assertEqual((first.origin_x, first.origin_y, first.device_scale), (100,120,1))
        right = self.project(origin=(2600,23), native=(2560,0,5120,1440), name=r"\\.\DISPLAY2", screens=screens)
        self.assertIs(right.screen, screens[2])
        self.assertEqual(right.origin_x, 2600)
        left = self.project(origin=(-1190,-100), size=(1000,1800), native=(-1200,-116,0,1804),
                            name=r"\\.\DISPLAY3", screens=screens)
        self.assertIs(left.screen, screens[1]); self.assertEqual((left.origin_x,left.origin_y),(-1190,-100))

    def test_fractional_scaling_changes_local_offset_and_size_not_monitor_origin(self):
        for dpr in (1., 1.25, 1.5, 2.):
            with self.subTest(dpr=dpr):
                monitor = (-2560,-120,0,1320)
                result = self.project(origin=(-2440,30), size=(1600,900), native=monitor,
                    screens=[screen("same-friendly",-2560,-120,round(2560/dpr),round(1440/dpr),dpr)])
                self.assertAlmostEqual(result.origin_x, -2560 + 120/dpr)
                self.assertAlmostEqual(result.origin_y, -120 + 150/dpr)
                self.assertAlmostEqual((result.origin_x+2560)*dpr,120)
                self.assertEqual(result.device_scale,dpr)

    def test_exact_device_name_is_not_permission_to_use_stale_geometry(self):
        with self.assertRaises(ScreenGeometryError):
            self.project(screens=[screen(r"\\.\DISPLAY1",0,0,1920,1080)])
        result=self.project(screens=[screen(r"\\.\display1",0,0,2560,1440)])
        self.assertEqual(result.diagnostics["match"],"device-and-geometry")

    def test_ambiguous_or_unmatched_screens_reject_instead_of_using_first(self):
        for rows in ([],[screen("friendly",2560,0,2560,1440)],
                     [screen("same",0,0,2560,1440),screen("same",0,0,2560,1440)]):
            with self.subTest(count=len(rows)), self.assertRaises(ScreenGeometryError):
                self.project(screens=rows)

    def test_invalid_dpr_and_nonintersecting_client_cannot_authorize_placement(self):
        for dpr in (0.,-1.,float('nan'),float('inf')):
            with self.subTest(dpr=dpr), self.assertRaises(ScreenGeometryError):
                self.project(screens=[screen("bad",0,0,2560,1440,dpr)])
        with self.assertRaises(ScreenGeometryError):self.project(origin=(3000,3000))
        with self.assertRaises(ScreenGeometryError):self.project(size=(0,900))


if __name__ == '__main__': unittest.main()
