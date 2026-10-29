# Base display consumer follow-up

This follows PR44 and adopts PR45's display provider and wording helpers. It is
not part of the originally frozen 1.0.4 package. No release, installer, user
installation or running game was changed.

## Consumer contract

- The existing first-pose worker prepares only the requested nightmare floor,
  including while an author is selected so a later author change can repaint
  without disk access. UI resolution only peeks; missing/invalid base assets
  keep the structural fallback. Difficult mode cannot use nightmare assets.
- Explicit custom galleries, including an invalid or empty route, retain their
  priority. Missing author declarations are distinct from registration and
  loading failures. Preferred selection is unchanged.
- Base pixels are already on the native canvas using the provider's verified
  image-to-native transform. Their display bridge stays identity; recognition
  masks and pose gates remain separate.
- Real overlay and fixed-map caches include display kind/resource identity.
  A small descriptor is carried on display ndarray views through copies;
  the legacy OpenCV translation explicitly carries it forward. Existing
  compositors continue adjudicating pose/frame reuse and reject changed
  author/resource/mode/round provenance before painting.
- Presentation alone records provenance but does not consume a notice. The
  final accepted pose transaction, or an actually visible fixed map with a
  current verified pose, consumes one missing-author notice per map/floor/round.
  G reopen, author changes and imports do not reset that deduplication.
- Resource generations use a fresh worker-loaded library and provider. The
  base cache holds at most six entries and 96 MiB including retained masks;
  oversized individual entries are refused. Resource invalidation and explicit
  end-round release new base/fixed/prepared display caches. This is not a bound
  on whole-process RSS or temporary renderer allocations.
- Overlay and fixed-map opacity/visibility controls retain their independent
  existing paths.

## Verification and outstanding limitation

Local Python 3.13, Windows, production methods with synthetic hashed registered
assets and controlled display sinks; no game or real HWND acceptance:

- `python -m unittest discover -s tests -p 'test_*display*.py'`: 68 passed,
  including 14 new consumer tests. Production shared resolver, authoritative
  render spec, CPU overlay renderer/cache, provenance copy/late source guards,
  missing/invalid/custom/difficult cases, round notice and byte bounds covered.
- Native resource validation: 9 passed; queued suites: 16 passed;
  capture start failure: 4 passed. Native similarity runtime: 28 passed.
- Opacity suites: 13 passed, 1 failed. The failing
  `test_actual_authorized_compositor_reuses_retained_surface_at_zero_and_full`
  also fails on untouched PR44 head `a8d668a` (runtime opacity suite 5 passed,
  1 failed). Its actual transaction returns `commit-context-stale` before
  painting. This remains a baseline integration issue requiring review; this
  follow-up does not change resource grant rules to conceal it.
- `git diff --check` passed.

Existing PR45 evidence for two real maps/six floors validates provider pixel
preparation only. The above does not establish live game correctness, long-run
RSS stability or end-to-end latency. Worker cold preparation adds real work;
the prior real-asset probe measured roughly 90–225 ms and is not a consumer
latency acceptance result. Keep this PR draft until the baseline compositor
failure and independent consumer review are resolved.
