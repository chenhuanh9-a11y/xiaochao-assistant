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
from route_map_display import load_background

def main():
 p=argparse.ArgumentParser();p.add_argument('--structures',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--before',type=Path,help='Captured PR8 paths, preserving the exact original endpoints');p.add_argument('--render-root',type=Path);p.add_argument('--require-normal',action='store_true');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 before=json.loads(a.before.read_text(encoding='utf-8')) if a.before else None
 report=[];panels=[]
 for floor in ['1','2']:
  f=a.structures/f'structures/nightmare/nightmare-09-{floor}.npz'
  with np.load(f,allow_pickle=False) as z:mask=z['clean_mask']
  w=Walkable(mask);assert w.topology is not None;t=w.topology
  background=load_background(mask,asset_root=a.render_root)
  if a.require_normal:assert background.image is not None,background.status
  if floor=='1':
   ids=[i for i,r in enumerate(t.room_ids) if r=='53:2'];assert len(ids)==4
   points=[t.points[ids[0]],t.points[ids[1]]]
  else:
   # Deterministic distant source centres with a real connected path.
   start=min(range(len(t.points)),key=lambda i:sum(t.points[i]));candidates=sorted(range(len(t.points)),key=lambda i:sum(t.points[i]),reverse=True)
   target=next(i for i in candidates if t.path(t.points[start],t.points[i]))
   points=[t.points[start],t.points[target]]
  captured=next((r for r in before['floors'] if r['floor']==floor),None) if before else None
  if captured:points=captured['points']
  route=FloorRoute(tuple(tuple(round(v) for v in p) for p in points))
  snapped=route_polyline(route,w);assert snapped
  old=Walkable(mask);old.topology=None
  old_points=captured['polyline'] if captured else route_polyline(route,old)
  class RecordedPath:
   def contains(self,p):return w.contains(p)
   def path(self,start,goal):return old_points[0] if tuple(start)==route.points[0] and tuple(goal)==route.points[-1] else None
  for caption,walk in [('修正前：PR8 记录路径',RecordedPath() if captured else old),('当前：普通通道正交，源斜道保留',w)]:
   bgra=render_layer(mask,route,walk,background=background);rgba=Image.fromarray(bgra[:,:,[2,1,0,3]],'RGBA');base=Image.new('RGBA',rgba.size,'#172027');base.alpha_composite(rgba);base=base.convert('RGB');base.thumbnail((1000,800),Image.Resampling.LANCZOS)
   panel=Image.new('RGB',(1040,1010),'#ffffff');d=ImageDraw.Draw(panel);font=lambda n:ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',n)
   d.text((20,16),f'{display_name("nightmare-09","nightmare-09")} · {floor}F',font=font(25),fill='#172b40');d.text((20,55),caption,font=font(25),fill='#265db5');panel.paste(base,(20,100));d.text((20,930),background.status,font=font(20),fill='#526275');d.text((20,958),'底图为正常源渲染；门图负责规划；识别二值 mask 未改。',font=font(20),fill='#526275');panels.append(panel)
  report.append({'mapId':'nightmare-09','sourceLayout':'104001','floor':floor,'points':route.to_json()['points'],'polyline':snapped,'oldPolyline':old_points,'registeredMask':t.data['maskSha256'],'registration':t.data['registration'],'normalRenderStatus':background.status,'sourceDoors':len(t.data['doors']),'lineSegments':sum(len(s)-1 for s in snapped),'diagonalSegments':sum(x[0]!=y[0] and x[1]!=y[1] for s in snapped for x,y in zip(s,s[1:]))})
 if before and before.get('straight'):
  sample=before['straight'];floor=sample['floor'];mask=np.load(a.structures/f'structures/nightmare/nightmare-09-{floor}.npz',allow_pickle=False)['clean_mask'];w=Walkable(mask)
  new=route_polyline(FloorRoute(tuple(map(tuple,sample['points']))),w);x0,y0,x1,y1=sample['crop'];background=load_background(mask,asset_root=a.render_root)
  if a.require_normal:assert background.image is not None
  for caption,lines in [('端点修正前：直道内存在斜接头',sample['polyline']),('端点修正后：接入轴线，全部正交',new)]:
   if background.image is not None:
    detail=Image.new('RGBA',background.image.size,'#172027');detail.alpha_composite(background.image);detail=detail.convert('RGB')
   else:detail=Image.fromarray(np.where((mask>0)[:,:,None],np.array([218,228,238],np.uint8),np.array([245,247,251],np.uint8)).astype(np.uint8),'RGB')
   d=ImageDraw.Draw(detail)
   for line in lines:d.line([tuple(p) for p in line],fill='#2f66eb',width=3)
   for px,py in sample['points']:d.ellipse((px-2,py-2,px+2,py+2),fill='#e03150')
   crop=detail.crop((x0,y0,x1,y1)).resize((250,700),Image.Resampling.NEAREST);panel=Image.new('RGB',(1040,1010),'#ffffff');d=ImageDraw.Draw(panel);font=lambda n:ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',n)
   d.text((20,20),caption,font=font(27),fill='#172b40');d.text((20,65),'2F 局部：同一自由路点 (158,458) → (158,650)',font=font(24),fill='#526275');panel.paste(crop,(350,130));d.text((20,900),'红点为真实路点；仅放大实际路径，无绘制箭头或平滑替代。',font=font(21),fill='#526275');panels.append(panel)
  report.append({'floor':'2','case':'straight-corridor-offgrid','points':sample['points'],'beforePolyline':sample['polyline'],'polyline':new,'diagonalSegments':sum(x[0]!=y[0] and x[1]!=y[1] for s in new for x,y in zip(s,s[1:]))})
 canvas=Image.new('RGB',(2080,1010*((len(panels)+1)//2)),'#ffffff')
 for i,panel in enumerate(panels):canvas.paste(panel,((i%2)*1040,(i//2)*1010))
 canvas.save(a.output/'topology-editor-comparison.png');(a.output/'topology-demo.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps([{k:r[k] for k in ['floor','case','lineSegments','diagonalSegments','sourceDoors'] if k in r} for r in report]))
if __name__=='__main__':main()
