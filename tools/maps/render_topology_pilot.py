"""Offline render through the actual PR4 route model; no GUI/game inputs."""
import argparse
import json
import sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'app'))
from custom_routes import Walkable,FloorRoute,render_layer,route_polyline
from route_topology import display_name

def main():
 p=argparse.ArgumentParser();p.add_argument('--structures',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 report=[];panels=[]
 for floor in ['1','2']:
  f=a.structures/f'structures/nightmare/nightmare-09-{floor}.npz'
  with np.load(f,allow_pickle=False) as z:mask=z['clean_mask']
  w=Walkable(mask);assert w.topology is not None;t=w.topology
  if floor=='1':
   ids=[i for i,r in enumerate(t.room_ids) if r=='53:2'];assert len(ids)==4
   points=[t.points[ids[0]],t.points[ids[1]]]
  else:
   # Deterministic distant source centres with a real connected path.
   start=min(range(len(t.points)),key=lambda i:sum(t.points[i]));candidates=sorted(range(len(t.points)),key=lambda i:sum(t.points[i]),reverse=True)
   target=next(i for i in candidates if t.path(t.points[start],t.points[i]))
   points=[t.points[start],t.points[target]]
  route=FloorRoute(tuple(tuple(round(v) for v in p) for p in points))
  snapped=route_polyline(route,w);assert snapped
  old=Walkable(mask);old.topology=None
  old_points=route_polyline(route,old)
  for caption,walk in [('原 mask 网格 A*',old),('房间／门拓扑接入',w)]:
   bgra=render_layer(mask,route,walk);rgba=Image.fromarray(bgra[:,:,[2,1,0,3]],'RGBA');base=Image.new('RGBA',rgba.size,'#f5f7fb');base.alpha_composite(rgba);base=base.convert('RGB');base.thumbnail((1000,900),Image.Resampling.LANCZOS)
   panel=Image.new('RGB',(1040,1010),'#ffffff');d=ImageDraw.Draw(panel);font=lambda n:ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',n)
   d.text((20,16),f'{display_name("nightmare-09","nightmare-09")} · {floor}F',font=font(25),fill='#172b40');d.text((20,55),caption,font=font(25),fill='#265db5');panel.paste(base,(20,100));d.text((20,958),'同一组自由路点；无跨层连线／新增箭头；静态规划，不等于内墙碰撞。',font=font(20),fill='#526275');panels.append(panel)
  report.append({'mapId':'nightmare-09','sourceLayout':'104001','floor':floor,'points':route.to_json()['points'],'polyline':snapped,'oldPolyline':old_points,'registeredMask':t.data['maskSha256'],'registration':t.data['registration'],'sourceDoors':len(t.data['doors']),'lineSegments':sum(len(s)-1 for s in snapped),'diagonalSegments':sum(x[0]!=y[0] and x[1]!=y[1] for s in snapped for x,y in zip(s,s[1:]))})
 canvas=Image.new('RGB',(2080,2020),'#ffffff')
 for i,panel in enumerate(panels):canvas.paste(panel,((i%2)*1040,(i//2)*1010))
 canvas.save(a.output/'topology-editor-comparison.png');(a.output/'topology-demo.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps([{k:r[k] for k in ['floor','lineSegments','diagonalSegments','sourceDoors']} for r in report]))
if __name__=='__main__':main()
