# Isolated Qt editor base input

`prepare_qt_editor_base.py` copies an explicitly authorized, already produced
editor package into a **new** `work/` sandbox. It reuses the producer's manifest
verification and the existing LIGHT profile partition. It never redraws maps,
changes routing/recognition algorithms, overwrites an input, discovers installed
data, or builds/installs software. Optional author display files are omitted
without opening their bytes. Only editor manifest-declared assets are copied.

## Current prepared input

- Sandbox: `work/qt-editor-base-next`
- Future build resource root: `work/qt-editor-base-next/_internal`
- Future build inventory: `work/qt-editor-base-next/resource-inventory.json`
- Provenance: `work/qt-editor-base-next/editor-base-provenance.json`
- Two-floor check: `work/qt-editor-base-next/representative-check.json`

This input contains 487 files / 110,706,338 bytes, including 158 editor files /
10,372,817 bytes, 26 maps / 78 floors. It excludes 72 author display files /
26,223,275 bytes from the original runtime seed. Runtime catalogs and registered
typed structures remain present; optional author originals do not.

Inventory SHA256:
`0afffe6d02e8674f6a4aa80a7bbbb252217a8ccfa64a051aeecfda94d713b90c`.

## Provenance and permission boundary

The source is the **outer** `installation/assets/custom-maps` in
`E:/codex project/2026-10-25/task/work/resource-action-ux-20261030/baseline-pr64-pr6-2327a333`.
The original Qt seed used only `installation/_internal`, which explains the
missing editor package. The seed and delivered f3/2051 installers are unchanged.

The existing package version is `20261029-increment1`, schema
`xingtu-custom-maps-v1`, manifest SHA256
`cb1629e55b0ac7be2b82959f6875e55ba0f250a0784c197f600b3a9517c17d06`.
See `docs/custom-maps.md` and producers
`tools/maps/build_custom_map_package.py`, `extend_custom_map_package.py` and
`complete_incremental_editor_connections.py`. The existing legacy editor's
blank/maps rendering, native transforms, reduced road graphs, reviewed road
patches, stairs, and incremental source-rendered floors are preserved byte for
byte. They are classified as editor basic rendering, rather than optional
multi-author route originals. No raw game unpack or original source room data
is copied. The receipt retains the source version/hash metadata.

The user explicitly permits bundling this editor base for the migration.
This does **not** assert an independent public redistribution license or grant
permission to publish the local artwork in Git. Assets and receipts stay under
ignored `work/`; source tools/docs/tests contain no product artwork. Preserve
the package's existing limitations: incremental stair associations have not
acquired new gameplay/internal-polyline verification, dynamic door state is
unknown, and legacy rendering retains its existing edge artifacts.

## Reproduce into another NEW sandbox

From this checkout, run PowerShell (the target must not already exist):

```powershell
.\.venv-qt-isolated\Scripts\python.exe tools/prepare_qt_editor_base.py `
  --source 'E:\codex project\2026-10-25\task\work\resource-action-ux-20261030\baseline-pr64-pr6-2327a333\installation\assets\custom-maps' `
  --resources work/qt-lab-installation/_internal `
  --target work/qt-editor-base-next-copy `
  --expected-manifest-sha256 cb1629e55b0ac7be2b82959f6875e55ba0f250a0784c197f600b3a9517c17d06 `
  --resource-version 1.0.5-baseline-pr64-pr6-2327a333 `
  --validate-map nightmare-03
```

The optional check uses existing `client_providers.build_engine_factories`,
`CustomMapLibrary.validate_reference`, graph planning and `render_game_layer`.
For nightmare-03, B1 native canvas is 1080×1260, F1 is 1620×1920; both exact mask
hashes match the existing typed references. One registered stair route has four
cells and cost 325, with visible route lines on both saved PNG layers. This is
a saved-resource check, not live-game or all-map recognition acceptance.

No new runtime dependency is required. Deployment adds 10,372,817 uncompressed
resource bytes to the LIGHT profile; Setup compression/installed overhead is
unmeasured because no new Setup was built. Future packaging can consume the
resource root and inventory through the existing `build_qt_client.py` options.

Source launch command (provided for integration; not run by this resource task):

```powershell
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/client.py --sandbox work/qt-editor-base-next
```

Targeted tool checks:

```powershell
.\.venv-qt-isolated\Scripts\python.exe -m unittest discover -s tools -p test_prepare_qt_editor_base.py -v
```
