# Qt Quick migration map

## Bounded behavior audit after F2

**F2CB49F7 remains the only delivered current candidate. None of the following
source repairs is contained in that Setup.** The baseline is actual Tk
`3438365b4cef003f76d08a001f772c888156bd03`; `38490F4F` is the earlier artifact
fingerprint, not another Git commit. Historical build receipts below remain
version-scoped. The current coherent source checkpoint is `89e2e6b`.

Five independent audit partitions covered the code/call chains below. Coverage
of a partition is not a claim of behavior equivalence, real game acceptance or
frozen-program validation. Implementers ran targeted checks; independent
reviewers inspected fixed code and evidence. Do not describe their inspection
as independently executing the player's game.

| Domain / old contract | Changes found and concrete locations | Source closure and evidence | Still open / unverified |
| --- | --- | --- | --- |
| A: identity, entry preselection, provisional group, unique identity, first/repeated fit, floors, memory, author selection. Original callers own rank budgets, current-frame residuals and group/pose authority. | `client_engine.stage_similarity_rank`, `LiveRuntime._rank_identity/_continue_group/_solve_observation`, `_AlignmentHost.fit`; original helpers in `app/alignment_structure_dispatch.py`, `round_layer_group_commit.py`, `native_similarity_groups.py`; `client_idvb0059_adapter.py`. | Explicit rank-none reset `6681766`; strong-conflict consumer `ebdf0dc`; relations/verified-pool writer `78c5baa`; author-only reproject without fit `56d7094`; persistent 0059 adapter/material-accepted memory `9c0764f`; actual dispatch order and relation recovery/conditional pose-index fallback `e75f768`. Original extracted bodies are AST-equivalent to 343. Targeted tests include non-unit basis, stale frames, independent floors and real generated input through unchanged 0059 numerical functions. Original floor-intent/conflict restoration is `7ac5770`. | Internal maintenance enqueue was independently found blocked by UI command availability and fixed in `89e2e6b`; the actual coordinator-to-session-to-runtime remember chain still needs its controlled integration receipt. The real map08/B1 continuous failure input is unavailable. Two single saved frames are being checked with real providers, not treated as a group/floor sequence or historical-H ground truth. |
| B: global input, startup preparation, repeated G, mouse map-X/drag, capture, foreground suspension, exit/IPC and other-assistant coexistence. | `client_input.py`, `client_native_keys.py`, `client_map_input.py`, `client_capture.py`, `client_capture_exclusion.py`, coordinator native/focus paths. | `89e2e6b` revokes the frame lifetime before cancelling on foreground loss and applies the old-assistant guard to automatic enabling as well as manual enabling. Safe full-client fallback uses owner-thread exclusion of owned Qt surfaces, original `capture_client`, and finally restoration; it is injected into production composition, drains pending restoration on normal close and enrolls before WindowController show. Fake native/worker checks and independent helper review are version-scoped. | Ordered second-G, G automatic round start/start-before-game, map-X and original transient drag consumers remain in progress. Native affinity/new QWindow events, real global keys and the final frozen error/exit/restart journey are not certified by fake transport checks. An owner-ACK timeout does not bound an uninterruptible native capture/DwmFlush call. |
| C: resources, configuration, optional catalogs, preferences, original custom-route migration and transactions. | `client_providers.py`, `client_custom_gallery.py`, `client_settings.py`, `client_resources.py`, `client_downloads.py`, original app import/recovery/store services. | `fd82f89` restores bad optional catalog tolerance while keeping typed resource validation strict, the production settings-sibling legacy-route path and Save-As service support. `89e2e6b` separates Chinese user summaries from bounded raw details, reports post-write scan failure accurately and suppresses cancellation/duplicate/stale errors. Source transaction/import/receipt checks pass without modifying real user data. | Online catalog URL is externally missing; no endpoint is guessed. Original helper import/readback evidence is partial for native PID/exit observation. Recent problems are 32 visible, queue 64, batch 16, per-run 1 MiB plus one backup; cross-run aggregate retention is still unbounded. No historical user logs were deleted. |
| D: actual old layout/icons, Home images/context, help/editor, real export destination, startup update prompt. | `ClientHome.qml`, `ClientCustomGallery.qml`, `ClientInfoService`, `QtClient` composition; existing resource/help/editor pages. | `89e2e6b` connects actual applied mode/profile to Home and waits for the correct async gallery before selecting. Navigation/context changes cancel stale previews. Startup uses original prompt/skip rules, waits while main is hidden and does not activate it. Real owned QML Save-As selects the original schema/path and preserves overwrite verification. Independent source review found no remaining P0/P1 in this bounded batch. | Offscreen dialogs are not Windows native-picker acceptance. Earlier visual receipts remain tied to their recorded source/build; final candidate ordinary/large visible pages still need review. |
| E: static reference versus aligned surface, author fallback, position/editing, visibility/opacity and notices. | Runtime `reference_payload`/`render_payload`; `client_coordinator.py`; `ClientReference.qml`, `ClientAligned.qml`; `client_reference_presentation.py`, `client_screen_geometry.py`, original display notice policy. | `fd82f89` restores durable static independence from pose/window geometry, the original provisional exception, shared effective-author policy and correct full-fit source classification. Declared broken author layers keep the original structural fallback; undeclared layers use the actual base-map provider. Screen names are verified by unique native/Qt geometry rather than exact friendly-name equality. `89e2e6b` unifies game/own-editor focus, visible-area placement with explicit Qt-coordinate markers, no-identity display-only position preview, pool draining and current-material fallback notices. Actual owned QML coordinator entry, focus round trip, cancel, drag and Finish pass with no warnings and a drained worker. | F2 static pixels were Ready/visible then hidden with preference true and opacity .52; logs do not uniquely attribute every hide. Source fixes do not prove the player's exact incident resolved. Old unmarked position provenance stays unknown and is only clamped. Preview layout can retain temporary vertical whitespace during image-size/drag convergence. Real mixed-DPI game overlay and final pixel latency are unverified. |

