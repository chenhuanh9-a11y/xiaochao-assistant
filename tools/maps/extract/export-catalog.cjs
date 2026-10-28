'use strict';
// Schema adapter and geometry export. No game code, textures or networking.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function dict(v,where){if(v?.kind!=='dict'||!Array.isArray(v.entries))throw Error(where+': expected encoded Python dict');return v.entries;}
function literal(v){
  if(v?.kind==='dict'){
    const out={};for(const [k,x] of dict(v,'literal')){
      const key=Array.isArray(k)?k.join('_'):String(k);
      if(Object.hasOwn(out,key))throw Error('ambiguous normalized dictionary key '+key);
      Object.defineProperty(out,key,{value:literal(x),enumerable:true,writable:true});
    }return out;
  }
  if(Array.isArray(v))return v.map(literal);
  if(v&&typeof v==='object')throw Error('unsupported literal value '+v.kind);
  return v;
}
function fromLiterals(raw,version){
  const data=k=>{if(!raw[k]||raw[k].partial)throw Error('missing/partial required module '+k);return literal(raw[k].exports.data);};
  const layouts=data('levelLayouts'),defs=data('roomDefinitions'),images=data('mapImages');
  const groups=literal(raw.levelLayouts.exports.level_id_to_ids);
  const maps={};
  for(const [id,record] of Object.entries(layouts)){
    if(!Array.isArray(record.room_data))throw Error(id+': missing room_data');
    const doors=record.door;
    // Known source layout uses a dictionary of external door references.
    // Other shapes remain unparsed instead of being guessed.
    const normalizedDoors=doors&&!Array.isArray(doors)&&typeof doors==='object'?
      Object.entries(doors).map(([i,p])=>{if(!Array.isArray(p)||p.length!==2||!p.every(Number.isInteger))throw Error(id+': bad external door ref');return {doorIndex:Number(i),roomIndex:p[0],doorSlot:p[1]};}):[];
    maps[id]={levelId:Number(id),groupIds:Object.entries(groups).filter(([,a])=>a.map(String).includes(id)).map(([g])=>Number(g)),
      rooms:record.room_data.map((r,roomIndex)=>{if(!Array.isArray(r)||r.length!==3)throw Error(id+': bad room_data');return {roomIndex,roomConfigId:r[0],position:r[1],rotation:r[2]};}),
      normalizedDoors,externalDoorStatus:doors===undefined?'absent':Array.isArray(doors)?'unparsed-array':'source-slot-references'};
  }
  return {schema:'map-extraction-catalog-v1',gameConfigVersion:version||null,maps,roomDefinitions:defs,
    levelGroups:groups,levelRules:raw.levelRules&&!raw.levelRules.partial?data('levelRules'):{},
    mapImageConfigs:Object.fromEntries(Object.entries(images).map(([key,fields])=>[key,{fields,assetResolutionStatus:'not-extracted'}])),
    sourceModules:Object.fromEntries(Object.entries(raw).map(([k,v])=>[k,{sha256:v.sha256||null,partial:!!v.partial}]))};
}
function vector(p){return Array.isArray(p)&&p.length===3&&p.every(Number.isInteger);}
function worldVoxel(p,r){
  if(!vector(p)||!vector(r.position)||!Number.isInteger(r.rotation)||r.rotation<0||r.rotation>3)throw Error('unsupported voxel transform');
  const [x,y,z]=p,[a,b]=[[x,z],[z,-x-1],[-x-1,-z-1],[-z-1,x]][r.rotation];
  return [a+r.position[0],y+r.position[1],b+r.position[2]];
}
const key=p=>p.join(',');
function geometry(catalog,id){
  const map=catalog.maps[id];if(!map)throw Error('unknown map '+id);
  const slots=[],rooms=[],issues=[];
  for(const [index,r] of map.rooms.entries()){
    if(r.roomIndex!==index||!Number.isInteger(r.roomConfigId))throw Error(id+': unstable room indices');
    const def=catalog.roomDefinitions[r.roomConfigId];
    if(!def||!Array.isArray(def.room_voxels)){issues.push({roomIndex:index,reason:'missing_room_geometry'});continue;}
    const voxels=def.room_voxels.map(p=>worldVoxel(p,r));
    const occupied=new Set(voxels.map(key));
    rooms.push({roomIndex:index,roomConfigId:r.roomConfigId,position:r.position,rotation:r.rotation,
      roomType:def.room_type??null,worldVoxels:voxels,occupiedWorldFloors:[...new Set(voxels.map(p=>p[1]))].sort((a,b)=>a-b),
      visitableFloors:null,verticalAccess:'unknown',interiorWalkability:'unknown'});
    for(const [doorSlot,p] of (def.room_paths||[]).entries()){
      if(!Array.isArray(p)||p.length!==2)throw Error(id+': invalid room_paths');
      const world=p.map(x=>worldVoxel(x,r));
      if(world[0][1]!==world[1][1]||world[0].reduce((s,v,i)=>s+Math.abs(v-world[1][i]),0)!==1||!occupied.has(key(world[0]))){
        issues.push({roomIndex:index,doorSlot,reason:'unsupported_or_nonboundary_room_path'});continue;
      }
      slots.push({roomIndex:index,roomConfigId:r.roomConfigId,doorSlot,worldVoxels:world});
    }
  }
  const buckets=new Map();for(const s of slots){const k=s.worldVoxels.map(key).join('|');if(!buckets.has(k))buckets.set(k,[]);buckets.get(k).push(s);}
  const paired=[],unpaired=[],used=new Set();
  for(const s of slots){
    if(used.has(s))continue;
    const k=s.worldVoxels.map(key).join('|'),rev=[...s.worldVoxels].reverse().map(key).join('|');
    const own=buckets.get(k),other=(buckets.get(rev)||[]).filter(o=>o.roomIndex!==s.roomIndex);
    if(own.length!==1||other.length!==1){unpaired.push({...s,status:own.length>1||other.length>1?'ambiguous':'unpaired'});continue;}
    const o=other[0];used.add(s);used.add(o);
    const [a,b]=s.worldVoxels;
    paired.push({a:{roomIndex:s.roomIndex,doorSlot:s.doorSlot},b:{roomIndex:o.roomIndex,doorSlot:o.doorSlot},worldFloor:a[1],
      worldVoxels:s.worldVoxels,mapPlane:[(a[0]+b[0]+1)/2,-(a[2]+b[2]+1)/2],
      evidence:'reciprocal-source-room_paths',liveDoorState:'unknown',traversable:null});
  }
  return {schema:'map-room-door-geometry-v1',mapId:String(id),gameConfigVersion:catalog.gameConfigVersion||null,
    coordinates:{voxel:'world integer x,y,z; y is source height',mapPlane:'x,-z; tile units',
      cellCenter:'[x+0.5,-z-0.5]',rotation:'quarter turns; cell-boundary correction included',pixelRegistration:null},
    rooms,pairedDoors:paired,unpairedDoorPaths:unpaired,externalDoorReferences:map.normalizedDoors||[],
    externalMarkerStatus:'not-derived; slot references are not UI icons',transfers:[],
    transferStatus:'unknown; occupied height does not establish stairs or a visitable upper floor',
    corridorStatus:'room occupancy and source door paths only; no inferred full-walkable corridor/interior mask',issues};
}
function main(args){
  if(args.includes('--help')){console.log('node export-catalog.cjs (--literals literal-exports.json | --catalog catalog.json) --out NEW_DIR [--maps 104001,103001] [--game-version VERSION]');return;}
  const opts={};for(let i=0;i<args.length;i+=2){if(!['--literals','--catalog','--out','--maps','--game-version'].includes(args[i])||!args[i+1]||args[i+1].startsWith('--')||opts[args[i]])throw Error('invalid argument '+args[i]);opts[args[i]]=args[i+1];}
  if(!opts['--out']||!!opts['--literals']===!!opts['--catalog'])throw Error('provide one input and --out');
  const input=path.resolve(opts['--literals']||opts['--catalog']),bytes=fs.readFileSync(input),raw=JSON.parse(bytes);
  const catalog=opts['--literals']?fromLiterals(raw,opts['--game-version']):raw;
  const ids=opts['--maps']?opts['--maps'].split(','):Object.keys(catalog.maps);
  if(!ids.length||new Set(ids).size!==ids.length||ids.some(id=>!/^\d+$/.test(id)))throw Error('invalid/duplicate map selection');
  const outputs=ids.map(id=>geometry(catalog,id)); // validate before creating output
  const out=path.resolve(opts['--out']);if(fs.existsSync(out))throw Error('output exists; use a new directory');
  fs.mkdirSync(out,{recursive:true});const write=(name,v)=>fs.writeFileSync(path.join(out,name),JSON.stringify(v,null,2)+'\n',{flag:'wx'});
  write('catalog.json',catalog);for(const g of outputs)write(g.mapId+'.geometry.json',g);
  const manifest={schema:'map-extraction-run-v1',inputSha256:sha(bytes),gameConfigVersion:catalog.gameConfigVersion||null,
    maps:outputs.map(g=>({mapId:g.mapId,rooms:g.rooms.length,pairedDoors:g.pairedDoors.length,issues:g.issues.length})),
    limitations:['No textures/rendering','No complete interior collision','No inferred stairs/upper-floor targets','Static paired doors do not prove live openness'],
    outputs:Object.fromEntries(fs.readdirSync(out).sort().map(n=>[n,sha(fs.readFileSync(path.join(out,n)))]))};
  write('manifest.json',manifest);console.log(JSON.stringify(manifest.maps));
}
if(require.main===module){try{main(process.argv.slice(2));}catch(e){console.error(e.message);process.exitCode=1;}}
module.exports={fromLiterals,geometry,worldVoxel,literal,main};
