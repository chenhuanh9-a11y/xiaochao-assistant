# S1 author image import, isolated protocol v1

## Scope and authority

This change adds `s1-jiaye-handnotes`, a read-only author display profile for S1 difficult and nightmare. It contains 87 original JPEG canvases: 14 difficult groups and 15 nightmare groups, each B1–B3. Author B4 does not exist. Every B4 request retains the existing canonical-native fallback where the native product supports it. Normal S1 difficulty2 has no author profile.

Author art does not identify maps, validate source geometry, create recognition masks, alter native typed resources, choose another floor, or license projection between floors. The existing native provider and current-frame geometry-fit requirements remain authoritative. Original author annotations and margins are neither cropped nor rewritten.

This is a cloud validation integration. It requires the importer/display code in this patch; an unchanged released 1.0.15 importer does not recognize the package. No Windows installation, game session, frozen source, remote repository, or public release is changed. Final release must confirm the complete target native inventory and its version contract before shipping.

## Archive and schema

The ZIP contains `map-package.json` plus exactly 88 `data/` members: 87 JPEGs and one display-binding JSON. Existing archive entry, byte/count/expansion limits, case-insensitive duplicate detection, path normalization, anti-symlink checks, transaction locks, staging, journaled replacement, rollback, receipt ownership and uninstall remain in the common importer. S1 support does not loosen the S0 accepted versions, path scopes or dependency rules.

Metadata has `schema=xingtu-s1-author-display-package-v1`, `schemaVersion=1`, `revision=1`, `season=s1`, `sourceVersion=2.104.3099014`, `packageId=s1-jiaye-handnotes`, existing `clientCompatibility`, `files`, and `requiresFiles`. Inventory rows contain exact relative path, byte count and SHA-256. `clientCompatibility` is the existing line-level transaction declaration, not a claim that every historical client implements S1. The importer separately checks supported display versions and S1 protocol availability. A future formal display version must be deliberately integrated and tested rather than implicitly accepted.

Allowed owned destinations are only:

- `_internal/assets/authors/s1-jiaye-handnotes/{difficult|nightmare}/{exact native map ID}/B{1|2|3}.jpeg`
- `_internal/references/author-packs/s1-jiaye-handnotes/display-bindings.json`

The 182 read-only native dependencies are exact SHA/size-bound: for each mode, native manifest, shared-structure index, coordinate pack and structure-version bindings; then typed NPZ and native PNG for each of the 87 B1–B3 layers. They are never extracted or rewritten by the author package. This protects against same-sourceVersion but incompatible resource trees. B4 is absent from author dependency/owned inventories because no author registration uses it; actual fallback stays under the native display contract.

## Per-layer coordinate binding

The sidecar schema is `xingtu-s1-author-display-bindings-v1`. It requires `identityAuthority=false`, `structureValidationAuthority=false`, `crossFloorProjectionAllowed=false`, and `currentFrameGeometryFitRequired=true`.

Each of exactly 87 unique `(mode,mapId,floor)` entries explicitly binds:

- season, sourceVersion, original source group/path and original JPEG hash/size/full canvas shape;
- native image resource/hash and typed reference resource/hash;
- baseSnapshot, structureVersionKey, reference canvas height/width and `game-native-pixels:{mapId}:{floor}` coordinate space;
- finite, orientation-preserving similarity matrix `matrixDisplayToReference`, in homogeneous pixel coordinates with x right/y down;
- offline registration status/method, feature inlier count, silhouette overlap and evidence hashes.

The entire sidecar must match the code's independently reviewed hash allowlist. An archive cannot approve its own matrix by claiming `offline-reviewed`, substituting metrics, or changing its declared hashes. A new registration requires separate review and a deliberate allowlist change. These hashes establish integrity/approved bytes, not a publisher signature or an external guarantee of provenance.

## Windows native re-registration, 2026-11-06

The original local source contains the same 87 JPEGs / 83 unique hashes as the
candidate source inventory. They were re-registered against the actual Windows
native PNG/typed-NPZ inventory; the incompatible cloud dependency declarations
were not relabeled. The generic full-affine candidate was rejected by the
existing importer. RANSAC similarity fitting with held-out feature matches then
produced 87 valid bridges: minimum filled-outline IoU 0.991193, minimum 18 train
inliers and maximum supported hold-out median residual 4.305515 native pixels.
The six lowest-IoU native/warped-author overlays were visually reviewed. This
does not validate route instructions, every internal wall, or game accuracy.

Approved Windows sidecar SHA256:
`8e5a11d41c2bdd5c4fdc55a19089e1005e8fd47434bde75affa40f8928ac4b9d`.
It preserves original JPEG bytes/canvases, binds all 182 actual read-only native
dependencies and retains native-only B4 fallback. The older cloud registration
remains separately pinned and still rejects incompatible Windows dependencies.

