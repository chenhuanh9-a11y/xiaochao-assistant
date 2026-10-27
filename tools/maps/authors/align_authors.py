"""Single-worker, offline candidate registration. All writes local to task.
No shared module import, no shared feature caches, no source edits.
"""
import os,sys,time,math
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
sys.dont_write_bytecode=True
from author_audit import *
import cv2
import numpy as np
cv2.setNumThreads(1);cv2.ocl.setUseOpenCL(False)
MAX=1400
def pixels(p):
 a=np.array(Image.open(p).convert('RGBA'));return a
def feature(p):
 a=pixels(p);h,w=a.shape[:2];s=min(1,MAX/max(h,w));sz=(round(w*s),round(h*s))
 a=cv2.resize(a,sz,interpolation=cv2.INTER_AREA) if s<1 else a
 rgb=(a[:,:,:3].astype(float)*a[:,:,3:4]/255).astype('uint8')
 gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
 sift=cv2.SIFT_create(nfeatures=2200,contrastThreshold=.009,edgeThreshold=18)
 kp,ds=sift.detectAndCompute(gray,None)
 return dict(points=np.array([k.pt for k in kp],np.float32)/s,desc=ds,size=[w,h],scale=s)
def match(a,b):
 if a['desc'] is None or b['desc'] is None:return None
 bf=cv2.BFMatcher();ms=bf.knnMatch(a['desc'],b['desc'],k=2)
 pairs=[m for m,n in ms if m.distance<.72*n.distance]
 # Target descriptor duplicates would otherwise overcount repeated rooms.
 pairs=sorted(pairs,key=lambda m:m.distance);used=set();uniq=[]
 for m in pairs:
  if m.trainIdx not in used:uniq.append(m);used.add(m.trainIdx)
 if len(uniq)<8:return None
 uniq.sort(key=lambda m:m.queryIdx);p=np.array([a['points'][m.queryIdx] for m in uniq]);q=np.array([b['points'][m.trainIdx] for m in uniq]);hold=np.arange(len(p))%5==0
 cv2.setRNGSeed(20261026)
 M,ins=cv2.estimateAffine2D(p[~hold],q[~hold],method=cv2.RANSAC,ransacReprojThreshold=5,maxIters=2000,confidence=.995,refineIters=10)
 if M is None:return None
 ins=ins.ravel()>0;pred=p@M[:,:2].T+M[:,2];errs=np.linalg.norm(pred-q,axis=1);agree=errs<8
 if sum(ins)<6:return None
 hull=float(cv2.contourArea(cv2.convexHull(q[~hold][ins].astype('float32'))))/(b['size'][0]*b['size'][1])
 singular=np.linalg.svd(M[:,:2],compute_uv=False)
 if np.linalg.det(M[:,:2])<=0 or singular[0]/singular[1]>1.5:return None
 scale=math.hypot(M[0,0],M[1,0]);angle=math.degrees(math.atan2(M[1,0],M[0,0]))
 if scale<.08 or scale>12:return None
 supported=hold&agree
 return dict(matrixSourceToBase=np.vstack([M,[0,0,1]]).tolist(),inliers=int(sum(ins)),matches=len(p),targetHullFraction=hull,scale=scale,singularScales=singular.tolist(),rotationDegrees=angle,
  trainMedian=float(np.median(errs[~hold][ins])),holdoutCount=int(sum(hold)),holdoutSupported=int(sum(supported)),holdoutSupportedMedian=float(np.median(errs[supported])) if any(supported) else None,
  anchors=[dict(source=x.tolist(),base=y.tolist(),residual=float(e),heldOut=bool(ho),supported=bool(ok)) for x,y,e,ho,ok in zip(p,q,errs,hold,agree)],
  score=float(sum(ins))*min(1,hull/.16))
def bases():
 result=[]
 plan=read(BASE/'work/assistant-transparent-display-20261025/typed-shared-candidate-20261026/target-24-native-plan.json');guids={m['layout']:m['targetGuid'] for m in plan['mapping']}
 for directory,snap in [('nightmare-binary-structure-20261006','2.104.3034860'),('nightmare-binary-structure-update-20261010','2.104.3044243')]:
  root=BASE/'outputs'/directory
  for m in read(root/'index.json')['maps']:
   for f in m['floors']:
    p=root/f['folder']/'rendered-base.png';meta=read(p.parent/'source.json')
    result.append(dict(id=f'{snap}:{m["id"]}:{f["floorKey"]}',layout=m['id'],guid=guids[m['id']],floor=f['floorKey'],worldFloor=f['worldFloor'],snapshot=snap,path=str(p),sha256=sha(p),metadataPath=str(p.parent/'source.json'),metadataSha256=sha(p.parent/'source.json'),size=f['size'],bounds=meta['render']['bounds'],placementCount=len(meta['render']['placements']),doorCount=f['doors']))
 write(OUT/'base-dependencies.json',result);return result
def sources(author):
 rows=read(OUT/author/'inventory.json');out=[]
 if author=='nightingale':
  for r in rows:
   if 'size' not in r:continue
   stem=Path(r['path']).stem
   if stem not in ['-1楼','1楼','2楼']:continue
   floor={'-1楼':'B1','1楼':'1F','2楼':'2F'}[stem]
   out.append(dict(**r,floor=floor,group=str(Path(r['relative']).parent),role='floor'))
 else:
  for r in rows:
   if 'size' not in r:continue
   if r['relative'].startswith('1.一图流'):
    for f in ['B1','1F','2F']:out.append(dict(**r,floor=f,group=Path(r['path']).stem,role='composite'))
   elif r['relative'].startswith('2.分层图'):
    floor={'地下室路线':'B1','一楼路线':'1F','二楼路线':'2F'}[Path(r['path']).stem]
    out.append(dict(**r,floor=floor,group=Path(r['path']).parent.name,role='floor'))
 return out
def main(author):
 start=time.time();bb=bases();features={}
 for b in bb:features[b['id']]=feature(Path(b['path']))
 rr=sources(author);previous=None;af=None
 for i,r in enumerate(rr):
  dest=OUT/author/'registrations'/f'{i+1:03d}.json'
  if dest.exists():continue
  if r['path']!=previous:af=feature(Path(r['path']));previous=r['path']
  candidates=[]
  for b in bb:
   if b['floor']!=r['floor']:continue
   v=match(af,features[b['id']])
   if v:candidates.append(dict(baseId=b['id'],**v))
  candidates.sort(key=lambda x:x['score'],reverse=True)
  rec=dict(source=r,featureRuntime=dict(opencv=cv2.__version__,threads=cv2.getNumThreads(),maxDimension=MAX),candidates=candidates[:5],status='candidate-only')
  write(dest,rec)
  print(author,i+1,len(rr),r['group'],r['floor'],[(x['baseId'],x['inliers'],round(x['targetHullFraction'],2)) for x in candidates[:2]],flush=True)
 print('elapsed',time.time()-start)
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('author',choices=['wang-qingxin','nightingale'])
 main(parser.parse_args().author)
