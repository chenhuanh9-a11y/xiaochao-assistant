# S1 native candidate private preview

The `s1-auto-test` baked installation profile enables a separate current-frame
candidate purpose in the normal client composition root. It uses the actual
foreground capture provider, selected-mode catalog, structure solver, display
material and Qt publication chain. Settings, UI payloads, and recorded flags
cannot grant this purpose. `liveReady` and automatic identity remain unchanged.

Start prepares the selected S1 mode without selecting a map. An explicit map
opening captures a fresh frame, scans the complete catalog within its cooperative
budget, and can display the first verified full candidate before scanning ends.
Unknown regions remain visible. Candidate display does not assign `_map_id`,
confirm the identity gate, register a group, or retain a static reference sheet.
After scanning, current candidates can be switched without identity confirmation.
Manual confirmation remains a separate optional operation.

Close, cancel, settings/resource/context changes and new opening intents revoke
the current candidate. Same-floor passive probes preserve the opening's list and
do not renew its pose evidence. A changed/unknown floor or capture failure
withdraws the display; a changed valid floor captures and scans again. Old
capture errors and cleanup cannot revoke a newer request's material or pending
owner. All publication still rechecks runtime and capture ownership.

Automatic candidate floor evidence is limited to B1/B2. The existing manually
selected S1 map with automatic four-floor observation remains separate. B3/B4
source-image validation, game accuracy, scaling and latency remain unverified in
this private preview. The 500 ms identity-lock timing target is inapplicable to
an unlocked candidate. Synthetic runtime/QML tests are not game validation.

The new installer has its own AppId, application model, default installation
directory and user-data instance. It protects previous private registrations
and directories as well as the production installation. No public release or
automatic migration from another instance is implied.
