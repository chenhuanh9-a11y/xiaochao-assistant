# Offline entry candidate preprocessing

Run `python tools/maps/build_entry_candidates.py --config CONFIG.json --output NEW_STAGE`.
Only the standard library is required. This is a separate candidate format; it does not replace PR18 resources or change any runtime loader.

Config example (paths are relative to the explicitly selected resource root):

```json
{
  "resourceRoot": "AUTHORIZED_RESOURCE_ROOT",
  "radiiCells": [2, 4, 8],
  "sources": [
    {"entries": "references/nightmare-entries/side-entries.json", "index": "references/nightmare-shared-structure-index.json", "anchors": "references/nightmare-entries/side-door-anchors.json"},
    {"entries": "recognition/native/side-entries.json", "index": "references/difficult-shared-structure-index.json"}
  ]
}
```

`entry-candidates/1` uses the same producer for every mode and floor. Identity includes mode, profile, map and original floor string (B1 is not converted). Each immutable candidate includes the versioned source binding, structure-file SHA256, coordinate metadata, compiler hash, input entry hash, semantic gaps, anchors and neighborhoods at the configured radii. Optional `dependencies` lists explicit raw source files relative to the root; their bytes participate in invalidation. Even with these, `raw-source-dependency-closure-unverified` remains visible: cached renderer metadata is not proof of a verified raw source closure.

Geometric boundary evidence and source spawn markers have distinct kinds. Markers never gain boundary semantics. Missing structure bindings retain a row with no neighborhood and full-reference fallback. Missing doorway, wall classification, and uniqueness evidence remain explicit. Existing source semantic gaps are carried forward. Every local neighborhood preserves original node IDs and immediate adjacency, cutting edges at the window boundary without bridging removed nodes.

Content-addressed JSON files are reused only after exact byte validation. A changed source resource, entry, binding, coordinate, raw dependency, compiler or parameter creates a new file. Corrupt cache fails closed. Manifests are immutable and old files remain available. Output must be separate from the source tree; inputs and runtime resources are never overwritten. Generated resources and raw game exports stay outside public Git.

The descriptor signature is only an exact bucket, not a similarity score or uniqueness guarantee. Equal candidates are retained. `grantsIdentity` is always false. Runtime integration is pending: the owner must keep complete rival competition and the existing final structure checks, and use full references when semantic evidence or the optional index is unavailable. This producer does not create semantic walls, doorway widths/states, pathfinding channels or a solved minimum unique observation.

Validation command:

```powershell
python -B -m unittest discover -s tests -p test_entry_candidate_preprocessing.py -v
```

Actual offline generation: 132 floor rows, 246 neighborhoods. Nightmare: 72 rows, 24 boundary floors and 72 neighborhoods. Difficult: 60 rows, 58 bound floors and 174 marker-centered neighborhoods; map29 F1/F2 have no native versioned binding and remain blocked rows. All 132 output hashes verified; immediate second run reuses all 132. Eight targeted tests cover invalidation, identical rivals, marker semantics, B1, absent binding, stale anchors, corrupt cache, path containment and cut adjacency. No game was started and no live recognition performance or unique-identification claim is made.


## Source audit and recovery

Run `python tools/maps/audit_entry_sources.py --config CONFIG.json --allow-source-root AUTHORIZED_SOURCE_ROOT --output NEW_REPORT.json`. Repeat the allow option for each known source tree. The audit follows only entry renderer metadata, its source catalog, and directly named source modules; source references are read-only data. Relative raw-module paths remain unresolved until their authoritative base is established; they are never resolved against the current working directory. Reports retain missing, out-of-scope, hash-mismatch and legacy states. A report is partial evidence, never proof of complete dependency closure.

The actual audit found 130 native metadata identities matching layout/world-floor, with all 130 catalog hash checks passing. Map29 F1/F2 original legacy NPZ hashes match their recorded provenance, but there is no authoritative native layout binding in the examined entry/index/provenance evidence. Its source is an author reference/manual reconstruction, not inferred game-native geometry. Texture bytes, renderer dependency roots and source-to-product replay are still unverified; this must not block the other floor candidate rows.

For the next maintenance run: audit sources first, keep the existing manifest and generated files, then rerun the producer only after an input/producer change. Check `generated` versus `reused` and each semantic gap. A stale binding is an input error; fix the actual source mapping, not its declared version. A corrupt cached file is never overwritten: preserve evidence and use a new staging or explicitly quarantine the proven corrupt file before retrying. Existing outputs remain rollback artifacts. The declared-dependency-subset test ensures a nonempty list cannot silently clear the raw-closure gap.
