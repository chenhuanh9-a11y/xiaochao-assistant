"""Private approved-resource parity; two legacy floors, no UI or rank claims."""
from pathlib import Path
import unittest

import numpy as np

from client_providers import SharedReferenceProvider
from difficult_native_context import DifficultNativeContext
from difficult_native_identity import NativeDifficultPack


REPO = Path(__file__).resolve().parents[2]
ROOTS = tuple(REPO / 'work' / name / '_internal'
              for name in ('qt-editor-base-next', 'qt-lab-installation'))




class Map29BaselineTests(unittest.TestCase):
    @unittest.skipUnless(all((root / 'difficult-native-manifest.json').is_file()
                             for root in ROOTS), 'approved private resources absent')
    def test_same_legacy_reference_and_explicit_native_gap_in_both_roots(self):
        previous = {}
        for root in ROOTS:
            native = DifficultNativeContext(root)
            pack = NativeDifficultPack(root, native.interfaces['identityPack'],
                                       display_context=native)
            # Checks complete declared inventory, decoding only the two legacy NPZs.
            native.validate_identity_pack(pack)
            self.assertEqual(len(pack.maps), 30)
            self.assertEqual(set(pack.by_id['difficult-map-29'].layers), {'1', '2'})
            provider = SharedReferenceProvider(root, native.resources, {},
                legacy_reference_loader=native.legacy_reference)
            for floor in ('1', '2'):
                with self.subTest(root=root.name, floor=floor):
                    qt = provider('shared-structure', 'difficult-map-29', floor)
                    native_reference = native.legacy_reference('difficult-map-29', floor)
                    self.assertIsNotNone(qt)
                    self.assertIs(qt, native_reference)
                    self.assertTrue(np.any(qt.clean_mask))
                    self.assertIsNone(qt.metadata['sourceLayout'])
                    self.assertEqual(qt.metadata['sourceClass'], 'legacy-clean-working-draft')
                    self.assertIsNone(native.legacy_route_to_native('difficult-map-29', floor))
                    self.assertIn(dict(mapId='difficult-map-29', floor=floor,
                        reason='missing reliable game-layout binding'), native.unbound_layers)
                    binding = provider.binding_metadata('shared-structure', 'difficult-map-29', floor)
                    self.assertEqual(binding['metadata'], native_reference.metadata)
                    self.assertEqual(binding['shape'], list(native_reference.shape))
                    if floor in previous:
                        old = previous[floor]
                        np.testing.assert_array_equal(qt.clean_mask, old.clean_mask)
                        np.testing.assert_array_equal(qt.validation_mask, old.validation_mask)
                        self.assertEqual(qt.metadata, old.metadata)
                    previous[floor] = qt


if __name__ == '__main__':
    unittest.main()
