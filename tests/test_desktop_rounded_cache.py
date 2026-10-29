"""Real, withdrawn Tk regression tests; no game/window input or artificial PIL delay."""
import json
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk
import unittest
import types
from unittest.mock import patch

from PIL import Image

REPO = Path(__file__).parents[1]
sys.path.insert(0, str(REPO / "app"))


def load_revision(revision):
    source = subprocess.check_output(
        ["git", "-c", f"safe.directory={REPO}", "show", f"{revision}:app/desktop_rounded.py"],
        cwd=REPO, text=True, encoding="utf-8")
    module = types.ModuleType("rounded_reference")
    exec(compile(source, f"{revision}:app/desktop_rounded.py", "exec"), module.__dict__)
    return module


if __name__ == "__main__" and "--baseline" in sys.argv:
    index = sys.argv.index("--baseline")
    sys.modules["desktop_rounded"] = load_revision(sys.argv[index + 1])
    del sys.argv[index:index + 2]

from desktop_display import WindowDisplay
from desktop_rounded import (RoundedButton, RoundedFrame, RoundedEntry,
                             RoundedCombobox, set_rounded_scale)


class ResizeProbe:
    def __enter__(self):
        self.sizes = []
        original = Image.Image.resize
        def resize(image, size, *args, **kwargs):
            self.sizes.append((image.size, size))
            return original(image, size, *args, **kwargs)
        self.patch = patch.object(Image.Image, "resize", resize)
        self.patch.start()
        self.started = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.seconds = time.perf_counter() - self.started
        self.patch.stop()


