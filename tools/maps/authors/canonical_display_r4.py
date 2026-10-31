"""Deterministic author display normalization. No recognition or registration fit.

All source/R3/base resources are read-only. Outputs stay in the task workspace.
"""
from pathlib import Path
import json, hashlib, math, csv, sys, time
from collections import defaultdict, Counter
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2
cv2.setNumThreads(1)
cv2.ocl.setUseOpenCL(False)
from map_paths import AUTHOR_WORK as PREVIOUS, CANONICAL_WORK as OUT, font
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'app'))
from author_display_cleanup import derive_r4_display_alpha
AUTHORS=['wang-qingxin','nightingale']
PAD=12

def read(p):return json.loads(Path(p).read_text('utf8'))
def write(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb')as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def pixels(p):return np.array(Image.open(p).convert('RGBA'))
def apply(M,p):return(np.c_[np.array(p),np.ones(len(p))]@np.array(M).T)[:,:2]
def model_max(r):
 e=r.get('staticGeometryR3')
 if e:return max(e['residualsBasePixels'][i]for i in e['trainIndices']+e['heldOutIndices'])
 return None
def completeness(alpha,r):
 # A cheap domain test of the already registered transform, not identification.
 yy,xx=np.where(alpha[::8,::8]>=128);q=np.c_[xx*8,yy*8]
 p=apply(r['matrixBaseToSource'],q);w,h=r['sourceSizeWH']
 return float(np.mean((p[:,0]>=0)&(p[:,1]>=0)&(p[:,0]<w)&(p[:,1]<h)))if len(p)else 0.0

def choose(rows):
 ordered=sorted(rows,key=lambda x:(x['alignmentStatus']!='confirmed',-x['sampledCompleteness'],x['sourceFile']))
 good=[v for v in ordered if v['alignmentStatus']==ordered[0]['alignmentStatus']]
 bestcoverage=max(v['sampledCompleteness']for v in good)
 good=[v for v in good if v['sampledCompleteness']>=bestcoverage-.0005]
 native=next((v for v in good if v['representation']=='floor'),None)
 comp=next((v for v in good if v['representation']=='composite'),None)
 if not native:return good[0],'No equally complete/validated single-floor source; crop the bound composite floor.'
 if not comp:return native,'Single-floor source has the best completeness/validated alignment class.'
 gain=native['worstAxisScale']/comp['worstAxisScale']
 if native['worstAxisScale']>1.4 and gain>=1.5:
  return comp,'Composite has >=1.5x finer native sampling and the single-floor source has <43px/cell; same validated class and comparable completeness. Prefer the clearer composite crop.'
 return native,'Prefer the complete single-floor source; its sampling is adequate or the composite advantage is modest.'

def plan():
 OUT.mkdir(exist_ok=True);records=[];summaries=[]
 for author in AUTHORS:
  pack=PREVIOUS/'packages-r3'/author;manifest=read(pack/'manifest.json');manifest_hash=sha(pack/'manifest.json');bases={b['id']:b for b in read(pack/'base-dependencies.json')};groups=defaultdict(list)
  for r in manifest['registrations']:groups[r['baseId']].append(r)
  assert len(groups)==81
  for key,rows in sorted(groups.items()):
   b=bases[key];alpha=pixels(b['path'])[:,:,3];candidates=[]
   for r in rows:
    scales=np.linalg.svd(np.array(r['matrixSourceToBase'])[:2,:2],compute_uv=False)
    candidates.append(dict(registrationId=r['id'],sourceFile=r['sourceFile'],sourceOriginalPath=r['sourceOriginalPath'],sourceSha256=r['sourceSha256'],sourceSizeWH=r['sourceSizeWH'],representation=r['representation'],alignmentStatus=r['alignmentStatus'],sampledCompleteness=completeness(alpha,r),worstAxisScale=float(max(scales)),minimumNativePixelsPerCell=60/float(max(scales)),recordedGeometryMaximumBasePixels=model_max(r)))
   selected,why=choose(candidates);r=next(v for v in rows if v['id']==selected['registrationId'])
   record=dict(author=author,baseId=key,layout=r['layout'],stableCatalogGuid=r['stableCatalogGuid'],structureSnapshot=r['structureSnapshot'],floor=r['floor'],worldFloor=r['worldFloor'],authorGroup=r['authorGroup'],selected=selected,selectionReason=why,alternatives=[v for v in candidates if v['registrationId']!=selected['registrationId']],sourceR3ManifestPath=str(pack/'manifest.json'),sourceR3ManifestSha256=manifest_hash,matrixOriginalSourceToBase=r['matrixSourceToBase'],base=b,retainedOldStructure=r['layout']in['104023','104024','104025']and r['structureSnapshot']=='2.104.3034860')
   records.append(record)
  rr=[v for v in records if v['author']==author]
  summaries.append(dict(author=author,uniqueMapLayers=len(rr),stableLayouts=len({v['layout']for v in rr}),layoutVersionGroups=len({(v['layout'],v['structureSnapshot'])for v in rr}),currentMapLayers=sum(not v['retainedOldStructure']for v in rr),retainedMapLayers=sum(v['retainedOldStructure']for v in rr),selectedRepresentations=dict(Counter(v['selected']['representation']for v in rr)),alignmentCounts=dict(Counter(v['selected']['alignmentStatus']for v in rr))))
 write(OUT/'selection-plan.json',dict(schema='author-display-selection-plan-v1',purpose='One canonical display per author/layout/structure-version/floor; no registration rerun.',originalAndR3Preserved=True,displayPaddingBasePixels=PAD,records=records,summaries=summaries))
 for s in summaries:print(json.dumps(s,ensure_ascii=False),flush=True)

def compare():
 chosen_font=font(18);plan=read(OUT/'selection-plan.json')
 for author,key in [('wang-qingxin','2.104.3034860:104009:2F'),('nightingale','2.104.3034860:104002:2F')]:
  e=next(v for v in plan['records']if v['author']==author and v['baseId']==key);pack=PREVIOUS/'packages-r3'/author;m=read(pack/'manifest.json');rr=[v for v in m['registrations']if v['baseId']==key];w,h=e['base']['size'];sheet=Image.new('RGB',(1600,1000),'#18222b');d=ImageDraw.Draw(sheet)
  for j,role in enumerate(['floor','composite']):
   r=next(v for v in rr if v['representation']==role);a=pixels(pack/r['sourceFile']);warped=cv2.warpPerspective(a,np.array(r['matrixSourceToBase']),(w,h));im=Image.fromarray(warped[:,:,:3]);im.thumbnail((780,790));sheet.paste(im,(800*j+(800-im.width)//2,80));d.text((800*j+8,8),f'{author} {key} {role}\noriginal {r["sourceSizeWH"]}, canonical view only',font=chosen_font,fill='white')
   # Same base-space patch at equal zoom; no fitted or generated image content.
   x,y=w//2,h//2;patch=Image.fromarray(warped[:,:,:3]).crop((x-130,y-80,x+130,y+80)).resize((520,320));sheet.paste(patch,(800*j+130,660))
  path=OUT/'audit'/f'clarity-{author}.jpg';path.parent.mkdir(exist_ok=True);sheet.save(path,quality=94)
  print(path)

def build():
 plan=read(OUT/'selection-plan.json')
 for author in AUTHORS:
  pack=OUT/'packages'/author
  assert not pack.exists(),'New canonical output already exists; preserve instead of overwriting.'
  pack.mkdir(parents=True);(pack/'assets').mkdir();entries=[];dependencies=[];last_path=None;last_pixels=None
  previous_pack=PREVIOUS/'packages-r3'/author;r3=read(previous_pack/'manifest.json')
  byid={r['id']:r for r in r3['registrations']}
  assert sha(previous_pack/'manifest.json')==next(r['sourceR3ManifestSha256']for r in plan['records']if r['author']==author)
  for e in [v for v in plan['records']if v['author']==author]:
   started=time.perf_counter();b=e['base'];r=byid[e['selected']['registrationId']]
   src=previous_pack/r['sourceFile']
   assert sha(src)==r['sourceSha256']
   if str(src)!=last_path:last_pixels=pixels(src);last_path=str(src)
   original=last_pixels;H,W=original.shape[:2];assert [W,H]==r['sourceSizeWH']
   assert sha(b['path'])==b['sha256'] and sha(b['metadataPath'])==b['metadataSha256']
   alpha=pixels(b['path'])[:,:,3];clip=cv2.dilate(alpha,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*PAD+1,2*PAD+1)))
   yy,xx=np.where(clip>0);base_box=[int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1]
   bx0,by0,bx1,by1=base_box;M=np.array(r['matrixSourceToBase'],float);inv=np.linalg.inv(M)
   projection=apply(inv,[[bx0,by0],[bx1,by0],[bx1,by1],[bx0,by1]])
   unbounded=[math.floor(float(projection[:,0].min()))-2,math.floor(float(projection[:,1].min()))-2,math.ceil(float(projection[:,0].max()))+2,math.ceil(float(projection[:,1].max()))+2]
   x0=max(0,unbounded[0]);y0=max(0,unbounded[1]);x1=min(W,unbounded[2]);y1=min(H,unbounded[3])
   assert x1>x0 and y1>y0
   crop=original[y0:y1,x0:x1].copy();T=np.array([[1,0,x0],[0,1,y0],[0,0,1]],float);C=M@T
   projected_alpha=cv2.warpPerspective(clip,C,(x1-x0,y1-y0),flags=cv2.WARP_INVERSE_MAP|cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
   crop[:,:,3]=((projected_alpha.astype('uint16')*crop[:,:,3].astype('uint16')+127)//255).astype('uint8')
   crop[:,:,:3][crop[:,:,3]==0]=0
   # Keep the R4 crop and its one-time M @ T transform. Only the display
   # alpha changes; uncertain/textured backgrounds retain the original alpha.
   cleaned,cleanup=derive_r4_display_alpha(crop[:,:,[2,1,0,3]],author)
   crop[:,:,3]=cleaned[:,:,3]
   name=f'{e["structureSnapshot"]}-{e["layout"]}-{e["floor"]}.png';asset=pack/'assets'/name
   Image.fromarray(crop).save(asset,compress_level=6)
   B=np.array([[128/60,0,b['bounds']['minX']],[0,128/60,b['bounds']['minY']],[0,0,1]],float)
   key=[e['layout'],e['stableCatalogGuid'],e['floor'],e['structureSnapshot'],b['metadataSha256']]
   precision={'status':r['alignmentStatus'],'budgetBasePixels':4.0,'sourceR3RegistrationId':r['id'],'sourceR3ManifestSha256':e['sourceR3ManifestSha256']}
   proof=r.get('staticGeometryR3')
   if proof:
    precision.update(fitMaximumBasePixels=max(proof['residualsBasePixels'][i]for i in proof['trainIndices']),heldOutMaximumBasePixels=max(proof['residualsBasePixels'][i]for i in proof['heldOutIndices']),fitPointCount=len(proof['trainIndices']),heldOutPointCount=len(proof['heldOutIndices']))
    if'globalGeometryCertificate'in proof:precision['globalGeometryCertificate']=proof['globalGeometryCertificate']
   source=dict(originalPath=r['sourceOriginalPath'],originalSha256=r['sourceSha256'],originalSizeWH=[W,H],selectedRepresentation=r['representation'],authorGroup=r['authorGroup'],selectionReason=e['selectionReason'],cropRectOriginalSourceXYXY=[x0,y0,x1,y1],originalSourceToBase=M.tolist())
   entry=dict(id=f'{author}:{e["baseId"]}',layout=e['layout'],stableCatalogGuid=e['stableCatalogGuid'],floor=e['floor'],worldFloor=e['worldFloor'],structureSnapshot=e['structureSnapshot'],structureRole='retained'if e['retainedOldStructure']else'current',baseId=e['baseId'],baseKey=key,baseStructureHash=b['metadataSha256'],baseDisplayHash=b['sha256'],baseSizeWH=b['size'],asset=dict(path='assets/'+name,format='PNG RGBA',sizeWH=[x1-x0,y1-y0],sha256=sha(asset),bytes=asset.stat().st_size),coordinateSystem='original native crop pixels; x right, y down; column vectors',matrixAssetToBase=C.tolist(),matrixBaseToAsset=np.linalg.inv(C).tolist(),matrixAssetToWorld=(B@C).tolist(),matrixAssetToOriginalSource=T.tolist(),alignmentBaked=False,transformApplications=1,source=source,alignment=precision,display=dict(mode='overlay_on_shared_base',alphaApplied=True,alphaRecipe='shared rendered footprint alpha; dilate12 base px for author wall/route padding; linearly project into native crop; multiply original alpha',paddingBasePixels=PAD,alphaIsDisplayClipping=True,fullBaseMaskBBoxXYXY=base_box,unboundedSourceCropXYXY=unbounded,newCropDoesNotDiscardProjectedDisplaySupport=True),sourceContentCoverage=dict(sampledOriginalDomainCoverage=e['selected']['sampledCompleteness'],sourceEdgeTruncated=e['selected']['sampledCompleteness']<.9995,handling='Original missing pixels remain transparent and show the shared base; no author pixels invented.'),generationSeconds=time.perf_counter()-started)
   entry['display']['backgroundCleanup']=cleanup
   entries.append(entry)
   dependencies.append(dict(id=b['id'],baseKey=key,layout=b['layout'],stableCatalogGuid=b['guid'],floor=b['floor'],worldFloor=b['worldFloor'],structureSnapshot=b['snapshot'],structureHash=b['metadataSha256'],structureHashScope='Exact source.json bytes: placements, graph/door records, bounds and coordinate semantics; not author image pixels.',metadataPath=b['metadataPath'],metadataSha256=b['metadataSha256'],renderedBasePath=b['path'],renderedBaseSha256=b['sha256'],sizeWH=b['size'],bounds=b['bounds'],cellPixels=60,worldUnitsPerCell=128))
   print(author,e['baseId'],r['representation'],entry['asset']['sizeWH'],entry['asset']['bytes'],flush=True)
  manifest=dict(schema='canonical-author-display-v1',revision='r4-deduplicated-native-crops',author=author,uniqueMapLayerCount=len(entries),stableLayoutCount=24,layoutVersionGroupCount=27,currentMapLayerCount=72,retainedMapLayerCount=9,assetCount=len(entries),selectedSourceCounts=dict(Counter(v['source']['selectedRepresentation']for v in entries)),alignmentCounts=dict(Counter(v['alignment']['status']for v in entries)),recognitionDependency='Shared structural base owns identification/fit capability. Author acceptance covers base binding, inherited alignment, display clipping and crop coordinates only.',integration=dict(status='not_configured_in_mainline',mainlineWrites=False),baseDependencyFile='base-dependencies.json',baseImagesEmbedded=False,originalsAndAlternativesEmbedded=False,previousRelease=dict(manifestPath=str(previous_pack/'manifest.json'),sha256=sha(previous_pack/'manifest.json'),preserved=True),coordinateContract=dict(originalPixelsResampled=False,rgbPreservedWhereAlphaNonzero=True,alignmentBaked=False,transformApplications=1,assetToBase='M_originalSourceToBase @ T_cropOrigin',worldToVoxelZ='-worldY/128'),layers=entries)
  write(pack/'manifest.json',manifest);write(pack/'base-dependencies.json',dependencies)
  cols=['id','layout','structureSnapshot','floor','structureRole','selectedRepresentation','sourceOriginalPath','asset','assetWidth','assetHeight','alignmentStatus','displayValidation','sourceEdgeTruncated']
  with(pack/'coverage.csv').open('w',encoding='utf-8-sig',newline='')as f:
   w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
   for e in entries:w.writerow(dict(id=e['id'],layout=e['layout'],structureSnapshot=e['structureSnapshot'],floor=e['floor'],structureRole=e['structureRole'],selectedRepresentation=e['source']['selectedRepresentation'],sourceOriginalPath=e['source']['originalPath'],asset=e['asset']['path'],assetWidth=e['asset']['sizeWH'][0],assetHeight=e['asset']['sizeWH'][1],alignmentStatus=e['alignment']['status'],displayValidation='pending_deterministic_validation',sourceEdgeTruncated=e['sourceContentCoverage']['sourceEdgeTruncated']))
  write(pack/'alignment-issues.json',[dict(id=e['id'],layout=e['layout'],structureSnapshot=e['structureSnapshot'],floor=e['floor'],alignment=e['alignment'])for e in entries if e['alignment']['status']!='confirmed'])
  title='王清心'if author=='wang-qingxin'else'夜莺'
  (pack/'README.md').write_text(f'''# {title}规范化显示包 R4

每个“作者＋稳定布局＋结构版本＋楼层”只有一份canonical PNG：81层=当前72层＋保留旧结构9层，共27组版本地图、24个稳定layout。正式包不重复收录单层/合图，不内嵌共享底图，不复制原图备份和备选；原件及完整R3备份保留在原位置。

选源：{manifest['selectedSourceCounts']}。清晰度、完整性、已有对齐等级相近时优先单层；原采样显著更高或更完整时裁取合图。所有矩阵复用R3，不重跑识别或配准。

assets/中的PNG保持所选原图的原始像素采样，没有烘焙仿射、没有再次缩放RGB。透明区域仅用于显示裁切：使用精确版本共享底图的alpha外形＋12 base px的墙线/路线边缘留白，映射至原始裁片坐标；不是放宽4px精度门槛。来源原先截掉的边缘保持透明，由共享底图显示，不补造作者像素。

随后仅对已复核的nightingale（爱吃醋·噩梦）R4黑底来源且边界足够一致的外侧背景派生透明度，保留不同色内容及其2个原生像素邻域；不裁画布或改矩阵。王清心水印/纹理背景及其他作者保持原alpha，逐层结果见display.backgroundCleanup，不代表所有作者背景已清理。

加载时只对PNG应用matrixAssetToBase一次：M_asset_to_base=M_original_to_base*T_cropOrigin。PNG本身已带alpha，alignmentBaked=false，transformApplications=1。不要再使用原图矩阵或原裁切偏移重复套用。世界坐标、逆矩阵、base尺寸/结构版本/结构hash/显示hash均在manifest和base-dependencies。

作者包验收按共享底图架构：核对正确base版本、既有对齐精度、显示绑定、透明和裁切坐标。共享底图的识别能力不在每个作者包重复验收。接入配置状态单列为not_configured_in_mainline，未写主线。

当前唯一层计数精度结果：{manifest['alignmentCounts']}。夜莺新版104025/2F的单层/合图只保留一份、合并为一个精度问题；保留4px证据，不新增功能缺失数、不重新调参。详见alignment-issues.json。

覆盖见coverage.csv，确定性坐标/像素/透明检查见verification.json。原图路径/hash用于追溯，不要求交付包依赖旧R3目录来显示；显示只依赖本包PNG/矩阵和准确共享base。
''',encoding='utf8')
  print('built',author,len(entries),'unique layers',flush=True)

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('action',choices=['plan','compare','build'])
 globals()[parser.parse_args().action]()