### Independent review corrections and authorized changes

The first strong-conflict review incorrectly treated the experimental front
wrapper as a reachable daily producer. Follow-up tracing found only the base
engine in the actual 343 daily factory; its shared path has no such producer.
That extra P1 claim was withdrawn. The original consumer is restored, but no
new strong evidence or relaxed rejection is invented to make a test pass.

Chinese branding/installer, top-right initial control placement, in-round mode
locking, package-selection auto-import, explicit success dialog and edge snap
are user-authorized UI changes, rather than parity claims. Current review
restores old contracts without changing solver thresholds or bypassing valid
capture, floor, residual, context or material guards.

The user separately promoted automatic-mode current-group manual selection into
this version. `ClientRecognition.qml` and `client_group_member_choice.py` have
an owned-UI/helper receipt and runtime checkpoint `22c2a73`; independent
transaction review is pending. The implementation must commit
identity/route/aligned/reference together after current geometry and material
acceptance, never pretend native unique verification. Hold applies to the
current round/group until explicit **Restore automatic**, and resets on end,
reselection, resources or mode change. Reopen, ordinary failure and author
changes do not undo the user's choice. This adds no automatic learning write
and does not include PR70's full manual mode or the route plaza.

### Evidence and delivery boundaries

Private ignored evidence includes:

- `work/qt-evidence/f2-display-failure-evidence.json`: actual field log extracts;
- `work/qt-evidence/f2-screen-geometry-after.json`: actual three-screen read-only mapping;
- `work/qt-evidence/author-fallback-real-baseline-after.json`: locked installed resource choices;
- `work/qt-native-helper-fixture/coordinator-reference-preview/result.json`: actual source coordinator/QML preview entry, not a game;
- `work/qt-group-member-choice/`: new manual-group UI screenshots, not runtime acceptance;
- `work/recorded-capture-chain-8a98d5243d8e495291031937eb781360/available-inputs-current-check.json`: two independent historical saved frames, not a continuous replay.

No private game frames, author originals, logs or user preferences are committed.
The next candidate must come from one pushed clean source commit and its exact
resource inventory. It needs frozen-process visible/close/exit checks as well
as source checks. Recorded evidence must remain non-live and must be rejected
by the game's aligned publisher; a separate explicitly controlled material
seam is not real game authorization. Actual game/global-key/mixed-DPI behavior,
map08/B1 reproduction, online service availability and 500ms/300ms performance
remain unverified until measured. Difficult map29's missing original native
bindings remain a supplied-baseline gap. None is silently declared solved.

## Follow-up source after 24857072

The F5 node (`f5b568c68910c9141b346feb7fae8f78cb6151d1`) is built and retained. Its exact frozen program also passed QWindow close → actual exit-choice mouse click → normal process termination, confirmed by the original Popen handle, with eight actual service pools empty and input/capture closed. The 8.13-second case duration includes cold startup and test preparation; it is not a measured X-click latency. The additional test driver is outside the 3,025 unchanged product projection files. This run was performed by the button implementer and independently reviewed as evidence, not independently executed end to end.

The next source adds only three optional display-package registrations (展十噩梦, 酥居速刷噩梦/困难), leaving old `suju` preferences and recognition resources unchanged. Actual isolated first imports add 80/80/60 files and repeats add zero; Qt Backend consumes 26/26/29 maps, 78/78/58 layers. All three original ZIPs also pass read-only inspection against the exact F5 built resource tree, so their required base hashes match the build input. Difficult map29 retains its existing fallback rather than claiming missing author coverage. Original author ZIPs are not embedded or committed. Duplicate imports now report existing content only after the original transaction/receipt path completes and refreshes actual state. Unmanaged identical files still acquire their real receipt.

| Existing user journey | Actual Qt entry / consumer | Current evidence and limit |
| --- | --- | --- |
| Choose ZIP, confirm if necessary, observe progress | `ClientResources.qml` FileDialog `open` / `onAccepted` → `ResourceService.importArchive` → worker inspection/transaction, owned confirmation Dialog and status Label | F5 actual QML picker/confirmation → original helper committed two synthetic files with request/archive binding; a later real startup consumes the receipt, scans `installed` and displays the actual success message. It uses an owned offscreen picker; Windows native file-picker mouse interaction and the original helper PID/exit-code observation remain outside coverage |
| Complete or repeat an import, see refreshed resources | `resourcesChanged` → `ClientCoordinator._resources_changed` → runtime revocation, `session.refreshRuntime`, `backend.refresh`; QtClient also refreshes route choices/providers/custom library | Real first/repeat receipt and lease tests pass; duplicate helper notices survive the actual-state refresh. No pre-commit success or fake skip of unmanaged identical bytes |
| Cancel/fail/close during work | `ResourceService.cancel` / confirmation rejection / writer cancellation; coordinator drains accepted writes before quitting | Existing targeted cancellation/transaction checks reused; no claim of native file-picker cancellation or active-game import acceptance |
| Navigate away/back or refresh | Persistent QML pages and stable backend identity/preview jobs | F5 frozen probe confirms selection/scroll retained and zero unchanged-refresh model mutations |
| X: background / exit / cancel | Original close-choice Dialog; background hides only main; exit uses `requestClose` / pool drain / `closeReady` / quit | F5 actual frozen exit, input/capture closure and original process-handle termination pass. Active-game keyboard/foreground behavior was not exercised |

Independent review is `/root/impeccable_finish_reviewer`, which changed no implementation file. It viewed source normal/large screenshots, then frozen F5 screenshots and the recorded PE/process evidence. The frozen tests were run by implementers/integrator; three new registrations and later source changes require their own final-candidate coverage statement. The two user PNGs remained unmaterialized, so review uses the explicit written feedback and verified 3438365b source/reference images rather than claiming their pixels were inspected.

