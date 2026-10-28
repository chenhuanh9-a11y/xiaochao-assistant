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

`entry-candidates/1` uses the same producer for every mode and floor. Identity includes mode, profile, map and original floor string (B1 is not converted). Each immutable candidate includes the versioned source binding, structure-file SHA256, coordinate metadata, compiler hash, input entry hash, semantic gaps, anchors and neighborhoods at the configured radii. Optional `dependencies` lists explicit raw source files relative to the root; their bytes participate in invalidation. Without these, `raw-source-dependency-closure-unverified` remains visible: cached renderer metadata is not proof of a verified raw source closure.

Geometric boundary evidence and source spawn markers have distinct kinds. Markers never gain boundary semantics. Missing structure bindings retain a row with no neighborhood and full-reference fallback. Missing doorway, wall classification, and uniqueness evidence remain explicit. Existing source semantic gaps are carried forward. Every local neighborhood preserves original node IDs and immediate adjacency, cutting edges at the window boundary without bridging removed nodes.

Content-addressed JSON files are reused only after exact byte validation. A changed source resource, entry, binding, coordinate, raw dependency, compiler or parameter creates a new file. Corrupt cache fails closed. Manifests are immutable and old files remain available. Output must be separate from the source tree; inputs and runtime resources are never overwritten. Generated resources and raw game exports stay outside public Git.

The descriptor signature is only an exact bucket, not a similarity score or uniqueness guarantee. Equal candidates are retained. `grantsIdentity` is always false. Runtime integration is pending: the owner must keep complete rival competition and the existing final structure checks, and use full references when semantic evidence or the optional index is unavailable. This producer does not create semantic walls, doorway widths/states, pathfinding channels or a solved minimum unique observation.

Validation command:

```powershell
python -B -m unittest discover -s tests -p test_entry_candidate_preprocessing.py -v
```

Actual offline generation: 132 floor rows, 246 neighborhoods. Nightmare: 72 rows, 24 boundary floors and 72 neighborhoods. Difficult: 60 rows, 58 bound floors and 174 marker-centered neighborhoods; map29 F1/F2 have no native versioned binding and remain blocked rows. All 132 output hashes verified; immediate second run reuses all 132. Eight targeted tests cover invalidation, identical rivals, marker semantics, B1, absent binding, stale anchors, corrupt cache, path containment and cut adjacency. No game was started and no live recognition performance or unique-identification claim is made.
