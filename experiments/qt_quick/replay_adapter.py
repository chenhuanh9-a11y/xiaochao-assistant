"""Consume the existing core's RoundPose.snapshot schema; never solve a pose."""
from pathlib import Path
import json
from alignment_core_v2 import RoundPose

def load_pose(path: Path, floor: str):
    data = json.loads(path.read_text(encoding="utf-8"))
    pose = RoundPose(data["mapId"], data["routeProfileId"], data["anchorFloor"],
                     data["rootMatrix"], data.get("floorRelations", {}),
                     data.get("generation", 0), data.get("reliable", False),
                     data.get("source", "replay"))
    matrix = pose.pose_for_floor(floor)
    if matrix is None:
        raise ValueError("Replay has no relation for requested floor")
    return {"mapId": pose.map_id, "profileId": pose.route_profile_id,
            "floor": floor, "matrix": matrix.tolist(), "source": pose.source,
            "reliable": pose.reliable}
