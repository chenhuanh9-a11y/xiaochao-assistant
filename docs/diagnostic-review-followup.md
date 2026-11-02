# Diagnostic log review follow-up

Base product source remains `980d64949dcec3bcfb747dbf8bacb6dfc00fbded`.
This follow-up starts at `dd52ffe1387ac339099a51917ea26edb95678855` and changes only
logging, tests, and CI. It does not integrate another feature branch.

## Boundaries and changes

- Recognition observations have a separate `events-qt-recognition-*.jsonl` stream,
  sequence and rotating file (1 MiB plus one backup). They cannot rotate the
  problem-history file. Recognition queue admission stops at half the existing
  bounded queue, reserving capacity for problems and lifecycle observations.
- Full loss ranges appear initially or after actual loss changes, not on every
  accepted record. Export's live snapshot still includes complete bounded status.
- `lostTotal` and compatibility `droppedTotal` count failed/rejected writes;
  `suppressedTotal` separately counts deliberate coalescing/sampling. A failed
  batch reports its confirmed successful prefix; only its unconfirmed suffix is
  marked lost. A failing row could have partial bytes, so “lost” means not
  confirmed persisted, not proof that zero bytes reached disk.
- Noninterference fixtures are exact selected source files from the product base,
  with a source commit and SHA-256 manifest. Capture modules not executed as
  fixtures need only a source hash. Loading/comparison no longer invokes git or
  requires the base commit in local history; Windows CI now checks out depth 1.

## Evidence and limits

Red tests on the pre-fix service reproduced diagnostic rows sharing problem
history, absent suppression semantics, and unaccounted failed batch rows. Loading
an old baseline with git history unavailable failed; the pinned loader passed.

Local Linux Qt checks use real service queues and rotating writers with synthetic
metadata. A 60-second producer-clock test forces rotation with a 4 KiB budget;
separate wall-clock execution lasted 60.09 seconds / 238 ticks / 714 observations.
The problem file retained its original row (993 bytes); recognition wrote 389,931
bytes to its own file, with zero actual loss. This is not game or installation QA.
Fault injection covers a writer failing after one confirmed row and recovery;
sequence intervals cover only the two unconfirmed rows.

Random resettable install identity, default-off screenshots, separately selected
image export, capture authorization and recognition/pose thresholds are unchanged.
