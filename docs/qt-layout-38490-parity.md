# Qt layout source and icon provenance

The pre-upgrade test fingerprint `38490F4F` maps to commit
`3438365b4cef003f76d08a001f772c888156bd03` in the existing
`D:/CodexBuilds/star-ui-sync-3438365b-20261030/evidence/build-process.json`.
The current Tk `desktop_main_ui.py`, `desktop_theme.py`, `desktop_lucide.py`
and `star_brand.py` are unchanged from that commit.

Sakitams' merged [PR 2](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/2)
introduced the desktop visual structure (`d0a3a0a`, merge `e0f95de`).
[PR 14](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/14)
restored the narrow rail and home proportions (merge `6bc321f`). Both are
ancestors of `3438365b`. PR 12 is the custom-map feature, and PR 61 is the
later map-resource integration; neither is the original desktop redesign.

The Qt correction uses `_layout_shell` and `_layout_home` geometry: 80/96 DIP
navigation rail, 28/48 DIP workspace insets, 720 DIP two-column breakpoint,
26.1% side column with 250 DIP minimum and 3.4% quantized gap, and 26/30/34
pixel headline sizes. It restores the hero container, three round-state cells,
four shortcut rows and the help/feedback entries. The requested blue/white
palette is retained; this is a structural restoration, not an assertion of
pixel identity to the old navy Tk palette. Existing Qt service actions remain.

The brand PNG is copied unchanged from that build's input resource stage,
`assets/brand/xingtu-round.png`, SHA-256
`4e86c8732c5302f1e8ec7d715aeaea27cb3ac4ee22773919bef24b6a68982230`.
Navigation uses the existing Lucide icon bytes; custom navigation uses the
existing Tk `_route_mask` rather than a document glyph. The controls action
and collapsed orb use the existing Lucide panel icon. Its license remains in
`THIRD_PARTY_LUCIDE_LICENSE.txt`. AppButton now renders `icon.source` rather
than dropping it in its custom content item.

Validation: own Qt offscreen windows at 1000x720, 1920x1080 and 650x470,
settings/help, and expanded/collapsed controls produce no QML warnings.
`work/layout-343-reference/qt-layout.json` records disabled input/capture and
26/30/34 pixel headline measurements; screenshots are beside it. The existing
gallery layout/action test passes after the shared button content change.
A fake host calls the actual Tk `_build_home` via `DesktopMainUI.__new__`,
without constructing DailyAssistant, capture, hotkeys or a game controller.
Its geometry receipt is `tk343-home-geometry.json`. Safe offscreen PrintWindow
returned a black bitmap, which was discarded: there is no verified pre-upgrade
Tk pixel screenshot comparison. Source geometry is the reference; earlier
bluewhite/static-library drafts are not evidence of the shipped layout.

The Impeccable scan reports existing and inherited Tk color literals outside
the experiment's DESIGN.md palette as advisory findings. Those literals retain
the verified incumbent light UI colors; the scan does not certify QML geometry.
No Setup was rebuilt or installed and no player settings were changed.
