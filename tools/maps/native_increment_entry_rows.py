"""Entry proposal extraction shared with the original native source pipeline."""
import hashlib,json,re,sys
from pathlib import Path
import cv2
import numpy as np
ORIGINAL=Path(".")
RUNTIME=Path(".")
def read(p):return json.loads(Path(p).read_text(encoding="utf8"))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def build_row(source,indexed,contour_graph):
    mid,floor=source['mapId'],source['floor']
    archive=ORIGINAL/indexed['resourcePath']
    with np.load(archive,allow_pickle=False) as z:
        assert set(z.files)=={'shared_mask','metadata'}
        whole=z['shared_mask'];meta=json.loads(z['metadata'].item())
    binding={k:indexed[k] for k in ['profile','mapId','floor','variant']}
    assert meta['binding']==binding
    version={k:indexed[k] for k in ['structureVersionKey','sourceVersion','baseSnapshot']}
    assert {k:meta[k] for k in version}==version
    assert sha(source['sourceCatalog'])==source['sourceCatalogSHA256']
    assert sha(source['sourceMetadata'])==source['sourceMetadataSHA256']
    assert sha(source['asset'])==source['assetSHA256']
    assert sha(source['isolatedRoomImage'])==source['isolatedRoomImageSHA256']
    image=cv2.imdecode(np.fromfile(source['isolatedRoomImage'],np.uint8),cv2.IMREAD_UNCHANGED)
    entry=(image[:,:,3]>0).astype(np.uint8)
    assert entry.shape==whole.shape==tuple(meta['canvas']['shapeHW'])
    assert meta['canvas']['layout']==source['layout']
    assert meta['canvas']['worldFloor']==source['worldFloor']
    assert meta['sourceGeometry']['bounds']==source['bounds']
    assert np.any(entry) and not np.any((entry>0)&(whole==0))
    contours,_=cv2.findContours(entry,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    assert len(contours)==1
    outline=cv2.approxPolyDP(contours[0],2,True).reshape(-1,2).astype(float)
    moments=cv2.moments(entry,binaryImage=True)
    center=np.array([moments['m10']/moments['m00'],moments['m01']/moments['m00']])
    assert len(outline)>=4 and np.isfinite(center).all()
    unit=float(source['targetPixelsPerVoxel'])
    nodes=contour_graph(whole)
    chains=[]
    for i,node in enumerate(nodes):
        ids=[node['previous'],i,node['next']]
        if not all(nodes[j]['valid'] for j in ids):continue
        points=np.array([nodes[j]['point'] for j in ids])
        if np.linalg.norm(points[1]-center)>unit*12:continue
        chains.append(dict(points=points.tolist(),rays=node['rays'].tolist(),
                           sourceNode=i,sourceNeighborNodes=ids))
    assert chains
    room=source['room'];definition=source['definition']
    scene=re.search(r'random01_enter_m_\d+',definition['room_map_path']).group()
    row=dict(mapId=mid,floor=floor,kind='main',
        material=f"game-room-{room['roomConfigId']}:{scene}",
        center=center.tolist(),outline=outline.tolist(),unit=unit,region='game-native-canvas',
        neighbors=[],chains=chains,sourceProduct=archive.name,structureResource=archive.name,
        profileId=binding['profile'],variantId=binding['variant'],sourceKind='game-entry-room-proposal',
        sourceArray='shared_mask',canvas=meta['canvas'],**version,
        entryRoomSource=dict(roomIndex=source['roomIndex'],roomConfigId=room['roomConfigId'],
            sourceRoomType=definition['room_type'],position=room['position'],rotation=room['rotation'],
            localFloor=source['localFloor'],worldFloor=source['worldFloor'],
            roomMapPath=definition['room_map_path'],sourceCatalog=source['sourceCatalog'],
            sourceCatalogSHA256=source['sourceCatalogSHA256'],sourceMetadata=source['sourceMetadata'],
            sourceMetadataSHA256=source['sourceMetadataSHA256'],sourceTexture=source['asset'],
            sourceTextureSHA256=source['assetSHA256']),
        outlineRole='isolated true renderer alpha contour, not semantic known walls',
        centerRule='binary centroid of isolated entry-room true renderer alpha',
        chainRule='whole shared_mask contour_graph; original previous/self/next all valid; center distance <= unit*12',
        chainRuleReference=str(RUNTIME/'work/export_difficult_entries.py'),
        extractorEnvironment=dict(opencv=cv2.__version__,numpy=np.__version__,source='single_map_structure.contour_graph'),
        unknownFilteringUnavailable=True,knownWallSupportUnavailable=True,
        gates=[],ports=[],passports=[],sourceDoorRecordsUsed=False,
        authorFeaturesUsed=False,proposalOnly=True,authorizesPose=False,authorizesIdentity=False)
    points=np.asarray([c['points'] for c in chains]);rays=np.asarray([c['rays'] for c in chains])
    assert points.shape==(len(chains),3,2) and rays.shape==(len(chains),2,2)
    assert np.isfinite(points).all() and np.isfinite(rays).all()
    main=np.linalg.norm(points[:,1,None]-outline[None],axis=2).min(axis=1)<=3
    stat=dict(mapId=mid,layout=source['layout'],floor=floor,
        sourceGameRoom=dict(roomIndex=source['roomIndex'],roomConfigId=room['roomConfigId'],
            position=room['position'],rotation=room['rotation'],roomType=definition['room_type'],
            worldFloor=source['worldFloor'],localFloor=source['localFloor'],roomMapPath=definition['room_map_path']),
        sourceCatalog=source['sourceCatalog'],sourceCatalogSHA256=source['sourceCatalogSHA256'],
        sourceTexture=source['asset'],sourceTextureSHA256=source['assetSHA256'],
        sourceMetadata=source['sourceMetadata'],sourceMetadataSHA256=source['sourceMetadataSHA256'],
        rendererModulesVerified=source['rendererModulesVerified'],actualRendererBounds=source['bounds'],
        nativeRoomQuad=source['nativeRoomQuad'],center=center.tolist(),entryAlphaPixels=int(entry.sum()),
        alphaOutsideWholeSharedMask=0,rawWholeContourNodes=len(nodes),
        totalChains=len(chains),mainChains=int(main.sum()),neighborChains=int((~main).sum()),
        sourceNPZ=indexed['resourcePath'],sourceNPZSHA256=sha(archive),
        sourceArrays=['shared_mask','metadata'],versionBinding=version)
    return row,stat
