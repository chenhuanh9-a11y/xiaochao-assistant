'use strict';
// Data-only bridge. Does not select maps, execute modules, or activate resources.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {literal}=require('./export-catalog.cjs');
const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
function inside(a,b){const r=path.relative(a,b);return !r||(!r.startsWith('..'+path.sep)&&r!=='..'&&!path.isAbsolute(r));}
function validateRequired(raw){
  const required=['levelLayouts','roomDefinitions','mapImages','levelRules','outdoorMaps','roomArrange'],data={};
  const dict=(v,name)=>{
    if(v?.kind!=='dict'||!Array.isArray(v.entries)||v.entries.some(e=>!Array.isArray(e)||e.length!==2))throw Error('Expected typed dictionary '+name);
    return literal(v);
  };
  for(const key of required){
    if(!raw[key]||raw[key].partial)throw Error('Missing/partial required module '+key);
    data[key]=dict(raw[key].exports?.data,key+'.data');
  }
  for(const key of ['levelLayouts','roomDefinitions','mapImages'])if(!Object.keys(data[key]).length)throw Error('Empty required catalog '+key);
  dict(raw.levelLayouts.exports.level_id_to_ids,'levelLayouts.level_id_to_ids');
  const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  for(const [id,row] of Object.entries(data.levelLayouts)){
    if(!/^\d+$/.test(id)||!object(row)||!Array.isArray(row.room_data)||!row.room_data.length)throw Error('Invalid layout room_data '+id);
    for(const room of row.room_data)if(!Array.isArray(room)||room.length!==3||!Number.isInteger(room[0])||!Array.isArray(room[1])||room[1].length!==3||!room[1].every(Number.isInteger)||!Number.isInteger(room[2])||room[2]<0||room[2]>3||!Object.hasOwn(data.roomDefinitions,String(room[0])))throw Error('Invalid layout room binding '+id);
    if(row.door!==undefined&&!object(row.door))throw Error('Invalid layout door dictionary '+id);
  }
  for(const [id,row] of Object.entries(data.roomDefinitions))if(!object(row)||!Array.isArray(row.room_voxels)||row.room_voxels.some(v=>!Array.isArray(v)||v.length!==3||!v.every(Number.isInteger)))throw Error('Invalid room voxels '+id);
  for(const [id,row] of Object.entries(data.mapImages))if(!object(row))throw Error('Invalid map image fields '+id);
  return data;
}
function prepare(run,output,season,mode){
  if(!/^S\d+$/.test(season)||!['nightmare','difficult'].includes(mode))throw Error('Explicit season S<number> and mode required');
  run=fs.realpathSync(run);output=path.resolve(output);
  if(fs.existsSync(output))throw Error('Output already exists');
  let parent=path.dirname(output);while(!fs.existsSync(parent))parent=path.dirname(parent);
  const resolved=path.resolve(fs.realpathSync(parent),path.relative(parent,output));
  if(inside(run,resolved)||inside(resolved,run))throw Error('Output overlaps input');
  const names=['literal-exports.json','summary.json','modules.json','discovery.json'];
  const bytes=Object.fromEntries(names.map(n=>{
    const file=fs.realpathSync(path.join(run,n));
    if(!inside(run,file)||!fs.statSync(file).isFile())throw Error('Metadata escapes run: '+n);
    return [n,fs.readFileSync(file)];
  }));
  const [raw,summary,modules,discovery]=names.map(n=>JSON.parse(bytes[n]));
  validateRequired(raw);
  if(typeof summary.installedVersion!=='string'||!summary.installedVersion.trim()||/\s/.test(summary.installedVersion))throw Error('Verified installedVersion required');
  if(!Array.isArray(discovery))throw Error('Invalid discovery');
  const normalized={},records=[],targets={};
  for(const key of ['levelLayouts','roomDefinitions','mapImages','levelRules','outdoorMaps','roomArrange']){
    if(!raw[key]||raw[key].partial||!raw[key].exports?.data)throw Error('Missing/partial required module '+key);
  }
  for(const [key,item] of Object.entries(raw)){
    if(!/^[a-zA-Z][a-zA-Z0-9]*$/.test(key)||['constructor','prototype'].includes(key))throw Error('Unsafe module key');
    const filename=modules[key];
    if(typeof filename!=='string'||!filename||filename!==path.basename(filename)||/[\\/]/.test(filename))throw Error('Unsafe module filename');
    const selected=discovery.filter(r=>r.key===key&&r.filename===filename);
    if(selected.length!==1||!['res','Documents/res'].includes(selected[0].layer))throw Error('Ambiguous selected module '+key);
    const row=selected[0],file=fs.realpathSync(path.join(run,filename));
    const candidates=discovery.filter(r=>r.key===key);
    if(['res','Documents/res'].some(layer=>candidates.filter(r=>r.layer===layer).length>1))throw Error('Duplicate module source '+key);
    const preferred=candidates.find(r=>r.layer==='Documents/res')||candidates.find(r=>r.layer==='res');
    if(preferred!==row)throw Error('Selected module violates explicit overlay rule');
    if(!inside(run,file)||!fs.statSync(file).isFile())throw Error('Module escapes run');
    const data=fs.readFileSync(file),digest=sha(data);
    if(!/^[a-f0-9]{64}$/.test(item.sha256)||digest!==item.sha256||row.bytes!==data.length)throw Error('Module hash/size mismatch '+key);
    if(summary.moduleSources?.[key]!==row.layer)throw Error('Module source mismatch '+key);
    const exported={};
    for(const [name,value] of Object.entries(item.exports||{}))Object.defineProperty(exported,name,{value:literal(value),enumerable:true});
    Object.defineProperty(normalized,key,{value:{...exported,partial:!!item.partial,sha256:digest},enumerable:true});
    targets[key]={module:key};records.push({...row,decodedSha256:digest,decodedBytes:data.length});
  }
  // Match the downstream renderer's legacy comma-to-underscore convention.
  const imageKeys=new Set();
  for(const rawKey of Object.keys(normalized.mapImages.data)){
    const key=rawKey.replace(',', '_');
    if(imageKeys.has(key))throw Error('Ambiguous renderer image key '+key);
    imageKeys.add(key);
  }
  const scope={seasonId:season,mode,mapSelectionApplied:false,runtimeIntegration:'not_performed',competitionScopeEnforced:false};
  const manifest={schema:'extraction-increment-bridge-v1',updateIni:'version='+summary.installedVersion,targets,records,scope,
    inputHashes:Object.fromEntries(names.map(n=>[n,sha(bytes[n])])),moduleBytesCopied:false};
  // All validation precedes creation. A failed write leaves an unpromoted stage.
  fs.mkdirSync(output,{recursive:true});
  const write=(n,v)=>fs.writeFileSync(path.join(output,n),JSON.stringify(v,null,2)+'\n',{flag:'wx'});
  fs.writeFileSync(path.join(output,'literal-exports.json'),bytes['literal-exports.json'],{flag:'wx'});
  write('normalized-literals.json',normalized);write('extraction-manifest.json',manifest);
  return manifest;
}
function main(args){
  if(args.includes('--help')){console.log('prepare-increment-inputs.cjs --run EXTRACTED_RUN --output NEW_BRIDGE --season S1 --mode nightmare');return;}
  const value=n=>{const i=args.indexOf(n);if(i<0||!args[i+1])throw Error('Required '+n);return args[i+1];};
  console.log(JSON.stringify(prepare(value('--run'),value('--output'),value('--season'),value('--mode')).scope));
}
if(require.main===module){try{main(process.argv.slice(2));}catch(e){console.error(e.message);process.exitCode=1;}}
module.exports={prepare,validateRequired};
