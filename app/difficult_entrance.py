"""Difficulty-specific direction -> local shape candidates, without runtime lock."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import cv2
import numpy as np
from side_door_recognition import anchor_descriptor, topology, descriptor_similarity, descriptor_containment
from marked_map_retrieval import _direction_from_topology
from nightmare_candidate import map_color_mask


def best_icon(image, icon, center_mask=None):
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    found = []
    for scale in (.6,.7,.8,.9,1.,1.1,1.2,1.4,1.6,1.8,2.):
        w,h = round(icon.shape[1]*scale), round(icon.shape[0]*scale)
        if w>gray.shape[1] or h>gray.shape[0]:
            continue
        template=cv2.resize(icon,(w,h),interpolation=cv2.INTER_AREA)
        score_map=cv2.matchTemplate(gray,template,cv2.TM_CCOEFF_NORMED)
        if center_mask is not None:
            allowed = center_mask[h//2:h//2+score_map.shape[0], w//2:w//2+score_map.shape[1]]
            score_map[allowed == 0] = -1
        _,score,_,point=cv2.minMaxLoc(score_map)
        found.append({"x":point[0],"y":point[1],"width":w,"height":h,"score":float(score)})
    return max(found,key=lambda r:r["score"]) if found else None


def descriptor(image, anchor):
    mask=map_color_mask(image[:,:,:3],live=True)*255
    # Shape units follow the normalized reference geometry. Icon sizes here are
    # fitted in the same ~210px crop space, not in the original 840px poster.
    step=anchor["width"]*.20
    return anchor_descriptor(mask,anchor,16,step_override=step,sample_radius_override=max(1,step*.28))


class DifficultEntrance:
    def __init__(self, manifest:Path, asset_root:Path, vision_index:Path):
        raw=json.loads(manifest.read_text(encoding="utf-8"))
        if raw.get("mode")!="difficult":
            raise ValueError("difficult resources required")
        encoded=json.loads(vision_index.read_text(encoding="utf-8"))["exitIcon"]
        self.icon=np.frombuffer(base64.b64decode(encoded["pixels"]),np.uint8).reshape(encoded["height"],encoded["width"])
        self.references=[]
        for row in raw["maps"]:
            if row["manualReferenceStatus"]!="clean-reference":
                continue
            image=cv2.imdecode(np.fromfile(asset_root/row["manualReference"],np.uint8),cv2.IMREAD_COLOR)
            image=cv2.resize(image,(210,210),interpolation=cv2.INTER_AREA)
            anchor=best_icon(image,self.icon)
            self.references.append({"mapId":row["id"],"direction":row["direction"],"shape":row["shape"],
                "anchor":anchor,"descriptor":descriptor(image,anchor)})

    def rank(self, frame, anchors):
        result={"mode":"difficult","entryKind":"unknown","direction":None,"candidates":[],"displayAllowed":False}
        if not anchors:
            return {**result,"reason":"exit-marker-not-visible"}
        anchor=anchors[0]
        margin=anchor["score"]-(anchors[1]["score"] if len(anchors)>1 else 0)
        result["anchorScore"]=anchor["score"]
        result["anchorMargin"]=margin
        if anchor["score"]<.78 or margin<.10:
            return {**result,"reason":"exit-marker-ambiguous"}
        query=descriptor(frame,anchor)
        t=topology(query)
        direction=_direction_from_topology(t) if t else None
        result["direction"]=direction
        result["anchor"]={key:anchor[key] for key in ("x","y","width","height")}
        for ref in self.references:
            if ref["direction"]!=direction:
                continue
            score=descriptor_similarity(query,ref["descriptor"])
            contained=descriptor_containment(query,ref["descriptor"])
            result["candidates"].append({"mapId":ref["mapId"],"shape":ref["shape"],
                "score":float(score*.5+contained*.5),"referenceIconScore":ref["anchor"]["score"]})
        result["candidates"].sort(key=lambda r:r["score"],reverse=True)
        result["reason"]="advisory-direction-shape-pending-current-frame-fit"
        return result
