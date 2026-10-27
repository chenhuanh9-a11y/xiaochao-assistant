from pathlib import Path
import json,hashlib,csv,os
os.environ['OMP_NUM_THREADS']='1'
os.environ['OPENBLAS_NUM_THREADS']='1'
from PIL import Image,ImageDraw,ImageFont

from map_paths import AUTHOR_ROOTS as ROOTS, TILEMAP_ROOT as BASE, AUTHOR_WORK as OUT, font

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory():
 OUT.mkdir(exist_ok=True)
 for author,root in ROOTS.items():
  rows=[]
  for p in sorted(root.rglob('*')):
   if not p.is_file():continue
   row=dict(path=str(p),relative=str(p.relative_to(root)),bytes=p.stat().st_size,sha256=sha(p))
   if p.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
    with Image.open(p) as im:row.update(size=list(im.size),mode=im.mode)
   rows.append(row)
  write(OUT/author/'inventory.json',rows)
  print(author,len(rows),'images',sum('size' in x for x in rows))
  groups={}
  for row in rows:
   if 'size' in row:groups.setdefault(str(Path(row['relative']).parent),[]).append(Path(row['relative']).name)
  print(json.dumps(groups,ensure_ascii=False,indent=2))
  if author=='wang-qingxin':images=[r for r in rows if '1.一图流' in r['relative']]
  else:images=[r for r in rows if 'size' in r and '三合一' in Path(r['path']).stem]
  contact(images,OUT/author/'inventory-contact.jpg')
def contact(rows,path):
 chosen_font=font(16)
 tw,th=320,310;sheet=Image.new('RGB',(tw*5,th*((len(rows)+4)//5)),'#ededed');d=ImageDraw.Draw(sheet)
 for i,r in enumerate(rows):
  im=Image.open(r['path']).convert('RGB');im.thumbnail((tw-8,th-57));x=i%5*tw;y=i//5*th;sheet.paste(im,(x+(tw-im.width)//2,y+57));label=f'{i+1:02d} '+r['relative'];d.text((x+4,y+3),label[-39:-18]+'\n'+label[-18:] if len(label)>22 else label,fill='black',font=chosen_font)
 sheet.save(path)
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description=__doc__ or 'Inventory configured authorized author inputs.')
 parser.parse_args()
 inventory()