The delivered 248 Setup remains unchanged. This follow-up restores the actual Tk resource-page geometry (author preview cards / 5:4 split / selectors below the preview), removes gallery search and clears its stale filter, and counts distinct available map IDs: a real 72-floor/variant-row input displays 24 maps. Package transactions still have the existing separate management/download pages; this is not a claim of complete 3438365b page or pixel identity.

Home restores three actual author previews with the original structure-preview fallback. Help restores the four original topics, numbered steps, current bindings and existing operation images. Custom maps restore gallery browsing and editing as separate views, retaining the original canvas coordinate conversion and planning; thumbnail metadata is cached across point writes. Shared buttons now show default/hover/pressed/keyboard-focus/selected/disabled states. An independent reviewer viewed normal/large screenshots of all three surfaces plus home and seven control-state screenshots with no P0/P1 finding. The two new user Library PNGs could not be materialized because the current transfer helper requires unsupported Windows `os.setxattr`; their pixels were not reviewed.

Ordinary X now asks for background / exit / cancel. A complete source QtClient subprocess took the real QWindow close → original dialog → QTest exit-button path, drained eight service pools and exited 0 in 1.45 seconds, confirmed by its parent's Popen wait. A failed first test mistook its system console child for a helper; that exact owned test process was later identity-checked and forcibly cleaned up, recorded as a failed test rather than an exit pass. Neither result certifies active-game or future frozen-process shutdown.

The next production build names the executable, PE product metadata, original supplied icon, window icon and Windows app identity as 星图. The original AppId and user paths stay fixed. Parent-bound helpers use their own frozen profile to choose the fixed expected executable; PID/creation-time/retained-handle/path checks remain. Installer startup checks both old/new executables, and migration retires only owned old program files and verified shortcuts. PE/ISCC/native adoption remains a build/acceptance item until the new candidate is produced; 248 is not relabelled as containing these changes.

The integrated source and F5 frozen own-window probes pass 25 checks, including stable gallery selection/scroll, custom browsing → editing and a real two-point route, with zero QML warnings. Early test-picker locators/lifecycle setup failed before import and were not counted as passes. The corrected documented preselection-before-open input reached the original confirmation, launcher and helper commit. The observer ran after the synchronous close signal, so the original helper PID/exit code was not captured: the actual request-bound receipt and current unlocked helper image are separate evidence. A readback-only launch subsequently verified the original Qt receipt consumer, installed package row, visible success message and X-confirmed process exit 0; no second import occurred. All 3,025 product projection files remained unchanged. Do not call this fully observed original-helper process exit or native Windows picker acceptance.

## Delivered node and remaining scope (24857072)

The delivered original-product candidate is `24857072c7ed9328c122c2a68014bbb0dc865613`, fingerprint `24857072`: **星图-安装程序-1.0.5-qt-24857072c7ed.exe**, 154,993,030 bytes, SHA256 `165447b33cd2836920e71b9965d2fd00dd88ff531145d8fdcd45298748d5c959`. Its runtime source is identical to `45d28c69`; only the Chinese installer/identity receipt was rebuilt. Original AppId, settings, resource and gallery paths are retained. The 248, 45, AD7, 2051 and f3 artifacts remain unchanged. Later documentation/evidence is not a new product build.

The action inventory below is connected in the 248 candidate, including editor assets/visible canvas, actual old-layout proportions/icons, light native controls, manual learning, live author replacement, separate core-import/recovery helpers, online service, return-to-game, settings, floating/pinned/aligned windows, help/history/diagnostics and chest auto-tap. Remaining issues must be classified by their actual boundary rather than listed as missing UI features:

| Boundary | Current evidence | Remaining item |
| --- | --- | --- |
| Frozen pages/editor/theme | 24 own-window assertions, zero QML warnings; actual mouse route with two points; empty outer/missing legacy library uses the baked editor base | Native-game behavior is separate; no old/new pixel-identity claim |
| Import helper protocol | Exact frozen helper and parent self-check clone: parent-handle identity rejection and normal-exit commit passed; no write while the parent remained alive; all 3,020 source projection files unchanged | Main Qt internal launcher/button → helper → close integration is not covered by the external fixture |
| Upgrade preservation/recovery and Chinese controls | Filesystem projection/backup and interrupted resource transactions passed. An inert native Inno fixture with the unchanged production backup/preservation/restore code passed success (exit 0) and deliberate copy failure (exit 5): 17 old files restored byte-for-byte, new file removed. Actual hidden native controls contain Chinese Next/Cancel/Finish, license and completion text | Fixture GUID only, uninstall registration/shortcuts disabled; actual original-AppId registry/shortcut/uninstall adoption and visible product wizard are intentionally untested |
| Online packages | Service, progress/cancel/retry and local import handoff connected; local/controlled checks passed | Confirmed production map-index URL absent; no real-server acceptance or guessed URL |
| Difficult map29 | Same two legacy-clean floors and all 30 candidates retained, matching the old loader | Two reliable native-layout bindings were already unavailable in the supplied r6/light input; this is a baseline data gap |
| Native game and performance | Shared algorithms and same-input saved-frame evidence retained; source/offline/own-window checks cover the changed seams | Real foreground/global shortcuts, DPI/focus/transparent surfaces, both-mode game lifecycle and first-correct-pixel/long-run measurements remain unverified |

No game capture, real installed-product upgrade, formal release or Qt-to-main acceptance is inferred from these checks. The online configuration gap does not block the implemented offline import/editor paths. No new feature scope or repeated product build is required to describe these remaining acceptance items.

## Follow-up corrections after AD7

