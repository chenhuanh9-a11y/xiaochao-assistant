"""Every room of the installed custom map package is reachable from the main door.

Runs against ``assets/custom-maps`` (or ``XINGTU_CUSTOM_MAPS_ROOT``) and is
skipped when no package is installed.
"""
import json
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from custom_maps import CustomMapLibrary  # noqa: E402

PATCHES = ROOT / "tools" / "maps" / "custom-road-patches.json"


def reachable(library, map_id) -> set:
    """Nodes reachable from the floor-1 main door through rooms, doors and stairs."""
    graph = library.graph(map_id)
    floor = library.floor(map_id, "1")
    _label, x, y = floor.entrances[0]
    u, v, w = floor.to_native @ np.array([x, y, 1.0])
    start = ("1", graph.floors["1"].nearest(u / w, v / w, radius=200))
    seen, stack = {start}, [start]
    while stack:
        level, index = stack.pop()
        steps = [(level, nxt) for nxt, _cost in graph.floors[level].neighbours[index]]
        steps += [node for node, _cost in graph.links.get((level, index), ())]
        for node in steps:
            if node not in seen:
                seen.add(node)
                stack.append(node)
    return seen


class ReachabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = CustomMapLibrary()
        if not cls.library.available:
            raise unittest.SkipTest(cls.library.error or "custom map package not installed")

    def test_every_room_is_reachable_from_the_main_door(self):
        for map_id, _name in self.library.maps():
            seen = reachable(self.library, map_id)
            graph = self.library.graph(map_id)
            for level, floor in graph.floors.items():
                for room, members in floor.room_members.items():
                    if not floor.is_room[room]:
                        continue
                    missing = [index for index in members if (level, index) not in seen]
                    with self.subTest(map=map_id, floor=level, room=room):
                        self.assertEqual(missing, [], "room points cut off from the main door")

    def test_confirmed_patch_links_are_in_the_package(self):
        if "roadPatches" not in json.loads((self.library.root / "manifest.json").read_text("utf-8"))["sources"]:
            self.skipTest("installed package predates the road patches")
        for link in json.loads(PATCHES.read_text("utf-8"))["links"]:
            floor = self.library.graph(link["mapId"]).floors[link["floor"]]
            with self.subTest(map=link["mapId"], floor=link["floor"], a=link["a"]["index"]):
                self.assertIn(link["b"]["index"], [nxt for nxt, _cost in floor.neighbours[link["a"]["index"]]])


if __name__ == "__main__":
    unittest.main()
