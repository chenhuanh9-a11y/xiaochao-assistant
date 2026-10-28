import queue
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from reference_position_editor import ReferencePointerMailbox, ReferencePositionEdit


class ReferencePositionStateTests(unittest.TestCase):
    def test_release_keeps_editing_for_multiple_drags(self):
        edit = ReferencePositionEdit(1, ('map', '1'), (100, 100), (200, 300))
        edit.pointer('down', 110, 115)
        edit.place(edit.pointer('up', 150, 165))
        self.assertEqual(edit.position, (140, 150))
        self.assertTrue(edit.active)
        edit.pointer('down', 145, 155)
        edit.place(edit.pointer('up', 205, 215))
        self.assertEqual(edit.position, (200, 210))
        self.assertTrue(edit.active)

    def test_native_thread_cannot_mutate_editor_state(self):
        edit = ReferencePositionEdit(1, (), (0, 0), (100, 100))
        errors = []
        def mutate():
            try: edit.pointer('down', 1, 2)
            except RuntimeError as error: errors.append(str(error))
        thread = threading.Thread(target=mutate); thread.start(); thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIsNone(edit.offset)

    def test_fast_down_move_up_share_one_scalar_queue_marker(self):
        target = queue.Queue(); box = ReferencePointerMailbox(7, 2**40+9, target)
        box.post('down', -20, 3)
        for x in range(100): box.post('move', x, x)
        box.post('up', 105, 106)
        self.assertEqual(target.qsize(), 1)
        self.assertEqual(target.get(), dict(kind='reference-pointer', generation=7, hwnd=2**40+9))
        self.assertEqual(box.take(), [('down', -20, 3), ('move', 99, 99), ('up', 105, 106)])

    def test_close_discards_pending_and_late_native_events(self):
        target = queue.Queue(); box = ReferencePointerMailbox(1, 99, target)
        box.post('down', 1, 2); box.close()
        self.assertFalse(box.post('up', 50, 60))
        self.assertEqual(box.take(), [])

    def test_queue_overflow_cancels_instead_of_losing_critical_up(self):
        box = ReferencePointerMailbox(1, 99, queue.Queue())
        for i in range(64): box.post('down', i, i)
        self.assertFalse(box.post('up', 80, 80))
        self.assertEqual(box.take(), [('cancel', 0, 0)])


if __name__ == '__main__': unittest.main()
