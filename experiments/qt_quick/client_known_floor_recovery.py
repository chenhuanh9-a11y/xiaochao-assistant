"""Original bounded locator fallback for one already selected map/floor.

This is geometry recovery, not map identification. The caller owns selection,
frame/capture lifetime, final canonical residual and material/core commits.
"""
from __future__ import annotations

import cv2
import numpy as np


def recover_registered_floor(host, frame, map_id, floor, reference, cancelled=None):
    """Return a legacy registered-floor fit, or None for ordinary fallback.

    Only entry-first, registered, not-yet-verified target floors participate.
    Both locators are restricted to map_id. No broad rank, group preference or
    identity writer is called here; this cannot enable a cold-entry policy.
    """
    obsolete = lambda: cancelled is not None and cancelled()
    if obsolete():
        return dict(success=False, reason="structure-work-obsolete")
    engine = host.engine
    profile = (engine.identity_profile_for_map(map_id)
        if callable(getattr(engine, "identity_profile_for_map", None))
        else getattr(engine, "identity_profile_id", None))
    locator = getattr(engine, "side_entry_identity", None)
    if (profile != "shared-structure" or not callable(getattr(locator, "rank_entry", None))
            or not callable(getattr(locator, "has_registered_floor", None))
            or not locator.has_registered_floor(map_id, floor)):
        return None
    group = getattr(host, "group", None)
    if group is not None and group.map_id == map_id and str(floor) in group.verified_floors:
        return None
    if reference is None:
        return None
    host._log("registered-floor-local-started", mapId=map_id, floor=floor)

    def fit(candidate, diagnostics, reason):
        checked = candidate["verification"]
        return dict(success=True, reason=reason,
            matrixSourceToClient=np.asarray(checked["matrixSourceToClient"]),
            coordinateSpace="shared-structure-to-client", routeProfileId="shared-structure",
            selectedRouteCurrentFrameResidual=dict(checked["residual"],
                validationBasis="actual-rendered-profile-structural-mask",
                poseVerifiedAgainstRenderedProfile=True),
            difficultEntryValidation=checked, localFloorDiagnostics=diagnostics)

    def same_target(candidate):
        return (candidate.get("mapId") == map_id
                and str(candidate.get("floor", floor)) == str(floor))

    # As in the original caller, a main locator error is terminal rather than
    # silently converting missing entrance resources into broad search.
    try:
        main_rows, main_diagnostics = engine.entry_identity.rank(
            frame, floor, map_id=map_id, budget_seconds=.35)
        if obsolete():
            return dict(success=False, reason="structure-work-obsolete")
        main = next((row for row in main_rows if same_target(row) and row.get("poseVerified")
                     and (row.get("verification") or {}).get("success")), None)
        if main is not None:
            host._log("registered-floor-main-attempt", mapId=map_id, floor=floor,
                      success=True, diagnostics=main_diagnostics)
            return fit(main, main_diagnostics, "registered-floor-main-geometry")
    except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
        host._log("registered-floor-main-unavailable", mapId=map_id, floor=floor, error=str(error))
        return dict(success=False, reason="registered-floor-main-resource-unavailable")

    try:
        candidates, diagnostics = locator.rank(frame, floor, map_id=map_id, budget_seconds=.35)
    except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
        candidates = []
        diagnostics = dict(complete=False, reason="registered-floor-resource-unavailable", error=str(error))
    if obsolete():
        return dict(success=False, reason="structure-work-obsolete")
    # Bound the selected map/floor even if a producer violates its map_id filter.
    # Never filter away a foreign rival and then grant artificial uniqueness.
    if any(not same_target(row) for row in candidates):
        return dict(success=False, reason="registered-floor-candidate-scope-mismatch")
    if (diagnostics.get("complete") and diagnostics.get("competitiveHypotheses") == 1
            and candidates and not (candidates[0].get("verification") or {}).get("success")
            and ((candidates[0].get("verification") or {}).get("regions") or {}).get("regionalEdgeVerified")):
        try:
            candidates[0] = locator.refine_candidate(frame, candidates[0])
        except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
            diagnostics = dict(diagnostics, refinementError=str(error))
    if obsolete():
        return dict(success=False, reason="structure-work-obsolete")
    if any(not same_target(row) for row in candidates):
        return dict(success=False, reason="registered-floor-candidate-scope-mismatch")
    from map_identity_engine import verified_side_candidate
    candidate = verified_side_candidate(candidates, diagnostics, floor, [], frame.shape,
                                        profile_id="shared-structure")
    if candidate is not None and candidate.get("poseVerified") is not True:
        candidate = None
    host._log("registered-floor-local-attempt", mapId=map_id, floor=floor,
              success=candidate is not None, diagnostics=diagnostics,
              refinementMs=candidates[0].get("continuousRefinementMs", 0.) if candidates else 0.)
    return fit(candidate, diagnostics, "registered-floor-local-geometry") if candidate is not None else None