class RoundedCacheTk(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Real Tk runtime unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_non_raster_properties_reuse_images(self):
        button = RoundedButton(self.root, text="Native label")
        skins = button._skins
        count = len(skins.images)
        expected_font = skins.font(("Arial", -19, "bold"))
        with ResizeProbe() as probe:
            button.configure(font=("Arial", -19, "bold"), padx=27, pady=13,
                             fg="#123456", activeforeground="#234567", anchor="w")
        self.assertEqual(probe.sizes, [])
        self.assertEqual(len(skins.images), count)
        style = button.cget("style")
        self.assertEqual(skins.style.lookup(style, "foreground"), "#123456")
        self.assertEqual(tuple(map(int, skins.style.lookup(style, "padding"))), (27, 13))
        self.assertEqual(button.cget("font"), expected_font)
        self.assertEqual(skins.font(skins.style.lookup(style, "font")), expected_font)
        self.assertEqual(button.cget("text"), "Native label")

    def test_same_scale_and_repeated_style_are_idle(self):
        button = RoundedButton(self.root)
        frame = RoundedFrame(self.root, shadow=10, padx=12)
        set_rounded_scale(self.root, 1.25)
        self.root.update_idletasks()
        original = ttk.Widget.configure
        style_writes = []
        def configure(widget, cnf=None, **kwargs):
            if "style" in kwargs or "padding" in kwargs:
                style_writes.append(kwargs)
            return original(widget, cnf, **kwargs)
        with ResizeProbe() as probe, patch.object(ttk.Widget, "configure", configure):
            for _ in range(20):
                set_rounded_scale(self.root, 1.25)
                button._restyle()
                frame._restyle()
        self.assertEqual(probe.sizes, [])
        self.assertEqual(style_writes, [])

    def test_window_display_metrics_do_not_rasterize_old_radius(self):
        button = RoundedButton(self.root, text="DPI")
        frame = RoundedFrame(self.root, shadow=10, padx=12, pady=9)
        button.pack()
        frame.pack()
        display = WindowDisplay(self.root, object(), preferred=(600, 400), minimum=(300, 200),
                                on_scale=lambda scale: set_rounded_scale(self.root, scale))
        # The real refresh order is apply_widgets(), then on_scale(). The root
        # remains withdrawn; bypass only monitor/geometry discovery, not these methods.
        display.apply_widgets()
        display.on_scale(1.0)
        display.scale = 1.25
        with ResizeProbe() as metrics:
            display.apply_widgets()
        self.assertEqual(metrics.sizes, [], "new native metrics must reuse old-radius rasters")
        with ResizeProbe() as changed:
            display.on_scale(display.scale)
        self.assertGreater(len(changed.sizes), 0, "a genuinely new radius must be rendered")
        self.assertEqual(tuple(map(int, frame.cget("padding"))), (27, 23))
        with ResizeProbe() as repeated:
            display.apply_widgets()
            display.on_scale(display.scale)
        self.assertEqual(repeated.sizes, [])

    def test_raster_changes_and_native_states_remain_distinct(self):
        button = RoundedButton(self.root)
        skins = button._skins
        first = button.cget("style")
        with ResizeProbe() as changed:
            button.configure(bg="#345678", activebackground="#456789",
                             highlightcolor="#56789a", radius=18)
        self.assertTrue(changed.sizes)
        self.assertNotEqual(button.cget("style"), first)
        button.state(["disabled"])
        self.assertTrue(button.instate(["disabled"]))
        button.state(["!disabled", "active", "focus"])
        self.assertTrue(button.instate(["active", "focus"]))
        entry = RoundedEntry(self.root)
        entry.insert(0, "editable")
        combo = RoundedCombobox(self.root, values=("a", "b"), state="readonly")
        combo.set("b")
        set_rounded_scale(self.root, 2.0)
        self.assertEqual(entry.get(), "editable")
        self.assertEqual(combo.get(), "b")
        self.assertEqual(str(combo.cget("state")), "readonly")
        self.assertIs(skins, entry._skins)

    def test_pixel_key_covers_colors_geometry_shadow_and_focus(self):
        skins = RoundedButton(self.root)._skins
        arguments = dict(fill="#345678", outline="#6789ab", parent="#abcdef",
                         radius=12, width=1, focus=False, button=True, shadow=0)
        original = skins.picture(**arguments)
        self.assertIs(skins.picture(**arguments), original)
        for name, value in (("fill", "#112233"), ("outline", "#223344"),
                            ("parent", "#334455"), ("radius", 18), ("width", 2),
                            ("focus", True), ("shadow", 10)):
            with self.subTest(parameter=name):
                self.assertIsNot(skins.picture(**dict(arguments, **{name: value})), original)
        self.assertIs(skins.picture(**dict(arguments, button=False)), original)
        focused = skins.picture(**dict(arguments, focus=True))
        self.assertIsNot(skins.picture(**dict(arguments, focus=True, button=False)), focused)

    def test_interpreters_and_toplevel_scales_are_independent(self):
        button = RoundedButton(self.root)
        second = tk.Tk()
        second.withdraw()
        try:
            other = RoundedButton(second)
            self.assertIsNot(button._skins, other._skins)
            self.assertIsNot(button._skins.images[0], other._skins.images[0])
        finally:
            second.destroy()
        child = tk.Toplevel(self.root)
        child.withdraw()
        inside = RoundedButton(child)
        style = inside.cget("style")
        set_rounded_scale(self.root, 1.5)
        self.assertEqual(inside.cget("style"), style)
        set_rounded_scale(child, 1.25)
        self.assertNotEqual(inside.cget("style"), style)

    def test_distinct_native_metrics_do_not_retain_duplicate_tk_images(self):
        button = RoundedButton(self.root)
        before = len(self.root.tk.call("image", "names"))
        with ResizeProbe() as probe:
            for padding in range(16, 80):
                button.configure(padx=padding, font=("Arial", 10 + padding % 3, "bold"))
        self.assertEqual(probe.sizes, [])
        self.assertEqual(len(self.root.tk.call("image", "names")), before)

    def test_restyle_observes_parent_theme_and_tcl_padding(self):
        parent = tk.Frame(self.root, bg="#112233")
        frame = RoundedFrame(parent, padx=5, pady=7, shadow=10)
        original = frame.cget("style")
        parent.configure(bg="#223344")
        frame._restyle()
        self.assertNotEqual(frame.cget("style"), original)
        original = frame.cget("style")
        current_theme = frame._skins.style.theme_use()
        themes = frame._skins.style.theme_names()
        alternative = next(name for name in themes if name != current_theme)
        frame._skins.style.theme_use(alternative)
        frame._restyle()
        self.assertNotEqual(frame.cget("style"), original)
        for value in (5, (5, 7), (5, 7, 5, 7), "2p"):
            frame.configure(padding=value)
            frame._restyle()
            self.assertEqual(tuple(map(int, frame.cget("padding"))), (15, 17))


def memory_snapshot(root):
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged",
                                               "peak_nonpaged", "nonpaged", "pagefile",
                                               "peak_pagefile", "private")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not query(wintypes.HANDLE(-1), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    skins = root._xiaochao_round_skins
    return dict(styles=len(skins.cache), images=len(skins.images),
                tk_images=len(root.tk.call("image", "names")),
                private_bytes=counters.private, working_set_bytes=counters.ws)


def probe_retention():
    root = tk.Tk()
    root.withdraw()
    try:
        button = RoundedButton(root, font=("Arial", 10, "bold"))
        frame = RoundedFrame(root, shadow=10, padx=12)
        display = WindowDisplay(root, object(), preferred=(600, 400), minimum=(300, 200),
                                on_scale=lambda scale: set_rounded_scale(root, scale))
        display.apply_widgets()
        display.on_scale(1.0)
        result = {"initial": memory_snapshot(root)}
        for round_index in range(3):
            with ResizeProbe() as measured:
                for _ in range(20):
                    display.apply_widgets()
                    display.on_scale(display.scale)
                    root.update()
            result[f"fixed_{(round_index + 1) * 20}"] = dict(
                memory_snapshot(root), resize_calls=len(measured.sizes))
        for round_index in range(3):
            with ResizeProbe() as measured:
                for _ in range(10):
                    for scale in (1.25, 1.5, 2.0, 1.0):
                        display.scale = scale
                        display.apply_widgets()
                        display.on_scale(scale)
                        root.update()
            result[f"dpi_roundtrips_{(round_index + 1) * 10}"] = dict(
                memory_snapshot(root), resize_calls=len(measured.sizes))
        for round_index in range(3):
            with ResizeProbe() as measured:
                for index in range(round_index * 32, (round_index + 1) * 32):
                    button.configure(padx=100 + index, font=("Arial", 10 + index % 4, "bold"))
                    frame.configure(padx=100 + index)
                    root.update()
            result[f"unique_metrics_{(round_index + 1) * 32}"] = dict(
                memory_snapshot(root), resize_calls=len(measured.sizes))
        print(json.dumps(result, indent=2))
    finally:
        root.destroy()


def compare_pixels(revision):
    from desktop_rounded import _Skins
    from PIL import ImageTk
    reference = load_revision(revision)
    root = tk.Tk()
    root.withdraw()
    try:
        old, new = reference._Skins(root), _Skins(root)
        count = 0
        for radius in (10, 12, 15, 18, 24):
            for width in (0, 1, 2):
                for shadow in (0, 10, 15):
                    for focus, button in ((False, False), (False, True), (True, False), (True, True)):
                        arguments = dict(fill="#345678", outline="#6789ab", parent="#abcdef",
                                         radius=radius, width=width, shadow=shadow,
                                         focus=focus, button=button)
                        before = ImageTk.getimage(old.picture(**arguments))
                        after = ImageTk.getimage(new.picture(**arguments))
                        assert (before.size == after.size and before.mode == after.mode
                                and before.tobytes() == after.tobytes()), arguments
                        count += 1
        print(json.dumps(dict(pixel_identical_cases=count, baseline_images=len(old.images),
                              candidate_images=len(new.images))))
    finally:
        root.destroy()


def probe_refresh_work():
    """Report cold/warm costs on the real UI thread, with an after(1) heartbeat."""
    root = tk.Tk()
    root.withdraw()
    report = {}
    try:
        with ResizeProbe() as cold:
            buttons = [RoundedButton(root, text=f"Button {index}", padx=15 + index,
                                     font=("Arial", -13 - index % 3, "bold"))
                       for index in range(24)]
            frames = [RoundedFrame(root, shadow=10, padx=12 + index) for index in range(8)]
        report["cold"] = dict(ms=round(cold.seconds * 1000, 3), resize_calls=len(cold.sizes),
                              sizes=sorted(set(str(pair) for pair in cold.sizes)))
        display = WindowDisplay(root, object(), preferred=(600, 400), minimum=(300, 200),
                                on_scale=lambda scale: set_rounded_scale(root, scale))
        display.apply_widgets()
        display.on_scale(1.0)
        stages = [("same_scale", lambda: [set_rounded_scale(root, 1.0) for _ in range(20)]),
                  ("dpi_metrics", display.apply_widgets),
                  ("dpi_radius", lambda: display.on_scale(display.scale)),
                  ("warm_metrics", display.apply_widgets),
                  ("warm_radius", lambda: display.on_scale(display.scale))]
        display.scale = 1.25
        for name, action in stages:
            root.update()
            beat = []
            start = time.perf_counter()
            root.after(1, lambda: beat.append((time.perf_counter() - start) * 1000))
            with ResizeProbe() as measurement:
                action()
            while not beat:
                root.update()
            report[name] = dict(ms=round(measurement.seconds * 1000, 3),
                                heartbeat_ms=round(beat[0], 3),
                                resize_calls=len(measurement.sizes),
                                styles=len(root._xiaochao_round_skins.cache),
                                images=len(root._xiaochao_round_skins.images))
        print(json.dumps(report, indent=2))
    finally:
        root.destroy()


if __name__ == "__main__":
    if "--compare-ref" in sys.argv:
        compare_pixels(sys.argv[sys.argv.index("--compare-ref") + 1])
    elif "--stress" in sys.argv:
        probe_retention()
    elif "--probe" in sys.argv:
        probe_refresh_work()
    else:
        unittest.main()
