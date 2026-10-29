# UI startup layout recovery

The hidden help page could keep re-requesting its own width after a DPI refresh. Its wrapped labels and automatically sized Canvas child alternated between two widths, so `WindowDisplay.refresh()` never finished `update_idletasks()`. The fix makes the Canvas own the embedded page width on both Canvas and content Configure events, and writes only when that allocation changes. It does not remove the idle drain or disable animations.

## Related page work

- Reentering an unchanged gallery keeps its cards, while explicit refresh still rechecks file status and visible thumbnail stamps. Deletion clears the image; recreation and replacement reload it.
- Overview structure and cover decoding use the existing bounded preview workers. Hidden pages pause submissions/uploads, and resource generation, widget request and DPI checks reject stale results.
- Invalid compressed structure data follows the existing unavailable-preview result instead of escaping the future and stopping the home refresh timer.
- Frozen nightmare packs resolve installed resources rather than falling back to a producer checkout. Historical producer paths remain provenance. `tools/packaged_resource_contract.py` is a read-only pre-freeze gate for declared runtime paths; it does not certify image availability.

## Verification and limits

The baseline full `DailyAssistant` constructor used actual packaged resources, followed by a 125% DPI refresh, and exceeded a 32-second bound with repeated stacks in `desktop_display.refresh -> update_idletasks`. The hidden help content alternated widths 505/490 pixels. Keeping all baseline modules and replacing only the page-width fix made the repeated DPI/page sequence return.

The combined affected suite ran 50 tests with no failures or skips, including actual Tk allocation, gallery deletion/recreation, hidden completion, corrupt NPZ, queue limits and installed-path contracts. Independent reviews covered scrollbar thresholds, home height, stale image results and bounded resource work. Both-mode factory/preload checks on the old actual executable code succeeded with developer-directory access denied: stale producer paths alone were not demonstrated to cause the hang.

A separate complete-constructor replay uses real package data and real Tk components, isolated settings, no game monitoring or input, and invisible nonactivating test windows. Hardware DPI changes, normal visible desktop usability, game latency, installer execution and long-running player memory remain separate acceptance work. Finite DPI/layout costs remain; these checks do not establish a universal frame-rate or eliminate every historical freeze.

For the next build, run the runtime-path gate on its staged resources and repeat the full-constructor/page checks against that executable's code and native dependencies. Do not reuse the withdrawn candidate's player-acceptance status.
