# Display coordinate bridge differential check

`DisplayBridgeResolver.Resolve(catalog, entry, profile, referenceToClient)` maps the exact
selected NPZ current-reference coordinates to a real display layer. It calculates:

```
referenceToCanonical = nativeToCanonical * referenceToNative
displayToReference = inverse(referenceToCanonical) * displayToCanonical
displayToClient = referenceToClient * displayToReference
canonicalToClient = referenceToClient * inverse(referenceToCanonical)
```

The caller must already own current-frame geometry validation and must check
`result.IsCurrent()` before consuming a prepared result. Display results grant no
map identity, structure pose or cross-floor authority. Resource snapshots bind file
content because a same-path/same-size replacement is an actual supported import case.
These are inherited source-product SHA-256 checks, not a new release gate.

Supported source bindings:

* S0 nightmare and difficult shared clean structures, with per-floor canvas and
  version matching, and explicit current-reference calibration when declared.
  Nightmare/S1 coordinate packs repeat source layout/world floor; difficult owns
  those fields in `runtimeInterfaces.sourceManifest`. Its actual `sourceLayout`,
  per-floor `sourceHeight`, resource path and versions are checked independently
  against the NPZ canvas and included in `IsCurrent()` resource snapshots.
* Installed baked author layers with actual directory binding, image digest and
  pixel dimensions. Difficult display offsets must agree independently between
  the coordinate pack and catalog `matrixDisplayToNative`.
* S0 `base-map`: raw custom-map image and its declared image-to-native affine,
  verified against the current native mask, image and topology. The original
  Python provider warps this raw image before returning identity; C# retains the
  raw-image matrix. It does not claim to render Python's roads/exits/routes here.
* S1 raw native images, including the registered no-coordinate-change overlay,
  original renderer digest, resource inventory, exact NPZ/image binding and actual
  decoded alpha-footprint comparison.
* Independent `AuthorCoordinateAdapter` admission rules and the
  `OpenCvDisplayRasterValidator.ValidateNativeCorners` reconstruction API for old
  native-static-corner registrations. Its request paths must come from the trusted
  primary-profile catalog/index, never from candidate evidence pairs.

Explicit gaps:

* `difficult-map-29` legacy-clean references have no game-native layout or trusted
  native-to-canonical bridge. `Resolve` returns `legacy-clean-native-bridge-unavailable`.
* The current production canonical packs do not contain old native-static-corner
  rows. `Resolve` does not invent a trusted primary/native-image context for such
  rows; its adapter rejects them without that context. The standalone validator
  supports those inputs but integrating a different old resource pack needs its
  actual trusted catalog/index binding, which is not present in these native packs.
* Imported optional/generic author sidecars and S1 author packages are not yet
  merged into this resolver's base author catalogs. Missing bindings are rejected;
  installed base native displays remain independently supported.

Run from the repository root (the root coordinator owns all execution):

```powershell
& 'E:\codex project\2026-11-08\xingtu-dotnet-lab\python-local-runtime\python.exe' csharp/tests/StarMap.Display.Tests/create_python_oracle.py --source . --resources 'E:\codex project\2026-11-08\xingtu-dotnet-lab\resources' --output '<evidence-directory>\display-python-oracle.json'
dotnet run --project csharp/tests/StarMap.Display.Tests -- 'E:\codex project\2026-11-08\xingtu-dotnet-lab\resources' '<evidence-directory>\display-python-oracle.json' --output '<new-csharp-evidence-directory>'
```

The oracle calls original production `AuthorCoordinateAdapters`,
`shared_route_validation_spec`, `SharedStructureResourceStore`, `BaseMapDisplay`,
`S1NativeContext`/`S1NativeDisplay` and `validate_native_static_corner_registration`.
It uses real packaged resources with synthetic native poses and separately labelled
synthetic corner images. It does not use historical expected geometry as solver input.
Positive/negative gates, actual layer matrices, forged corner evidence and same-length
image replacement are checked. Fixture copies are retained for inspection.
The final positional argument also accepts the new evidence directory without
`--output`. Each actual verdict, reason and matrix is saved in `cases/*.json`
before comparison. The final `display-actual.json` contains all actuals and
failures; mismatches return exit code 1. Replacement fixtures remain under that
directory's `scratch/`; previous Temp fixtures are untouched.

The first C# run incorrectly rejected `difficult-map-01/1` before any composition:
the coordinate pack omits `sourceLayout`/`worldFloor`, while the actual NPZ declares
`canvas.layout=103019` and `canvas.worldFloor=2`. Requiring duplicate fields from the
nightmare coordinate schema compared missing values to valid NPZ values. The
corrected reader follows difficult's existing source manifest ownership instead:
`sourceLayout=103019`, floor `1`'s `sourceHeight=2`, exact reference NPZ path and
version tuple must all agree. A fixture changes the independent source height,
then checks both a fresh rejection and stale-snapshot invalidation. This is not
an inferred floor-number mapping or a relaxed numeric threshold.

The independent review then found a different calibrated-neutral case: the clean
NPZ channel is in current-reference coordinates, while native author/image layers
are in native coordinates. Its display-to-canonical bridge must include
`nativeToCanonical * referenceToNative`. The new oracle constructs a separate
48x32 typed NPZ with nonidentity calibration (axis scales 1.25/0.75, translation
10/-6) and a nonidentity native-to-canonical chart (axis scales 1.5/2, translation
7/-3). It calls the original `shared_route_validation_spec` with the actual loaded
clean array, which retains identity display-to-reference and the caller pose.
The independently checked clean point (12,9) has canonical coordinates (44.5,-1.5)
and client coordinates (34.8,-15.5).

Root's `display-calibrated-before-01` run returned 1 with exactly this calibration
failure; the existing 391 adapter / 391 bridge / 5 corner cases still passed.
The corrected neutral source-domain argument is a single production change;
author/native image bridges, admission checks and numeric thresholds are unchanged.
The same newly generated oracle must be reused for the after run. This regression
requires the new `calibrationCases` field, so an old uncalibrated oracle cannot
silently stand in for it. After-run results and fresh independent review remain
the root coordinator's responsibility.

This proves static resource admission and coordinate calculation only, after the
commands pass. It does not prove capture, GUI placement, game behavior or latency.
OpenCV/OpenCvSharp use the existing Vision dependencies and licenses in
`csharp/src/StarMap.Vision/ThirdParty/`; no package was added.
