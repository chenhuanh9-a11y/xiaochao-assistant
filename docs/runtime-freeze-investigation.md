# 1.0.3 runtime freeze investigation

Baseline: `090b56018be26170cb36e768fdad1866c6a515ca` (2026-10-29).
The existing Setup was independently read back as 115,923,799 bytes. It was not modified or installed.

## Established blocking paths and changes

- `_log` previously performed mkdir, serialization and append under `log_lock` on its caller, including Tk and the hotkey listener. A single bounded background writer now owns disk IO. Producers snapshot at most 512 nodes, depth 8 and 16,384 text characters, without retaining frame objects or invoking arbitrary string conversion. Queue capacity is 1,024. High-volume observations are displaced before other events; saturation can still drop other events, and cumulative losses/write failures are reported. This does not guarantee lossless diagnostics under a permanently blocked disk.
- The physical probe worker now remains occupied through logging and publication. The screenshot lock is still released after capture; no UI handoff waits while holding it. Request/generation/frame validation is unchanged.
- Probe/capture thread-start failures release owned lanes. Delayed capture callbacks reject superseded generations, and success is reported only after the worker starts.
- The complete event-poll callback has a rescheduling boundary, so prelude exceptions no longer permanently stop its timer. Closed applications do not schedule another callback.
- Diagnostic export waits up to two seconds on its background thread and records flush status. Shutdown drains through nonblocking Tk polling for at most two seconds; a blocked sink can lose the remaining tail at process exit. A writer-start failure is exposed in its status and does not prevent application startup.

## Evidence and boundaries

- Actual hidden Windows Tk mainloop + production `_log` extracted from baseline/current source, with legacy lock held for 600ms: baseline call 553.99ms / maximum heartbeat gap 554.32ms; fix call 0.075ms / maximum heartbeat gap 11.05ms. No game or installed application was operated.
- Production probe method, injected blocking logger and publisher, 1MiB synthetic frame: baseline admits 20 additional workers after the first; fix admits zero. Thread-start failure releases both lanes and permits another request.
- Actual writer, 20,000 synthetic events and 20ms injected sink delay: pending queue capped at 1,024, process threads stayed at 5, producer p50/p95/max 0.0115/0.0193/0.5091ms. RSS samples 20.12–21.92MB include the measurement list itself; this is a 23.7s diagnostic stress check, not a game long-run acceptance test. Queue drained; 18,858 observations were counted as dropped.
- Targeted regression suite: async writer, probe lifecycle, capture start failure, native similarity runtime, queued core contract, queued first-pose delivery. Existing similarity prefix tests now invoke the extracted single poll iteration so their intentional sentinel exceptions still reach the test.

These establish reproducible code-level failure mechanisms, not the cause of every player's freeze. There are no player hang stacks in this evidence. Existing inspected release stderr files are build logs, not runtime hang evidence.

## Remaining gates

- The follow-up resource-validation change below closes the identified Tk stat/hash path. Other unrelated UI IO paths and full-game behavior remain outside this evidence.
- Real repeated open/close, floor transitions, image-truth correctness, visible-pixel latency, native handle/RSS long runs and player acceptance remain unverified. Shared mode/frame/floor gates were not relaxed.
- Coordinate same-file changes with the author-display fallback work before merge. No merge, release, announcement, upload, installation or new Setup is included in this draft. Build a new patch version from the final reviewed remote commit; preserve released 1.0.3.


## Follow-up: resource validation lane

The production registry's `validate_state` now runs in one physical background lane, with one replaceable pending request and one result. Scope binds engine/index/provider identity, resource epoch, mode, capture/cycle/floor, state identity/generation, resource key, domains, member reference content, member set and evidence floor. A changed scope cannot consume an old result; cancellation keeps the physical worker occupied until it finishes. No resource check is replaced with a presumed success.

A successful result grants at most 500ms while continuous background rechecks run. New scopes have no grant; expiry hides/revokes pose authority without waiting for IO. Known import/removal/publication immediately cancels old work. A failing file check also revokes a previously MEMBER_VERIFIED core grant, durable confirmation and caches. True end-round clears rejection for the next independent round. Only explicitly trusted UI member-choice/confirmation transitions may carry an identical file binding without extending its original completion time.

Durable identity remains a capture/search hint while closed or on a new capture generation. It does not authorize pixels: provisional commit, sibling delivery and both display paths require current resource validity in addition to their unchanged frame/floor/pose checks. After temporary expiry, recovery records only the current frame/surface context and schedules the original first-pose worker after validation; stale context is discarded. Pixels and core authorization are not restored from cache. First-pose thread-start failure releases its lane and allows retry.

Validation:

- Hidden native Windows Tk plus real registry, synthetic three-member condition resources, hot digest cache and 20ms injected stat: baseline 72 UI stat calls / callback max 165.90ms / heartbeat max 171.97ms; follow-up 0 UI stat calls, 72 background calls / callback max 0.318ms / heartbeat max 10.875ms.
- Real hashed-file mutation revokes a genuinely authorized/committed AlignmentCoreV2 pose; new generation, close/cancel, late results, repeated group changes and expired-grant recovery are covered. Recovery tests execute production scheduling and real Thread.start; the expensive sensor loop is a recording substitute, not a solver or pixel acceptance claim.
- Small 5.27s run: 200 UI scheduler calls, 100 generation changes, real registry and 10ms injected stat; callback p95/max 0.124/0.658ms, pending <=1, result <=1, process threads sampled at 24 throughout, RSS 58.31→58.43MB. Process CPU delta was below the psutil timer's measurement resolution; do not interpret the reported zero as zero CPU use.
- 99 targeted tests passed including the previous 56 plus resource lane, resource lifecycle, group display and group review. Fixtures are synthetic; real difficult-mode assets, full-game long runs and visible-pixel correctness remain unverified by this follow-up.

This follow-up is a separate reviewable commit after `5f06fd27`. Author fallback (PR45) and version-only patch (PR46) are intentionally not mixed into this P0 commit; final integration/build belongs to the coordinating review.
