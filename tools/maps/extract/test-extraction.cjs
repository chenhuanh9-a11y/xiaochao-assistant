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
    fs.writeFileSync(path.join(game,'Documents/update.ini'),'version=fixture-1\n');
    run('extract-map-configs.cjs',['--game',game,'--out',out]);
    const summary=JSON.parse(fs.readFileSync(path.join(out,'summary.json')));
    assert.equal(summary.layoutRecordCount,1);assert(Object.values(summary.moduleSources).every(x=>x==='Documents/res'));
    const bridge=path.join(tmp,'bridge');
    run('prepare-increment-inputs.cjs',['--run',out,'--output',bridge,'--season','S1','--mode','nightmare']);
    const normalized=JSON.parse(fs.readFileSync(path.join(bridge,'normalized-literals.json')));
    assert.equal(normalized.levelLayouts.data['900001'].room_data.length,2);
    assert.equal(normalized.mapImages.data['9001_0'].image_path,'synthetic-only.png');
    const bm=JSON.parse(fs.readFileSync(path.join(bridge,'extraction-manifest.json')));
    assert.equal(bm.scope.seasonId,'S1');assert.equal(bm.scope.competitionScopeEnforced,false);
    assert.equal(fs.readdirSync(bridge).filter(n=>n.endsWith('.bin')).length,0);
    const old=path.join(tmp,'baseline.json');fs.writeFileSync(old,JSON.stringify({maps:{},mapImageConfigs:{}}));
    run('../normalize_increment_catalog.cjs',['--run',bridge,'--old-catalog',old,'--output',path.join(bridge,'catalog-candidate')]);
    const candidate=JSON.parse(fs.readFileSync(path.join(bridge,'catalog-candidate/latest-catalog-candidate.json')));
    assert.equal(candidate.gameConfigVersion,'fixture-1');assert.equal(candidate.maps['900001'].rooms.length,2);
    assert.equal(candidate.inputScope.seasonId,'S1');assert.equal(candidate.inputScope.mode,'nightmare');
    run('prepare-increment-inputs.cjs',['--run',out,'--output',bridge,'--season','S1','--mode','nightmare'],false);
    run('prepare-increment-inputs.cjs',['--run',out,'--output',path.join(out,'nested'),'--season','S1','--mode','nightmare'],false);
    const original=fs.readFileSync(path.join(out,'literal-exports.json'));
    const bad=JSON.parse(original);bad.levelLayouts.partial=true;
    fs.writeFileSync(path.join(out,'literal-exports.json'),JSON.stringify(bad));
    const rejected=path.join(tmp,'rejected');
    run('prepare-increment-inputs.cjs',['--run',out,'--output',rejected,'--season','S1','--mode','nightmare'],false);
    assert.equal(fs.existsSync(rejected),false);
    fs.writeFileSync(path.join(out,'literal-exports.json'),original);
    for(const mutate of [
      v=>{v.mapImages.exports.data.entries.push(['9001_0',{}]);},
      v=>{v.levelLayouts.sha256='0'.repeat(64);},
      v=>{v.levelLayouts.exports.data={kind:'code'};}
    ]){
      const changed=JSON.parse(original);mutate(changed);
      fs.writeFileSync(path.join(out,'literal-exports.json'),JSON.stringify(changed));
      run('prepare-increment-inputs.cjs',['--run',out,'--output',rejected,'--season','S1','--mode','nightmare'],false);
      assert.equal(fs.existsSync(rejected),false);
    }
    fs.writeFileSync(path.join(out,'literal-exports.json'),original);
    const modulesPath=path.join(out,'modules.json'),moduleBytes=fs.readFileSync(modulesPath),moduleMap=JSON.parse(moduleBytes);
    moduleMap.levelLayouts='../outside.bin';fs.writeFileSync(modulesPath,JSON.stringify(moduleMap));
    run('prepare-increment-inputs.cjs',['--run',out,'--output',rejected,'--season','S1','--mode','nightmare'],false);
    assert.equal(fs.existsSync(rejected),false);fs.writeFileSync(modulesPath,moduleBytes);
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

