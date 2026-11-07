"""Run inherited synthetic IO contracts through S1's four-floor capability.

The original fixtures advertised only B1/B2. This compatibility wrapper keeps
the current B1/B2/B3/B4 capability explicit; it does not loosen production admission.
"""
import unittest
import test_s1_async_evidence as asynchronous
import test_s1_native_candidate_preview as native


class FourFloorNativeContracts(native.NativeCandidateContracts):
    def setUp(self):
        super().setUp()
        self.registry.capability['observedFloors'] = (-1, -2, -3, -4)


class FourFloorAsyncContracts(asynchronous.AsyncEvidenceContracts):
    def setUp(self):
        super().setUp()
        self.registry.capability['observedFloors'] = (-1, -2, -3, -4)


if __name__ == '__main__':
    unittest.main()
