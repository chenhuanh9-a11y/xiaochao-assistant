# Display package optimization and upgrade flow

`tools/maps/optimize_map_packages.py` maintains the current display-only Q90
strategy. It reads an outer ZIP containing current map packages and an existing
base bundle, and writes a **new** candidate directory. It does not install a
package, replace a running application's resources, or publish images. The
repository contains source and synthetic tests; author images and private input
or output archives belong outside the repository.

## Dependencies and invocation

Use Python with Pillow (including WebP support), NumPy and OpenCV installed.
For example, an isolated environment can install `Pillow numpy opencv-python`.
These libraries are needed even for import/help; the script has no download or
dependency installation step. The source lock records the versions actually
used. Codec/library versions can change output bytes, so a successful build does
not imply that a previous candidate's ZIP hash will be reproduced.

```powershell
python tools/maps/optimize_map_packages.py --help
python tools/maps/optimize_map_packages.py `
  --source-archive ./private-input/maps.zip `
  --base-bundle ./private-input/base-bundle `
  --output ./private-output/q90-candidate `
  --expected-source-sha256 <pinned-outer-zip-sha256>
```

The hash pin is optional. `--workers` defaults to 3. The output must not exist,
and must be outside the base bundle; existing evidence is never overwritten.
A build failure after processing begins may leave a partial directory plus its
source lock for diagnosis. Use a new destination for the next attempt. Source
archives and the source base bundle are read only.

The Python entry points are `main(argv=None)` and
`build_packages(source_archive, base_bundle, output, *, workers=3,
expected_source_sha256=None)`. Importing the module does not parse arguments,
read inputs, or start a build. A command error exits with status 2.

## Current resource contract

Supported IDs are `difficult-zhan10`, `nightmare-suju`,
`nightmare-nightingale` and `nightmare-wang-qingxin`. The outer ZIP must contain
unique package IDs, including `nightmare-suju`; any subset meeting that rule is
accepted for bounded maintenance/testing. The original native/full-reference
closure and Suju dependency arrangement are intentional current contracts,
not a universal converter for future package formats.

Each inner ZIP has `map-package.json` and payloads under `data/`. Its `files`
records must exactly cover those payloads, with matching source sizes/hashes.
Paths must be canonical relative POSIX paths with no traversal, Windows aliases
or case collisions. The tool rejects oversized archives and unsupported
packages rather than extracting arbitrary content.

Only existing PNG/WebP display assets under `_internal/assets/suju/`,
`_internal/assets/zhan10-*` and `_internal/assets/authors/` are encoded:

| Input and package | Output policy |
| --- | --- |
| Difficult WebP | WebP Q90, method 6, exact alpha, original dimensions |
| Difficult legacy PNG | Lossless PNG optimize/compression level 9 |
| Suju and optional-author PNG | Lossless PNG optimize/compression level 9 |

An encoding is retained only when it is smaller than the original. Image
format/path is retained; an unexpected format or animated asset fails. The
historical `.lossless.webp` filename remains for compatibility even when the
new image is lossy Q90. Q90 does **not** promise exact RGB. PNG promises identical
decoded RGBA. Every selected result is checked for unchanged dimensions, exact
decoded alpha and successful OpenCV decoding. There is no cropping, resizing,
palette reduction or coordinate recalibration. Per-image records include both
source and output hashes/sizes plus identity `sourceToOutput` and
`outputToOriginal` matrices.

NPZs, recognition-directory payloads and other non-display payloads retain their
original bytes. Display-related JSON hash/byte references, full-reference policy
and native runtime closure totals are updated. JSON changes are applied locally;
all unchanged JSON values retain their original lexical bytes, including
matrices and their numeric notation. This also applies to the package manifest
and the base catalog. These checks prove display/geometry preservation, not
recognition accuracy, runtime alignment, visual acceptability or gameplay.

## Outputs and provenance

The destination contains:

- `<packageId>-display-optimized-r1.zip`: updated current-format packages.
- `staging/<packageId>/`: the same expanded payload files for offline review.
- `base-prerequisite/_internal/references/nightmare-author-route-catalog.json`:
  the base catalog with Suju display hashes/byte counts updated.
