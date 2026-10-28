'use strict';
// Entire fixture is invented. Exercises IDX/WPK -> static literals -> geometry.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process');
const {geometry,worldVoxel}=require('./export-catalog.cjs');
const op=new Map([...fs.readFileSync(path.join(__dirname,'lib/netease_opmap.py'),'utf8').matchAll(/'([A-Z][A-Z0-9_]*)'\s*:\s*(\d+)/g)].map(m=>[m[1],+m[2]]));
const i32=n=>{const b=Buffer.alloc(4);b.writeInt32LE(n);return b;};
function marshal(v){
  if(v===null)return Buffer.from('N');
  if(typeof v==='number')return Buffer.concat([Buffer.from('i'),i32(v)]);
  if(typeof v==='string'||Buffer.isBuffer(v)){const b=Buffer.from(v);return Buffer.concat([Buffer.from(Buffer.isBuffer(v)?'s':'u'),i32(b.length),b]);}
  if(Array.isArray(v))return Buffer.concat([Buffer.from('['),i32(v.length),...v.map(marshal)]);
  const entries=v instanceof Map?[...v]:Object.entries(v);
  return Buffer.concat([Buffer.from('{'),...entries.flatMap(([k,x])=>[marshal(k),marshal(x)]),Buffer.from('0')]);
}
function moduleBytes(filename,values){
  const names=Object.keys(values),constants=[],code=[];
  function emit(v){
    if(Array.isArray(v)){for(const x of v)emit(x);code.push(op.get('BUILD_LIST'),v.length);}
    else if(v&&typeof v==='object'){const entries=v instanceof Map?[...v]:Object.entries(v);for(const [k,x]of entries){emit(k);emit(x);}code.push(op.get('BUILD_MAP'),entries.length);}
    else{assert(constants.length<256);code.push(op.get('LOAD_CONST'),constants.length);constants.push(v);}
  }
  for(let i=0;i<names.length;i++){emit(values[names[i]]);code.push(op.get('STORE_NAME'),i);}
  emit(null);code.push(op.get('RETURN_VALUE'),0);
  return Buffer.concat([Buffer.from('c'),...Array.from({length:5},(_,i)=>i32(i===3?200:0)),
    marshal(Buffer.from(code)),marshal(constants),marshal(names),marshal([]),marshal(Buffer.alloc(0)),
    marshal(filename),marshal('<module>'),marshal('<module>'),i32(1),marshal(Buffer.alloc(0)),marshal(Buffer.alloc(0))]);
}
function fixture(root){
  const data={
    levelLayouts:['goldrush_random_room_export_configs.py',{data:new Map([[900001,{room_data:[[9001,[0,2,0],0],[9002,[2,2,0],0]],door:new Map([[1,[1,1]]])}]]),level_id_to_ids:new Map([[904,[900001]]])}],
    levelRules:['goldrush_random_room_level_configs.py',{data:new Map([[904,{theme:1,difficulty:4}]])}],
    mapImages:['goldrush_minimap_configs.py',{data:new Map([[[9001,0],{image_path:'synthetic-only.png'}]])}],
    roomDefinitions:['random_advanture_room_configs.py',{data:new Map([[9001,{room_type:0,room_voxels:[[0,0,0],[1,0,0],[0,1,0]],room_paths:[[[1,0,0],[2,0,0]]]}],[9002,{room_type:0,room_voxels:[[0,0,0]],room_paths:[[[0,0,0],[-1,0,0]],[[0,0,0],[1,0,0]]]}]])}],
    roomArrange:['goldrush_room_arrange.py',{data:{}}],scenes:['gold_rush_scn.py',{data:{}}],
    sceneGroups:['gold_rush_scn_group.py',{data:{}}],renderGlobalData:['goldrush_global_data.py',{data:{}}],
    dungeonParams:['goldrush_dungeon_params.py',{data:{}}],outdoorMaps:['goldrush_outdoor_scn_configs.py',{data:{}}]};
  const idx=Buffer.alloc(32+Object.keys(data).length*36);idx.write('SKPW');idx.writeUInt32LE(Object.keys(data).length,12);
  const chunks=[];let offset=0,index=0;
  for(const [filename,values]of Object.values(data)){
    const clear=moduleBytes(filename,values),body=Buffer.from(clear);
    // Inverse of stage1's reversible header transform, p=0 (no encrypted prefix).
    const n=Math.min(64,body.length);for(let i=0;i<n;i++)body[i]=clear[n-1-i]^0x5a;
    const prefix=Buffer.alloc(8);prefix.writeUInt16LE(0x4358);
    const payload=Buffer.concat([prefix,body]),header=Buffer.alloc(48);header.write('1DPW');header.writeUInt32LE(payload.length,32);
    idx.writeUInt32LE(offset,32+index*36+24);chunks.push(header,payload);offset+=48+payload.length;index++;
  }
  fs.mkdirSync(path.join(root,'res'),{recursive:true});fs.writeFileSync(path.join(root,'res/script.idx'),idx);fs.writeFileSync(path.join(root,'res/script0.wpk'),Buffer.concat(chunks));
}
function run(script,args,ok=true){const r=cp.spawnSync(process.execPath,[path.join(__dirname,script),...args],{encoding:'utf8'});assert.equal(r.status,ok?0:1,r.stderr);return r;}
test('synthetic full package extraction, overlay, geometry, fail-closed boundaries',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'map-extract-fixture-'));
  try{
    const game=path.join(tmp,'game'),out=path.join(tmp,'literal'),geo=path.join(tmp,'geometry');fixture(game);
    // A second same-format layer proves explicit overlay selection.
    fs.mkdirSync(path.join(game,'Documents'),{recursive:true});fs.cpSync(path.join(game,'res'),path.join(game,'Documents/res'),{recursive:true});
    run('extract-map-configs.cjs',['--game',game,'--out',out]);
    const summary=JSON.parse(fs.readFileSync(path.join(out,'summary.json')));
    assert.equal(summary.layoutRecordCount,1);assert(Object.values(summary.moduleSources).every(x=>x==='Documents/res'));
    run('export-catalog.cjs',['--literals',path.join(out,'literal-exports.json'),'--out',geo,'--game-version','synthetic']);
    const g=JSON.parse(fs.readFileSync(path.join(geo,'900001.geometry.json')));
    assert.equal(g.rooms.length,2);assert.equal(g.pairedDoors.length,1);assert.equal(g.unpairedDoorPaths.length,1);
    assert.deepEqual(g.rooms[0].occupiedWorldFloors,[2,3]);assert.equal(g.rooms[0].visitableFloors,null);assert.deepEqual(g.transfers,[]);
    assert.equal(g.pairedDoors[0].traversable,null);assert.equal(g.coordinates.pixelRegistration,null);
    run('export-catalog.cjs',['--catalog',path.join(geo,'catalog.json'),'--out',geo],false);
    run('extract-map-configs.cjs',['--game',game,'--out',path.join(game,'forbidden')],false);
    fs.writeFileSync(path.join(game,'Documents/res/script.idx'),'bad');
    run('extract-map-configs.cjs',['--game',game,'--out',path.join(tmp,'bad')],false);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('cell rotation is about boundaries; ambiguous doors never become edges',()=>{
  assert.deepEqual(worldVoxel([0,0,0],{position:[0,0,0],rotation:1}),[0,0,-1]);
  const d={maps:{1:{rooms:[{roomIndex:0,roomConfigId:1,position:[0,0,0],rotation:0},{roomIndex:1,roomConfigId:1,position:[0,0,0],rotation:0}]}},roomDefinitions:{1:{room_voxels:[[0,0,0]],room_paths:[[[0,0,0],[1,0,0]]]}}};
  const g=geometry(d,'1');assert.equal(g.pairedDoors.length,0);assert(g.unpairedDoorPaths.every(p=>p.status==='ambiguous'));
});
