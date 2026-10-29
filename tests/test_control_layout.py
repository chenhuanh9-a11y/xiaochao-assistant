from pathlib import Path
import json
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from control_layout import load_layout, validate_layout, preset_layout
import identityv_daily_assistant as app
import test_reference_position_controller as position_fixture


class LayoutSchemaTests(unittest.TestCase):
    def test_old_future_malformed_and_unknown_preferences_recover(self):
        values = [None, [], {}, {'schemaVersion': 2}, dict(preset_layout(), extra=True)]
        invalid = preset_layout(); invalid['enabled'] = ['floor']
        values.append(invalid)
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(load_layout(value), preset_layout())
                with self.assertRaises(ValueError): validate_layout(value)

    def test_order_enable_and_suboptions_are_detached_only_layout(self):
        proposal = preset_layout('common')
        proposal['order'].reverse()
        result = validate_layout(proposal)
        self.assertEqual(result['enabled'], ['round', 'map', 'display'])
        proposal['order'].reverse()
        proposal['display']['referenceOpacity'] = False
        self.assertNotEqual(result['order'], proposal['order'])
        self.assertTrue(result['display']['referenceOpacity'])
        self.assertNotIn('floor', result)
        self.assertNotIn('gameMode', result)

    def test_duplicate_missing_order_and_nonboolean_flag_rejected(self):
        for mutation in ('order', 'flag', 'enabled', 'version'):
            value = preset_layout()
            if mutation == 'order': value['order'][-1] = 'display'
            if mutation == 'flag': value['display']['referenceOpacity'] = 1
            if mutation == 'enabled': value['enabled'].append('display')
            if mutation == 'version': value['schemaVersion'] = True
            with self.assertRaises(ValueError): validate_layout(value)


class LayoutControllerTests(unittest.TestCase):
    def setUp(self):
        fixture = position_fixture.PositionControllerTests('test_zero_reference_edit_finish_and_cancel_preserve_independent_saved_alpha')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.a = fixture.a
        self.a.control_layout = preset_layout()
        self.a._sync_game_control_view = Mock()
        self.a._place_quick_panel = Mock()
        self.a.assistant_shell_mode = 'quick'

    def test_apply_persists_layout_and_reopen_without_touching_game_or_alpha(self):
        a = self.a
        state = a.game_mode, a.current_floor, a.sheet_opacity, a.reference_opacity, a.sheet_move_mode, a.gate
        proposal = preset_layout('common')
        proposal['order'] = ['round', 'map', 'display', 'recognition']
        with patch.object(app, 'tk_widget_is_viewable', return_value=True):
            self.assertTrue(a.apply_control_layout(proposal)['success'])
        actual = json.loads(a.settings_path.read_text('utf-8'))
        self.assertEqual(actual['gameControlLayout'], validate_layout(proposal))
        a.control_layout = load_layout(actual['gameControlLayout'])
        self.assertEqual(a.control_layout_snapshot(), validate_layout(proposal))
        self.assertEqual(state, (a.game_mode, a.current_floor, a.sheet_opacity, a.reference_opacity, a.sheet_move_mode, a.gate))
        a._place_quick_panel.assert_called_once()
        a._sync_game_control_view.assert_called_once()

    def test_save_failure_rolls_back_layout_and_does_not_render_draft(self):
        a = self.a
        a._save_settings = Mock(return_value=False)
        self.assertFalse(a.apply_control_layout(preset_layout('common'))['success'])
        self.assertEqual(a.control_layout, preset_layout())
        self.assertNotIn('gameControlLayout', a.settings)
        a._sync_game_control_view.assert_not_called()

    def test_same_layout_does_not_write_and_invalid_proposal_never_applies(self):
        a = self.a
        a._save_settings = Mock(return_value=True)
        self.assertTrue(a.apply_control_layout(preset_layout())['success'])
        self.assertFalse(a.apply_control_layout({'enabled': ['floor']})['success'])
        a._save_settings.assert_not_called()

    def test_active_position_edit_cannot_collapse_and_cancel_does_not_save(self):
        a = self.a
        a.sheet_move_mode = True
        self.assertEqual(a.collapse_control()['reason'], 'reference-edit-active')
        a._set_sheet_move_mode = Mock()
        a.show_sheet = Mock()
        a._save_settings = Mock()
        position = a.sheet_position
        result = a.cancel_reference_sheet_move()
        self.assertTrue(result['success'])
        a._set_sheet_move_mode.assert_called_once_with(False)
        a.show_sheet.assert_called_once()
        a._save_settings.assert_not_called()
        self.assertEqual(a.sheet_position, position)


if __name__ == '__main__': unittest.main()
