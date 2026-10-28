"""Actual controller methods with surface IO replaced; no live GUI."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from test_reopen_lifecycle import method


class IndependentVisibility(unittest.TestCase):
    def test_reference_gate_ignores_overlay_intent_but_keeps_evidence_gates(self):
        call = method('_reference_sheet_may_show')
        for overlay in (False, True):
            for reference in (False, True):
                state = SimpleNamespace(gate=SimpleNamespace(confirmed_id='map'),
                    pack=SimpleNamespace(by_id={'map': object()}),
                    _round_identity_lock_context=lambda _: {},
                    sheet_visibility_enabled=overlay, reference_sheet_visibility_enabled=reference,
                    game_foreground=True, map_is_open=True, current_floor='1',
                    _current_frame_floor_conflicts=lambda _: False,
                    _current_frame_matches_display_environment=lambda: True)
                self.assertEqual(call(state), reference)
                state.floor_required = True
                self.assertFalse(call(state))
                self.assertEqual(state.reference_sheet_visibility_enabled, reference)
                state.floor_required = False
                state._round_identity_lock_context = lambda _: {'provisional': True}
                self.assertFalse(call(state))

    def test_overlay_toggle_never_hides_or_changes_reference_intent(self):
        call = method('toggle_sheet_visibility')
        for overlay in (False, True):
            for reference in (False, True):
                state = SimpleNamespace(sheet_visibility_enabled=overlay,
                    reference_sheet_visibility_enabled=reference,
                    _ui_action_begin=Mock(), _sync_sheet_visibility_ui=Mock(),
                    _restore_sheet_visibility=Mock(return_value=False), _set_status=Mock(),
                    aligned_overlay=Mock(), hide_sheet=Mock(), _save_settings=Mock(),
                    _log=Mock(), _report_control_action_result=Mock(return_value={}))
                call(state)
                self.assertEqual(state.sheet_visibility_enabled, not overlay)
                self.assertEqual(state.reference_sheet_visibility_enabled, reference)
                state.hide_sheet.assert_not_called()
                self.assertEqual(state.aligned_overlay.hide.call_count, int(overlay))

    def test_reference_toggle_never_changes_overlay_or_rolls_back_unavailable_intent(self):
        call = method('set_reference_sheet_visible')
        for overlay in (False, True):
            for reference in (False, True):
                state = SimpleNamespace(sheet_visibility_enabled=overlay,
                    reference_sheet_visibility_enabled=not reference,
                    aligned_overlay=Mock(), show_sheet=Mock(return_value=False), hide_sheet=Mock(),
                    _save_settings=Mock(return_value=True),
                    _report_control_action_result=lambda *args: {})
                result = call(state, reference)
                self.assertEqual(state.sheet_visibility_enabled, overlay)
                self.assertEqual(state.reference_sheet_visibility_enabled, reference)
                self.assertEqual(result['enabled'], reference)
                self.assertFalse(result['visible'])
                state.aligned_overlay.hide.assert_not_called()

    def test_reference_editor_available_when_overlay_hidden(self):
        state = SimpleNamespace(sheet_visibility_enabled=False,
            reference_sheet_visibility_enabled=True, pack=SimpleNamespace(maps=[object()]))
        self.assertTrue(method('_reference_editor_available')(state))

    def test_closed_overlay_restore_does_not_show_reference(self):
        state = SimpleNamespace(sheet_visibility_enabled=True, game_foreground=True,
                                map_is_open=False, show_sheet=Mock())
        self.assertFalse(method('_restore_sheet_visibility')(state))
        state.show_sheet.assert_not_called()


if __name__ == '__main__':
    unittest.main()
