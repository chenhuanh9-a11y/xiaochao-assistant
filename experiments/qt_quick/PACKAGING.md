# Qt private Setup recipe

This recipe builds the Qt client from a clean, explicitly named commit. Fixed installer profiles provide isolated tests or a user-authorized replacement of the original 星图 installation. It does not build the Tk UI, install or launch an executable, create a portable ZIP, publish artifacts, or change the separate `qt-lab` checkout. The coordinator must finish source/live-chain checks and authorize the build before running the final command. Preparing this recipe did **not** perform a build.

Delivered baseline (2026-10-30): commit `f3d349347b1416f846ac2d3b4b553c63517a4562` produced the first private Qt Setup. Its package and installed state must remain separate from subsequent source changes. Current migration coverage is tracked in [MIGRATION.md](MIGRATION.md); source/offline/own-window evidence is not native-game acceptance or a performance claim. The optional installer-instance parameter below prepares a separate layout preview through the same build chain. This parameter change itself has not built, installed or launched anything.

All pinned runtime/build packages are now installed in `.venv-qt-isolated`; the read-only environment check passes with the explicit Inno compiler. The coordinator's exact inventory has 401 files: 328 retained, 72 optional author images omitted, and the old version metadata replaced. Provider checks report nightmare 26 maps/78 typed layers and difficult 30 declared maps/58 typed layers, with the explicit map29 binding gap preserved. The 209-file notice gate passes. The clean committed source/inventory and final artifact checks below remain required.

## Product and path contract

| Item | Qt test contract |
| --- | --- |
| Entry | `experiments/qt_quick/client.py`; QML uses `Path(__file__).parent` |
| Frozen program | `XingtuQtClientTest.exe`, onedir staging tree used only as an intermediate |
| Installer identity | `AF19AFD9-3016-44C9-9D20-6F216C5D8C51`, “Xingtu Qt Test Client” |
| Default destination | `{autopf}\XingtuQtClientTest`; separate from the installed Tk product; previous installer paths are not adopted |
| Frozen resources | `sys._MEIPASS` (standard onedir `_internal`); client QML/Python helpers at its root; analyzed shared sources under `app/` |
| Writable data | EXE-adjacent `work/qt-client-runtime`, created on first run with the existing ownership marker |
| First-run seed | Copy only this product's bundled `_internal/assets`, `references`, optional `recognition`, native manifest and metadata into its new owned runtime sandbox; never adopt Tk installation/config/data |
| UAC | Explicit PyInstaller `uac_admin=True`; distinct Qt manifest assembly identity; actual PE RT_MANIFEST #1 checked before COLLECT and again after assembly |
| Shortcuts | Direct EXE target, working directory `{app}`; the EXE requests UAC. No automatic post-install launch |
| Delivery | Only `setup/XingtuQtTest-Setup-<display>-qt-test-<commit12>.exe`, plus private build evidence; no signing/install/game/public-release claim |

The installer preserves runtime work/imported resources on uninstall; it contains no `[UninstallDelete]`. The test product's first-run copy is independent of the installer staging directory. Source `client_paths.py` remains the single source/frozen path definition.

## Side-by-side layout preview instance

`--instance` accepts only fixed profiles; the two isolated profiles below retain their existing behavior. The production replacement is described separately below. It does not accept arbitrary installer paths, labels or AppIds. Omitting it preserves the first test client's installer identity, folder, group, filename prefix and `-qt-test` version suffix.

| Instance | AppId | Install folder | Start-menu group / shortcut / uninstall name | Setup prefix and suffix |
| --- | --- | --- | --- | --- |
| `client` (default) | `AF19AFD9-3016-44C9-9D20-6F216C5D8C51` | `{autopf}\XingtuQtClientTest` | `Xingtu Qt Test Client` | `XingtuQtTest-Setup`, `-qt-test` |
| `layout-preview` | `D4B8E562-EA62-4A43-AF1D-043A5646971E` | `{autopf}\XingtuQtLayoutPreview` | `Xingtu Qt Layout Preview` | `XingtuQtLayout-Setup`, `-qt-layout-test` |

