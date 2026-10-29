import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from route_display_notice import DisplayNoticeContext, FallbackNoticeTracker, display_feedback


class DisplayFeedbackTests(unittest.TestCase):
    def test_missing_author_uses_actual_source_name(self):
        base = display_feedback("base-map", "missing")
        self.assertEqual(base.notice, "此图暂无作者路线，已显示基础地图")
        self.assertEqual(base.success, "已识别并贴合基础地图")
        structural = display_feedback("shared-structure", "missing")
        self.assertEqual(structural.notice, "此图暂无作者路线，已显示结构底图")
        self.assertNotIn("基础地图", structural.status)

    def test_bad_registration_and_load_error_are_not_missing(self):
        for kind in ("base-map", "shared-structure"):
            registration = display_feedback(kind, "registration-failed")
            loaded = display_feedback(kind, "load-error")
            self.assertIn("配准未通过", registration.status)
            self.assertIn("图片读取失败", loaded.status)
            self.assertEqual(registration.notice, "")
            self.assertEqual(loaded.notice, "")
            self.assertNotIn("原图仍可浏览", registration.status)

    def test_custom_and_author_do_not_emit_fallback_or_invent_route(self):
        self.assertEqual(display_feedback("author-route", "missing").notice, "")
        custom = display_feedback("custom-route", "missing")
        self.assertEqual(custom.notice, "")
        self.assertEqual(custom.success, "已识别并贴合自定义地图")
        self.assertEqual(display_feedback("invalid", "missing").success, "")
        self.assertEqual(display_feedback("base-map", "unknown").notice, "")


class FallbackNoticeTrackerTests(unittest.TestCase):
    def setUp(self):
        self.state = FallbackNoticeTracker()
        self.context = DisplayNoticeContext(1, "nightmare", "new-map", "1", 3, 4)
        self.state.begin_round(1)

    def consume(self, captured=None, current=None, **kwargs):
        return self.state.consume(captured or self.context, current or self.context,
            **dict(display_kind="base-map", author_status="missing", current_commit=True, **kwargs))

    def test_reopen_frames_repeated_status_and_author_recovery_dont_spam(self):
        self.assertTrue(self.consume())
        for frame in range(5, 105):
            c = replace(self.context, frame_generation=frame)
            self.assertEqual(self.consume(c, c), "")
        self.assertEqual(self.state.consume(self.context, self.context,
            display_kind="author-route", author_status="none", current_commit=True), "")
        self.assertEqual(self.consume(), "")

    def test_resource_refresh_and_structure_to_base_do_not_repeat(self):
        self.assertTrue(self.state.consume(self.context, self.context,
            display_kind="shared-structure", author_status="missing", current_commit=True))
        c = replace(self.context, resource_generation=4)
        self.assertEqual(self.consume(c, c), "")

    def test_new_floor_mode_map_and_round_are_independent(self):
        self.assertTrue(self.consume())
        for changes in ({"floor": "2"}, {"floor": "B1"}, {"mode": "difficult"}, {"map_id": "other"}):
            c = replace(self.context, **changes)
            self.assertTrue(self.consume(c, c))
            self.assertEqual(self.consume(c, c), "")
        self.state.end_round()
        self.assertEqual(self.consume(), "")
        self.state.begin_round(2)
        c = replace(self.context, round_id=2)
        self.assertTrue(self.consume(c, c))

    def test_stale_async_each_context_field_does_not_consume_once_key(self):
        for changes in ({"round_id": 2}, {"mode": "difficult"}, {"map_id": "other"},
                        {"floor": "2"}, {"resource_generation": 4}, {"frame_generation": 5}):
            self.assertEqual(self.consume(replace(self.context, **changes), self.context), "")
        self.assertTrue(self.consume())

    def test_cancel_or_failed_commit_does_not_consume_key(self):
        self.assertEqual(self.state.consume(self.context, self.context,
            display_kind="base-map", author_status="missing", current_commit=False), "")
        self.assertTrue(self.consume())

    def test_non_missing_error_then_recovery_does_not_suppress_missing_notice(self):
        for reason in ("registration-failed", "load-error", "none"):
            self.assertEqual(self.state.consume(self.context, self.context,
                display_kind="base-map", author_status=reason, current_commit=True), "")
        self.assertTrue(self.consume())

    def test_repeated_begin_same_round_preserves_dedup(self):
        self.assertTrue(self.consume())
        self.state.begin_round(1)
        self.assertEqual(self.consume(), "")


if __name__ == "__main__":
    unittest.main()