AD7 (`ad7ac284`) remains unchanged. The follow-up source fixes the verified editor canvas width collapse (26 maps loaded while its image width became -20px), validates editor-library candidates on the existing worker, and supplies an independently owned `_internal/qt-editor-base` fallback without overwriting old libraries or galleries. Conflicting gallery data remains intact and read-only; maps remain browsable.

The layout reference is now the actual pre-upgrade `38490F4F` build at `3438365b`, including Sakitams PR2 and the merged PR14 proportions. See [version, geometry and icon evidence](../../docs/qt-layout-38490-parity.md). Earlier static drafts are historical only. The follow-up uses original brand/Lucide/route icon bytes and a native QML light palette for Basic controls, including selection/disabled/focus/popups. A safe Tk geometry receipt is available; its failed black screenshot was discarded, so no pre-upgrade pixel-identity claim is made.

Explicit live manual confirmation now reuses the original `record_confirmation` and `reload_memory` on the existing worker. A valid human identity label remains independent of pose success; errors report learning separately without changing confirmation/pose outcomes. The acceptance check precedes disk IO, allowing snapshot/end-round to remain responsive while an already accepted write drains. Automatic, replayed and obsolete pre-acceptance results do not write labels.

The branch now contains main's PR66 display-only live upgrade service. Core import uses the original separate import helper and request-bound restart receipt, with normal-exit confirmation and the original closed-app checks; both import and recovery helpers are included in 248. AD7 did not contain these follow-up changes.

Map29 is retained: both legacy-clean floors and all 30 candidates are present, and Tk/Qt use the same loader with identical arrays/metadata. The two pending reliable game-layout bindings are a declared original r6 baseline gap, not a Qt map exclusion. The light profile's omitted author PNGs are a separate display-resource decision.

Representative checks cover real Qt map/floor choices and mouse route drawing, dark/light control pixels, manual-learning lifecycle, real synthetic import/recovery transactions and frozen projection file preservation. The full-client probe checks visible image geometry and actually sends mouse events to draw a route, rather than accepting a nonempty model. File-copy/backup simulation does not certify native Inno installation, registry behavior or UAC. Real parent-process waiting now has separate frozen-clone evidence as summarized above. Native-game/DPI/first-visible-pixel and long-run performance remain unverified. The online index URL is still unconfigured; no URL was guessed. PR68 remains excluded.

## Shipped test package and subsequent source

The first private Qt test Setup is source commit `f3d349347b1416f846ac2d3b4b553c63517a4562`, delivered separately from Tk. Its exact bytes are unchanged: 123,691,737 bytes, SHA256 `8ba23b65bfe803ced13422dbed706d8406e8201daaab296eba5f502d2c1cde0b`. User feedback supports the interaction direction; it is not native-game functional acceptance. The original `main.py` / `Main.qml` prototype, production Tk entrypoints and sibling `qt-lab` remain available.

**The 2051 layout-preview node** (`2051ea2e08c9424af8c35b407757638c2bdff4d6`) adds manual recognition/floor controls, settings module editing and local shortcut recording, update/history/diagnostic feedback, and a custom gallery editor/display adapter. Root built that isolated preview and checked its frozen self-check/own-window UI; installation/native-game acceptance and formal release are separate. Its product identity/directory is distinct from f3.

## AD7 features after 2051 (historical node)

The next source connects `OnlinePackagesService` / `ClientOnlinePackages.qml` and the original `MapPackageDownloadService` to the main shell. Checking/downloading remains explicit; an unconfigured address makes no startup request. A successful download is only a cache result: package ID, size, SHA256, resource generation and the user's selection generation must still match before the existing local inspection/import/confirmation/lease path accepts it. Cancellation, local changes, and even an away-and-back selection discard late automatic import. Eight fake/local checks cover these seams, including a real synthetic importer handoff; no real HTTP request was made.

The explicit Return to Game action now uses one shared `app/game_window_focus.py` service from both Tk and the Qt facade. It validates the still-bound HWND/PID/game identity before and after the original restore/ordinary foreground request, reports failure rather than retrying or attaching input, and performs no native action at construction. Coordinator feedback is visible in the home status. Fake native checks and the own-window unconfigured-online composition check do not establish real Win32/browser/server acceptance.

**Neither online-package flow nor Return to Game is present in the 2051 binary.** Both are included in AD7; their real-server/native acceptance remains separate. The two existing private products/worktrees remain untouched. PR68 is registered only in ignored `work/qt-evidence/pr68-compatibility-followup.json`; its outline change has not been fetched, merged or applied.

The runtime still uses the shared production providers, entry verifiers, solvers, residual gates and core. Manual selection never grants a pose; confirmation and manual floor correction acquire a frame and run the original fit/residual/core path. The old two-second manual floor hold and latest-valid observation policy are now extracted into `app/floor_observation_policy.py` and reused by Tk and Qt. The engine owner reports 192 controlled old/new state combinations match, 55 relevant offline checks and six existing floor seams pass. A wider rerank fixture lacks `_cancel_native_resource_validation`; that failure is recorded separately, not counted as a pass.

## Earlier layout reference history (superseded after AD7)

The readable Tk source establishes preferred 1000x720 and minimum 650x470 DIP, 96/80-DIP rail, a 60-DIP header, home columns and control geometry. Blue/white visual references are the existing `library-bluewhite-20261026-0533` static drafts; resource composition follows `map-library-static-20261029` revision 3 and the actual Tk 5:4 resource split. Control geometry is also supported by existing own-window Tk screenshots (352x243 expanded, 64x64 collapsed). These artifacts do not independently prove approval or identify 38490F4F real-game pixels. The restored source preserves blue/white color preference rather than copying the current Tk dark palette.

Reference receipts are in ignored `work/qt-evidence/layout-reference-main.json` and `layout-reference-window.json`. The root integration probe in `post-f3-integration/client-probe.json` passed 18 actual own-Qt-window assertions with zero QML warnings; subsequent size/page evidence is in `post-f3-layout/layout-probe.json`. Both use isolated resources/config and disabled game capture/keys. No native-game, mixed-DPI, first-visible-pixel or long-run performance acceptance is inferred. The 500ms initial / 300ms reopen targets remain unproven.

