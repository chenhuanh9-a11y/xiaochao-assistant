# Two-stage native similarity review (successor to PR32)

Base: `9c161f6ccdc427657f3c509cd0ead67d56a59fc6`. Separate branch
`codex/similarity-two-stage`; PR32 and its Setup input are unchanged.

## Runtime boundary

An established native entrance family remains a provisional identity. Its first
ordinary fit still passes the existing current-frame geometry and rendered-resource
bridge checks. A provisional reference is now allowed only with a current core
pose authorization (or its remembered, same-map/floor closed-map context), and
contains a visible provisional badge. No floor threshold or pose gate was relaxed.

Identity continuation no longer runs inside the foreground pose worker. An owner
thread schedules one group review after the foreground is idle. It shares the
existing identity task slot, including terminal-event ownership and cancellation.
The nominal budget is 200 ms including resource validation, with at least one
second between requests and no repeated request for the same scope/signature.
This is a cooperative computation budget, not a hard interruption of filesystem
IO and not a measured key-to-display latency guarantee.

`rank_established_group` obtains all rivals from the bound entrance conditions,
validates those resources, and calls the original rank/identity gates with that
complete member set. It skips source-family discovery and icon advice. The
Difficult wrapper still suppresses identity if its declared native inventory is
incomplete; the two modes do not have separate new recognition algorithms.

Background errors, incomplete evidence and stale results do not mutate display
state. A changed frame is rejected; a newer capture containing exactly identical
pixels, bounds and lifecycle context may consume the result. A selected target
must pass the original unique identity verdict before it is staged. A different
member goes through the existing sibling structure-fit/bridge/pose transaction;
the source selection is not cleared in advance. Identity promotion still requires
the current core authorization and global residual gate. Same-member results can
promote using the recorded global verdict from an already committed current pose.

A floor change drops old-floor provisional pose/evidence but carries the previously
established family into subsequent ranking. A final member identity survives the
floor change; its original evidence floor is distinct from the currently observed
floor. Explicit contradictions can reopen that final family on the new floor,
without granting its old-floor pose any authority. This does **not** authorize showing a
provisional F1 pose on F2/B1. Round end, mode/resource replacement reset that family.
Final native members may re-enter group review on two distinct current-frame
explicit structural contradictions; low-confidence/residual misses alone do not
count, a valid pose commit breaks the sequence, and there is no lifetime attempt
limit.

## Verification

Run from this branch using the existing OpenCV 4.12 project Python, `-B`:

- `test_group_review_lifecycle.py`: 26 PASS (21 new cases and 5 inherited baseline
  cases). Includes slow completion, unconsumed terminal, cancellation, duplicate
  terminal, stable identical pixels after capture advancement, two synthetic
  entrance cohorts (three members and two members), actual original rank dispatch
  with every rival retained, Difficult inventory suppression, floor invalidation,
  staged member commit, repeated strong-conflict reopening through the real failure
  normalizer, final-identity floor transition, same-member final promotion, and reference badge.
- `test_native_similarity_runtime.py`: 28 PASS.
- `test_native_group_display.py`: 5 PASS (overlaps inherited cases above).
- `test_independent_visibility_runtime.py`: 5 PASS.
- `test_reference_position_controller.py`: 13 PASS.
- `test_rerank_shared_lane.py`: 9 PASS.
- `test_unconfirmed_identity_recovery.py`: 8 PASS.
- `test_stale_failure_display.py`: 4 PASS.

These are offline synthetic pixels/state/producer fixtures. Actual owner methods,
resource validation and original ranking dispatch are exercised; OS surfaces,
expensive locator results and clocks are substituted where stated in each test.
The fixture cohorts are not two new real-game successful frame replays. No private
frame/log, original map package, executable, or user setting is committed.

## Review/acceptance still required

- The new native-member path reuses the existing sibling compositor transaction;
  these tests do not constitute a full live target paint/rollback/bridge acceptance.
  Review that shared transaction before allowing a build.
- F2/B1 without adequate current-floor evidence still waits safely. A provisional
  full reference is not promised throughout such an interval. This does not prove
  that every previously observed dual-disappearance interval is eliminated.
- Current native condition resources originate on F1. This patch preserves group
  identity/scope across a floor change and tests the new-floor contradiction state
  transition, but does not create new floor relation evidence. Actual F2/B1 pixels
  and final target rendering still need representative replay/live acceptance.
- Persistently changing pixels can reject background verdicts until a stable
  observation arrives. There is no universal non-starvation claim.
- No game/GUI operation, Setup rebuild, install or release was performed. Real
  p50/p95/max key-to-display, missing-display/wrong-floor rates, and CPU/RSS remain
  unmeasured for this successor. The 300 ms objective is not an achieved result.


## PR36 final-paint failure repair (integrated d5c8d262)

The review probe reproduced a real owner/core defect: a rejected target paint restored only the gate, leaving the target observation/pose and UI selection behind and revoking the source grant. The probe uses synthetic frames and controlled fit/resource/sink boundaries, not game capture.

Native sibling commits now prepare against an isolated core and compatibility projections. The original observation, authorization and surface stay untouched until target paint and sibling identity commit both succeed. Selection updates occur at that boundary. Rejected paint and exceptions discard target state and target windows; expired frame/floor/surface/context never reactivates the old grant. Request cancellation alone preserves a still-valid source and does not overwrite a newer pending request. Target token counters remain monotonic after failure.

The PR35 retained path is included: target paint uses a fresh surface, bypassing the old select-surface pre-hide operation. The original retained pixels, clip, token and display bridge are preserved on failure. On success the new surface enters the bounded cache; displaced surfaces use the existing capture-aware release path. Hidden-user preference leaves an empty real surface ready for future repaint. The ordinary non-sibling pipeline is unchanged.

`test_native_switch_atomic.py` exercises the actual native review staging, owner, core, pose transaction, and compositor method with controlled surface I/O: rejection/exception, successful switch, identity rejection after paint, close/frame changes, stale request, cancellation/new pending, hidden preference, non-recycled tokens, and both mode lifecycles. Retained packets are built by the real raster cache; no native HWND/game interaction is claimed. No GUI visibility latency, 300ms end-to-end result, or complete resolution of every disappearing-display cause is claimed.
