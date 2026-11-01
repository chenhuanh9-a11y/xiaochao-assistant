"""Normalize an EXISTING production caller result and copy its exact read-only inputs.

No recognition is executed. Only explicitly verified historical commits are accepted.
The result is local work/ data and must never be committed with author/game pixels.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
from map_resource_context import load_browse_context
from alignment_core_v2 import RoundPose


def prepare(result_path, target, input_kind="unverified"):
    result_path, target = Path(result_path).resolve(), Path(target).resolve()
    if target.exists() or not target.is_relative_to(ROOT / "work"):
        raise ValueError("target must be a NEW directory beneath this clone/work")
    source = json.loads(result_path.read_text(encoding="utf-8"))
    if source.get("success") is not True or source.get("sourceStable") is not True:
        raise ValueError("historical result must report success and stable production source")
    commits = [row for row in source["logs"] if row.get("event") == "alignment-v2-runtime-committed"
               and row.get("currentFrameVerified") is True and row.get("globalResidualVerified") is True]
    if not commits:
        raise ValueError("historical result lacks a verified runtime commit and matrix")
    commit = commits[-1]
    matrix = commit["renderedMatrixSourceToClient"]
    pose = RoundPose(commit["mapId"], commit["routeProfileId"], str(commit["floor"]), matrix,
                     source="historical-production-display")
    frame = Path(source["input"]).resolve()
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    if digest(frame).lower() != source["inputSHA"].lower():
        raise ValueError("historical input bytes no longer match the recorded input SHA")
    root = Path(source["resourceRoot"]).resolve()
    sheets = [row for row in source["logs"] if row.get("event") == "sheet-shown"
              and row.get("cycleId") == commit.get("cycleId")]
    candidates = {Path(row["path"]).resolve() for row in sheets}
    context = load_browse_context(root, "nightmare")
    if context.error:
        raise ValueError(context.error)
    exact = [Path(path).resolve() for profile in context.profiles if profile.profile_id == pose.route_profile_id
             for item in profile.maps if item.id == pose.map_id for variant in item.variants
             for floor, path in variant.layers.items() if floor == pose.anchor_floor and Path(path).resolve() in candidates]
    exact = list(dict.fromkeys(exact))
    if len(exact) != 1:
        raise ValueError("historical display resource has no unambiguous profile/map/floor binding")
    route = exact[0]
    if not route.is_relative_to(root):
        raise ValueError("historical display image escapes resource root")
    sizes = {}
    for role, image in (("frame", frame), ("route", route)):
        with Image.open(image) as opened:
            sizes[role] = list(opened.size)
    expected_shapes = [row["sourceShape"][:2] for row in source.get("renderCalls", [])
                       if len(row.get("sourceShape", [])) == 3]
    if list(reversed(sizes["route"])) not in expected_shapes:
        raise ValueError("historical render source dimensions do not match exact display asset")
    target.mkdir(parents=True)
    records = {}
    for role, image in (("frame", frame), ("route", route)):
        name = role + image.suffix.lower()
        shutil.copy2(image, target / name)
        records[role] = dict(file=name, sha256=digest(image), size=sizes[role])
    kinds = {"synthetic": "production-result-on-synthetic-frame", "recorded": "production-result-on-recorded-frame",
             "unverified": "production-result-input-unverified"}
    envelope = dict(schema="xingtu-qt-render-replay-v1", mapId=pose.map_id,
                    profileId=pose.route_profile_id, floor=pose.anchor_floor,
                    matrix=pose.pose_for_floor(pose.anchor_floor).tolist(),
                    evidenceKind=kinds[input_kind], recordedCurrentFrameVerified=True,
                    coordinateSpace="recorded-display-pixels-to-client", liveAuthorization=False,
                    sourceResultSha256=digest(result_path), sourceFingerprint=source.get("fp"), **records)
    output = target / "replay.json"
    output.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    print(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--target", type=Path, default=ROOT / "work/qt-replay/known-production")
    parser.add_argument("--input-kind", choices=["synthetic", "recorded", "unverified"], default="unverified")
    args = parser.parse_args()
    prepare(args.result, args.target, args.input_kind)