Both providers prepare: nightmare 26 maps/78 typed layers; difficult 30 declared maps/58 typed layers, with map29's two reliable native-layout bindings explicitly missing. Existing same-input saved-frame evidence matches the old map/floor/canonical/display H exactly; historical or synthetic input never grants public live authority. The 200ms refresh trigger is a scheduling interval, not a latency guarantee.

## Production action baseline and current Qt status

Production method names without a module qualifier belong to `app/identityv_daily_assistant.py::DailyAssistant`. UI composition comes from `app/desktop_main_ui.py::DesktopMainUI`. The original 18-action inventory is retained below, with the previously omitted chest auto-tap action added. The delivered-node summary above takes precedence over historical source/2051 status cells. Qt owners are relative to this directory. Earlier prototype-only implementation statements are historical, not the current client status.

| User action | Existing command / service and final consumer | Qt owner / entrypoint | Current state and migration gap |
| --- | --- | --- | --- |
| Open home, navigate, minimize/restore, close or return to game | `DesktopMainUI._build`, `show_page`, `_open_control`, `_return_to_game`; `focus_game_from_desktop`, `request_close`, `request_desktop_close`, `close` | `client.py`, `Client.qml`, `ClientHome.qml`; `SessionService` / `WindowController` | In 248: navigation, drained close and return-to-game via the shared bound-window helper. Own-window close cases pass; actual game foreground restoration is unverified. |
| Prepare/start, cancel preparation, end a round | `_route_presentation_snapshot`, `start_round`, `toggle_round`, `end_round`, `_service_round_start_intent`; home `_start_round` chooses toggle while preparing | `SessionService` + `ClientHome.qml`; `WindowController` controls | Integrated `LiveRuntime` and `SessionService` own start/end/map work with shared providers/core. Synthetic chains pass; no native-game round lifecycle acceptance is claimed. |
| Change gameplay mode | `set_game_mode` → `_apply_game_mode_setting`; guards import and hotkey editing, validates resource availability | `SettingsService` / `SessionService` → `EngineAdapter` | Gameplay mode/settings now reach the integrated session/runtime; browser mode remains a separate context. Both explicit-root providers prepare; missing native bindings remain visible gaps. |
| Browse installed maps, select author/source, search, inspect floor/variant | `MapResourcesPage` / `MapResourceBrowser`; `map_resource_context.load_browse_context`, `map_resource_model.MapResourceModel`; preview stamps and bounded image work | `ResourceService` / `ClientGallery.qml` | In 248: existing stable resource model and async/versioned previews. Own-window checks confirm stable selection/scroll, zero unchanged-refresh model mutations and light search controls. Browsing remains separate from live identity. |
| Import a ZIP, cancel, confirm required replacement | `inspect_map_package`, `continue_auto_map_package_import`, `confirm_map_package_import`, `cancel_map_package_import`; `star_map_import.inspect_package`, `install_author_package_live`, `install_package`; helper/receipt handling | `ResourceService` / `ClientResources.qml`; `client_import_handoff.py` | In 248: existing transaction/lease services, live author replacement, core-import helper and request-bound receipt. Native cloned helper wait/reject/commit passes; main Qt internal launcher and running-game import acceptance remain separate. |
| Download/check packages and show progress/cancel | `check_online_map_packages`, `download_online_map_package`, `cancel_online_map_package`, `online_map_packages_snapshot`; `star_map_download.MapPackageDownloadService` | `OnlinePackagesService` / `ClientOnlinePackages.qml`, then `ResourceService` | In 248: existing bounded transport, progress/cancel/retry and tuple-bound local import. Unconfigured status makes no startup request. Real-server acceptance awaits the confirmed map-index URL. |
| Upgrade an installed package, recover/rollback interrupted work | Same import flow; `star_map_import.recover_package_upgrade` / `rollback_package_upgrade`; `map_package_upgrade.commit_upgrade`, `recover_pending_upgrade`, `rollback_upgrade` | `ResourceService` / `ClientResources.qml` | In 248: existing local transactions, hash-bound recovery/rollback confirmation and separate normal-exit helper; journals/backups/conflict checks retained. Isolated interrupted-transaction recovery passes; native recovery-helper end-to-end UI handoff remains unverified. |
| Inspect and remove an owned author package | `managed_author_packages_snapshot`, `inspect_author_package_removal`, `confirm_author_package_removal`; `author_package_management.inspect_author_package_removal`, `remove_author_package_live` | `ResourceService` / `ClientResources.qml` | Integrated owned-package inspection/removal and hash-bound confirmation exist through `ResourceService`; the current UI resource-management readiness probe passes. Broader destructive-operation acceptance is not implied. |
| Use another author's route or coffin variant | `select_route_profile` → `_apply_route_profile_setting`; `route_selection_snapshot` distinguishes selected/effective/fallback; `_apply_coffin_variant_setting`; `OptionalAuthorDisplays`, author catalog and per-floor display bridge | `SettingsService` / `SessionService`; `ClientGallery.qml` / `ClientSettings.qml` | In 248: author preference/runtime reconfiguration and original coffin catalog/adapter contract; stale or mismatched display bindings rejected. Exact display bindings remain provider-owned; native-game pixels remain unverified. |
| Create/copy/rename/delete/import/export a custom gallery; draw/reorder/undo routes and use gallery | `CustomMapEditor` controls; `custom_maps.GalleryStore`, `MapGraph.plan`, `render_game_layer`; `custom_map_snapshot`, `use_custom_gallery` | `ClientCustomGallery.qml` / `CustomGalleryService`; `client_custom_display.py` | In 248: real GalleryStore/MapGraph CRUD/routes/undo/import/export/confirmation and original render_game_layer display adapter, separate from identity/pose. Baked 26-map/78-layer fallback is included; frozen actual image/floor selection and mouse route drawing pass. |
| Edit map/action shortcuts; confirm/cancel conflict-checked candidate | `begin_hotkey_edit`, `hotkey_editor_snapshot`, `confirm_hotkey_edit`, `cancel_hotkey_edit`; `hotkey_bindings.parse_chord`, `validate_unique`, `modifiers_match`; atomic `_save_settings` | `SettingsService` / `ClientSettings.qml` | Post-f3 settings provide Qt-local keyboard/mouse shortcut recording, conflict validation, confirm/cancel/unbind and atomic draft save. Recording and text-entry suppression are combined without enabling native polling. Real native shortcut acceptance remains separate. |
| Use global shortcuts for map edges, controls, round, visibility, candidates, floor, confirmation | `ACTION_HOTKEYS`, `_poll_management_hotkeys`, `_handle_action_hotkey`, `_handle_map_hotkey_event`; `session_state_machine` foreground and transition guards | `NativeShortcutBackend` → coordinator / `SessionService` / `LiveRuntime` | `client_input.NativeShortcutBackend` is connected and defaults disabled until explicit window binding/enabling. Foreground/text-entry guards exist. Post-f3 candidate/confirm/reselect/cycle-floor commands now reach the shared runtime; real global-key acceptance remains unclaimed. |
| Open/hide/collapse/expand/drag floating controls; reorder modules | Actual `GameControlView` command map constructed in `_build_ui` (lines 7423–7452); `set_control_visible`, `collapse_control`, `expand_control`, control drag methods; `control_layout_snapshot`, `apply_control_layout`, `control_layout` | `WindowController` / `Client.qml`; `SettingsService` layout preferences | Integrated controls, collapse/show/hide and window routing exist. Post-f3 manual controls and real module order/visibility/display options are connected. Expanded/collapsed control geometry follows original own-window evidence; native focus parity remains unverified. |
| Toggle aligned visibility; independently adjust aligned/pinned/interface opacity and size | `toggle_sheet_visibility`, `change_sheet_opacity`, `change_reference_opacity`, `change_interface_opacity`, `change_sheet_scale`; `OpacityUpdates`; `_save_settings` | `SettingsService` / `WindowController` | Integrated settings/controller apply aligned/reference/interface opacity and size preferences. Sound is connected. Grid/experimental fields retain their old compatibility semantics rather than advertising new visual features. Native display behavior remains unverified. |
| Show/hide pinned reference map; edit position, Finish or Cancel | `reference_sheet_snapshot`, `set_reference_sheet_visible`, `show_sheet`, `_reference_sheet_may_show`, `move_sheet`, `finish_reference_sheet_move`, `cancel_reference_sheet_move`; `ReferencePositionEdit`, `ReferencePointerMailbox` | `ClientReference.qml` / coordinator / `WindowController` | Integrated `ClientReference.qml` and coordinator reference editing use shared edit state. Own-window probe verifies persistent edit after release, movement, Cancel rollback/passthrough and Finish persistence. Native game context/DPI acceptance remains. |
| Show current aligned route; reopen/close map, switch floor, drag/zoom | `_start_alignment`, `_align_confirmed`, `_show_aligned_overlay`, `_restore_aligned_overlay_surface`, `_mark_overlay_display_verified`, `_suppress_map_surfaces_immediately`; `AlignmentCoreV2`, `RoundPose`, `AlignmentPoseTransaction`, `RoundLayerGroup` | Existing `ReplayCanvas.qml` for saved matrices only; `ClientAligned.qml` / coordinator / `LiveRuntime` | Integrated `ClientAligned.qml` consumes revocable `LiveRuntime` render authorization through the coordinator. A 200ms refresh schedule drives current map work. Synthetic frame chains exercise floor/refresh authorization; replay itself never authorizes a game overlay. |
| Recapture, choose/reselect candidate, confirm map, inspect/correct floor | `capture_now`, `_start_map_reselection`, `_cycle_candidate_by_key`, `on_select`, `confirm_route`, `select_floor`, `_open_recognition_details`; `MapIdentityEngine`, `RuntimeMapRecognizer`, `fast_structure_alignment.try_alignment` | `LiveRuntime` / `EngineAdapter` / `SessionService`; capture/input adapters | Real capture provider and shared live-runtime recognition/alignment are connected behind explicit user binding/enabling. Automatic synthetic floor/refresh chains pass. Post-f3 candidate navigation/selection/confirmation/reselection and floor selection/cycling use the shared gate/runtime. Manual floor intent is distinct from sensor output; no caller H or relabelled pose is accepted. |
| Read help, runtime/route notices, update notices/history, export diagnostics, donation | `DesktopMainUI._build_help`, `show_update_dialog`, `_check_updates`, `_export_diagnostics`; `UpdateService`; `route_notification_snapshot`, `FallbackNoticeTracker`, route/status windows; `support_window.open_support_window` | `ClientHelp.qml` / `ClientInfoService` | Integrated `ClientHelp.qml`/`ClientInfoService` show runtime facts, support assets, real update-service state and opt-in diagnostics export. The probe uses a local update fixture; post-f3 source additionally provides actual release history/notes/read/skip state, check retry and diagnostics receipts/path choice. The follow-up shares the original bounded diagnostic ZIP implementation; native dialog/browser acceptance remains unverified. |

