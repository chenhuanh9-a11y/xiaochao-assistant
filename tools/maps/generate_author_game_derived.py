"""Generate the six reviewed 104001 game-derived author-structure products.

The current game render is warped back into the existing author canvas. The
author validation_mask is copied byte-for-byte; route/canonical/root metadata
remain untouched. This does not bind identity or alter recognition code.
"""
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path
import cv2
import numpy as np

FLOORS = {"B1": 1, "1": 2, "2": 3}

def read(p): return json.loads(Path(p).read_text(encoding="utf-8"))

def matrix(value): return np.asarray(value, dtype=np.float64)

def author_to_game(profile, floor, crosswalk, pack):
    coffin_to_game = matrix(crosswalk["matrixAuthorProfileToGamePixel"][{"B1":"B1","1":"1F","2":"2F"}[floor]])
    if profile == "zhan10-coffin": return coffin_to_game
    speed_to_canonical = matrix(pack["profiles"]["zhan10-speed"]["maps"]["nightmare-09"]["floors"][floor]["matrixProfileToCanonical"])
    coffin_to_canonical = matrix(pack["profiles"]["zhan10-coffin"]["maps"]["nightmare-09"]["floors"][floor]["matrixProfileToCanonical"])
    return coffin_to_game @ np.linalg.inv(coffin_to_canonical) @ speed_to_canonical

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", type=Path, default=Path(".")); ap.add_argument("--run", type=Path, required=True); ap.add_argument("--crosswalk", type=Path, required=True); ap.add_argument("--output", type=Path, default=Path("outputs/game-derived-structure")); ap.add_argument("--index", type=Path, default=Path("references/nightmare-author-structure-index.json")); args = ap.parse_args()
    root=args.root.resolve(); run=(root/args.run).resolve(); output=(root/args.output).resolve(); index_path=(root/args.index).resolve(); crosswalk=read(args.crosswalk); pack=read(root/"references/nightmare-canonical-coordinate-pack.json"); index=read(index_path); by_key={(e["profileId"],e["mapId"],e["variantId"],e["floor"]):e for e in index["entries"]}; report=[]
    for profile in ("zhan10-speed","zhan10-coffin"):
      for floor, game_floor in FLOORS.items():
        key=(profile,"nightmare-09","variant-01",floor); entry=by_key[key]
        # Always read the pre-existing author product, even when the index now
        # points at a generated replacement from an earlier run.
        legacy_rel=entry.get("sourceLegacyAtlas") or entry["resourcePath"]
        source_npz=root/"outputs/噩梦结构预处理-v1"/legacy_rel
        source_png=run/"known-batch-1"/f"level_104001_floor_{game_floor}_60px.png"
        short_profile={"zhan10-speed":"speed","zhan10-coffin":"coffin"}[profile]
        target_rel=Path(f"game-derived/nightmare-09/{short_profile}/variant-01/floor-{floor}.npz")
        target=(root/"outputs/噩梦结构预处理-v1"/target_rel)
        target.parent.mkdir(parents=True,exist_ok=True)
        with np.load(source_npz,allow_pickle=False) as old: validation=np.asarray(old["validation_mask"],dtype=np.uint8); source_shape=validation.shape
        raw=np.fromfile(source_png,dtype=np.uint8); image=cv2.imdecode(raw,cv2.IMREAD_UNCHANGED); assert image is not None and image.ndim==3 and image.shape[2]==4, source_png
        game_mask=(image[:,:,3]>0).astype(np.uint8); H=author_to_game(profile,floor,crosswalk,pack); game_to_author=np.linalg.inv(H); width,height=source_shape[1],source_shape[0]
        clean=(cv2.warpPerspective(game_mask,game_to_author,(width,height),flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT,borderValue=0)>0).astype(np.uint8)
        # Match preprocess_author_structure.build_products exactly: the clean
        # edge is the full 5x5 morphological gradient (inner + outer edge),
        # while distance uses OpenCV's maskSize=3 contract.
        edge=cv2.morphologyEx(clean,cv2.MORPH_GRADIENT,np.ones((5,5),np.uint8)).astype(np.uint8)
        distance=cv2.distanceTransform(clean,cv2.DIST_L2,3).astype(np.float16)
        np.savez_compressed(target,clean_mask=clean,clean_edge_5px=edge,clean_distance=distance,validation_mask=validation)
        old_path=entry.get("sourceLegacyAtlas") or entry["resourcePath"]
        source_render_rel=source_png.relative_to(root).as_posix()
        entry.update({"resourcePath":target_rel.as_posix(),"resourceBytes":target.stat().st_size,"maskPixels":int(clean.sum()),"edgePixels":int((edge>0).sum()),"distanceMax":float(distance.max()),"validationMaskPixels":int(validation.sum()),"gameDerived":True,"sourceLayoutId":"104001","sourceGameVersion":"2.104.3059230","sourceGameFloor":FLOORS[floor],"sourceGameFloorKey":{"B1":"B1","1":"1F","2":"2F"}[floor],"sourceCurrentRender":source_render_rel,"sourceLegacyAtlas":old_path,"matrixAuthorProfileToGamePixel":H.tolist(),"matrixGamePixelToAuthorProfile":game_to_author.tolist(),"validationMaskPreserved":"copied byte-for-byte from original author product","identityInjection":False,"authorBinding":"existing nightmare-09 entry retained; no new identity mapping"})
        report.append({"key":"/".join(key),"oldResourcePath":old_path,"newResourcePath":target_rel.as_posix(),"shape":list(source_shape),"cleanPixels":int(clean.sum()),"validationPixels":int(validation.sum()),"matrixAuthorProfileToGamePixel":H.tolist(),"sourceGameFloor":FLOORS[floor]})
    index["entries"]=[by_key[(e["profileId"],e["mapId"],e["variantId"],e["floor"])] for e in index["entries"]]; index.setdefault("provenance",{}); index["provenance"]={"sixGameDerivedEntries":"104001 current-game render warped into existing author canvases", "sourceGameVersion":"2.104.3059230", "validationMasks":"unchanged byte-for-byte", "routeCanonicalRoot":"unchanged", "identity":"no injection", **(index.get("provenance") if isinstance(index.get("provenance"),dict) else {})}; index_path.write_text(json.dumps(index,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); (output/"game-derived-report.json").write_text(json.dumps({"entryCount":len(report),"entries":report,"index":str(index_path),"output":str(output)},ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); print(json.dumps({"entryCount":len(report),"output":str(output),"index":str(index_path)},ensure_ascii=False))

if __name__ == "__main__": main()
