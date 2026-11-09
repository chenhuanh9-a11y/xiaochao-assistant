# Pure alignment authorization and round layer differential

Source binding: `6a541090b693ab3de47006200b85b14fa955aced`, original
`app/alignment_core_v2.py`, `app/round_layer_group.py`, and the first two evidence
functions in `app/alignment_observation.py`. No capture, image solver, identity
selection or author selection is implemented by this module.

`StarMap.Core.Alignment` contains:

* `Matrix3`: immutable finite full 3x3 matrices, general inversion/composition,
  including projective bottom rows and singular finite inputs. It does not impose
  affine, axis or determinant-size restrictions. Nonfinite arithmetic is not
  retained as a valid coordinate matrix.
* `RoundPose`, `Observation`, `AlignmentCandidate`, `Adjudication` and
  `DisplayAuthorization`, preserving the source lifetime fields and defaults.
* `AlignmentCoreV2`: observation begin/frame update/stages; reliable initial pose;
  floor-scoped anchors; bounded late candidate cache; adjudicate/authorize/commit;
  current/continuity checks; equivalent-display renewal; provisional/shared
  projection; verified compositor migration seam; close/reset and source events.
* `RoundLayerGroup`: explicit floor relations, one root, general canonical/profile/
  recognition matrices, translation/adoption/centered client reprojection,
  per-floor visual tracking, displayed pixels and verification sets.
* `AlignmentObservationEvidence`: canonical evidence wrapper and the original
  legacy/canonical/source-heldout verification predicate. The wrapper returns an
  independent map; callers must use the returned map.

Matrices, input/output dictionaries, nested arrays, masks, metadata and snapshots
are copied or immutable. Authorization handles have no public setters; revocation
remains visible to holders. Core transitions run under one owner lock. Events are
defensive snapshots, bounded to the source's last 256 entries while the supplied
sink receives every emitted event. These ownership choices do not make sensor
claims true and do not add acceptance flags.

Minimal Runtime integration:

1. Identity and resource providers choose the exact map/floor and reviewed static
   coordinate relations outside this core. `BeginObservation` receives the current
   cycle and frame generation; `UpdateFrame`, `Close` and `ResetRound` retain their
   distinct source lifetimes.
2. The actual current-frame solver supplies a canonical `AlignmentCandidate` only
   after its existing geometry/evidence checks. A display bridge supplies coordinate
   conversion; its existence does not supply `CurrentFrameVerified`, `ResidualVerified`
   or a reliable pose.
3. `Adjudicate` returns a proposal and proposed root. `Authorize` issues a token.
   `Commit` belongs to the actual display consumer; `CanDisplay` adjudicates the
   current observation/cycle/map/floor/frame. Provisional commits do not learn a root.
4. Floor relations are supplied explicitly. `RoundPose.WithFloorRelation` is the
   immutable equivalent of updating the source relation dictionary; neither it nor
   `RoundLayerGroup.SetRelation` fits a scale from another floor. A missing relation
   stays missing unless the original independently verified floor-anchor path is
   selected by the caller's actual evidence.

The original source has observable weak boundaries, intentionally covered instead
of silently strengthened during migration:

* Revoking an issued token prevents its late `Commit`, but an old accepted
  adjudication can be authorized again inside the same open observation.
* `UpdateFrame` keeps the observation ID. The original `Authorize` does not recheck
  the adjudicated candidate frame, so that same old adjudication can issue a token
  for the new frame and commit. A new observation or close rejects it.
* A mismatched `CanDisplay` request may enter the source continuity check and revoke
  the current token when its continuity deadline is zero/expired.

The Python oracle actually exercises these outcomes. Runtime must apply its existing
request/ticket/epoch/frame checks before consuming background results; this port is
not a new stale-result policy. The source's hardcoded provisional floor eligibility
(`1`/`2`) is preserved even when a custom S1 floor list is supplied.

Generate the oracle with the original Python modules:

```powershell
& 'E:\codex project\2026-11-08\xingtu-dotnet-lab\python-local-runtime\python.exe' csharp/tests/StarMap.Alignment.Tests/create_python_oracle.py --source . --output '<new-evidence-directory>\alignment-python-oracle.json'
```

The implementation agent ran this command successfully at
`E:\codex project\2026-11-09\full-csharp-validation\alignment-python-oracle-02\alignment-python-oracle.json`:
17 core cases / 414 state transitions, 4 round group cases / 33 transitions, and
18 evidence cases. Full finite non-affine matrices, stale map/floor/frame/cycle,
terminal stages, wrong/superseded tokens, late authorize after revoke/update,
close/reset, continuity expiry/renewal, independent floors, reviewed cross-floor
relations, high-confidence door/dynamic anchors, translation-only refresh, missing/
singular relations, client resize and tracking invalidation are included.