## Shared runtime boundary

### 198B follow-up: actual caller parity

The game report exposed orchestration omissions despite unchanged solver files.
The follow-up restores the original `NativeSimilarityState` consumer and its
separate `GROUP_LOCKED` provisional display and `MEMBER_VERIFIED` identity. A
fresh explicit map/capture within a provisional group uses the old 1.85s rank path and accepts that frame's
proposal; passive group review keeps its original 0.2s budget/1s interval. A
group's provisional member is not replaced by a final candidate before its own
pose/material transaction succeeds. Non-group auto-confirm still requires the
top strong suggestion and hard gate. No extra pose/recovery identity consumer
or mode-specific recognition algorithm was introduced.

Native group promotion uses an isolated target core and material acknowledgement:
the Qt staging Image must accept the target after matrix/client geometry checks,
then the runtime rechecks capture/frame/floor/resource/epoch scope and commits
core, identity, render and reference together. Rejection, identity failure or a
late acknowledgement cannot revive an expired source. The still-current source
is retained while a sibling is checked. Hidden staging is material acceptance,
not proof of GPU presentation or real-game authorization.

Production sample validation and the original optional portable-seed path are
restored; diagnostic history remains isolated. The supplied baseline contains
no portable-seed file, so restoring its path does not claim seeds were loaded.
Original entry-seed/pose-index call parameters are restored with their existing
switches and validation. Solver thresholds and algorithms are unchanged.

