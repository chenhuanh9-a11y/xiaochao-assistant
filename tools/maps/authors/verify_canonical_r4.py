from canonical_display_r4 import *
import zipfile

def validate():
 summaries=[];cache={};proof_index=[]
 def checked_sha(p):
  p=Path(p);signature=(str(p),p.stat().st_size,p.stat().st_mtime_ns)
  if signature not in cache:cache[signature]=sha(p)
  return cache[signature]
 for author in AUTHORS:
  pack=OUT/'packages'/author;m=read(pack/'manifest.json');deps={d['id']:d for d in read(pack/'base-dependencies.json')};r3path=Path(m['previousRelease']['manifestPath']);assert checked_sha(r3path)==m['previousRelease']['sha256'];old=read(r3path);oldbyid={v['id']:v for v in old['registrations']}
  assert m['uniqueMapLayerCount']==len(m['layers'])==len(deps)==81
  assert len({(e['layout'],e['structureSnapshot'],e['floor'])for e in m['layers']})==81
  assert len({e['asset']['path']for e in m['layers']})==81
  assert len({e['layout']for e in m['layers']})==24
  assert len({(e['layout'],e['structureSnapshot'])for e in m['layers']})==27
  assert sum(e['structureRole']=='current'for e in m['layers'])==72
  assert sum(e['structureRole']=='retained'for e in m['layers'])==9
  assert not(pack/'originals').exists()and not(pack/'history').exists()
  maximum_algebra=0.;maximum_world=0.;maximum_coordinate=0.;losses=[];edges=[];layers=[];previous_path=None;previous_pixels=None
  for e in m['layers']:
   d=deps[e['baseId']];assert e['baseKey']==d['baseKey'];assert checked_sha(d['renderedBasePath'])==d['renderedBaseSha256'];assert checked_sha(d['metadataPath'])==d['metadataSha256'];assert e['baseStructureHash']==d['metadataSha256']and e['baseDisplayHash']==d['renderedBaseSha256'];meta=read(d['metadataPath']);assert str(meta['layout'])==e['layout']and meta['worldFloor']==e['worldFloor'];assert e['worldFloor']=={'B1':1,'1F':2,'2F':3}[e['floor']]
   src=Path(e['source']['originalPath']);assert checked_sha(src)==e['source']['originalSha256'];asset=pack/e['asset']['path'];assert checked_sha(asset)==e['asset']['sha256'];img=pixels(asset);ah,aw=img.shape[:2];assert[aw,ah]==e['asset']['sizeWH'];assert 0 in img[:,:,3]and int(img[:,:,3].max())==255
   if str(src)!=previous_path:previous_pixels=pixels(src);previous_path=str(src)
   original=previous_pixels;x0,y0,x1,y1=e['source']['cropRectOriginalSourceXYXY'];original_crop=original[y0:y1,x0:x1];assert original_crop.shape==img.shape
   visible=img[:,:,3]>0;assert np.array_equal(img[:,:,:3][visible],original_crop[:,:,:3][visible])
   M=np.array(e['source']['originalSourceToBase']);C=np.array(e['matrixAssetToBase']);I=np.array(e['matrixBaseToAsset']);T=np.array(e['matrixAssetToOriginalSource']);assert np.max(np.abs(C-M@T))<1e-9;maximum_algebra=max(maximum_algebra,float(np.max(np.abs(I@C-np.eye(3)))))
   B=np.array([[128/60,0,d['bounds']['minX']],[0,128/60,d['bounds']['minY']],[0,0,1]],float);maximum_world=max(maximum_world,float(np.max(np.abs(np.array(e['matrixAssetToWorld'])-B@C))))
   points=np.array([[0,0],[aw,0],[0,ah],[aw,ah],[aw*.37,ah*.61]],float);error=float(np.max(np.abs(apply(C,points)-apply(M,apply(T,points)))));maximum_coordinate=max(maximum_coordinate,error);assert error<1e-8
   alpha=pixels(d['renderedBasePath'])[:,:,3];clip=cv2.dilate(alpha,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*PAD+1,2*PAD+1)));expected=cv2.warpPerspective(clip,C,(aw,ah),flags=cv2.WARP_INVERSE_MAP|cv2.INTER_LINEAR);expected=((expected.astype('uint16')*original_crop[:,:,3].astype('uint16')+127)//255).astype('uint8');assert np.array_equal(img[:,:,3],expected)
   # Independent domain check: translated crop must not remove any source-covered
   # structural footprint pixel. This is rendering/coordinate validation only.
   bw,bh=e['baseSizeWH'];valid_original=cv2.warpPerspective(np.full(original.shape[:2],255,'uint8'),M,(bw,bh),flags=cv2.INTER_NEAREST);valid_crop=cv2.warpPerspective(np.full((ah,aw),255,'uint8'),C,(bw,bh),flags=cv2.INTER_NEAREST);foot=alpha>=128;new_loss=int(np.sum(foot&(valid_original>0)&(valid_crop==0)));assert new_loss==0,(e['id'],new_loss);losses.append(new_loss);domain_coverage=float(np.sum(foot&(valid_original>0))/np.sum(foot));e['sourceContentCoverage']['exactOriginalDomainCoverage']=domain_coverage
   if domain_coverage<.9995:edges.append(dict(id=e['id'],coverage=domain_coverage,sourceFile=e['source']['originalPath'],handling=e['sourceContentCoverage']['handling']))
   r=oldbyid[e['alignment']['sourceR3RegistrationId']];assert r['baseId']==e['baseId']and r['sourceSha256']==e['source']['originalSha256'];assert np.array_equal(M,np.array(r['matrixSourceToBase']));assert e['alignment']['status']==r['alignmentStatus']
   proof=r.get('staticGeometryR3');compact=[]
   if proof:
    for j in proof['trainIndices']+proof['heldOutIndices']:
     o=proof['observations'][j];p=np.array(o['source'])-[x0,y0];q=np.array(o['point']);residual=float(np.linalg.norm(apply(C,[p])[0]-q));assert abs(residual-proof['residualsBasePixels'][j])<1e-7;compact.append(dict(assetPixel=p.tolist(),basePixel=q.tolist(),residualBasePixels=residual,heldOut=j in proof['heldOutIndices']))
    e['alignment']['validatedArchitecturalAnchors']=compact
    if e['alignment']['status']=='confirmed':assert max(v['residualBasePixels']for v in compact)<=4
    else:assert e['alignment']['globalGeometryCertificate']['projectedLPLowerBoundBasePixels']>4
   else:e['alignment']['validationEvidence']='Inherited confirmed R3 registration and exact transform; original evidence remains in preserved R3 audit. No new fit performed.'
   e['display']['validation']=dict(baseBinding='passed',nativeRGBPreservation='passed',alphaClipping='passed',cropCoordinateChain='passed',additionalCropLossPixels=0,independentOfR3AtDisplayLoad=True)
   e['alignmentBaked']=False;assert e['transformApplications']==1
   layers.append(dict(id=e['id'],baseId=e['baseId'],selectedRepresentation=e['source']['selectedRepresentation'],asset=e['asset']['path'],alignmentStatus=e['alignment']['status'],displayBindingAndCrop='passed',originalDomainCoverage=domain_coverage,newCropLossPixels=0))
   print('validated',author,e['baseId'],flush=True)
  assert maximum_algebra<1e-7 and maximum_world<1e-7
  write(pack/'manifest.json',m);write(pack/'source-edge-notes.json',edges)
  with(pack/'coverage.csv').open('r',encoding='utf-8-sig',newline='')as f:coverage=list(csv.DictReader(f));cols=list(coverage[0])
  for row in coverage:row['displayValidation']='passed'
  with(pack/'coverage.csv').open('w',encoding='utf-8-sig',newline='')as f:w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(coverage)
  summary=dict(author=author,status='passed',uniqueMapLayers=81,current=72,retained=9,canonicalAssets=81,selectedSourceCounts=m['selectedSourceCounts'],alignmentCounts=m['alignmentCounts'],displayBindingCropAlphaPassed=81,newCropLossPixels=sum(losses),maximumInverseError=maximum_algebra,maximumWorldMatrixError=maximum_world,maximumCropCoordinateChainError=maximum_coordinate,visibleNativeRGBPreserved=True,originalAndR3Preserved=True,sourceEdgeNotes=len(edges),sharedBaseImagesEmbedded=False,originalsOrAlternativesEmbedded=False,recognitionReRun=False,registrationReFit=False,integrationStatus=m['integration']['status'])
  write(pack/'verification.json',summary);write(pack/'display-validation.json',layers);summaries.append(summary)
 # Check the published full R3 archives stay byte-identical.
 v3=read(PREVIOUS/'verification-r3.json')
 for p in v3['packages']:assert checked_sha(p['archive'])==p['archiveSha256']
 write(OUT/'verification.json',dict(status='passed',uniqueAuthorMapLayers=162,canonicalAssets=162,displayBindingCropAlphaPassed=162,alignmentConfirmedUniqueLayers=161,alignmentPrecisionIssuesUniqueLayers=1,recognitionReRun=False,registrationReFit=False,allR3ArchivesUnchanged=True,packages=summaries))
 print('validated162 canonical layers; no new clipping loss',flush=True)

def premul_warp(a,C,wh):
 a=a.astype('float32')/255;data=np.dstack([a[:,:,:3]*a[:,:,3:4],a[:,:,3]])
 return cv2.warpPerspective(data,np.array(C),tuple(wh),flags=cv2.INTER_LINEAR)

def preview():
 chosen_font=font(17)
 for author,key in [('wang-qingxin','2.104.3034860:104009:2F'),('nightingale','2.104.3044243:104025:2F')]:
  pack=OUT/'packages'/author;m=read(pack/'manifest.json');e=next(v for v in m['layers']if v['baseId']==key);d=next(v for v in read(pack/'base-dependencies.json')if v['id']==key);asset=pixels(pack/e['asset']['path']);wh=e['baseSizeWH'];w,h=wh;p=premul_warp(asset,e['matrixAssetToBase'],wh);yy,xx=np.indices((h,w));checker=np.where(((xx//25+yy//25)%2)[:,:,None],.25,.38);checker=np.repeat(checker,3,axis=2);only=p[:,:,:3]+checker*(1-p[:,:,3:4]);base=pixels(d['renderedBasePath']).astype('float32')/255;under=base[:,:,:3]*base[:,:,3:4]+checker*(1-base[:,:,3:4]);overlay=p[:,:,:3]+under*(1-p[:,:,3:4]);edge=cv2.morphologyEx((base[:,:,3]>=.5).astype('uint8')*255,cv2.MORPH_GRADIENT,np.ones((3,3),'uint8'));overlay[edge>0]=[0,1,1]
  # Original and cropped source coordinates must give equivalent display sampling
  # on the opaque interior; only2 representative renders are compared.
  original=pixels(e['source']['originalPath']);direct=premul_warp(original,e['source']['originalSourceToBase'],wh);opaque=p[:,:,3]>.999;difference=np.abs(p[:,:,:3]-direct[:,:,:3])[opaque];rgbmax=float(difference.max()*255);changed_fraction=float(np.mean(np.max(difference,axis=1)>1/255))
  assert changed_fraction<.001,(author,changed_fraction)
  sheet=Image.new('RGB',(1800,1100),'#17212b');draw=ImageDraw.Draw(sheet);draw.text((8,8),f'R4 {author} {key}\nLeft: actual alpha on checkerboard | Right: overlay on shared base + cyan boundary\nSource crop RGB unscaled; display matrix applied once',font=chosen_font,fill='white')
  for j,img in enumerate([only,overlay]):
   im=Image.fromarray(np.rint(np.clip(img,0,1)*255).astype('uint8'));im.thumbnail((880,970));sheet.paste(im,(900*j+(900-im.width)//2,120+(970-im.height)//2))
  path=OUT/'audit'/f'display-{author}.jpg';sheet.save(path,quality=94);write(OUT/'audit'/f'display-{author}.json',dict(id=e['id'],opaqueInteriorMaximumRGBDifference=rgbmax,fractionOverOneRGBLevel=changed_fraction,cropCoordinateParity='passed',preview=str(path)));print(path,flush=True)

def package():
 verification=read(OUT/'verification.json');assert verification['status']=='passed';result=[]
 for author in AUTHORS:
  pack=OUT/'packages'/author;archive=OUT/f'{author}-canonical-display-r4.zip';assert not archive.exists()
  with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1)as z:
   for p in sorted(pack.rglob('*')):
    if p.is_file():z.write(p,arcname=str(Path(author)/p.relative_to(pack)))
  with zipfile.ZipFile(archive)as z:assert z.testzip()is None;count=len(z.infolist())
  previous=next(v for v in read(PREVIOUS/'verification-r3.json')['packages']if v['author']==author);result.append(dict(author=author,archive=str(archive),archiveSha256=sha(archive),archiveBytes=archive.stat().st_size,zipCrcEntries=count,uniqueMapLayers=81,previousFullAuditArchiveBytes=previous['archiveBytes'],bytesReduced=previous['archiveBytes']-archive.stat().st_size));print(author,archive.stat().st_size,flush=True)
 write(OUT/'archives.json',result);(OUT/'SHA256SUMS.txt').write_text('\n'.join(v['archiveSha256']+'  '+Path(v['archive']).name for v in result)+'\n',encoding='ascii')

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description='Validate, preview or package the historical R4 canonical display recipe.')
 parser.add_argument('action',choices=['validate','preview','package'])
 globals()[parser.parse_args().action]()
