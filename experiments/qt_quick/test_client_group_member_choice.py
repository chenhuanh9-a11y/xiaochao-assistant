"""Scoped intent planning and owned offscreen controls, with no live authority."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from PySide6.QtCore import Property
from PySide6.QtTest import QTest

from client_group_member_choice import plan_group_member_choice, group_member_choice_is_current
import test_client_recognition as ui


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


class GroupSession(ui.FakeSession):
    busy = Property(bool, lambda self: bool(self.state.get("busy")), notify=ui.FakeSession.changed)


class GroupChoiceUiTests(ui.RecognitionTests):
    def setUp(self):
        with patch.object(ui, "FakeSession", GroupSession):
            super().setUp()
        self.session.state.update(state(), groupMemberRows=[dict(mapId="map-a", name="地图甲"),
            dict(mapId="map-b", name="地图乙"), dict(mapId="map-c", name="组外地图")])
        self.session.state["commands"].update({action: dict(available=True)
            for action in ("select_group_member", "resume_group_automatic")})
        self.session.changed.emit()
        ui.APP.processEvents()

    # Original candidate tests use a non-group snapshot and remain in their own module.
    test_candidate_select_previous_next_confirm_reselect_and_floor_flow = None
    test_unsupported_empty_state_disables_commands_and_has_no_browser_dependency = None

    def test_click_pending_ack_hold_has_no_resume_or_global_candidate_action(self):
        self.assertTrue(self.page.property("groupVisible"))
        self.assertTrue(self.item("groupMember_map-a").isEnabled())
        self.click("groupMember_map-b")
        self.assertEqual(self.session.calls[-1], ("select_group_member", payload(state())))
        self.assertTrue(self.item("groupMember_map-a").property("selected"))
        self.session.state.update(groupMemberSelectionPending=True, pendingGroupMemberId="map-b",
            desiredGroupMemberId="map-b",groupMemberChoiceStatus="awaiting-geometry")
        self.session.changed.emit(); ui.APP.processEvents()
        self.assertFalse(self.item("groupMember_map-b").isEnabled())
        self.assertTrue(self.item("groupMember_map-b").property("selected"))
        self.assertIn("已选地图乙，等待定位；当前地图地图甲",self.item("groupMemberCurrent").property("text"))
        self.session.state.update(groupMemberSelectionPending=False,
            groupMemberSelectionReason="切换未生效，仍使用地图甲；请打开地图重试")
        self.session.changed.emit(); ui.APP.processEvents()
        self.assertTrue(self.item("groupMember_map-b").property("selected"))
        self.assertTrue(self.item("groupMember_map-a").isEnabled())
        self.assertIn("自动重试", self.item("groupMemberReason").property("text"))
        self.session.state.update(groupMemberSelectionPending=False, currentGroupMemberId="map-b",
            manualGroupMemberHeld=True, manualGroupMemberId="map-b", identityPhase="MANUAL_MEMBER_CONFIRMED",
            groupMemberSelectionReason="",groupMemberChoiceStatus="confirmed")
        self.session.changed.emit(); ui.APP.processEvents()
        self.assertIn("本局已手动选择：地图乙", self.item("groupMemberCurrent").property("text"))
        self.assertFalse(self.item("groupMember_map-b").isEnabled())
        self.assertNotIn("groupMemberResumeAutomatic", [item.objectName() for item in self.page.findChildren(ui.QObject)])
        self.assertTrue(self.session.state["manualGroupMemberHeld"])
        self.assertFalse(any(action == "select_candidate" for action, _ in self.session.calls))
        self.assertEqual(self.warnings, [])

    def test_refresh_keeps_button_instances_and_disabled_actions_explain_why(self):
        button = self.item("groupMember_map-b")
        button.forceActiveFocus()
        for n in range(20):
            self.session.state.update(busy=bool(n % 2), pendingAction="refresh_map")
            self.session.changed.emit(); ui.APP.processEvents()
            self.assertIs(self.item("groupMember_map-b"), button)
            self.assertTrue(button.isEnabled())
            self.assertTrue(button.hasActiveFocus())
        self.session.state.update(resourceLocked=True, busy=False)
        self.session.changed.emit(); ui.APP.processEvents()
        self.assertFalse(button.isEnabled())
        self.assertIn("正在更新资源", self.item("groupMemberReason").property("text"))
        before = len(self.session.calls); self.click("groupMember_map-b")
        self.assertEqual(len(self.session.calls), before)
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
