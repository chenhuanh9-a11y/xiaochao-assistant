"""Scoped intent planning and owned offscreen controls, with no live authority."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch


from client_group_member_choice import plan_group_member_choice, group_member_choice_is_current


def state():
    return dict(mode="nightmare", roundActive=True, identityPhase="GROUP_LOCKED",
        groupId="group-ab", groupGeneration=8, cycleId=4, floor="1",
        groupMemberIds=["map-a", "map-b", "map-a"], currentGroupMemberId="map-a",
        provisionalMapId="map-a", manualGroupMemberHeld=False)


def payload(current, map_id="map-b"):
    return dict(mapId=map_id, **{name: current[name]
        for name in ("groupId", "groupGeneration", "cycleId", "floor")})


class ChoicePlanTests(unittest.TestCase):
    def test_plan_is_input_only_and_rejects_late_group_floor_cycle_mode_or_round(self):
        current = state()
        original = deepcopy(current)
        request = payload(current)
        plan = plan_group_member_choice(current, "select_group_member", request)
        self.assertTrue(plan.accepted and plan.changed and plan.hold)
        self.assertEqual((plan.map_id, plan.reason), ("map-b", "prepare-member"))
        self.assertEqual(current, original)
        self.assertEqual(request, payload(original))
        self.assertTrue(group_member_choice_is_current(plan, current))
        verified = dict(current, identityPhase="MEMBER_VERIFIED", durableMapId="map-a")
        self.assertTrue(plan_group_member_choice(verified, "select_group_member", payload(verified)).accepted)
        with self.assertRaises(FrozenInstanceError):
            plan.hold = False
        for changes in (dict(floor="2"), dict(groupGeneration=9), dict(cycleId=5),
                        dict(mode="difficult"), dict(roundActive=False),
                        dict(groupMemberIds=["map-a", "map-c"]), dict(resourceLocked=True)):
            self.assertFalse(group_member_choice_is_current(plan, dict(current, **changes)))

    def test_current_first_hold_duplicate_and_rejected_resume_do_not_grant_geometry(self):
        current = state()
        first = plan_group_member_choice(current, "select_group_member", payload(current, "map-a"))
        self.assertTrue(first.changed and first.hold)
        self.assertEqual(first.reason, "prepare-current-member")
        self.assertFalse(current["manualGroupMemberHeld"])
        held = dict(current, manualGroupMemberHeld=True, manualGroupMemberId="map-a",
                    identityPhase="MANUAL_MEMBER_CONFIRMED")
        duplicate = plan_group_member_choice(held, "select_group_member", payload(held, "map-a"))
        self.assertTrue(duplicate.accepted)
        self.assertFalse(duplicate.changed)
        resume = plan_group_member_choice(held, "resume_group_automatic", payload(held))
        self.assertFalse(resume.accepted)
        self.assertEqual(resume.reason,"unsupported-action")
        self.assertTrue(held["manualGroupMemberHeld"])
        # No profile, author image or pose was required/created by the planner.
        self.assertNotIn("liveAuthorization", vars(first))
        self.assertNotIn("matrix", vars(first))

    def test_outside_group_stale_or_pending_requests_have_explicit_reasons(self):
        current = state()
        cases = [(current, payload(current, "map-c"), "outside-current-group"),
                 (current, dict(payload(current), floor="2"), "choice-scope-changed"),
                 (dict(current, contextPending=True), payload(current), "context-unavailable"),
                 (dict(current, groupMemberSelectionPending=True), payload(current), "choice-pending"),
                 (dict(current, roundActive=False), payload(current), "group-unavailable")]
        for snapshot, request, reason in cases:
            plan = plan_group_member_choice(snapshot, "select_group_member", request)
            self.assertFalse(plan.accepted)
            self.assertEqual(plan.reason, reason)
            self.assertTrue(plan.message)



if __name__ == "__main__":
    unittest.main()
