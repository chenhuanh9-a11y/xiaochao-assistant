# Locked S1 fit failure evidence

The solver, identity lock, current-floor gates and rendering authorization are unchanged. Diagnostic collection is requested only when the production S1 pool is already locked and the existing automatic failure sampling option is enabled. A successful scan does not create a failure sample. Cancelled, obsolete and unpublished scans do not enter terminal failure sampling.

`client_providers` creates one scan-owned `_AlignmentHost`. Its existing 24-event memory tail now counts omitted events and can collect small fit observations. The provider copies typed summaries into the scan report. On a current locked failure, runtime passes that evidence and the acknowledged prior's resource/floor/index/scope binding to the existing automatic recorder. No additional worker, capture, timer or network operation is introduced.

The automatic journal's `diagnostic.fitEvidence` contains:

- At most two fit observations, two initial candidates per observation, and nine regional metrics per candidate. Reasons, point/chain/seed counts, proposal matrices, regional gates and residual verdicts remain separate.
- At most three local-prior attempts and three fast attempts. Actual prior matrices and local/global search kinds distinguish a rejected hint from subsequent unrestricted success or failure. Missing prepared resources have an explicit code. A prior's old event cannot masquerade as the current attempt's rejection.
- Full input dimensions and each solver component's exclusive XYXY observation bounds. The sample's XYWH crop, client origin, shape and pixel scale are separate: the automatic extractor's saved crop need not be the component selected by a solver attempt.
- Catalog resource digest, target floor, typed reference metadata digest/shape, source catalog and renderer digests. The prior also has its source/target floor, index digest and hashed owner scope. These data confer no identity, pose or display authority.

The new copy accepts only enumerated codes, bounded finite numbers, small vectors, valid numeric 3×3 matrices and fixed SHA256 fields. It never passes through arbitrary diagnostics, exception messages, paths, frame/mask arrays, names or titles. It has a 256-container visit budget, explicit omission counts, and a 6144-byte JSON budget (default separators, including truncation metadata). Optional candidate detail is pruned before actual prior proposals.

Existing storage rules remain authoritative: six automatic images per mode, 12 MiB, seven-day retention, 24 journal rows and 256 KiB journal, shared cooldown and identity-bound Windows handles. The automatic option's old true/false/default semantics are unchanged. Recorder admission checks the current capture/attempt/scope again before and after writing. Loss of frame authority prevents attaching these fit details; diagnostic errors or image-capacity exhaustion do not change the solver verdict.

Detailed numeric evidence uses this bounded local journal rather than enlarging the 2048-byte recognition-summary channel. Existing diagnostic export may include the journal with the user's selected local samples; this change does not upload anything or expand image regions.

## Crop observation replay

`tools/replay_s1_crop_observation.py` takes an already saved PNG, a typed reference NPZ, the journal's XYWH crop and an output path. It reports corner-chain/ratio-seed measurements at 0.75, 1 and 1.25 crop scale, with actual resize scales and crop-to-client point conversion:

```powershell
python tools/replay_s1_crop_observation.py --sample retained.png --reference reference.npz --crop 10 20 300 400 --output replay.json
```

The explicit observation is only the retained crop. Resized point coordinates are mapped back with OpenCV's pixel-center convention, then the saved client crop offset is added. It is never padded into a fabricated full frame. Component selection, removed border pixels, unseen UI/fog, acknowledged prior matrix and matrix ground truth are not reconstructed. The replay neither proves a full solver failure nor establishes the correct map/pose. User images and reference resources are not part of this source patch.
