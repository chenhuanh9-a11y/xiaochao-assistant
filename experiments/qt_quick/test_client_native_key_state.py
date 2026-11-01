"""Pure key-state regression: no hook, OS key state, input, or window starts."""
import unittest
from client_native_keys import PassThroughKeyboardSource, initial_pressed_keys
from hotkey_bindings import Chord, modifiers_match

FAMILIES=((0x10,0xa0,0xa1,0x2a,0x36),(0x11,0xa2,0xa3,0x1d,0x1d),(0x12,0xa4,0xa5,0x38,0x38))

class InitialModifierStateTests(unittest.TestCase):
    def source(self, pressed):
        source=PassThroughKeyboardSource()
        source.update_keys([0x78,ord('G')])
        source._held=initial_pressed_keys(source._keys,lambda key:0x8000 if key in pressed else 0)
        return source

    def bare_allowed(self, held):
        pairs={0x10:(0x10,0xa0,0xa1),0x11:(0x11,0xa2,0xa3),0x12:(0x12,0xa4,0xa5)}
        return modifiers_match(Chord('F9',0x78),lambda key:0x8000 if any(k in held for k in pairs.get(key,(key,))) else 0)

    def test_held_at_start_then_physical_release_restores_bare_keys(self):
        for generic,left,right,left_scan,right_scan in FAMILIES:
            for side,scan in ((left,left_scan),(right,right_scan)):
                with self.subTest(generic=generic,side=side):
                    source=self.source({generic,side})
                    self.assertEqual(source.held,{side})
                    self.assertFalse(self.bare_allowed(source.held))
                    event=source._transition_event(side,scan,0,False,77)
                    self.assertEqual(event['held'],frozenset())
                    self.assertTrue(self.bare_allowed(event['held']))

    def test_releasing_one_physical_side_keeps_the_other_modifier_held(self):
        for generic,left,right,left_scan,right_scan in FAMILIES:
            with self.subTest(generic=generic):
                source=self.source({generic,left,right})
                event=source._transition_event(left,left_scan,0,False,77)
                self.assertEqual(event['held'],{right})
                self.assertFalse(self.bare_allowed(event['held']))
                event=source._transition_event(right,right_scan,0,False,77)
                self.assertTrue(self.bare_allowed(event['held']))

    def test_generic_and_side_callback_forms_share_state_and_preserve_raw_vk(self):
        for generic,left,right,left_scan,right_scan in FAMILIES:
            for side,scan,flags in ((left,left_scan,0),(right,right_scan,1)):
                with self.subTest(generic=generic,side=side):
                    source=self.source(set())
                    event=source._transition_event(generic,scan,flags,True,77)
                    self.assertEqual(event['vk'],generic)
                    self.assertEqual(event['held'],{side})
                    event=source._transition_event(side,scan,flags,False,77)
                    self.assertTrue(self.bare_allowed(event['held']))
                    source._transition_event(side,scan,flags,True,77)
                    event=source._transition_event(generic,scan,flags,False,77)
                    self.assertEqual(event['vk'],generic)
                    self.assertTrue(self.bare_allowed(event['held']))
                    source._transition_event(generic,scan,flags,True,77)
                    event=source._transition_event(generic,scan,flags,False,77)
                    self.assertTrue(self.bare_allowed(event['held']))

if __name__=='__main__':unittest.main()