Controls now keep difficulty selection/enabled styling stable during passive
refresh, distinguish group provisional/member identity, and state when the
user's aligned-display preference is off. Runtime transitions and surface
acceptance/hide/drop reasons use the existing bounded asynchronous diagnostic
writer, independently of the human-facing recent-problem list. Cross-run log
retention is still unbounded; no historical logs were deleted in this repair.

Post-F2 source restores the game's floor-button mouse-intent producer. The
existing Win32 input thread observes pass-through left presses only for the
bound foreground game and stamps a nonblocking current-frame context. The
original hit-test body is shared with Tk. Ordered UI clicks create the original
2-second intent, revoke old work/display, and request a fresh capture; the click
records the target floor immediately, without fabricating an observed floor or
pose. New highlight evidence must match before that floor's pose is authorized.
The click does not add a hard lock against later stable automatic floor changes.
Queued clicks from a changed capture, cycle,
window or resource context are rejected. This source repair is **not included
in the retained F2CB49F7 installer**, and no new Setup is implied.

The dormant Qt intent branch's extra hard lock was removed to preserve the
original automatic/hold rules. The original V2 known-frame floor-conflict guard
is also restored before pose work (including manual confirmation and cached or
retained refresh); an unknown observed floor still permits the original manual
fallback. A conflict reports observed/target floors and `fitAttempted=false`.
The native adapter follows Microsoft's [LowLevelMouseProc](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc)
and [MSLLHOOKSTRUCT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-msllhookstruct)
contracts, reusing the existing message loop and unhook/drain lifecycle.

The representative author-fallback gap is closed in the next source node:
Tk and Qt share the extracted original effective-author policy and shared
validation branch. Base catalog authors and registered difficult legacy layers
are consulted before structural fallback. An entirely undeclared nightmare
author layer can use the existing BaseMapDisplay through the actual committed
custom-library service; a declared but missing/corrupt/unregistered layer uses
the original structural fallback. Preferred settings remain unchanged, with
effective profile and display provenance reported separately. These checks
cover representative real baseline and synthetic author cases, not every
author/map combination; no optional author originals are bundled by the repair.

F2's later real run supplied a separate presentation failure: a valid F1 pose
was dropped because Qt's friendly screen name differed from the Win32 DISPLAY
identifier. Screen correspondence now requires matching full native monitor
geometry against Qt origin plus logical-size/DPR, with bounded rounding and
unique selection; an ID match also requires matching geometry. No primary
screen guess or pose/capture guard bypass is used. Source tests cover fractional
scaling, negative coordinates and ambiguity; actual read-only desktop evidence
uses this machine's three 100% screens and synthetic client rectangles, with no
game or visible window.

The same run also exposed a caller classification difference: Qt labelled any
full shared fit after an existing pose as translation-only reopening work. The
original full-fit consumer uses initial-structure-v2. That classification is
restored; actual cached translation refresh retains its translation-only guard.
This is not proof that every recorded rejected fit was geometrically correct.

Remaining native-game timing and original continuous map08/B1 pose equality need
separate evidence; the original failing frames are unavailable.
Difficult highlight margin/diagnostic-field
differences do not explain the reported nightmare B1 geometry failure. Synthetic
state/Qt tests and saved-frame replay are explicitly distinct from game testing.

Both nightmare and difficult must use the **same** identity, initial/subsequent alignment, floor transition, front/side entry strategy, job queue/cancellation and commit implementation. Differences belong in resource providers, supported-floor metadata, coordinate contracts and required parameters. Missing entrances/bindings must remain explicit capability gaps. The integrated `LiveRuntime` now adapts the shared production path; further parity work must retain that single owner; importing or constructing `DailyAssistant` as a headless service is not a valid shortcut because it owns Tk variables/widgets, native input, scheduling, capture and display lifecycle.

The Qt boundary should expose immutable snapshots, structured command results and revocable render payloads. `SessionService` already defines injected `snapshot()` / `dispatch(action, payload, cancelled)` and treats operation receipts as distinct from runtime authority; preserve that distinction during integration. Keep the existing `MapIdentityEngine`, `fast_structure_alignment`, `AlignmentCoreV2` / `RoundPose`, `AlignmentPoseTransaction`, `RoundLayerGroup`, `session_state_machine`, resource and transaction services as the computation/validation owners. UI code owns presentation and native-window delivery. A replacement UI must not create a second mode-specific solver or fabricate poses to complete a screen.

## Baseline seam risks (some now implemented; acceptance remains)

