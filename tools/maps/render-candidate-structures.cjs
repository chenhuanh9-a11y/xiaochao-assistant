#!/usr/bin/env node
// Candidate renderer run: current layout/room/config data + explicitly legacy
// image bytes. Outputs are candidates only and carry their provenance in JSON.
const fs = require('node:fs'); const path = require('node:path');
const arg = n => { const i = process.argv.indexOf(n); return i >= 0 ? process.argv[i + 1] : null; };
if (process.argv.includes('--help')) {
 console.log('Usage: node render-candidate-structures.cjs --run RUN --old-vendor AUTHORIZED_RENDERER_ROOT --output STAGE [--levels 104001,104002]');
 console.log('External source_renderer.js and licensed assets are required; no game resources or renderer are supplied.');
 process.exit(0);
}
const run = path.resolve(arg('--run') || path.join(__dirname, 'run-20261025-r6'));
const candidate = path.join(run, 'catalog-candidate', 'latest-catalog-candidate.json');
const oldVendor = path.resolve(arg('--old-vendor') || path.join(__dirname, '../../../resources-private/renderer'));
const out = path.resolve(arg('--output') || path.join(run, 'candidate-structures')); fs.mkdirSync(out, { recursive: true });
const { createRenderer } = require(path.join(oldVendor, 'tools', 'source_renderer.js'));
const catalog = JSON.parse(fs.readFileSync(candidate, 'utf8'));
const selected = arg('--levels') ? new Set(arg('--levels').split(',').map(String)) : null;
const results = [], failures = [];
for (const levelId of Object.keys(catalog.maps).filter(id => !selected || selected.has(id)).sort((a, b) => Number(a) - Number(b))) {
  // Recreate the renderer per layout so image/shader caches cannot grow across
  // the complete catalog and turn a valid candidate run into an OOM.
  const renderer = createRenderer({ dataFile: candidate, assetsDir: path.join(oldVendor, 'assets'), staticExitMarkers: false, staticDoorFrames: false });
  let floors; try { floors = renderer.availableFloors(levelId); } catch (e) { failures.push({ levelId, stage: 'availableFloors', message: String(e.message || e) }); continue; }
  for (const floor of floors) try { const result = renderer.renderLevelFloor(levelId, floor, { outDir: out, write: true, outputCellPixels: 60 }); if (!result) throw Error('renderer returned no result (likely no drawable room/image source)'); const metadataPath = path.join(out, `level_${levelId}_floor_${floor}.json`); const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8')); metadata.candidateProvenance = { layoutSource: 'current package 2.104.3059230', roomDefinitionSource: 'current package 2.104.3059230', imageSource: 'legacy vendor assets; exact current mapImages field match only', imageBytesReextracted: false, doors: 'not rendered; current door resource not extracted' }; fs.writeFileSync(metadataPath, JSON.stringify(metadata, null, 2) + '\n'); results.push({ levelId: Number(levelId), floor, out60: result.out60, placements: result.placementCount, warnings: result.diagnostics?.warnings || [] }); } catch (e) { failures.push({ levelId: Number(levelId), floor, stage: 'render', message: String(e.message || e) }); }
}
const report = { candidate, oldVendor, output: out, generated: results.length, failures: failures.length, results, failures, provenance: 'Structure candidates combine current package layout/room/image configuration with explicitly legacy image bytes. They are not current-package image extraction and are not production acceptance.' };
fs.writeFileSync(path.join(out, 'render-report.json'), JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({ output: out, generated: results.length, failures: failures.length })); if (failures.length) process.exitCode = 2;