A separate `missingAuthorLayers` list contains all 29 B4 identities with `fallback=canonical-native-if-available`. Missing/duplicate/foreign layers, forged B4 images, invalid matrices, mismatched native references and unreviewed draft bindings reject.

## Evidence for this registration

The official original ZIP has 87 JPEGs / 83 unique hashes. Repeated B1 images cannot independently identify a layout. Offline SIFT with one-to-one train feature matching and RANSAC similarity fitting produced candidates; full filled external-outline overlap and three-floor consistency were then checked. The initial six degenerate fits were rejected and are retained as failed evidence, not approved registrations.

All 29 groups have a unique common candidate among per-floor IoU >= 0.98 IDs; all 29 also rank first by mean three-floor IoU. The smallest mean margin is 0.096472 for nightmare 107010 over 107007. Approved layers have minimum IoU 0.987179, minimum inliers 17, minimum inlier coverage 0.400699, and maximum median native reprojection error 3.7212 px. That is not a maximum per-pixel error bound.

Independent review decoded/hashes actual native images, checked cross-floor uniqueness and exact duplicate classes, and visually inspected six worst layers plus a native-resolution worst-case crop. It did not independently refit every layer. Filled silhouette overlap uses the same image pair, can hide internal holes, and is not independent external truth. Route instructions, numbered annotations, live-game recognition accuracy and every typed anchor are not validated by those scores.

## Consumer wiring

`S1AuthorDisplays` joins a copied author-display catalog and display adapters onto the S1 engine after its native context is built. `qt_shared_provider.resources`, native catalog and native adapters remain unchanged. `prepare_display_layer` already consumes exact optional bitmap/matrix registrations and retains existing fallback behavior. Full JPEG pixels are decoded to BGRA without resampling or cropping; the 3x3 matrix bridges original display pixels into the unchanged native reference.

The season browse context adds only validated installed profiles. The actual Qt import picker freezes the selected season, ResourceService passes it through inspection and installation, and the real package manager labels this package S1/both. Native gallery ID `s1-native-structure` maps to runtime `shared-structure`, allowing native → author → native selection. Import/removal notifications use existing engine/resource invalidation. CLI accepts `--season s1`, defaulting to S0.

Whole-package dependency/integrity checks occur during background installed-profile preparation, not every frame or reopen. Pixel caching is thread-locked, read-only, at most six images / 96 MiB. Each open rechecks the current JPEG, pinned sidecar and current typed NPZ before a hit; it never returns stale cached art after those bytes change.

## Validation and remaining release gates

Cloud tests exercise malicious archive inputs, S0 transactions, real import/reimport/receipt behavior, all 87 original-pixel consumer layers, all 29 B4 native fallbacks, real browse counts and catalog hash validation, normal-mode isolation, native→author→native identity preservation, approved upgrade/removal, and cached data mutation rejection. The real Qt-owned offscreen chooser and ResourceService flow are also exercised. This is not the Windows-native chooser or game acceptance.

Final packaging must merge exact hunks, rerun joint tests, verify the complete Windows resource inventory against `requiresFiles`, reconcile the formal client version, and perform Windows/game UI acceptance. If dependencies differ, reject this ZIP rather than silently regenerate assertions or accept sourceVersion alone. Rebuild/review registrations only when the changed resources require it. No candidate or unreviewed mapping may be promoted solely to make installation pass.

### Concrete native-inventory caveat

The cloud `106001--1-shared.npz` is identical in the early geometry tree and the self-consistent rendered-display resource tree: SHA-256 `5d1514ad108bdfba8a25e523b885c7d94397856ff4d861f17a1888d504f0e3a9`. The different expectation comes from `s1-rendered-display-20261105/display-registration-update/seasons/s1/difficult/native-map-manifest.json`, SHA-256 `f74c067fd63b381a80da103aca315ceff5ebb20983deb2d3b010f6d65ee04077`. Its NPZ entry expects `324fbef4f15b0e9eb3f0498dcbb495a02287fce49366ca582257eaf08266d360` / 22652 bytes. This is not evidence that the two available cloud NPZ files differ, nor proof of the final Windows file bytes. The final Windows typed inventory remains unavailable.

The current author ZIP was actually inspected against an isolated tree containing that incompatible manifest, and rejected with `missing-or-incompatible-dependency: _internal/seasons/s1/difficult/native-map-manifest.json`. See `native-inventory-caveat.json` and `incompatible-base-rejection.json` in the evidence bundle. Final packaging must bind/revalidate the chosen verified target; this cloud result cannot be relabeled final-native consistency.