Root owns all C# builds and execution. After its required bare build, run:

```powershell
dotnet run --project csharp/tests/StarMap.Alignment.Tests -- 'E:\codex project\2026-11-09\full-csharp-validation\alignment-python-oracle-02\alignment-python-oracle.json' '<new-csharp-evidence-directory>'
```

Every actual result/state is written before comparison. `summary.json` records
failures and exit code 1 preserves evidence. Additional C# ownership checks mutate
input/output matrix rows, metadata, anchor/tracking masks, display pixels and token
snapshots to ensure they cannot rewrite core state. These checks test the requested
copy boundary, not a substitute for production verification.

Root's first C# run (`alignment-csharp-01`) completed all 465 checks, with three
differences from two `close` operations that omitted a cycle and one subsequent
event history. The harness conditional `cycle ? typedCycle : null` selected the
implicit string-to-cycle conversion for the null branch, raised an argument error
before calling `Close`, and also produced CS8625. The corrected harness invokes
`Close(reason: ...)` for an omitted cycle. No production core rejection rule,
oracle02 expectation or comparison tolerance changed; reuse oracle02 for the next run.
Root's `alignment-csharp-02` then passed 465/465 with zero build warnings/errors.

A subsequent ownership audit found byte-memory structs bypassing array copies:
`Memory<byte>`, `ReadOnlyMemory<byte>` and `ArraySegment<byte>` can retain mutable
backing arrays. They now receive owned buffer copies, preserving segment offset
and count. Additional C# regressions call actual `UpdateAnchorPool`/`AnchorPools`
and `SetTracking`/`TrackingForFloor`, mutate both original buffers and exported
buffer views (including the backing array behind a read-only view), then independently
read retained evidence again. Oracle02 and all policy comparisons remain unchanged;
Root owns the subsequent build and run for these added copy checks.

Passing this differential proves the selected pure state and numeric sequences.
Synthetic candidate verification flags are test inputs to the original adjudicator;
no fixture flags are imported by production code. It does not prove Runtime
integration, current-frame pixel verification, physical-client provenance, WPF
presentation acknowledgement, game behavior or latency. No package/dependency or
resource was added or changed; C# build/run/commit remain unperformed by this agent.

Synchronous event sinks can reenter Core. The port captures each entry observation
as Python does: an accepted callback that opens another context returns the old
observation ID, and that result cannot authorize the new context. Authorization
revocation callbacks can still return an old-context token, which then fails the
current-context commit check. This preserves source behavior rather than adding
new policy: reauthorization of an old adjudication within the same observation,
including after `UpdateFrame`, remains possible and needs the Runtime request epoch.

Evidence copying supports strings, immutable `Matrix3`/`AlignmentCycleId`,
reference-free value types, JSON, dictionaries, arrays and enumerable containers.
`Memory<T>`, `ReadOnlyMemory<T>` and `ArraySegment<T>` own recursively copied
elements, including when nested inside arrays; segments retain offset/count.
Numeric/reference-free arrays use one bulk clone. Boxed mutable reference-free
structs also receive a boxed copy. Other reference-containing structs throw
`NotSupportedException`. Existing serializable class values become owned
`JsonElement` values; typed array/buffer elements whose copied representation is
incompatible with the element type also fail explicitly. No arbitrary struct
field traversal or new serialization fallback was introduced. Mutating entry
points complete evidence copying before changing owner state, so rejected values
cannot leave a partially updated observation, pool, tracking, relation or display.

`AlignmentRegressionChecks` runs alongside oracle02 without altering that oracle.
For focused before/after evidence, run `StarMap.Alignment.Tests --regressions-only
<new-evidence-directory>`; it writes `alignment-regressions.json` and returns 1
if any check fails. Its 18 cases exercise actual synchronous callbacks, actual
anchor/tracking owners, nested typed buffers, copy rejection atomicity and the
known source reauthorization boundary. These are pure-state ownership regressions;
they do not establish production pixel evidence or actual display/game acceptance.

The first post-change run preserved the callback and rejection behavior but
exposed a compiler common-type conversion in the generic buffer-copy switch
expression: writable memory and segments became `ReadOnlyMemory<T>` before
boxing. The copy now uses separate return statements, so each branch boxes its
own runtime type. A new direct regression checks all three view types, empty
views and repeated copying for byte, float, object and nested-memory elements;
the existing ownership assertions remain unchanged. Preserve the failed
`alignment-regressions-after-01` and `alignment-csharp-04` evidence.
