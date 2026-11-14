# S0 synthetic font bank goldens

All banks here are synthetic Hershey-font control data. They contain no game
images or other game assets. The first golden remains the existing production
asset at `app/control-data/s0-floor-templates-v1.npz` (Python 3.13.3,
OpenCV 4.12.0.88, NumPy 2.2.6).

`opencv-4.12.0-numpy-2.2.5.npz` is a positive cross-build sample generated
from source captured with `git show 396eed7d1529d615d30e1da3a321dae6f4094389`.
It loads OpenCV 4.12.0.88 from the production environment with the separate
ambient NumPy 2.2.5. Its array values and compressed bytes exactly match the
production asset: 0 different vector elements, max absolute difference 0, and
worst row cosine 0.9999999999999172. See
`provenance-opencv-4.12.0-numpy-2.2.5.json` and the private per-row report.

`opencv-5.0.0-numpy-2.2.5.npz` is a negative rendering-drift sample generated
with the ambient OpenCV 5.0.0.93 and NumPy 2.2.5. It differs from the production
asset in 4,423,678 of 4,423,680 vector elements, with max absolute difference
0.10858726338483393 and worst row cosine 0.5797574121680589. This records a
real font/image-operation change; it is not evidence that both OpenCV versions
are compatible. See `provenance.json` and the private per-row report.

Both files use the captured producer's `generate_bank()` mathematics and
ordering, and each NPZ contains only `vectors` and `labels`. The output wrapper
does not pass `allow_pickle=False` as an array member. The two sidecars record
the actual build inputs, commands, source commit, array contracts, byte lengths
and SHA-256 values.
