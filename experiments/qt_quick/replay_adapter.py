"""Consume the existing core's RoundPose.snapshot schema; never solve a pose."""
from pathlib import Path
import json
import hashlib
from PySide6.QtCore import QUrl
from PIL import Image
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


def load_render_replay(path: Path, allowed_root: Path):
    """Read an isolated immutable presentation record; never grant live display authority."""
    path, allowed_root = path.resolve(), allowed_root.resolve()
    if not path.is_relative_to(allowed_root) or path.stat().st_size > 512 * 1024:
        raise ValueError("回放必须位于本实验 work 目录内")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "xingtu-qt-render-replay-v1":
        raise ValueError("不支持的回放格式")
    pose = RoundPose(data["mapId"], data["profileId"], data["floor"], data["matrix"],
                     source="historical-render-replay", reliable=False)
    matrix = pose.pose_for_floor(data["floor"])
    def image(role):
        record = data[role]
        local = (path.parent / record["file"]).resolve()
        if not local.is_relative_to(path.parent) or local.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("回放图片路径或大小无效")
        if hashlib.sha256(local.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("回放图片校验失败")
        with Image.open(local) as opened:
            width, height = opened.size
            if [width, height] != record["size"] or width * height > 16_000_000:
                raise ValueError("回放图片尺寸不匹配")
        return QUrl.fromLocalFile(str(local)).toString(), width, height
    frame_url, width, height = image("frame")
    route_url, route_width, route_height = image("route")
    labels = {"production-result-on-synthetic-frame": "生产算法结果 · 合成输入回放",
              "production-result-on-recorded-frame": "历史结果回放 · 录制输入",
              "production-result-input-unverified": "历史结果回放 · 输入来源未核实"}
    kind = data.get("evidenceKind", "production-result-input-unverified")
    return dict(frameUrl=frame_url, routeUrl=route_url, clientWidth=width, clientHeight=height,
                routeWidth=route_width, routeHeight=route_height, matrix=matrix.tolist(),
                mapId=pose.map_id, profileId=pose.route_profile_id, floor=data["floor"],
                label=labels.get(kind, labels["production-result-input-unverified"]),
                evidenceKind=kind, recordSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                recordedCurrentFrameVerified=bool(data.get("recordedCurrentFrameVerified")),
                liveAuthorization=False)
