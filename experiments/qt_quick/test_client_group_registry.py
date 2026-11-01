"""The confirmed list cannot become a global map alias or infer extra members."""
from types import SimpleNamespace
import unittest

from client_group_registry import ConfirmedFrontGroups, CONFIRMED_FRONT_GROUPS


class ConfirmedFrontGroupTests(unittest.TestCase):
    def registry(self, mode):
        prefix = "nightmare-" if mode == "nightmare" else "difficult-map-"
        ids = {prefix + number: object() for group in CONFIRMED_FRONT_GROUPS[mode] for number in group}
        return ConfirmedFrontGroups(SimpleNamespace(mode=mode, by_id=ids))

    def test_exact_confirmed_members_and_entry_scope(self):
        nightmare, difficult = self.registry("nightmare"), self.registry("difficult")
        self.assertEqual(len(nightmare.groups), 3)
        self.assertEqual(len(difficult.groups), 5)
        self.assertEqual(nightmare.group_for("nightmare-04", floor="1", entrance_kind="front")["memberIds"],
                         ("nightmare-04", "nightmare-12", "nightmare-19"))
        self.assertEqual(difficult.group_for("difficult-map-05", floor="1", entrance_kind="front")["memberIds"],
                         ("difficult-map-05", "difficult-map-09-2"))
        for registry, member in ((nightmare, "nightmare-04"), (difficult, "difficult-map-22")):
            self.assertIsNone(registry.group_for(member, floor="1", entrance_kind="side"))
            self.assertIsNone(registry.group_for(member, floor="2", entrance_kind="side"))
            self.assertIsNone(registry.group_for(member, floor="1", entrance_kind="unknown"))
            self.assertIsNone(registry.group_for(member, floor="2", entrance_kind="front"))
        for member in ("nightmare-13", "nightmare-17", "nightmare-18"):
            self.assertIsNone(nightmare.group_for(member, floor="1", entrance_kind="front"))
        self.assertIsNone(difficult.group_for("difficult-map-18", floor="1", entrance_kind="front"))

    def test_registered_family_requires_each_actual_member_binding(self):
        registry = self.registry("difficult")
        group = registry.group_for("difficult-map-05", floor="1", entrance_kind="front")
        def provider(profile, member, floor):
            if member.endswith("09-2"):
                return None
            return SimpleNamespace(metadata=dict(binding=dict(mapId=member, floor=floor), structureVersionKey="real-version"))
        with self.assertRaisesRegex(ValueError, "difficult-map-09-2/1"):
            registry.validate(group, provider)


if __name__ == "__main__":
    unittest.main()
