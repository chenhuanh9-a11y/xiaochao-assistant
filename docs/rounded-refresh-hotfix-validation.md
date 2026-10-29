# Rounded skin refresh validation — 2026-10-29

Baseline: `70ce63ffc05f5eeeef42c901e08bfcad91b002cf`. Its
`app/desktop_rounded.py` is identical to release source
`090b56018be26170cb36e768fdad1866c6a515ca`. This change only touches that production
file; the other additions are the targeted test/probes and this record.

The existing exact-scale guard and complete-style cache already avoid image work
on unchanged refreshes. The verified gap is that font, text color and padding
participate in the complete-style key, so distinct native metrics create duplicate
border images. `WindowDisplay.refresh` applies new native metrics before calling
`on_scale`; that order can also rasterize the old radius before applying the new
one. This patch shares images by their actual drawing inputs, within one Tk
interpreter, and avoids reapplying an unchanged native style/frame padding.

## Real withdrawn Tk evidence

Python 3.13, Tk 8.6.15, Pillow 12.3.0 on Windows. Only self-created withdrawn Tk
windows were used. No game input, installed-app interaction, artificial sleep,
slow-PIL injection, background Tk calls, animation disablement, build or install.

The probe creates 24 buttons with different native metrics and eight shadow cards.
It calls the production `WindowDisplay.apply_widgets()` and `on_scale()` in their
real order. Monitor discovery, visible-window geometry and the 350 ms application
refresh timer are **not** exercised by this isolated probe. The tested transition
is 100% to 125%, where border thickness stays unchanged while radius changes.

One measured run (milliseconds; diagnostic samples, not p95/game acceptance):

| Stage | Before resize calls / duration | After resize calls / duration | Before / after heartbeat delay |
| --- | ---: | ---: | ---: |
| Cold construction | 160 / 659.245 | 8 / 124.641 | not sampled |
| 20 identical scale calls | 0 / 0.007 | 0 / 0.007 | 1.046 / 1.056 |
| DPI native metrics, old radius | 152 / 553.571 | 0 / 24.635 | 553.670 / 24.727 |
| DPI new radius | 160 / 579.826 | 8 / 40.517 | 579.921 / 40.574 |
| Warm native metrics | 0 / 2.363 | 0 / 2.797 | 2.481 / 2.880 |

Retained images after the transition: **452 before, 14 after**. Initial source
rasters were button `592×592 → 148×148`, shadow-card mask `172×172 → 688×688`,
and card `688×688 → 172×172`; these are border tiles, not desktop-sized images.

## Retention probe

Separate self-process runs use one button and one card. No forced GC or cache
clearing. Repeated fixed metrics (60 rounds) produce zero new images in both
versions. Four-scale roundtrips (100%, 125%, 150%, 200%; 30 rounds) stabilize after
warmup in both versions. Private bytes are process measurements, including
allocator noise, and are not an assertion of leak-free behavior.

| Phase | Before skins / Tk images / private bytes | After skins / Tk images / private bytes |
| --- | ---: | ---: |
| 10 DPI roundtrips | 56 / 60 / 39,256,064 | 38 / 42 / 35,151,872 |
| 30 DPI roundtrips | 56 / 60 / 39,256,064 | 38 / 42 / 35,151,872 |
| 32 distinct native metrics | 280 / 284 / 76,865,536 | 38 / 42 / 35,151,872 |
| 64 distinct native metrics | 504 / 508 / 114,597,888 | 38 / 42 / 35,151,872 |
| 96 distinct native metrics | 728 / 732 / 153,821,184 | 38 / 42 / 35,151,872 |

Style metadata counts still grow `16 → 80 → 144 → 208` in both versions for
arbitrarily unique native metrics. Genuinely unique raster parameters also remain
cached for the interpreter lifetime. This is duplicate-raster removal, **not** a
globally bounded style/image cache. It establishes one avoidable retention source;
it does not establish the cause of all reported freezes or doubled application
memory during long game sessions.

## Verification and reproduction

Eight new real-Tk tests passed without skips. They cover native font/padding/text,
states and editable values, same-style no-op, DPI metrics/radius separation,
rendering-key changes, interpreter/toplevel isolation, unique native metrics,
parent background/theme changes, and Tcl padding forms (one/two/four values and
physical units). Existing desktop animation tests and display-context tests also
passed: **24 targeted tests total**. The original implementation was observed red
for duplicate rasters and repeated native style writes before the patch.

Pixel comparison against the baseline checks all RGBA bytes, dimensions and mode
for 180 radius/stroke/shadow/focus/button combinations: **180 identical**. Native
font normalization and the opaque RGB tile's alpha behavior are unchanged.

Run from the repository with a Python that can access the installed Tcl/Tk runtime:

```powershell
python -m unittest discover -s tests -p 'test_desktop_*.py'
python -m unittest discover -s tests -p 'test_display_context_snapshot.py'
python tests/test_desktop_rounded_cache.py --probe
python tests/test_desktop_rounded_cache.py --stress
python tests/test_desktop_rounded_cache.py --baseline 70ce63ffc05f5eeeef42c901e08bfcad91b002cf --probe
python tests/test_desktop_rounded_cache.py --baseline 70ce63ffc05f5eeeef42c901e08bfcad91b002cf --stress
python tests/test_desktop_rounded_cache.py --compare-ref 70ce63ffc05f5eeeef42c901e08bfcad91b002cf
```

Actual interpreter used:
`E:/codex/worktrees/candidate10-difficult-integration-prep/加页手记小抄/work/venv-opencv412/Scripts/python.exe`.
The restricted shell cannot read this installation's Tcl `init.tcl`; those runs
skip real-Tk tests and do not count as verification. The reported runs used the
approved elevated execution context, with no `TCL_LIBRARY`/`TK_LIBRARY` override.

Visible UI layout, actual game timing and long-session whole-app memory remain
unverified. No 1.0.4 artifact was changed, rebuilt, installed or published here.
