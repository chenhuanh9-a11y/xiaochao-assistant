// Current authoritative F1 entry-room proposals, one real source room per map.
// No UI frame, author asset, old author feature, solver or pose is consumed.
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const required=n=>{const i=process.argv.indexOf(n);if(i<0||!process.argv[i+1])throw Error('Required: '+n);return path.resolve(process.argv[i+1]);};
const OUT=required('--output'),PLAN=required('--plan'),VENDOR=required('--vendor');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
if(fs.existsSync(OUT))throw new Error('Preserve existing full-entry candidate');
const plan=read(PLAN),targets=plan.entries.filter(r=>r.floor==='1');
if(new Set(targets.map(r=>r.runtimeMapId)).size!==plan.targetMaps)throw new Error('F1 target coverage mismatch');
const {roomImagePlacement,rotateVector}=require(path.join(VENDOR,'tools/source_renderer.js'));
const {decodePng,rotateImage,encodePng,blit}=require(path.join(VENDOR,'tools/build_level_map.js'));
const {rasterizeImageView}=require(path.join(VENDOR,'tools/cocos_hsb_shader.js'));
fs.mkdirSync(OUT);fs.mkdirSync(path.join(OUT,'rooms'));
const catalogs=new Map(),entries=[],gaps=[];
for(const target of targets){
  try{
    const metadata=read(target.nativeSourceMetadata);
    const catalogPath=metadata.sourceCatalog.sourceFile;
    if(sha(catalogPath)!==metadata.sourceCatalog.sourceFileSha256)throw new Error('Catalog content mismatch');
    if(!catalogs.has(catalogPath))catalogs.set(catalogPath,read(catalogPath));
    const catalog=catalogs.get(catalogPath);
    if(String(metadata.levelId)!==target.sourceLayout||metadata.floor!==target.sourceWorldFloor)throw new Error('Target map/floor mismatch');
    for(const [name,hash] of Object.entries(metadata.rendererModules)){
      if(sha(path.join(VENDOR,name))!==hash)throw new Error(`Renderer module mismatch: ${name}`);
    }
    const rooms=catalog.maps[target.sourceLayout].rooms;
    const roomIndex=metadata.roomDrawOrder.frontDoorRoomIndex;
    const room=rooms[roomIndex],definition=catalog.roomDefinitions[String(room.roomConfigId)];
    if(room.roomIndex!==roomIndex||definition.room_type!==2)throw new Error('Source front room does not have room_type2');
    const first=rooms.findIndex(r=>catalog.roomDefinitions[String(r.roomConfigId)].room_type===2);
    if(first!==roomIndex)throw new Error('Source first front room disagrees with renderer draw order');
    if(!/random01_enter_m_\d+/.test(definition.room_map_path))throw new Error('Unbound main-entry scene family');
    const localFloor=metadata.floor-room.position[1];
    const cfgRecord=catalog.mapImageConfigs[`${room.roomConfigId}_${localFloor}`];
    if(!cfgRecord||cfgRecord.assetFiles.length!==1)throw new Error('Texture binding missing or ambiguous');
    const asset=path.join(VENDOR,cfgRecord.assetFiles[0]);
    const image=decodePng(fs.readFileSync(asset));
    const placement=roomImagePlacement(room,cfgRecord.fields,image);
    const declared=metadata.placements.find(p=>p.roomIndex===roomIndex);
    if(!declared)throw new Error('Entry room not rendered on requested floor');
    for(const k of ['x','y','W','H','steps'])if(placement[k]!==declared[k])throw new Error(`Placement differs ${k}`);
    const b=metadata.bounds,cell=metadata.targetPixelsPerVoxel;
    const W=Math.round((b.maxX-b.minX)/128*cell),H=Math.round((b.maxY-b.minY)/128*cell);
    if(W!==target.nativeCanvasWH[0]||H!==target.nativeCanvasWH[1])throw new Error('Native canvas mismatch');
    const rect={x:(placement.x-b.minX)*cell/128,y:(placement.y-b.minY)*cell/128,
      W:placement.W*cell/128,H:placement.H*cell/128};
    if(!Object.values(rect).every(Number.isInteger))throw new Error('Unsupported noninteger native quad; do not round silently');
    const rotated=rotateImage(image,placement.steps);
    const quad={W:rect.W,H:rect.H,px:Buffer.alloc(rect.W*rect.H*4)};
    rasterizeImageView(quad,rotated,{x:0,y:0,W:rect.W,H:rect.H},declared.encodedSpecular);
    const canvas={W,H,px:Buffer.alloc(W*H*4)};blit(canvas,quad,rect.x,rect.y);
    const imagePath=path.join(OUT,'rooms',`${target.sourceLayout}-1.png`);
    fs.writeFileSync(imagePath,encodePng(W,H,canvas.px));
    const polygons=definition.room_voxels.filter(v=>room.position[1]+v[1]===metadata.floor).map(v=>({
      voxel:v,worldCorners:[[v[0],v[2]],[v[0]+1,v[2]],[v[0]+1,v[2]+1],[v[0],v[2]+1]].map(([x,z])=>{
        const [rx,ry]=rotateVector(x*128,z*128,room.rotation);
        return [room.position[0]*128+rx,-room.position[2]*128-ry];
      })}));
    const row={mapId:target.runtimeMapId,layout:target.sourceLayout,floor:'1',worldFloor:metadata.floor,localFloor,
      roomIndex,roomConfigId:room.roomConfigId,room,definition,configRecord:cfgRecord,
      asset,assetSHA256:sha(asset),sourceCatalog:catalogPath,sourceCatalogSHA256:sha(catalogPath),
      sourceVersion:catalog.gameConfigVersion,sourceMetadata:target.nativeSourceMetadata,
      sourceMetadataSHA256:sha(target.nativeSourceMetadata),sourcePNG:target.nativeSourcePNG,
      rendererModulesVerified:metadata.rendererModules,actualPlacement:placement,bounds:b,
      canvasWH:[W,H],targetPixelsPerVoxel:cell,nativeRoomQuad:rect,
      matrixWorldToNative:[[cell/128,0,-b.minX*cell/128],[0,cell/128,-b.minY*cell/128],[0,0,1]],
      sourceVoxelPolygons:polygons,isolatedRoomImage:imagePath,isolatedRoomImageSHA256:sha(imagePath),
      roomPathsUsedForProposal:false,sourceDoorRecordsUsed:false,doorPortsInvented:false,
      authorPixelsUsed:false,sourceRoomType2MeansEntryRoomProposalOnly:true,
      noRankOrSolver:true,noPoseAuthorization:true,
      limitation:'Legacy-reused texture bytes under exact current mapImages fields; no live gate or known-wall semantics.'};
    fs.writeFileSync(path.join(OUT,'rooms',`${target.sourceLayout}-source.json`),JSON.stringify(row,null,2));
    entries.push(row);
  }catch(e){gaps.push({layout:target.sourceLayout,mapId:target.runtimeMapId,floor:'1',reason:e.message});}
  if((entries.length+gaps.length)%4===0)console.log(`room alpha ${entries.length}/${targets.length}; gaps ${gaps.length}`);
}
const report={scope:'F1 current game entry-room source extraction only; no rank/pose',
  authoritativePlan:PLAN,expectedMaps:targets.map(r=>r.runtimeMapId),expectedCount:targets.length,
  actualCount:entries.length,entries,gaps};
fs.writeFileSync(path.join(OUT,'render-inventory.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({expected:targets.length,actual:entries.length,gaps,output:OUT}));
if(gaps.length)process.exitCode=1;
