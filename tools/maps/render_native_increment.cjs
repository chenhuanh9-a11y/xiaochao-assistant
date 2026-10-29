#!/usr/bin/env node
// Candidate renderer run: current layout/room/config data + explicitly legacy
// image bytes. Outputs are candidates only and carry their provenance in JSON.
const fs = require('node:fs'); const path = require('node:path');
const arg = n => { const i = process.argv.indexOf(n); return i >= 0 ? process.argv[i + 1] : null; };
const required=n=>{const v=arg(n);if(!v)throw Error('Required: '+n);return v;};
const run=path.resolve(required('--run'));
const candidate = path.join(run, 'catalog-candidate', 'latest-catalog-candidate.json');
const oldVendor=path.resolve(required('--vendor'));
const out=path.resolve(required('--output'));if(fs.existsSync(out))throw Error('Preserve prior output: '+out);
const catalog = JSON.parse(fs.readFileSync(candidate, 'utf8'));
const selected=new Set(required('--levels').split(',').map(s=>s.trim()));
if (!selected.size || [...selected].some(id=>!/^\d+$/.test(id) || !Object.hasOwn(catalog.maps,id))) throw Error('All selected levels must exist in the catalog');
for (const input of [run, oldVendor]) {
  const rel=path.relative(input,out), reverse=path.relative(out,input);
  if (!rel || (!rel.startsWith('..'+path.sep) && rel!=='..' && !path.isAbsolute(rel)) || (!reverse.startsWith('..'+path.sep) && reverse!=='..' && !path.isAbsolute(reverse))) throw Error('Output overlaps input');
}
const { createRenderer } = require(path.join(oldVendor, 'tools', 'source_renderer.js'));
fs.mkdirSync(out,{recursive:true});
const results = [], failures = [];
for (const levelId of Object.keys(catalog.maps).filter(id => !selected || selected.has(id)).sort((a, b) => Number(a) - Number(b))) {
  // Recreate the renderer per layout so image/shader caches cannot grow across
  // the complete catalog and turn a valid candidate run into an OOM.
  const renderer = createRenderer({ dataFile: candidate, assetsDir: path.join(oldVendor, 'assets'), staticExitMarkers: false, staticDoorFrames: false });
  let floors; try { floors = renderer.availableFloors(levelId); } catch (e) { failures.push({ levelId, stage: 'availableFloors', message: String(e.message || e) }); continue; }
  for (const floor of floors) try { const result = renderer.renderLevelFloor(levelId, floor, { outDir: out, write: true, outputCellPixels: 60 }); if (!result) throw Error('renderer returned no result (likely no drawable room/image source)'); const metadataPath = path.join(out, `level_${levelId}_floor_${floor}.json`); const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8')); metadata.candidateProvenance = { layoutSource: 'current package '+catalog.gameConfigVersion, roomDefinitionSource: 'current package '+catalog.gameConfigVersion, imageSource: 'legacy vendor assets; exact current mapImages field match only', imageBytesReextracted: false, doors: 'not rendered; current door resource not extracted' }; fs.writeFileSync(metadataPath, JSON.stringify(metadata, null, 2) + '\n'); results.push({ levelId: Number(levelId), floor, out60: result.out60, placements: result.placementCount, warnings: result.diagnostics?.warnings || [] }); } catch (e) { failures.push({ levelId: Number(levelId), floor, stage: 'render', message: String(e.message || e) }); }
}
const report = { candidate, oldVendor, output: out, generated: results.length, failures: failures.length, results, failures, provenance: 'Structure candidates combine current package layout/room/image configuration with explicitly legacy image bytes. They are not current-package image extraction and are not production acceptance.' };
fs.writeFileSync(path.join(out, 'render-report.json'), JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({ output: out, generated: results.length, failures: failures.length })); if (failures.length) process.exitCode = 2;