Both use the same `XingtuQtClientTest.exe` basename and staging-product layout, retaining the existing known-process shortcut guard. The preview has its own EXE-adjacent `work/qt-client-runtime`; it does not adopt the first test client's settings or resources. The separate AppId, folder and menu identity allow the user to install the preview alongside `f3`, while the build never installs it. Actual side-by-side installation, menu entries and uninstall behavior still require acceptance; parameter inspection is not proof of those native operations.

`installerInstance` is recorded in both `build-record.json` and `installation-profile.json`. The preview's default output directory adds `-layout-preview` after the commit prefix; any explicit `--output-dir` must still be new. Source cleanliness, resource inventory, license, UAC manifest, native/QML closure and final size/hash gates remain identical. No product instance enables `[Run]`, force-closing applications, or deleting user data on uninstall.

For the next authorized build, append `--instance layout-preview` to the existing command; for example, after committing and pushing the final source:

```powershell
.\.venv-qt-isolated\Scripts\python.exe tools/build_qt_client.py --instance layout-preview --source-commit FULL_40_CHARACTER_FINAL_COMMIT --resource-root work/qt-lab-installation/_internal --resource-inventory work/qt-resource-inventory.json --iscc 'E:/codex/tools/inno-setup-6.7.3/compiler/ISCC.exe' --output-dir work/qt-setup/NEW_COMMIT_LAYOUT_PREVIEW
```

Use the same command with `--plan-only` first if a read-only adopted-input report is needed. The placeholder commit/output above are not build inputs. Only the coordinator runs the single actual freeze/Setup chain after integration checks; this document does not authorize changing the delivered `f3` artifact or starting an installer.

## Original-product replacement profile (source preparation)

The next user-requested Setup uses `--instance production`. This replaces the **original 星图 product**, not either isolated Qt test. Preparing this profile has not run a compiler, created a Setup, installed/uninstalled anything or stopped a process. Preserve the delivered f3 and layout installers and the old Tk source/installer as recovery inputs.

The authoritative original identity was read from `D:/CodexBuilds/star-main-71908cde-20261029/installer/64710EF6-LIGHT-r1/effective-installer.iss`, the delivered original 1.0.5 / 64710EF6 recipe. Its `[Code]` backup/rollback, uninstall resource preservation and occupied-EXE guard are reused in this template only for production. No original `[Run]` action is copied. The public `xiaochao-assistant.spec` is a development skeleton and is **not** the product-identity authority.

| Contract | Original installation | Qt `production` |
| --- | --- | --- |
| AppId | `D04394A0-DACE-4FF9-874D-3DCB43EE53D1` | same |
| App / start-menu name | `星图` | same |
| Main executable / staging product | `第五人格小抄助手.exe` | same; Qt entry and runtime replace its UI |
| Default install directory | `{localappdata}\Programs\XiaochaoAssistant` | same |
| Upgrade path/group | `UsePreviousAppDir=yes`, `UsePreviousGroup=yes` | same; original registered path required when present |
| Installer privilege / EXE privilege | `lowest` / `requireAdministrator` | same; no UAC bypass |
| Settings / memories | `{app}/work/nightmare-memory/ui-settings.json`, mode memory directories | reuse in place via production path adapter; no isolated seeding |
| Other user data | work receipts/custom galleries/routes; outer `assets/custom-maps` and existing user resources | remain in place; never copied from a sandbox or owned as new installer payload |
| Frozen base resources | `{app}/_internal` | same resource root; only inventoried authorized base resources bundled |

Only the exact known uninstall GUID was checked read-only in HKCU/HKLM, 32/64-bit views; no matching key was present in this execution session. No broad registry enumeration, private setting values or user process inspection was performed. Installed path adoption on a real host therefore remains an acceptance item, not a claimed result.

The production runtime chooses `installerInstance.id=production` from generated `installation-profile.json`. The build record/profile also include the EXE product, `runtimeDataProfile=production`, original default directory, installer privilege and path-adoption flag. The runtime adapter is responsible for resolving app/data/resource roots; changing installer identity alone is insufficient. The existing pinned Qt/Python, license, manifest and resource-closure gates still apply. Production's Setup prefix is `星图-Qt-Setup`, display suffix `-qt`, and default output `<commit12>-production`; output must be new.

