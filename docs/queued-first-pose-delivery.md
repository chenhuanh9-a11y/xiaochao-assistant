# Queued first-pose delivery

After a current-frame structural commit, the owner can still release a queued
first-pose job when RGB animation differs from the committed source. A repeated
cold solve can fail even though the previous pose remains valid. Cleanup now
uses the existing exact map-geometry comparison, including F2 semantics, for
shared-structure frames. It still requires the queued frame to equal the current
frame and all existing lifecycle, profile, pose and display-authorization gates.
Changed map evidence remains queued. No pose/identity threshold is changed and
no new display authorization is created by this cleanup.

The retained-work event records scalar cleanup predicates for diagnosis; it does
not record images, raw hashes of user pixels, private paths or game details.

## Evidence and limits

- Seven offline owner tests cover equivalent observations, changed observations,
  unconsumed newer frames, legacy RGB behavior, F2 semantics and lifecycle gates.
- A private saved failure frame was checked with OpenCV 4.12 and the installed
  structure/author resources. Fresh first-pose fitting reproduced
  `initial-structure-supported-chains-insufficient`; the logged committed pose
  passed the original current-frame structural residual on the same pixels.
- On that frame with a controlled UI/core grant, changing only pixels outside
  the map makes the old cleanup keep work and the new cleanup discard it.
  Changing map walls keeps work in both versions. This is a controlled scheduling
  regression, not an end-to-end replay of the original incident.
- The successful source frame from the original incident was not retained.
  Its exact failed cleanup predicate cannot be reconstructed from old logs.
  The new scalar event supplies that missing diagnostic if the issue recurs.
- These worker/solver methods already existed in the previously accepted branch.
  This change does not establish which later integration caused the user's
  broader instability, nor prove that every failure has been resolved.
- No installation, Tk/window construction, game input or live GUI acceptance was
  performed. The uploaded installer is unchanged and publication remains held.

Run `python -m unittest discover -s tests -p test_queued_first_pose_delivery.py`.
Private captures and logs are intentionally excluded from the repository.
