"""Pure timing metadata; no GUI/process-start/visible-pixel claims."""
import unittest
from qt_startup_state import StartupState, LABELS


class StartupStateTests(unittest.TestCase):
    def test_known_stages_once_with_monotonic_elapsed_and_detached_rows(self):
        now = [10.]
        state = StartupState(lambda: now[0])
        now[0] += .5
        state.mark("import-client")
        state.mark("import-client")
        rows = state.snapshot()
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[-1]["elapsedMs"], 500)
        rows[-1]["stage"] = "mutated"
        self.assertEqual(state.label(), LABELS["import-client"])
        self.assertEqual(state.snapshot()[-1]["stage"], "import-client")

    def test_arbitrary_stage_is_rejected_and_storage_is_bounded(self):
        state = StartupState()
        with self.assertRaises(ValueError):
            state.mark("C:/private-path")
        for _ in range(100):
            for stage in LABELS:
                state.mark(stage)
        self.assertLessEqual(len(state.snapshot()), 16)

    def test_only_actual_early_hook_changes_origin(self):
        state = StartupState()
        self.assertEqual(state.origin, 'python-module-import')
        state.runtime_hook()
        self.assertEqual(state.origin, 'python-runtime-hook')
        late = StartupState()
        late.mark('create-services')
        late.runtime_hook()
        self.assertEqual(late.origin, 'python-module-import')
