## Safety correction after 4d53 review (supersedes retirement claims below)

Verified-path-then-open/unlink was not race-safe. Log appends now open no-follow
handles (Windows reparse-point handles with write/delete sharing denied), verify
file identity/content on that handle, then write and derive ownership on it.
Parent directories are pinned during each transaction. Journal updates likewise
validate and write one handle, never replace a potentially substituted pathname.

Automatic unlink/rename/age retirement is DISABLED. Two fixed 1 MiB slots per
stream and 16 MiB/32 owned files per root remain bounded; after exhaustion writes
fail explicitly, rather than deleting even a seemingly owned path. Seven-day
expiry is no longer promised. Existing pre-handle ledgers are not silently
adopted. Availability cost: old files must be exported and handled manually before
capacity is reusable. No user disk was cleaned. Interrupted journal writes fail
closed rather than reconstructing ownership.

# Diagnostic privacy and storage follow-up

Product baseline: `980d64949dcec3bcfb747dbf8bacb6dfc00fbded`.
Review input: `153931ff15a2d733bb7ae61cac0a0a60a3a4dfb4`.
This branch is not a replay onto current main and must not be merged wholesale.

## Privacy at persistence boundaries

Non-bound foreground capture rejection now records only `foregroundRole=other`,
plus the already bound expected game HWND. It no longer queries or records the
other process PID/HWND. Capture authorization branches are unchanged. Fixed
baseline comparison permits precisely this diagnostic-only source substitution;
same-input capture/pose/decision tests remain in the Windows workflow.

Qt problem/recognition logs, Tk async event logs, optional lifecycle logs and hang
stacks use the shared disk boundary. Tk unhandled-startup errors also use it.
Qt's separate pre-import startup reporter already has a fixed structured schema,
no exception messages/source lines and a 16 KiB single latest-report cap; that
reporter is not converted to rotating JSON logs by this change.

The boundary accepts only fixed production keys and code values. Arbitrary nested
keys, titles, program/account labels, private paths and free-form exception text
are removed; known exception types, application modules/line numbers, stable
reason codes and numeric evidence remain. UI problems can still show original
in-memory text. Exported current summaries and legacy logs are sanitized too,
without rewriting legacy source files. Problem grouping uses bounded run-local
random group IDs, not persisted hashes of private messages, so redaction does not
merge different problems or destroy retained repeat accounting.

## Explicit budgets and ownership

- Each stream: at most 1 MiB UTF-8 bytes plus one 1 MiB backup. Long rows become a
  complete JSON truncation record retaining safe correlation; JSON/UTF-8 tokens
  are never cut. The final exported log payload is byte-bounded again after
  sanitization, since replacing legacy text can expand it.
- Each diagnostic directory: at most 16 MiB / 32 **owned log files**, with seven-day
  lazy expiry checked on writes. Different directories have separate budgets;
  this is not a machine-wide disk quota. A live active stream is retained.
- Ownership registry: capped at 128 KiB; interrupted registry temporary files
  have a bounded reserve. Unknown files are not included in the owned budget,
  are never adopted or removed, and may require user-managed cleanup.
- Retirement/rotation verifies exact names, sizes and SHA-256 ownership. Links,
  reparse points, hardlinks, changed content and unknown targets are preserved.
  If safe mutation/ownership cannot be established, writing fails explicitly
  rather than expanding the budget or touching external targets. Process/file
  transactions serialize writers. Qt batches publish ownership once per at most
  16 records (rather than fsync/rename per record); failures verify and record the
  confirmed prefix without adopting a partial/changed suffix. Crash-interrupted ambiguous writes fail closed;
  this is not a claim of automatic recovery from arbitrary partial disk writes.

Only temporary-directory fault injections were executed. No user log directory
was cleaned during development.

## Measured scope

On Linux, real elapsed 60.092 seconds with continuously generated synthetic
problems and recognition observations yielded 1,888 problems accepted/retained
(1,776,084 bytes); 708 recognition attempts, 675 persisted (318,986 bytes), with
33 intentionally rate-suppressed and zero actual write loss. Both accepted-stream
retention rates were 100%; recognition retention relative to attempts was 95.34%.
This is a measured input rate, not a guaranteed arbitrary-rate retention period.
The same 60-second mixed-stream test runs in Windows CI and reports its own counts.

Tests include actual readonly-file OS denial then recovery, byte/multibyte/overlong
rows, historical expiry/capacity, malicious nested fields/exception text, and
preservation of changed/unknown/hardlinked and outside-link targets. Linux writer
checks are not real-game, installed-app or long-term reliability validation.

## Necessary dependencies for a future independent main replay (analysis only)

Remote main observed as `11a7145dcb20e91c1439b72e1e254e3be71dd24a` (PR 80 merged).
The merge base with the product baseline is `8c763f689f203788df285b46577b835d4ee4e286`;
there are 25 baseline-side versus 30 main-side commits. No rebase/merge is attempted.

A new main-based implementation must independently port these diagnostic seams:
1. `DiagnosticStream` and privacy/schema/storage modules; retain main's settings
   ownership/lifecycle and add only resettable random identity integration.
2. Main's existing ProblemsService/AsyncEventLog: identity, separate recognition
   stream, suppression/loss accounting, private bounded sinks and history groups.
3. Export/help seams: actual sessions/export IDs, privacy and image opt-in boundary.
   Do not replace main's help, settings or input files wholesale.
4. Environment module is absent on this main; port read-only bound-window/runtime
   observation explicitly, with main's own providers and UI-thread snapshots.
5. Recognition observation hooks and original control-band sampling must be
   applied to main's actual runtime/failure-store interfaces without changing its
   decisions, ownership recovery, thresholds, cancellation or capture guards.
6. Establish new main-based noninterference fixtures and provenance hashes.
   The current 980d fixtures prove this branch only, not a future combined tree.

Overlapping files include runtime, capture, coordinator, problems/history, export,
help/client/info, failure samples and environment diagnostics. Those require
hunk-level compatibility analysis; do not import the 25 hotfix-history commits or
PR 76 to satisfy dependencies. Any conflict or missing production entry point is
a reportable dependency, not permission to replace the main implementation.


Windows first mixed-stream run retained last attempted problem 1912/1920. The
initial test incorrectly equated attempted with accepted entries; follow-up
measurement records both, all genuine loss counters/ranges and the last accepted
versus retained sequence. No unaccepted event is silently counted as retained.
This prompted bounded batch ownership publication to reduce per-row disk overhead,
not any relaxation of capture or recognition decisions.

Ownership-journal failure after confirmed complete log writes is reported as a
writer error, not mislabeled as lost log rows. An unregistered/ambiguous object
is preserved and future writes fail closed; no automatic adoption is attempted.
