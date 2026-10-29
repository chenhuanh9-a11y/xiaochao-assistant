#!/usr/bin/env node
// Pure data normalization. It never runs game code and never changes the
// existing catalog or renderer assets.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
function arg(name, fallback) { const i = process.argv.indexOf(name); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback; }
function required(name) { const value=arg(name); if(!value) throw Error('Required: '+name); return path.resolve(value); }
const run = required('--run');
const oldFile = required('--old-catalog');
const out = required('--output'); if(fs.existsSync(out)) throw Error('Preserve prior output: '+out); fs.mkdirSync(out, { recursive: true });
const oldBytes = fs.readFileSync(oldFile), old = JSON.parse(oldBytes.toString('utf8'));
const literals = JSON.parse(fs.readFileSync(path.join(run, 'literal-exports.json')));
const extraction = JSON.parse(fs.readFileSync(path.join(run, 'extraction-manifest.json')));
const selectedSources = Object.fromEntries(Object.keys(extraction.targets).map(key => {
  const rows = (extraction.records || []).filter(row => row.key === key);
  const row = rows.find(item => item.layer === 'Documents/res') || rows[0];
  return [key, row || null];
}));
const plain = value => value && value.kind === 'dict' ? Object.fromEntries(value.entries.map(([k, v]) => [plain(k), plain(v)])) : Array.isArray(value) ? value.map(plain) : value;
const exported = key => Object.fromEntries(Object.entries(literals[key].exports || {}).map(([k, v]) => [k, plain(v)]));
const layouts = exported('levelLayouts'), defs = exported('roomDefinitions').data, images = exported('mapImages').data;
const catalogVersion = value => (value.updateIni.match(/version\s*=\s*(\S+)/) || [])[1] || null;
const groupIds = exported('levelLayouts').level_id_to_ids || {};
const reverseGroups = new Map(); for (const [group, ids] of Object.entries(groupIds)) for (const id of ids) { if (!reverseGroups.has(String(id))) reverseGroups.set(String(id), []); reverseGroups.get(String(id)).push(Number(group)); }
function rotateVoxel(x, z, rotation) { const steps = ((Number(rotation) % 4) + 4) % 4; if (steps === 1) return [z, -x]; if (steps === 2) return [-x, -z]; if (steps === 3) return [-z, x]; return [x, z]; }
const maps = {}, worldCoordinates = [], assetGaps = [], doorProvenance = [];
for (const [id, raw] of Object.entries(layouts.data)) {
  const rooms = (raw.room_data || []).map((room, roomIndex) => ({ roomIndex, roomConfigId: room[0], position: [...room[1]], rotation: room[2], source: { module: 'levelLayouts', exportName: 'data', layoutId: Number(id), field: 'room_data', arrayIndex: roomIndex } }));
  const oldMap = old.maps?.[id]; const normalizedDoors = Object.entries(raw.door || {}).map(([doorIndex, pair]) => ({doorIndex:Number(doorIndex),roomIndex:pair[0],doorSlot:pair[1]}));
  maps[id] = { levelId: Number(id), groupIds: reverseGroups.get(id) || [], rooms, roomDataRaw: raw.room_data || [], doors: normalizedDoors, normalizedDoors, doorSourceShape: normalizedDoors.length ? 'current-layout-door-record' : 'absent-in-current-layout-module', source: { packageVersion: catalogVersion(extraction), module: 'levelLayouts', exportName: 'data', sourceFile: selectedSources.levelLayouts?.filename || null }, dataStatus: 'current_room_data_and_door_records' };
  if (normalizedDoors.length) doorProvenance.push({ layoutId: Number(id), count: normalizedDoors.length, source: 'current-layout-module', sourceVersion: catalogVersion(extraction), status: 'literal-extracted' });
  const floors = new Map(); for (const room of rooms) { const def = defs[String(room.roomConfigId)]; for (const voxel of def?.room_voxels || []) { const floor = room.position[1] + voxel[1]; const [rx, rz] = rotateVoxel(voxel[0], voxel[2], room.rotation); const x = room.position[0] + rx, z = room.position[2] + rz; if (!floors.has(floor)) floors.set(floor, []); floors.get(floor).push({ x, z, roomIndex: room.roomIndex, roomConfigId: room.roomConfigId, rotation: room.rotation }); } }
  for (const [floor, points] of floors) { const xs = points.map(p => p.x), zs = points.map(p => p.z); worldCoordinates.push({ layoutId: Number(id), floor, voxelBounds: { minX: Math.min(...xs), maxX: Math.max(...xs) + 1, minZ: Math.min(...zs), maxZ: Math.max(...zs) + 1 }, worldBounds: { minX: Math.min(...xs) * 128, maxX: (Math.max(...xs) + 1) * 128, minY: -(Math.max(...zs) + 1) * 128, maxY: -Math.min(...zs) * 128 }, transform: { rule: 'renderer.rotateVector(roomVoxelX, roomVoxelZ, room.rotation)', pivot: 'room.position voxel anchor', rotationsSeen: [...new Set(points.map(p => p.rotation))].sort((a, b) => a - b) }, source: 'current levelLayouts + current roomDefinitions', sourceVersion: catalogVersion(extraction) }); }
}
const mapImageConfigs = {}; for (const [rawKey, fields] of Object.entries(images)) { const key = rawKey.replace(',', '_'); const oldImage = old.mapImageConfigs?.[key]; const exact = !!oldImage && JSON.stringify(oldImage.fields) === JSON.stringify(fields); const assetFiles = exact ? oldImage.assetFiles : []; mapImageConfigs[key] = { key: [fields.room_id, fields.floor], fields, assetFiles, assetResolutionStatus: exact ? 'legacy_asset_exact_config_match' : 'unresolved_current_asset', source: { packageVersion: catalogVersion(extraction), module: 'mapImages', exportName: 'data', configKey: rawKey }, assetSource: exact ? { kind: 'legacy-reuse', catalogVersion: old.gameConfigVersion || null, note: 'bytes not re-extracted from current package' } : null }; if (!exact || !assetFiles.length) assetGaps.push({ key, imagePath: fields.image_path, reason: exact ? 'legacy asset path missing' : 'current fields differ from old catalog; no silent mapping' }); }
const catalog = { schemaVersion: 'latest-game-config-candidate-1', gameConfigVersion: (extraction.updateIni.match(/version\s*=\s*(\S+)/) || [])[1] || null, sourceScope: 'installed client literal configuration; no game execution', sourceModules: Object.fromEntries(Object.keys(extraction.targets).map(k => [k, selectedSources[k] ? { file: selectedSources[k].filename, layer: selectedSources[k].layer, entry: selectedSources[k].entry, split: selectedSources[k].split, offset: selectedSources[k].offset, dataSize: selectedSources[k].dataSize, packedBytes: selectedSources[k].packedBytes, decodedBytes: selectedSources[k].decodedBytes, sha256: selectedSources[k].decodedSha256, partial: k === 'dungeonParams' } : { missing: true }])), maps, roomDefinitions: defs, mapImageConfigs, levelRules: exported('levelRules').data, outdoorMaps: exported('outdoorMaps').data, roomArrange: exported('roomArrange').data, roomReferences: {}, doorRecords: doorProvenance, worldCoordinates, provenance: { currentRun: run, oldCatalog: oldFile, oldCatalogSha256: crypto.createHash('sha256').update(oldBytes).digest('hex'), currentConfigVersion: catalogVersion(extraction), oldImagesReusedOnlyWhenExactFieldsMatch: true, noAuthorPngClaim: true, worldCoordinateRule: 'renderer.rotateVector applied to room voxel X/Z using room.rotation' }, assetGaps };
fs.writeFileSync(path.join(out, 'latest-catalog-candidate.json'), JSON.stringify(catalog, null, 2) + '\n'); fs.writeFileSync(path.join(out, 'world-coordinate-candidates.json'), JSON.stringify(worldCoordinates, null, 2) + '\n'); fs.writeFileSync(path.join(out, 'asset-gap-report.json'), JSON.stringify({ count: assetGaps.length, gaps: assetGaps }, null, 2) + '\n');
console.log(JSON.stringify({ output: out, layouts: Object.keys(maps).length, roomDefinitions: Object.keys(defs).length, mapImages: Object.keys(mapImageConfigs).length, floors: worldCoordinates.length, currentDoorLayouts: doorProvenance.length, assetGaps: assetGaps.length }));
