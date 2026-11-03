# PR76 reconciliation on current main

Fixed comparison: PR76 `980d64949dcec3bcfb747dbf8bacb6dfc00fbded`, original base
`6c9881f16ddf646cfc3184e1e7f55d8f6a62bce6`, main
`1b434ea1d553f9f46b4595b44a0dd6962dee7723`. The original diff has 140 paths;
current merge-base diff has 138. Counts are file inventory, not independent fixes.
Do not merge the old branch wholesale or use old branch test totals as current
integration evidence.

## Behavior decisions

- Already on main: shortcut recording/committed hints/native mouse single owner,
  modifier behavior/freshness/onboarding from `9e04cc5` and subsequent integration;
  Tk silent capture errors; fail-closed Qt production identity; bounded environment,
  problem history and export from `5b0d60c`/`76546ad` and later privacy corrections.
- Retain main safety differences: no pathname automatic-image eviction; no promotion
  of uncommitted PNGs after a crash; automatic sample consent stays separate from
  export; foreign-window identities stay omitted. See `diagnostic-main-port.md`.
- Migrated resources/startup in `f3399dc`: OS resource lease, stable process identity,
  live additions/removal/recovery/terminal retirement, reference reader barriers,
  managed candidate startup repair, actionable resource errors, partial Qt cleanup,
  release identity and mandatory matching-runtime guard for installer-only reuse.
- Migrated capture/chest/presentation in `5370d14`: Windows safe-fallback capability
  and bounded cause metadata; keep main preflight/foreground-return semantics;
  terminal nonblocking chest shutdown and cleanup drain, unsaved compiled-binding
  conflict detection, no-save focus resume, main physical release gate and sticky
  error retained; exact reason classification and literal PlainText presentation.
- Remaining display integration: stale worker failure/cancel must not revoke a newer
  display owner (token, authorizationId, transformRevision); fresh staged capture
  after dragged group selection; first unobserved drag probe hides presentation,
  second may close the map; pixel-confirmed X hit instead of broad HUD region;
  queued `probe_map_after_drag` keeps authorized display; bounded open recovery;
  exact input-sequence reconcile. Keep main #89 foreground-return contract and
  narrow the floor-policy critical section without weakening origin checks.
- New production seam with PR91 `69345214`: `b9327e7` (separate verification branch)
  maps explicit bundled display images to verified program-owned qt-author-base.
  It never copies them into active resources or creates import receipts. Apply
  after the PR91 and resource changes. Its local parent `f53fb04` is only a build
  verification assembly and MUST NOT be merged as product integration.

## Evidence and remaining gates

- Resource/full export suite: 261 run, 255 passed, five native-Windows skips,
  one Linux-only subprocess.STARTUPINFO environment error. Not a full pass.
- Qt resource lifecycle suite with QGuiApplication initialized first: 68 run,
  67 passed and one platform skip.
- Chest/cleanup/exclusion/UI/diagnostic set: 92 passed. Subsequent narrow privacy
  suite: 11 passed. These are synthetic/mock boundary tests, not real game input.
- Production relocation/projection/managed-resource set: 76 run, 75 passed and
  one native-Windows skip. Includes real generated projection interpretation,
  valid author receipt preservation and startup transaction verification.
- Capture full-client tests need Windows because existing blank-frame imports
  initialize ctypes.windll. New compatibility tests and two user-message tests
  also require the display/input worker's preparation and raw-message seams.
- Final combined source must rerun those tests plus display, input, diagnostic,
  reader, startup and production suites. A clean Windows production freeze of
  the exact final commit plus both helpers is required. Verify actual PE/UAC,
  installer projection, resource lock, signature status and artifact SHA256.
  Actual Setup install/upgrade/rollback/uninstall and game acceptance remain
  separate gates. No merge, release, native installation or game evidence is
  claimed by this report.

## Every original changed path

M = selective migration; R = separate display/input integration pending;
S = superseded by safer main; C = main behavior retained or its compatible
regression coverage retained; H = historical evidence only. C is not a claim
that the old file is byte-identical or that a future combined build has passed.

