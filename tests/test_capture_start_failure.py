"""Execute the production scheduling/rollback tail with injected OS start failure."""
import ast
import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

class CaptureStartTests(unittest.TestCase):
    def run_case(self, silent, stale=False, after_failure=False):
        path=Path(__file__).parents[1]/'app/identityv_daily_assistant.py'
        tree=ast.parse(path.read_text('utf-8-sig'))
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_begin_capture')
        first=next(i for i,n in enumerate(method.body) if isinstance(n,ast.FunctionDef) and n.name=='start_failed')
        method.body=method.body[first:]
        ns=dict(threading=threading,token=3,action_token='test',capture_trace={})
        lock=threading.Lock();lock.acquire();ns['capture_lock']=lock
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),str(path),'exec'),ns)
        root=Mock()
        s=SimpleNamespace(_capture_request_lock=lock,events=queue.Queue(),capture_generation=3,
                          game_hwnd=1,_capture_and_rank=Mock(),_ui_action_fail=Mock(),_log=Mock(),
                          _report_control_action_result=Mock(),root=root,CAPTURE_START_DELAY_MS=10,
                          _sync_game_control_view=Mock())
        if after_failure: root.after.side_effect=RuntimeError('after unavailable')
        with patch.object(threading.Thread,'start',side_effect=RuntimeError('start unavailable')):
            result=ns['_begin_capture'](s,False,silent_hint=silent)
            if not silent and not after_failure:
                self.assertTrue(result)
                s._report_control_action_result.assert_not_called()
                if stale:s.capture_generation=4
                self.assertFalse(root.after.call_args.args[1]())
            else:self.assertFalse(result)
        self.assertFalse(lock.locked())
        s._report_control_action_result.assert_not_called()
        if stale:
            s._ui_action_fail.assert_not_called();self.assertTrue(s.events.empty())
        else:
            self.assertEqual([s.events.get()['kind'],s.events.get()['kind']],['capture-error','capture-finished'])
            s._ui_action_fail.assert_called_once()

    def test_immediate_failure(self):self.run_case(True)
    def test_delayed_failure(self):self.run_case(False)
    def test_stale_delayed_callback(self):self.run_case(False,stale=True)
    def test_after_registration_failure(self):self.run_case(False,after_failure=True)

class SilentCaptureErrorTests(unittest.TestCase):
    """A failed map-key wake check keeps the UI still but reports the failure."""
    def silent_branch(self):
        path=Path(__file__).parents[1]/'app/identityv_daily_assistant.py'
        tree=ast.parse(path.read_text('utf-8-sig'))
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and isinstance(n.test,ast.Call)
                    and ast.unparse(n.test)=="event.get('silentHint')"
                    and any(isinstance(m,ast.Expr) and 'map-key-visual-check-error' in ast.unparse(m) for m in n.body))
        loop=ast.For(target=ast.Name('_',ast.Store()),iter=ast.List([ast.Constant(0)],ast.Load()),body=branch.body,orelse=[])
        args=ast.arguments(posonlyargs=[],args=[ast.arg('self'),ast.arg('event')],kwonlyargs=[],kw_defaults=[],defaults=[])
        func=ast.FunctionDef('silent',args,[loop],[],None,type_params=[])
        ns={};exec(compile(ast.fix_missing_locations(ast.Module([func],[])),str(path),'exec'),ns)
        return ns['silent']

    def test_failure_is_recorded_and_shown_without_touching_the_route(self):
        s=SimpleNamespace(active_map_cycle_id=5,_log=Mock(),_record_route_issue=Mock(),_set_status=Mock(),
                          hide_route=Mock(),set_expanded=Mock())
        self.silent_branch()(s,dict(kind='capture-error',message='截图失败',silentHint=True))
        self.assertIsNone(s.active_map_cycle_id)
        s._record_route_issue.assert_called_once_with('recognition','capture-failed','识别失败：截图失败')
        s._set_status.assert_called_once_with('识别失败：截图失败','#ff8a65')
        s.hide_route.assert_not_called();s.set_expanded.assert_not_called()

if __name__=='__main__':unittest.main()
