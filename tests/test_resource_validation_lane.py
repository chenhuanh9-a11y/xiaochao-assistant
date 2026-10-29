import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from resource_validation_lane import ResourceValidationLane


def wait_result(lane,key):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        result=lane.take(key)
        if result is not None:return result
        time.sleep(.001)
    raise AssertionError('no result')


class ValidationLaneTests(unittest.TestCase):
    def test_churn_is_bounded_and_cancel_does_not_spawn(self):
        lane=ResourceValidationLane();entered=threading.Event();release=threading.Event();seen=[]
        def blocked():
            seen.append('first');entered.set();release.wait(3)
        lane.submit(('first',),blocked);self.assertTrue(entered.wait(2))
        try:
            for n in range(100):
                lane.submit((n,),lambda n=n:seen.append(n))
                self.assertEqual(lane.snapshot(),dict(running=True,pending=1,results=0))
            lane.cancel()
            lane.submit(('final',),lambda:seen.append('final'))
            self.assertEqual(seen,['first'])
        finally:release.set()
        self.assertTrue(wait_result(lane,('final',))['valid'])
        self.assertEqual(seen,['first','final'])
        self.assertIsNone(lane.take(('first',)))

    def test_same_key_after_cancel_is_distinct_and_recheck_after_take(self):
        lane=ResourceValidationLane();entered=threading.Event();release=threading.Event()
        def old():entered.set();release.wait(3);return False
        lane.submit(('key',),old);self.assertTrue(entered.wait(2))
        self.assertFalse(lane.submit(('key',),lambda:True))
        lane.cancel();lane.submit(('key',),lambda:True);release.set()
        self.assertTrue(wait_result(lane,('key',))['valid'])
        self.assertTrue(lane.submit(('key',),lambda:False))
        self.assertFalse(wait_result(lane,('key',))['valid'])

    def test_exception_and_start_failure_recover(self):
        lane=ResourceValidationLane()
        with patch.object(threading.Thread,'start',side_effect=RuntimeError('no thread')):
            lane.submit('x',lambda:True)
        result=wait_result(lane,'x')
        self.assertFalse(result['valid']);self.assertIn('no thread',result['error'])
        self.assertFalse(lane.snapshot()['running'])
        def fail():raise ValueError('invalid binding')
        lane.submit('x',fail)
        self.assertIn('invalid binding',wait_result(lane,'x')['error'])
        lane.submit('x',lambda:None)
        self.assertTrue(wait_result(lane,'x')['valid'])

if __name__=='__main__':unittest.main()
