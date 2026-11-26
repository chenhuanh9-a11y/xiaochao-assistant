"""S1 2.105.3133558 map list alignment with Android (困难 22 / 噩梦 20).

Android route-plaza grid rows: 106016–106025 and 107016–107020 added, 106009 and
106014 retired, 107001/107002 revised in place. If ``XINGTU_ANDROID_ASSETS``
points at Android ``mobile/app/src/main/assets``, the generator must reproduce
the bundled table exactly.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools" / "maps"))
import android_route_grid_index as grid  # noqa: E402
import diagnostic_schema  # noqa: E402

HARD = [f"1060{n:02d}" for n in range(1, 26) if n not in (6, 9, 14)]
NIGHTMARE = [f"1070{n:02d}" for n in range(1, 21)]
RETIRED = ("106009", "106014")
FLOORS = ("-1", "-2", "-3")


def s1_maps(prefix):
    return sorted({m for m, _ in grid.GRID_BY_MAP if m.startswith(prefix)})


class S1GridTableTests(unittest.TestCase):
    def test_counts_match_android_2105(self):
        self.assertEqual(s1_maps("106"), HARD)
        self.assertEqual(s1_maps("107"), NIGHTMARE)
        self.assertEqual(len(HARD), 22)
        self.assertEqual(len(NIGHTMARE), 20)

    def test_every_s1_map_has_three_floors_under_both_keys(self):
        for mid in HARD + NIGHTMARE:
            for floor in FLOORS:
                row = grid.GRID_BY_MAP.get((mid, floor))
                self.assertIsNotNone(row, (mid, floor))
                self.assertEqual(grid.GRID_BY_TEMPLATE[(f"season-s1-{mid}", floor)], row)
                cols, step, width, height = row
                self.assertGreaterEqual(cols * step, width)
                self.assertGreater(height, 0)

    def test_retired_maps_are_gone(self):
        for mid in RETIRED:
            for floor in FLOORS:
                self.assertIsNone(grid.lookup_android_grid(f"season-s1-{mid}", mid, floor))

    def test_new_and_revised_rows(self):
        self.assertEqual(grid.lookup_android_grid("season-s1-106016", "106016", "-1"), (288, 4, 1151, 1367))
        self.assertEqual(grid.lookup_android_grid("season-s1-107001", "107001", "-3"), (246, 4, 984, 1599))
        self.assertEqual(grid.lookup_android_grid("season-s1-106001", "106001", "-1"), (264, 4, 1056, 1489))

    def test_s0_rows_kept(self):
        self.assertTrue(any(not k[0].startswith(("106", "107")) for k in grid.GRID_BY_MAP))
        self.assertTrue(any(k[0].startswith("classic-") for k in grid.GRID_BY_TEMPLATE))


class S1DiagnosticCodesTests(unittest.TestCase):
    def test_new_map_ids_are_allowed_in_diagnostics(self):
        for mid in HARD + NIGHTMARE:
            self.assertIn(mid, diagnostic_schema.CODES)


@unittest.skipUnless(os.environ.get("XINGTU_ANDROID_ASSETS"), "需要安卓 assets 目录")
class S1GridGeneratorTests(unittest.TestCase):
    def test_generator_reproduces_bundled_table(self):
        import build_android_route_grid_index as builder
        version, grids = builder.android_s1_grids(Path(os.environ["XINGTU_ANDROID_ASSETS"]))
        self.assertEqual(version, "2.105.3133558")
        self.assertEqual(builder.render(grids), builder.TARGET.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
