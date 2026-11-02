"""Reviewer LL packet regressions with an independent physical-key oracle.

Derived from the externally supplied 11-case review script. No OS hooks are
registered: physical levels and lost delivery are explicit fixture inputs,
not observations of real hardware or the Windows secure desktop.
"""
import ctypes, time, unittest
from types import SimpleNamespace
from unittest.mock import patch
from client_native_keys import NativeKey
import test_client_shortcut_delivery as fx

KD, KU, SKD, SKU = 0x100, 0x101, 0x104, 0x105
EXT, ALTDN, UP = 0x01, 0x20, 0x80
# (vk, scan, extended)
LSH, RSH = (0xA0, 0x2A, 0), (0xA1, 0x36, 0)
LCT, RCT = (0xA2, 0x1D, 0), (0xA3, 0x1D, EXT)
LAL, RAL = (0xA4, 0x38, 0), (0xA5, 0x38, EXT)

class R(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fx.ShortcutDeliveryTests.setUpClass(); cls.app = fx.ShortcutDeliveryTests.app
    def setUp(self):
        fx.ShortcutDeliveryTests.setUp(self)
        self.physical = set()
        self.query_error = False
        self.source._user = SimpleNamespace(GetAsyncKeyState=self.async_state)

    def async_state(self, vk):
        if self.query_error:
            raise OSError("fixture: physical state unavailable")
        return 0x8000 if vk in self.physical else 0
    configure = fx.ShortcutDeliveryTests.configure
    mouse = fx.ShortcutDeliveryTests.mouse
    def send(self, msg, vk, scan, flags):
        p = NativeKey(vk, scan, flags, 0, 0)
        self.source._receive_key_message(msg, ctypes.addressof(p), self.foreground)
        # WH_KEYBOARD_LL runs before the current key's async state updates.
        # This set is independent of the source's _held / _tap_pressed caches.
        if msg in (KD, SKD):
            self.physical.add(vk)
        else:
            self.physical.discard(vk)
        self.app.processEvents()
    def down(self, k, alt_held=False, repeat=0):
        vk, scan, ext = k
        isalt = vk in (0xA4, 0xA5)
        msg = SKD if (isalt or alt_held) else KD
        fl = ext | (ALTDN if (isalt or alt_held) else 0)
        for _ in range(1 + repeat): self.send(msg, vk, scan, fl)
    def up(self, k, alt_held=False):
        vk, scan, ext = k
        # Real LL hook: releasing Alt itself arrives as WM_KEYUP (Alt no longer down).
        msg = SKU if alt_held else KU
        self.send(msg, vk, scan, ext | UP | (ALTDN if alt_held else 0))
    def n(self): return len(self.maps)

    def test_01_solo_tap_each_side_fires_once_on_release(self):
        for name, ks in (("SHIFT", (LSH, RSH)), ("CTRL", (LCT, RCT)), ("ALT", (LAL, RAL))):
            self.configure({"map": name})
            for k in ks:
                c = self.n(); self.down(k)
                self.assertEqual(self.n(), c, (name, hex(k[0]), "fired on press"))
                self.up(k)
                self.assertEqual(self.n(), c + 1, (name, hex(k[0])))

    def test_02_autorepeat_then_release_fires_exactly_once(self):
        for name, k in (("SHIFT", LSH), ("CTRL", RCT), ("ALT", LAL)):
            self.configure({"map": name}); c = self.n()
            self.down(k, repeat=20)
            self.assertEqual(self.n(), c)
            self.up(k); self.assertEqual(self.n(), c + 1, name)

    def test_03_combos_do_not_fire(self):
        cases = (("CTRL", LCT, (0x43, 0x2E, 0), False),   # Ctrl+C
                 ("CTRL", RCT, (0x56, 0x2F, 0), False),   # RCtrl+V
                 ("SHIFT", LSH, (0x41, 0x1E, 0), False),  # Shift+A
                 ("ALT", LAL, (0x09, 0x0F, 0), True),     # Alt+Tab (SYSKEY)
                 ("ALT", LAL, (0x73, 0x3E, 0), True),     # Alt+F4
                 ("CTRL", LCT, LSH, False),               # Ctrl+Shift
                 ("SHIFT", RSH, (0x5B, 0x5B, EXT), False))# Shift+Win
        for name, mod, other, alt in cases:
            self.configure({"map": name}); c = self.n()
            self.down(mod); self.down(other, alt_held=alt); self.up(other, alt_held=alt); self.up(mod)
            self.assertEqual(self.n(), c, (name, hex(other[0])))
            self.down(mod); self.up(mod)
            self.assertEqual(self.n(), c + 1, ("fresh tap after combo", name))

    def test_04_combo_other_key_released_after_modifier(self):
        self.configure({"map": "CTRL"}); c = self.n()
        self.down(LCT); self.down((0x43, 0x2E, 0)); self.up(LCT); self.up((0x43, 0x2E, 0))
        self.assertEqual(self.n(), c)

    def test_05_altgr_does_not_fire_ctrl_or_alt(self):
        fake_lctrl = (0xA2, 0x21D, 0)
        for name in ("CTRL", "ALT"):
            self.configure({"map": name}); c = self.n()
            self.down(fake_lctrl); self.send(SKD, 0xA5, 0x38, EXT | ALTDN)
            self.send(KU, 0xA2, 0x21D, UP | ALTDN); self.send(KU, 0xA5, 0x38, EXT | UP)
            self.assertEqual(self.n(), c, ("AltGr", name))
            d = LCT if name == "CTRL" else LAL
            self.down(d); self.up(d); self.assertEqual(self.n(), c + 1)

    def test_06_left_and_right_overlap(self):
        self.configure({"map": "SHIFT"}); c = self.n()
        self.down(LSH); self.down(RSH); self.up(LSH); self.up(RSH)
        self.assertEqual(self.n(), c)

    def test_07_binding_letter_unaffected_while_modifier_bound(self):
        self.configure({"map": "CTRL", "candidate-prev": "K"})
        self.down((0x4B, 0x25, 0)); self.up((0x4B, 0x25, 0))
        self.assertEqual(self.actions, ["candidate-prev"])
        self.assertEqual(self.n(), 0)

    def test_08_lost_keyup_secure_desktop_blocks_later_taps(self):
        # Ctrl+Alt+Del: secure desktop swallows all three key-ups from the LL hook.
        self.configure({"map": "SHIFT"}); c = self.n()
        self.down(LCT); self.down(LAL); self.send(SKD, 0x2E, 0x53, EXT | ALTDN)
        self.physical.clear()  # released on secure desktop; NO hook key-ups
        # back on the normal desktop, the user taps Shift several times
        for _ in range(3):
            self.down(LSH); self.up(LSH)
        print("\n[08] taps after lost key-ups:", self.n() - c, "tap_pressed:", sorted(hex(x) for x in self.source._tap_pressed))
        self.assertEqual(self.n(), c + 3, "solo Shift blocked by stale key-down state")

    def test_08b_still_physically_held_keys_block_solo_taps(self):
        self.configure({"map": "SHIFT"}); c = self.n()
        self.down(LCT); self.down(LAL)
        self.send(SKD, 0x2E, 0x53, EXT | ALTDN)
        self.assertEqual(self.async_state(LCT[0]), 0x8000)
        for _ in range(3):
            self.down(LSH); self.up(LSH)
        self.assertEqual(self.n(), c, "held combination must not be recovered away")
        self.physical.discard(LCT[0])  # mixed snapshot: Ctrl released, Alt/Del held
        self.assertEqual(self.async_state(LCT[0]), 0)
        self.assertEqual(self.async_state(LAL[0]), 0x8000)
        self.down(LSH); self.up(LSH)
        self.assertEqual(self.n(), c, "one released key must not clear other held keys")
        self.physical.clear()
        self.assertEqual(self.async_state(LAL[0]), 0)
        self.down(LSH); self.up(LSH)
        self.assertEqual(self.n(), c + 1)

    def test_08c_unknown_physical_state_remains_a_blocker(self):
        self.configure({"map": "SHIFT"}); c = self.n()
        self.down(LCT); self.down(LAL)
        self.send(SKD, 0x2E, 0x53, EXT | ALTDN)
        self.physical.clear()
        self.query_error = True
        for _ in range(3):
            self.down(LSH); self.up(LSH)
        self.assertEqual(self.n(), c, "query failure must not mean released")
        self.query_error = False
        self.down(LSH); self.up(LSH)
        self.assertEqual(self.n(), c + 1)

    def test_09_lost_keyup_same_modifier(self):
        # Win+L style: bound modifier itself loses its key-up.
        self.configure({"map": "CTRL"}); c = self.n()
        self.down(LCT)  # key-up lost
        self.physical.clear()  # physical release without hook delivery
        for _ in range(3):
            self.down(LCT); self.up(LCT)
        print("\n[09] taps:", self.n() - c)
        self.assertGreaterEqual(self.n(), c + 2)

    def test_10_long_hold_does_not_fire(self):
        self.configure({"map": "ALT"}); c = self.n()
        old = time.monotonic() - 30
        with patch("client_native_keys.time", SimpleNamespace(monotonic=lambda: old)):
            self.down(LAL)
        self.down(LAL, repeat=5)  # fresh repeats
        self.up(LAL)
        print("\n[10] 30s hold then release fired:", self.n() - c)
        self.assertEqual(self.n(), c)

    def test_11_injected_modifier_tap(self):
        self.configure({"map": "CTRL"}); c = self.n()
        self.send(KD, 0xA2, 0x1D, 0x10); self.send(KU, 0xA2, 0x1D, 0x10 | UP)
        print("\n[11] injected (LLKHF_INJECTED) tap fired:", self.n() - c)
        self.assertEqual(self.n(), c + 1)

if __name__ == "__main__": unittest.main(verbosity=2)
