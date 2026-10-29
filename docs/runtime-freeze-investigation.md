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

- Resource validation still performs `stat/resolve` and, on cache miss, reads/hashes on the Tk prelude. Actual production method + synthetic two-member resource group + hot digest cache + 20ms injected stat: 60 calls over 10 iterations, 123.39–124.56ms per iteration. Moving this to a worker requires generation-bound authorization revocation; this patch does not skip identity checks or substitute a TTL.
- Real repeated open/close, floor transitions, image-truth correctness, visible-pixel latency, native handle/RSS long runs and player acceptance remain unverified. Shared mode/frame/floor gates were not relaxed.
- Coordinate same-file changes with the author-display fallback work before merge. No merge, release, announcement, upload, installation or new Setup is included in this draft. Build a new patch version from the final reviewed remote commit; preserve released 1.0.3.