1. **Tk/runtime coupling:** production commands frequently read Tk variables and schedule `root.after`. The shared `LiveRuntime`/coordinator now provides the Qt runtime owner; retain one scheduler/command boundary as unsupported controls are completed; do not run two independent owners against a session.
2. **Stale results and revocation:** carry request/frame/open-cycle generation, mode/map/floor, resource identity and display source through worker → commit → Qt pixels. Mode change, close, end round, import and floor change must revoke old payloads immediately; hidden pages must still drain/cancel work.
3. **Three different selections:** resource browsing, preferred/effective author route and locked structural identity are separate. Each rendered floor needs its actual source/bridge; author changes must not silently alter identity, pose or floor. Audit the remaining legacy reset branch.
4. **Native windows and edit state:** preview no-activate/click-through flags are only a starting point. Pinned map, aligned overlay and interactive controls have different focus, hit-testing, coordinate/DPI, visibility and editing contracts. Restore click-through on Finish/Cancel/close/failure.
5. **Resource writer coordination:** live addition/removal, stopped-app upgrades and helper handoff have different capabilities. Preserve receipts, hash-bound confirmation, recovery and writer exclusion; invalidate real image/structure caches and older work after a committed change.
6. **Shortcuts:** one global listener must feed the shared command queue, preserve foreground/text-entry and repeat-edge guards, and shut down deterministically. Keep shortcut recording separate from action dispatch; show actual saved values and elevation/entry diagnostics when implemented.
7. **Settings compatibility and shutdown:** preserve unknown/user data, per-mode choices, geometry and atomic save behavior. Only a frozen, baked original-product profile uses its own EXE-parent legacy data paths; source/test profiles remain isolated and never discover an installed client implicitly. Drain transaction writers while keeping close outcomes visible.
8. **Acceptance evidence:** historical synthetic replay and source inspection prove neither live authorization nor game timing. Check both modes with representative supported floors/resources, success/failure/cancel/repeat/stale paths, and measure first correct visible pixels separately from solver time (500ms initial / 300ms reopened targets remain unproven).

## AD7 production-node history and retained boundaries

The next source integrates the existing online package service, explicit return-to-game, owned-window recovery/rollback confirmation and a separate parent-bound recovery helper. It preserves the original closed-app resource transaction checks. The reproduced exit hang was a window close veto after workers drained; `closeReady` now accepts closing only after accepted writes finish. See [SHUTDOWN.md](SHUTDOWN.md).

The coffin selector now follows the original catalog and coordinate-adapter contract, with exact binding checks and stale-pixel rejection. Sound, the two-step onboarding trial and the original bounded/redacted diagnostic ZIP exporter are connected. `experimentalAutoOverlay` is a forced-on legacy compatibility field; `difficultGridEnabled` was a historical recognition-option fallback, not an implemented Tk grid UI. They are preserved as data rather than advertised as new visual features. Reduce-motion controls the existing replay animation.

| Additional original action | Existing service | Current Qt consumer (included in 248) |
| --- | --- | --- |
| Enable chest auto-tap; select hold/repeat key and interval; show state and stop | `chest_auto_tap.ChestAutoTap`, original `chest-auto-tap.json` | `ClientChestAutoTap.qml` / `ChestAutoTapService`; one owner, explicit per-launch arming, existing conflict checks, validated foreground binding, text/recording/focus/close suspension |

The authorized editor input now contains 26 maps / 78 typed layers, prepared by `tools/prepare_qt_editor_base.py`. It contains no optional author images. Production uses the old outer editor directory when present, otherwise the baked base. Production installer projection preserves an existing resource plane as a whole; only a fresh installation receives the whole baseline. The editor base is a separate all-or-nothing family. Program/version identity still updates. Synthetic checks pass; no actual installation is inferred.

The next Setup targets the original AppId/name/EXE and uses the original settings, gallery, update-state and per-mode memory paths. Loading old history is connected; new human-label/learning writes were absent in AD7 and are addressed by the follow-up source above. Both delivered f3/2051 installers remain unchanged. Source completion does not imply a built, installed or game-accepted replacement.

The current remaining verification is listed at the top of this document: frozen import-helper normal-exit behavior itself now passes, while the Qt internal launch handoff, real original-product adoption and native game behavior remain separate. The online index URL is not configured in this execution environment, so real server index/download acceptance is blocked by missing configuration. No substitute URL has been guessed. Two difficult map29 native bindings remain explicitly unavailable in the supplied light input. First-visible-pixel latency and long-run resource targets remain unproven.

## Remaining acceptance order

1. Isolated native installer verification is complete: one successful revised-fixture run, one deliberate copy-failure run, native rollback plus original DeinitializeSetup restoration, Chinese control text and absent fixture registry identity. An earlier initialization-hook failure changed no old files; the successful run's ANSI report was decoded offline using observed Windows ACP 936 without rerunning Setup. The disposable GUID, disabled uninstall registration/shortcuts and inert payload deliberately exclude real product adoption. Do not repeat or expand this fixture.
2. Keep the main-Qt internal helper-launch integration gap explicit. Native helper identity/wait/commit itself now has frozen-process evidence; do not repeat it or call the external fixture a UI-button test.
3. When the confirmed map-index URL is supplied, check the existing online index/download/import chain. No server write, substitute URL or new download service is needed.
4. Retain representative native-game/window/shortcut/DPI and timing acceptance as separate work requiring safe real inputs. Existing same-input core evidence is reused; do not ask the user to retest every map or run a second game/input owner during their session.
5. Original-AppId installation/adoption/uninstall and formal release remain separate from the isolated preservation evidence. Do not install into the user's product directory to close this checklist.

Inspection basis: [repository rules](../../AGENTS.md), [development contracts](../../docs/development.md), actual [`GameControlView`](../../app/game_control_view.py) controls and its command map, [`DesktopMainUI`](../../app/desktop_main_ui.py), [`DailyAssistant`](../../app/identityv_daily_assistant.py), [`MapResourcesPage`](../../app/map_resources_page.py), [`MapResourceBrowser`](../../app/map_resource_browser.py), core/resource/package modules named above, and the existing Qt Python/QML files. This document changes no executable behavior.