- `build-manifest.json`: package totals, changes and per-image audit records.
- `base-prerequisite-audit.json`: original/candidate catalog hashes and edited
  hash-field locations.
- `source-lock.json`: input outer ZIP hash/size, nested member hashes, base
  catalog hash, build fingerprint/hash, tool source hash, library versions and
  selected strategy. Input basenames and output-relative paths are recorded;
  no personal absolute paths are written.
- `SHA256SUMS.txt`: package and base-prerequisite catalog hashes.

Resolve each report's `path` against the directory containing
`build-manifest.json`. Keep the lock with the build artifacts; pin the outer
source hash when rerunning. The lock describes this invocation, and does not
retroactively attest to older accepted candidates that lack that lock.

The tool requires a base catalog and
`_internal/references/build-fingerprint.txt`. Packages that declare a base
catalog dependency must match the source base catalog before processing starts.
The optimized packages' `requiresFiles` records then bind to the **candidate**
catalog hash. Other `requiresFiles` records are preserved; this builder does not
validate the whole client resource graph or substitute for importer inspection.

## Stage before installing or upgrading

Suju's display catalog is a base dependency outside its package payload
allowlist. Recompressing a PNG changes that catalog's bound hash even though
decoded pixels are identical. The base-prerequisite overlay is consequently a
release staging input, not a package to install with the additive importer.

For already accepted Q90 candidates, retain the accepted artifacts and their
audit records; running this tool again is not a prerequisite for an upgrade.
For a new candidate generation:

1. Build into a new private output directory and review source-lock, image
   audit and SHA256 records.
2. Prepare an isolated resource stage with the candidate base catalog through
   the repository's explicit staging contract. Do not write the overlay into a
   live installation by hand.
3. Inspect the candidate packages against that stage. On an installation with
   owned older map packages, use the explicit ownership/receipt checked upgrade
   transaction. The additive install operation rejects differing existing
   resources; removing them to evade that conflict loses the upgrade contract.
4. Verify resulting package receipts, catalog hashes and resource loading before
   accepting the stage. Keep the original stage and original packages for the
   transaction's rollback path.

The current importer commands are:

```powershell
python app/star_map_import.py inspect ./private-output/q90-candidate/package.zip ./isolated-install-root
python app/star_map_import.py install ./private-output/q90-candidate/package.zip ./isolated-install-root --application-closed
python app/star_map_import.py recover - ./isolated-install-root --application-closed
python app/star_map_import.py rollback <transaction-id> ./isolated-install-root --application-closed
```

Use `inspect` before `install`, and use recovery/rollback only for the relevant
transaction state. An existing owned package can be upgraded only when its
installed receipt and current files match the old ownership inventory. A new
incoming ZIP cannot assert ownership of existing files. Unknown bundled resources
without receipts fail closed; a build stage can seed a receipt only after all
files have been checked against the original package inventory and known frozen
baseline. See [the package upgrade contract](map-package-upgrades.md) for the
transaction and rollback preconditions, and `docs/resources-q90.lock.json` for
the separately pinned accepted package/base-overlay delivery. The optimizer's
`source-lock.json` records a new build invocation; those two locks serve distinct
provenance needs.

This source tool does not itself create installer receipts, implement rollback,
or change package ownership. Updating resources in a real installation requires
the separate staging/upgrade interface and its preconditions. Q90 keeps decoded
canvas dimensions, so compressed size savings alone do not establish lower
decoded memory use, lower RSS, lower latency or a smaller rebuilt installer.

## Focused verification

```powershell
python -m unittest discover -s tools/maps -p test_optimize_map_packages.py -v
python -m py_compile tools/maps/optimize_map_packages.py tools/maps/test_optimize_map_packages.py
```

The tests generate small synthetic PNG/WebP and ZIP fixtures. They exercise
exact alpha, PNG RGBA, Q90 RGB loss, native closure refresh, optional catalog
refresh, byte-preserved NPZ/recognition/matrices, source immutability, portable
provenance, catalog prerequisite rebinding and refusal of conflicting output or
invalid source inputs. They do not read author images, recompress the full
accepted collection, launch the GUI/game, or claim runtime/visual validation.
