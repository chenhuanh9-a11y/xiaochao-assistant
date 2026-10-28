"""Production-method lifecycle tests; no GUI, pixels or latency claims."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).parents[1] / 'app/identityv_daily_assistant.py'

def method(name):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(SOURCE), 'exec'), ns)
    return ns[name]

class Lifecycle(unittest.TestCase):
    def test_cancellation_never_records_geometry_failure_in_either_mode(self):
        call = method('_alignment_v2_note_worker_failure')
        for mode in ('nightmare', 'difficult'):
            for field in ('fit', 'alignmentV2WorkerFit'):
                with self.subTest(mode=mode, field=field):
                    old = {'reason': 'previous-real-failure'}
                    s = SimpleNamespace(game_mode=mode, _alignment_v2_retry_mode=lambda _: True,
                        _alignment_v2_worker_failure_state=old)
                    call(s, {field: {'success':False, 'reason':'structure-work-obsolete'},
                        'workerToken':(4,'map','1',0,0,1), 'mapId':'map','floor':'1'})
                    self.assertIs(s._alignment_v2_worker_failure_state, old)

    def test_real_geometry_failure_still_records_in_both_modes(self):
        call = method('_alignment_v2_note_worker_failure')
        for mode in ('nightmare', 'difficult'):
            s = SimpleNamespace(game_mode=mode, _alignment_v2_retry_mode=lambda _:True)
            call(s, {'fit': {'success':False,'reason':'geometry-miss'},
                'workerToken':(4,'map','1',0,0,1),'mapId':'map','floor':'1','sourceFrameGeneration':9})
            self.assertEqual(s._alignment_v2_worker_failure_state['frameGeneration'],9)

    def test_obsolete_delivery_requeues_only_current_open_lifecycle(self):
        call = method('_run_alignment_v2_for_current_frame_unlocked')
        for mode in ('nightmare','difficult'):
            for current in (True,False):
                with self.subTest(mode=mode,current=current):
                    s=SimpleNamespace(game_mode=mode, alignment_core_v2=object(),
                        _alignment_v2_runtime_is_enabled=lambda:True,
                        _shared_structure_mode_enabled=lambda:False,
                        active_map_cycle_id=4,_alignment_v2_reset_generation=1,
                        gate=SimpleNamespace(confirmed_id='map'),current_floor='1',
                        route_profile_id='shared-structure',map_is_open=current,map_visual_suppressed=not current,
                        _alignment_v2_requeue_latest_pose_recovery=Mock(),_log_core_chain_event=Mock(),
                        _current_frame_floor_conflicts=Mock(side_effect=AssertionError('cancelled work entered geometry')),
                        _alignment_v2_current_frame_candidate=Mock(side_effect=AssertionError('cancelled work entered candidate cache')))
                    event={'alignmentV2WorkerFit':{'success':False,'reason':'structure-work-obsolete'},
                        'workerToken':(4,'map','1',0,0,1),'routeProfileId':'shared-structure'}
                    result=call(s,'map','1','1',event)
                    self.assertEqual(result['reason'],'structure-work-obsolete')
                    self.assertEqual(s._alignment_v2_requeue_latest_pose_recovery.call_count,int(current))

if __name__=='__main__':unittest.main()