The build writes `_installer-owned-files.txt` from only the final staged program files, rejecting a `work/` payload. On install, reused original code backs up exact new/old-owned affected files, previous uninstall metadata and original settings under `{app}/work/installer-backups`; it restores affected files if upgrade fails. Old program code may be retired by its exact prior ownership list; resources are not retired by that rule. Work/import receipts/custom routes and outer user assets are never recursively removed. Production `[Files]` now comes from an exact generated projection rather than a tree wildcard. It splits program identity, the coupled base resource plane and the independent editor-base family. Before any copy, one cached decision checks for existing `_internal/assets`, `references`, `recognition` or the difficult root manifest (including invalid file collisions). Any existing/partial plane preserves **the whole plane**; Setup does not repair it by mixing individual baseline files. A fresh resource plane receives every inventoried baseline member together. `version.json`, `installation-profile.json` and `references/build-fingerprint.txt` are program identity and update independently. Production replaces the baseline fingerprint with the uppercase first eight source-commit characters; `fingerprintScheme=source-commit-prefix` and the replaced baseline hash are recorded in both build record and installation profile. This describes Qt source identity, not the older Tk fingerprint algorithm; isolated client/layout profiles keep their existing behavior. The editor family is seeded only when both outer `assets/custom-maps` and internal `assets/custom-maps` are absent; neither existing family is overwritten. Optional author displays remain excluded by the light profile. Existing imported catalogs, bindings, images, structure/recognition products and receipts remain byte-for-byte untouched by the resource projection, including paths also present in the baseline. `production-file-projection.json` records the exact families/include hash and `resourceInstallationPolicy` distinguishes bundled baseline metadata from retained installed resources. The generated ownership backup plan uses the same cached family decisions, so skipped existing resource bytes are not rolled back/replaced as new payload. Synthetic projection checks are not native Inno installation acceptance. On uninstall the reused original logic preserves resource files owned by historical uninstall logs and restores their original paths. Neither profile adds `[UninstallDelete]`, automatic launch or force-closing applications. Original Tk source and installer remain available; native rollback/reinstall and preservation acceptance still require an actual controlled installation, not just source checks.

After final integration/commit/push, the coordinator uses the same one build chain:

```powershell
.\.venv-qt-isolated\Scripts\python.exe tools/build_qt_client.py --instance production --plan-only --source-commit FULL_40_CHARACTER_FINAL_COMMIT --resource-root work/qt-lab-installation/_internal --resource-inventory work/qt-resource-inventory.json --iscc 'E:/codex/tools/inno-setup-6.7.3/compiler/ISCC.exe' --output-dir work/qt-setup/NEW_COMMIT_PRODUCTION
```

Only after final source/profile checks, remove `--plan-only` for the coordinator's single authorized build. This command does not authorize installation, ending a game, merging main, formal publication or uploading an artifact.

## Pinned build interpreter and tools

Use Windows x64 Python 3.13 and the isolated Qt environment, not the global interpreter. The exact union of runtime/core/build pins is in [`tools/requirements-qt-build.txt`](../../tools/requirements-qt-build.txt): PySide6-Essentials/shiboken6 6.11.1, NumPy 2.2.6, OpenCV-headless 4.12.0.88, Pillow 12.3.0, psutil 7.2.2, PyInstaller 6.22.2, hooks-contrib 2026.7 and pefile 2024.8.26. These pins are now installed in the isolated interpreter; this recipe never installs dependencies automatically.

```powershell
.\.venv-qt-isolated\Scripts\python.exe -m pip install -r tools/requirements-qt-build.txt
.\.venv-qt-isolated\Scripts\python.exe tools/build_qt_client.py --check-environment --iscc 'E:/codex/tools/inno-setup-6.7.3/compiler/ISCC.exe'
```

Read-only inspection on 2026-10-30 found that compiler at the path above (SHA256 `0a8757031b33777e4c9cbffee40f11a5062b36d25cbe144c1db73b6102b80ad7`); no compiler process was started. The latest read-only environment check confirms every listed pin, required source QML modules and compiler are available. The earlier missing-build-tools observation is superseded. The global interpreter has mismatched runtime packages and is unsuitable; the current isolated environment check remains authoritative.

## Exact resource input and closure