| Original path | Decision | Detail |
| --- | --- | --- |
| `app/chest_auto_tap.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/diagnostic_evidence.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `app/diagnostic_export.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/failure_sampling.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `app/hotkey_keys.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `app/identityv_daily_assistant.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `app/managed_base_resources.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/map_close_control.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `app/map_package_recovery_plan.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/map_package_removal.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/map_package_upgrade.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/package_identity.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/packaged_product_identity.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/qt_startup_diagnostics.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/resource_transaction.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/runtime_environment_diagnostics.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `app/star_map_import.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/star_map_import_helper.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `app/star_map_recovery_helper.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `docs/automatic-failure-sampling.md` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `docs/managed-base-resources.md` | M | Selective main adaptation in f3399dc / 5370d14 |
| `docs/qt-capture-compatibility.md` | M | Selective main adaptation in f3399dc / 5370d14 |
| `docs/qt-drag-group-choice.md` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `docs/qt-drag-probe-presentation.md` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `docs/qt-field-feedback-8f8b.md` | H | Old branch history only; not current-main validation |
| `docs/qt-map-open-recovery.md` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `docs/qt-pointer-diagnostics.md` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `docs/qt-preparation-diagnostics.md` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `docs/qt-running-refresh-drag.md` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `docs/runtime-environment-diagnostics.md` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/Client.qml` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/ClientControls.qml` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/ClientHelp.qml` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/ClientOnboarding.qml` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/ClientProblemsPanel.qml` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/ClientResources.qml` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/ClientSettings.qml` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/ClientToast.qml` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/backend.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_capture.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_capture_exclusion.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_chest_auto_tap.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_coordinator.py` | M | Selective main adaptation in f3399dc / 5370d14; display/input worker also changes this file |
| `experiments/qt_quick/client_failure_samples.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/client_info.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_input.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/client_input_diagnostics.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/client_live_runtime.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/client_map_input.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/client_native_keys.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/client_native_mouse.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/client_onboarding.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/client_package_manager.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_paths.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_preparation_diagnostics.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/client_problem_history.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/client_problem_messages.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_problems.py` | M | Selective main adaptation in f3399dc / 5370d14; preparation forwarding must be joined with display worker |
| `experiments/qt_quick/client_process_identity.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_reference_presentation.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_resource_errors.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_resource_startup.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_resource_startup_recovery.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_resources.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_session.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/client_settings.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/client_startup_cleanup.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/client_window_binding.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test8f8b_diagnostics_sampling.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/test8f8b_map_close_control.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test8f8b_stale_presentation.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test8f8b_window_binding_recovery.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_additions_recovery.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_capture_compat.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_chest_auto_tap.py` | C | Main explicit Qt intent/release gate plus current chest-intent regressions, not old replacement |
| `experiments/qt_quick/test_client_chest_binding_recovery.py` | C | Main explicit Qt intent/release gate plus current chest-intent regressions, not old replacement |
| `experiments/qt_quick/test_client_chest_focus_recovery.py` | C | Main explicit Qt intent/release gate plus current chest-intent regressions, not old replacement |
| `experiments/qt_quick/test_client_composition.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_control_actions.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_coordinator.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required; display/input worker also changes this file |
| `experiments/qt_quick/test_client_coordinator_input_recovery.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_diagnostic_integration.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/test_client_downloads.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_drag_group_choice.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_drag_group_choice_boundaries.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_drag_probe_presentation.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_game_floor_input.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_input.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_input_event_freshness.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_input_recovery.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_live_controls.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_map_input.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_map_open_recovery.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_map_recovery_late_error.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_onboarding.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_package_manager.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_partial_cleanup.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_pointer_diagnostics.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_preparation_diagnostics.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_problem_history.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/test_client_problems.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required; preparation forwarding must be joined with display worker |
| `experiments/qt_quick/test_client_process_identity.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_resource_errors.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_resource_readers.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_resource_recovery_lease.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_resource_startup.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_resource_startup_recovery.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_resources.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_running_refresh_drag.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `experiments/qt_quick/test_client_settings.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_shortcut_delivery.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `experiments/qt_quick/test_client_transaction_retirement.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_client_user_messages.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `experiments/qt_quick/test_field_failure_samples.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `experiments/qt_quick/test_field_map_close_input.py` | R | Display/input integration owned by separate worker; final SHA/tests pending |
| `tests/test8f8b_package_identity.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_automatic_failure_samples.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `tests/test_capture_environment_observation.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_capture_start_failure.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `tests/test_chest_auto_tap.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_diagnostic_export.py` | C | Main shortcut/onboarding/Tk/diagnostic integration retained; joint consumer tests still required |
| `tests/test_diagnostic_redaction.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `tests/test_live_author_import.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_managed_base_resources.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_map_package_removal.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_map_package_upgrade.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_qt_entry_startup_failure.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_qt_installer_projection.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_qt_installer_reuse_guard.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_qt_startup_failures.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_qt_startup_user_messages.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_resource_transaction.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_runtime_environment_diagnostics.py` | S | Keep newer main closed-schema/consent/handle-storage implementation |
| `tests/test_star_map_recovery_helper.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tests/test_upgrade_retirement.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tools/build_qt_client.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tools/qt-client.iss` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tools/qt_client_entry.py` | M | Selective main adaptation in f3399dc / 5370d14 |
| `tools/rebuild_qt_installer.py` | M | Selective main adaptation in f3399dc / 5370d14 |
