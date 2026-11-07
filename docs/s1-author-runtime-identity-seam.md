# S1 author switching and native identity ownership

This narrow integration follows the author importer, four-floor registration, locked-identity/pose-prior and structural-notice v2 changes. It does not alter import package bytes, native resource contracts or recognition algorithms.

## Ownership split

The S1 private pool and acknowledged pose proposal are native-geometry evidence. Their owner scope is round, season, mode, engine identity, exact native StructureCatalog/resource key, solver-policy generation, capture generation and capture context. It intentionally excludes author profile, display generation, frame, cycle and floor. A retained map ID or pose proposal never grants aligned display on a new frame/floor: the existing current observation, floor and full-fit gates remain required.

Presentation work still owns profile/settings/display generations, frame/cycle, current candidate session/ticket and capture context. Retiring presentation must reject old pixels and ACKs, without deleting unrelated native identity evidence. The structural-notice delivery and background queue use the same native scope, including the existing pool identity/revision and capture-current checks.

## Settings classification

`_s1_solver_policy` explicitly includes the current shared/native policy controls:

- effective `poseIndexCandidatesEnabled`, falling back to `difficultGridEnabled` exactly as the shared solver host does;
- `idvb0059Enabled`.

Changing these retires the private identity pool and acknowledged pose hint. Author preferences, visibility, opacity, route labels, shortcuts, window preferences and interaction layout do not alter the native source geometry. The existing whole-settings generation remains a cancellation fence for in-flight presentation work. Future solver knobs must be added to this explicit signature when introduced. Mode/season/resource/window/round/reselection and positive structural contradiction boundaries retain their existing revocation rules.

The legacy `mapSelectionMode` is retained in SettingsService/on disk as the S0 preference. S1 always uses effective `auto`, including settings written before the manual picker was removed and settings without this key. Runtime chooses this policy from its applied season, not an unapplied settings draft. Startup's settings-before-context ordering and queued season changes therefore do not erase S0 manual mode. The ignored key is not an S1 solver-policy change: saving it retains native pool/lock ownership while the existing whole-settings presentation fence remains in force.

Coordinator rejects stale manual-picker button/hotkey/confirm actions in ordinary S1 sessions and does not write the S0 selection preference from an S1 mode control. Explicit diagnostic manual-alignment sessions retain their separate existing API and are never entered by this normalization. Candidate purpose, resource capability, four-floor contract, current-frame fitting and production readiness gates are unchanged; effective `auto` grants none of these capabilities.

S0 still uses its original settings/display handling; the new solver-policy counter is consumed only by S1 native ownership. Material ACK now also enforces the already-established display-generation boundary in both seasons, matching render_payload's rejection of retired pixels.

## Current provisional author reprojection

A private S1 render deliberately leaves `_map_id`, durable identity and confirmed gate empty. Author selection previously returned before reprojection because `_map_id` was empty. The new path uses only a local display target from an already-current candidate render and its still-current material proposal/session/ticket. It never assigns durable identity, changes the floor, runs a pose sensor or modifies the canonical matrix.

It prepares original author pixels and their exact display bridge against that same current canonical pose, then rechecks the runtime scope, session/ticket, source render and capture context before publishing. The new token gets a new material proposal from the same verified frame/fit; the retired token cannot ACK or mint a pose hint. Closed, unknown, stale, revoked or missing current material cannot be reprojected from the historical private lock alone. Reopening still requires a fresh fit, including when the identity is retained.

## Verification boundaries

New focused tests exercise actual LiveRuntime transactions with synthetic capture/native fits and author pixels, plus the actual Qt SettingsService → ClientCoordinator → queued SessionService → LiveRuntime chain. The Qt test uses actual save/worker signals; peripheral input/window services are doubles and the QML handler is source-traced, not clicked. Real 87-image import/display coverage is a separate existing acceptance artifact.

Tests cover native→author→native unchanged canonical pose and floor, private-lock retention without durable promotion, new/old token ACK, closed-map fresh fit, unknown fit withdrawal, solver-setting revocation, ignored legacy or omitted selection mode, effective legacy knob aliases, late native evidence across author switching, genuine-policy rejection of that evidence, concurrent author supersession, close/end/capture-context changes, and session revocation during blocked reprojection. The legacy-mode tests also exercise real Qt saves/reloads, startup, B1–B4 reopen, queued S0→S1→S0 context behavior, stale manual control rejection, invalid purpose/resource denial, and blocked captures crossing save/close/end boundaries. Existing S0/runtime/settings/reprojection/custom display, locked-identity, pose-prior and notice tests are retained.

The old locked-identity reset test used an invented `fixtureSetting`; it now changes the actual solver knob `idvb0059Enabled` so it tests the intended policy boundary. Separate new tests prove presentation/interaction keys do not clear native identity.

No Windows-native UI, live-game accuracy or input-to-first-visible-pixel timing is claimed. Final packaging still requires exact native dependency compatibility and the formal client version contract.