Pass the **exact isolated resource root**, such as an explicitly approved clone-owned `work/.../_internal`, not its installation parent or an installed Tk directory. There is no resource discovery, environment-variable fallback or remote download. Every file must be declared with size and SHA256 in a separate private inventory:

```json
{
  "schema": "xingtu-qt-resource-inventory-v1",
  "profileId": "structure-editor-light",
  "resourceVersion": "EXACT_APPROVED_RESOURCE_VERSION",
  "files": {
    "references/nightmare-map-pack.json": {"bytes": 123, "sha256": "64_HEX_DIGITS"}
  }
}
```

The example is a schema illustration, not a usable inventory. Build the inventory from the authorized resource producer/lock, record its provenance, and hash the exact supplied files; generating hashes alone does not establish provenance. Keep it under ignored `work/` or another private input location. The entire root must match the inventory, including optional files that will be omitted, so stray user state/binaries cannot enter through a directory copy. Symlinks/junctions, path escapes, unsupported file types and undeclared files are rejected. Accepted trees are `assets/`, `references/`, `recognition/` and the named root manifests; older resource families depending on `outputs/` need an explicit seeding contract before using this recipe. Input and output directories may not contain each other.

Required base metadata includes the nightmare pack/catalog/canonical coordinates/shared structure index/version bindings, difficult native runtime manifest and difficult shared structure index. This is only the fixed entry set. The **complete closure** additionally contains every file consumed by their actual providers:

- Both modes' typed clean/validation structure NPZs, version bindings and matching `references/fast-structure` precomputed files.
- Nightmare front/side entry indices, entries/passports/search data, canonical coordinate rules and source-candidate metadata/products referenced by the selected provider.
- Difficult native identity/source manifests, coordinate bridges/packs, author catalog/display descriptors, full-reference and version-binding indices, native UI/floor-control templates and declared legacy-clean map29 resources.
- Native-similarity groups/binaries, floor metadata, recognition templates and any declared base display structures; preserve producer bytes, IDs and bindings.
- Approved base/help/brand assets come from that exact private inventory. The source-owned `assets/support/wechat.png` and `alipay.png` are added from the clean source checkout if absent from the inventory, with their separate hashes recorded. Other absence remains an explicit capability/UI gap. No new source art or raw game frames are generated or silently borrowed.

The recipe reuses `package_profiles.partition_resources`: optional author display files beneath `assets/suju/`, `assets/zhan10-native/`, `assets/zhan10-legacy-clean/` and `assets/authors/` are omitted and recorded. These remain separately imported author packages. Catalog/registration/structure data are retained. Inventory `version.json`, if present, is recorded as replaced metadata; the new client's source `version.json` is authoritative, while `resourceVersion` remains separately recorded. The generated `installation-profile.json` declares LIGHT, zero bundled optional-author files, no configured public download, and preserved local imports.

`packaged_resource_contract.check_package` gates path ownership; it explicitly does **not** prove resource completeness or recognition correctness. Before the one build, the coordinator must validate both real provider closures against the same selected root and preserve those reports. Public structure-exchange resources alone are not a complete runtime root. Do not claim either mode is fully usable merely because these filenames exist.

## Qt and Python closure

The spec analyzes `client.py` plus its helpers/shared services, explicitly includes QtCore/Gui/Qml/Quick/QuickControls2, NumPy/OpenCV and image decoders, and materializes the analyzed `app/*.py` source closure under `_internal/app` for the `APP_ROOT` contract. All local Qt QML files are included without map/art directories. The production Tk entrypoint, tkinter/_tkinter/PIL.ImageTk, alternative Qt bindings and optional AVIF/FFmpeg codec components are excluded. No full source/resource tree is copied into the application.

PyInstaller's installed QtQml hook collects QML plugin dependencies. The assembled tree must contain `QtQml`, `QtQuick`, `QtQuick.Controls`, `QtQuick.Controls.Basic`, `QtQuick.Dialogs`/`quickimpl`, `QtQuick.Layouts`, every plugin DLL declared by those `qmldir` files, and `platforms/qwindows.dll`. The gate also checks all client QML, resource hashes, shared source presence and absence of Tcl/Tk runtime files. The build removes unused GPL-only module families/plugins (including Timeline, Lottie, Virtual Keyboard, Quick3D, PDF and QML Compiler if collected) and optional Mesa software OpenGL, then validates the retained Qt module set and normal/delay-import closure without loading those binaries. Other needed LGPL-capable runtime dependencies remain; this is not a general size-optimization pass. A successful freeze is not a native-dialog, rendering, or shortcut smoke test.

