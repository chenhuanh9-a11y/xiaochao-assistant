"""Real scheduler/config/UI methods; deterministic timer/widget/controller I/O.

No Tk window, native opacity, input injection or game test is performed here.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from opacity_controls import OpacityUpdates, load_opacities, opacity_percent
from test_fixed_map_position_ui import load_methods, WidgetIO


class Timers:
    def __init__(self):
        self.pending = {}
        self.serial = 0

    def after(self, delay, callback):
        self.serial += 1
        self.pending[self.serial] = (delay, callback)
        return self.serial

    def cancel(self, token):
        self.pending.pop(token, None)

    def run(self, delay):
        for token, (due, callback) in tuple(self.pending.items()):
            if due == delay and token in self.pending:
                del self.pending[token]
                callback()


class Var:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class OpacityTests(unittest.TestCase):
    def make(self):
        timers = Timers()
        state = dict(sheetOpacity=.55, referenceOpacity=.55,
                     userVisible=False, referenceEnabled=True, referenceAdjusting=True)
        previews, saves = [], []
        def preview(surface, value):
            previews.append((surface, value))
            state['sheetOpacity' if surface == 'overlay' else 'referenceOpacity'] = value / 100
        updates = OpacityUpdates(timers.after, timers.cancel,
            {surface: lambda value, key=surface: preview(key, value)
             for surface in ('overlay', 'reference')}, lambda: saves.append(dict(state)))
        for key in ('overlay', 'reference'): updates.sync(key, 55)
        return timers, state, previews, saves, updates

    def test_legacy_config_migrates_without_changing_other_preferences(self):
        old = dict(sheetOpacity=.23, userVisible=False, customRoutes=['keep'])
        before = dict(old)
        self.assertEqual(load_opacities(old), (.23, .23))
        self.assertEqual(old, before)
        self.assertEqual(load_opacities({}), (.55, .55))
        self.assertEqual(load_opacities(dict(sheetOpacity=.23, referenceOpacity=0)), (.23, 0))

    def test_invalid_and_boundary_settings(self):
        self.assertEqual(load_opacities(dict(sheetOpacity=9, referenceOpacity=-2)), (1, 0))
        self.assertEqual(load_opacities(dict(sheetOpacity=float('nan'), referenceOpacity='bad')), (.55, .55))
        self.assertEqual(opacity_percent(0), 0)
        self.assertEqual(opacity_percent(100), 100)
        with self.assertRaises(ValueError): opacity_percent(float('nan'))

    def test_drag_coalesces_each_surface_and_never_saves_on_each_move(self):
        timers, state, previews, saves, updates = self.make()
        for value in range(101):
            updates.change('overlay', value)
            updates.change('reference', 100-value)
        self.assertEqual(len(timers.pending), 3)  # two preview lanes, one save
        self.assertEqual(previews, [])
        self.assertEqual(saves, [])
        timers.run(16)
        self.assertEqual(previews, [('overlay', 100), ('reference', 0)])
        self.assertEqual(saves, [])
        timers.run(300)
        self.assertEqual(len(saves), 1)
        self.assertEqual((state['sheetOpacity'], state['referenceOpacity']), (1, 0))

    def test_release_flushes_both_pending_values_before_single_save(self):
        timers, state, previews, saves, updates = self.make()
        updates.change('overlay', 0)
        updates.change('reference', 100)
        updates.commit()
        updates.commit()
        timers.run(16); timers.run(300)
        self.assertEqual(previews, [('overlay', 0), ('reference', 100)])
        self.assertEqual(len(saves), 1)
        self.assertEqual((saves[0]['sheetOpacity'], saves[0]['referenceOpacity']), (0, 1))
        self.assertEqual(timers.pending, {})

    def test_alpha_does_not_toggle_visibility_or_editing_and_roundtrips(self):
        _, state, _, saves, updates = self.make()
        updates.change('reference', 0); updates.commit()
        self.assertEqual(state['sheetOpacity'], .55)
        self.assertFalse(state['userVisible'])
        self.assertTrue(state['referenceEnabled'])
        self.assertTrue(state['referenceAdjusting'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text(json.dumps(saves[-1]), encoding='utf-8')
            self.assertEqual(load_opacities(json.loads(path.read_text(encoding='utf-8'))), (.55, 0))

    def test_snapshot_render_cannot_overwrite_pending_drag_or_loop_save(self):
        timers, _, _, saves, updates = self.make()
        updates.change('overlay', 83)
        self.assertEqual(updates.sync('overlay', 55), 83)
        self.assertEqual(updates.sync('reference', 27), 27)
        updates.change('reference', 27)  # Scale callback after programmatic set
        self.assertNotIn('reference', updates.pending)
        updates.commit()
        self.assertEqual(len(saves), 1)
        self.assertEqual(updates.sync('overlay', 83), 83)
        updates.change('overlay', 83)
        self.assertEqual(timers.pending, {})

    def test_close_cancels_scheduled_work_and_late_callback_is_noop(self):
        timers, _, previews, saves, updates = self.make()
        updates.change('overlay', 12)
        late = [callback for _, callback in timers.pending.values()]
        updates.close()
        for callback in late: callback()
        updates.change('reference', 44)
        self.assertEqual((previews, saves, timers.pending), ([], [], {}))

    def test_real_view_callbacks_sync_and_keyboard_release(self):
        ns = load_methods('game_control_view.py', 'GameControlView', {
            '_opacity_changed', '_sync_opacity_controls', '_opacity_key_released'})
        ns.update(opacity_percent=opacity_percent, tk=SimpleNamespace(TclError=RuntimeError))
        view = ns['GameControlView']()
        timers, state, previews, saves, view._opacity_updates = self.make()
        view.opacity_var, view.reference_opacity_var = Var(55), Var(55)
        view.opacity_values = {key: WidgetIO() for key in ('overlay', 'reference')}
        view._sync_opacity_controls(dict(sheetOpacityPercent=62, referenceOpacityPercent=31))
        self.assertEqual((view.opacity_var.get(), view.reference_opacity_var.get()), (62, 31))
        view._opacity_changed('reference', '0')
        view._opacity_changed('reference', '100')
        self.assertEqual(view.opacity_values['reference'].values['text'], '100%')
        self.assertEqual(previews, [])
        view._opacity_key_released(SimpleNamespace(keysym='Left'))
        self.assertEqual(previews, [('reference', 100)])
        self.assertEqual(len(saves), 1)
        self.assertEqual(state['sheetOpacity'], .55)
        self.assertEqual(timers.pending, {})


if __name__ == '__main__': unittest.main()