function bridgeFixture(tmp){
  const game=path.join(tmp,'game'),out=path.join(tmp,'run');fixture(game);
  fs.mkdirSync(path.join(game,'Documents'));fs.writeFileSync(path.join(game,'Documents/update.ini'),'version=fixture-1\n');
  run('extract-map-configs.cjs',['--game',game,'--out',out]);return out;
}
function addCommaAlias(file){
  const raw=JSON.parse(fs.readFileSync(file));
  raw.mapImages.exports.data.entries.push(['9001,0',raw.mapImages.exports.data.entries[0][1]]);
  fs.writeFileSync(file,JSON.stringify(raw));
}
test('bridge rejects tuple/comma alias before creating output',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-alias-'));
  try{
    const input=bridgeFixture(tmp),out=path.join(tmp,'bridge');addCommaAlias(path.join(input,'literal-exports.json'));
    run('prepare-increment-inputs.cjs',['--run',input,'--output',out,'--season','S1','--mode','nightmare'],false);
    assert.equal(fs.existsSync(out),false);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('normalizer independently rejects tuple/comma aliases',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'normalizer-alias-'));
  try{
    const input=bridgeFixture(tmp),bridge=path.join(tmp,'bridge');
    run('prepare-increment-inputs.cjs',['--run',input,'--output',bridge,'--season','S1','--mode','nightmare']);
    addCommaAlias(path.join(bridge,'literal-exports.json'));
    const old=path.join(tmp,'old.json');fs.writeFileSync(old,JSON.stringify({maps:{},mapImageConfigs:{}}));
    const out=path.join(tmp,'catalog');
    run('../normalize_increment_catalog.cjs',['--run',bridge,'--old-catalog',old,'--output',out],false);
    assert.equal(fs.existsSync(path.join(out,'latest-catalog-candidate.json')),false);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('bridge rejects each metadata symlink outside run',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-symlinks-'));
  try{
    const input=bridgeFixture(tmp);
    for(const name of ['literal-exports.json','summary.json','modules.json','discovery.json']){
      const p=path.join(input,name),saved=fs.readFileSync(p),outside=path.join(tmp,name),out=path.join(tmp,'bridge-'+name);
      fs.writeFileSync(outside,saved);fs.unlinkSync(p);fs.symlinkSync(outside,p);
      try{
        run('prepare-increment-inputs.cjs',['--run',input,'--output',out,'--season','S1','--mode','nightmare'],false);
        assert.equal(fs.existsSync(out),false);
      }finally{fs.unlinkSync(p);fs.writeFileSync(p,saved);}
    }
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('required module data must be typed dictionaries, not scalar empty-success',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-scalars-'));
  try{
    const input=bridgeFixture(tmp),bridge=path.join(tmp,'bridge');
    run('prepare-increment-inputs.cjs',['--run',input,'--output',bridge,'--season','S1','--mode','nightmare']);
    const file=path.join(input,'literal-exports.json'),original=fs.readFileSync(file),old=path.join(tmp,'old.json');
    fs.writeFileSync(old,JSON.stringify({maps:{},mapImageConfigs:{}}));
    for(const key of ['levelLayouts','roomDefinitions','mapImages','levelRules','outdoorMaps','roomArrange']){
      const raw=JSON.parse(original);raw[key].exports.data=7;
      fs.writeFileSync(file,JSON.stringify(raw));fs.writeFileSync(path.join(bridge,'literal-exports.json'),JSON.stringify(raw));
      const out=path.join(tmp,'bad-'+key),catalog=path.join(tmp,'cat-'+key);
      run('prepare-increment-inputs.cjs',['--run',input,'--output',out,'--season','S1','--mode','nightmare'],false);
      assert.equal(fs.existsSync(out),false);
      run('../normalize_increment_catalog.cjs',['--run',bridge,'--old-catalog',old,'--output',catalog],false);
      assert.equal(fs.existsSync(catalog),false);
    }
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('normalizer records actual partial status for optional modules',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-partial-status-'));
  try{
    const input=bridgeFixture(tmp),bridge=path.join(tmp,'bridge');
    const file=path.join(input,'literal-exports.json'),raw=JSON.parse(fs.readFileSync(file));
    raw.scenes.partial=true;raw.dungeonParams.partial=false;fs.writeFileSync(file,JSON.stringify(raw));
    run('prepare-increment-inputs.cjs',['--run',input,'--output',bridge,'--season','S1','--mode','nightmare']);
    const old=path.join(tmp,'old.json'),out=path.join(tmp,'catalog');fs.writeFileSync(old,JSON.stringify({maps:{},mapImageConfigs:{}}));
    run('../normalize_increment_catalog.cjs',['--run',bridge,'--old-catalog',old,'--output',out]);
    const c=JSON.parse(fs.readFileSync(path.join(out,'latest-catalog-candidate.json')));
    assert.equal(c.sourceModules.scenes.partial,true);assert.equal(c.sourceModules.dungeonParams.partial,false);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('required catalog emptiness and malformed room bindings fail before output',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-shapes-'));
  try{
    const input=bridgeFixture(tmp),file=path.join(input,'literal-exports.json'),original=fs.readFileSync(file);
    const mutations=[
      ...['levelLayouts','roomDefinitions','mapImages'].map(key=>raw=>{raw[key].exports.data.entries=[];}),
      raw=>{raw.levelLayouts.exports.data.entries[0][1]={kind:'dict',entries:[['room_data',[]]]};},
      raw=>{raw.levelLayouts.exports.data.entries[0][1]={kind:'dict',entries:[['room_data',[[999999,[0,2,0],0]]]]};},
      raw=>{raw.roomDefinitions.exports.data.entries[0][1]={kind:'dict',entries:[['room_voxels',7]]};}
    ];
    mutations.forEach((mutate,i)=>{
      const raw=JSON.parse(original);mutate(raw);fs.writeFileSync(file,JSON.stringify(raw));
      const out=path.join(tmp,'bad'+i);
      run('prepare-increment-inputs.cjs',['--run',input,'--output',out,'--season','S1','--mode','nightmare'],false);
      assert.equal(fs.existsSync(out),false);
    });
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('legacy extraction manifest without scope and unique comma image keys remain readable',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'legacy-increment-'));
  try{
    const input=bridgeFixture(tmp),bridge=path.join(tmp,'bridge');
    run('prepare-increment-inputs.cjs',['--run',input,'--output',bridge,'--season','S1','--mode','nightmare']);
    const manifestPath=path.join(bridge,'extraction-manifest.json'),manifest=JSON.parse(fs.readFileSync(manifestPath));
    delete manifest.scope;delete manifest.schema;delete manifest.inputHashes;delete manifest.moduleBytesCopied;
    // Retain the historical normalizer's actual field contract, not the bridge schema.
    fs.writeFileSync(manifestPath,JSON.stringify(manifest));
    const literalPath=path.join(bridge,'literal-exports.json'),raw=JSON.parse(fs.readFileSync(literalPath));
    raw.mapImages.exports.data.entries[0][0]='9001,0';fs.writeFileSync(literalPath,JSON.stringify(raw));
    const fields={image_path:'synthetic-only.png'},old=path.join(tmp,'old.json'),out=path.join(tmp,'catalog');
    fs.writeFileSync(old,JSON.stringify({gameConfigVersion:'fixture-old',maps:{},mapImageConfigs:{'9001_0':{fields,assetFiles:['synthetic-only.png']}}}));
    run('../normalize_increment_catalog.cjs',['--run',bridge,'--old-catalog',old,'--output',out]);
    const result=JSON.parse(fs.readFileSync(path.join(out,'latest-catalog-candidate.json')));
    assert.equal(result.inputScope,null);assert.equal(result.maps['900001'].rooms.length,2);
    assert.equal(result.mapImageConfigs['9001_0'].assetResolutionStatus,'legacy_asset_exact_config_match');
    assert.deepEqual(result.mapImageConfigs['9001_0'].assetFiles,['synthetic-only.png']);
    assert.equal(result.assetGaps.length,0);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