Existing project notices, installed metadata and exact dependency LICENSE/NOTICE/COPYRIGHT files are copied. [`qt-licenses/`](qt-licenses/NOTICE.txt) now contains 209 exact upstream v6.11.1 texts/attribution files (including full LGPL3/GPL3), 93 attribution declarations, source links and per-file byte hashes. Build-time notice collection additionally copies the installed Python/dependency texts and records actual DLL/PYD hashes. The selected route is dynamic Qt/PySide LGPL v3; no commercial license acceptance or application-wide license change is implied. `NOTICE-INVENTORY.json` binds the final retained native files and omissions. Exact corresponding-source/build correspondence, recipient source access/delivery, replaceability and final native-runtime terms still require artifact/distribution review; source links alone are not legal clearance.

## Plan, then the single authorized build

Commit the final source first. Run from a clean checkout at its full 40-character commit; ignored environments/resources/build outputs may exist. The script checks HEAD, working-tree cleanliness and source tree SHA without fetching or claiming remote verification. The inventory/selected-resource root must already be approved. The output must be a new directory beneath this checkout's ignored `work/` or `dist/`; existing output is refused.

```powershell
$qtCommit = 'FULL_40_CHARACTER_FINAL_COMMIT'
$qtResources = 'EXACT_APPROVED_ISOLATED_RESOURCE_ROOT'
$qtInventory = 'PRIVATE_RESOURCE_INVENTORY_JSON'
$qtCompiler = 'E:/codex/tools/inno-setup-6.7.3/compiler/ISCC.exe'
$qtOutput = 'work/qt-setup/NEW_BUILD_ID'
.\.venv-qt-isolated\Scripts\python.exe tools/build_qt_client.py --plan-only --source-commit $qtCommit --resource-root $qtResources --resource-inventory $qtInventory --iscc $qtCompiler --output-dir $qtOutput
# Run only after the coordinator explicitly authorizes the one build:
.\.venv-qt-isolated\Scripts\python.exe tools/build_qt_client.py --source-commit $qtCommit --resource-root $qtResources --resource-inventory $qtInventory --iscc $qtCompiler --output-dir $qtOutput
```

`--plan-only` performs read-only validation and prints the adopted inputs. The build stages inventoried retained resources, freezes once, reads the PE manifest without launching, validates collected files/plugins, then compiles Setup once. Source identity is rechecked before Setup and after compilation. A failed stage stops; it does not retry, install, publish or reuse a previous output. The build record starts with `buildCompleted=false` and becomes true only after the expected Setup exists.

Private `build-record.json` records source commit/tree, display/numeric version, resource version/inventory hash, retained/omitted file hashes, compiler/package versions, manifest/closure gates, installer instance, recipe hashes and final Setup bytes/SHA256. Deliver the Setup path and exact size/hash with this record; label build, installation, native GUI, game checks and publishing separately. The first `f3` Setup already has its own completed record. A later layout preview requires its own final clean committed source, complete inventory/provider reports and final frozen artifact/plugin/source-obligation review; these parameter edits do not create that new Setup.


## Frozen startup verification

The windowed wrapper records Python startup failures to its own work/qt-client-startup-error.txt and exits instead of waiting on an error dialog. Frozen --self-check --verify-resources writes work/client-self-check.json without creating Qt windows or enabling native input. Run only in an already elevated, authorized test context; do not bypass UAC.

Qt 6.11 on Windows consumes the operating system ICU ABI. A first freeze incorrectly found an unrelated PATH ICU78 build exporting suffixed symbols, causing QtCore import failure. The recipe now checks every retained ICU import against Windows System32 exports, omits collected foreign ICU DLLs and records that decision; it does not redistribute a system DLL. The corrected prior-stage diagnostic completed both mode preparations with exit code zero. Final Setup must pass its own check separately.
