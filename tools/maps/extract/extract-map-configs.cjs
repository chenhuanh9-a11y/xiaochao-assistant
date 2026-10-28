'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { decodeResource } = require('./lib/resource-codec.cjs');
const { Unmarshaller } = require('./lib/marshal.js');
const { recoverModules } = require('./lib/recover_source_literals.js');
const TARGETS = {
  "levelLayouts": "goldrush_random_room_export_configs.py",
  "levelRules": "goldrush_random_room_level_configs.py",
  "mapImages": "goldrush_minimap_configs.py",
  "roomDefinitions": "random_advanture_room_configs.py",
  "roomArrange": "goldrush_room_arrange.py",
  "scenes": "gold_rush_scn.py",
  "sceneGroups": "gold_rush_scn_group.py",
  "renderGlobalData": "goldrush_global_data.py",
  "dungeonParams": "goldrush_dungeon_params.py",
  "outdoorMaps": "goldrush_outdoor_scn_configs.py"
};

function parseArgs(args) {
  const options = {};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--help' || arg === '-h') { options.help = true; continue; }
    if (!['--game', '--out', '--compare'].includes(arg)) throw new Error('未知参数：' + arg);
    if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error(arg + ' 缺少路径');
    if (options[arg.slice(2)]) throw new Error('参数重复：' + arg);
    options[arg.slice(2)] = args[++i];
  }
  return options;
}
function inside(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}
function dictionary(value) {
  if (value?.kind !== 'dict' || !Array.isArray(value.entries)) throw new Error('配置不是预期的字典格式');
  return new Map(value.entries);
}
function canonical(value) {
  if (value?.kind === 'dict') return {kind: 'dict', entries: value.entries.map(([k,v]) => [canonical(k), canonical(v)])
    .sort((a,b) => JSON.stringify(a[0]).localeCompare(JSON.stringify(b[0])))};
  return Array.isArray(value) ? value.map(canonical) : value;
}
function readExact(fd, length, offset) {
  const buffer = Buffer.alloc(length);
  if (fs.readSync(fd, buffer, 0, length, offset) !== length) throw new Error('资源读取不完整，偏移 ' + offset);
  return buffer;
}
function scan(gameRoot, layer, output) {
  const root = path.join(gameRoot, layer);
  const idx = fs.readFileSync(path.join(root, 'script.idx'));
  if (idx.length < 32 || idx.subarray(0,4).toString() !== 'SKPW') throw new Error(layer + '：未知索引格式');
  const count = idx.readUInt32LE(12);
  if (idx.length < 32 + count * 36) throw new Error(layer + '：索引不完整');
  const descriptors = new Map(), matches = [];
  let decoded = 0;
  try {
    for (let i = 0; i < count; i++) {
      const entry = 32 + i * 36, split = idx[entry + 20], offset = idx.readUInt32LE(entry + 24);
      if (!descriptors.has(split)) {
        const fd = fs.openSync(path.join(root, 'script' + split + '.wpk'), 'r');
        descriptors.set(split, {fd, size: fs.fstatSync(fd).size});
      }
      const descriptor = descriptors.get(split);
      if (offset + 48 > descriptor.size) throw new Error(layer + '：资源头越界，记录 ' + i);
      const header = readExact(descriptor.fd, 48, offset);
      if (header.subarray(0,4).toString() !== '1DPW') throw new Error(layer + '：未知资源头，记录 ' + i);
      const size = header.readUInt32LE(32);
      if (size > descriptor.size - offset - 48) throw new Error(layer + '：资源内容越界，记录 ' + i);
      const bytes = decodeResource(readExact(descriptor.fd, size, offset + 48));
      if (!bytes) continue;
      decoded++;
      for (const [key, name] of Object.entries(TARGETS)) {
        if (!bytes.includes(Buffer.from(name))) continue;
        const reader = new Unmarshaller(bytes), code = reader.readObject();
        if (typeof code.filename !== 'string' || !code.filename.endsWith(name)) continue;
        if (reader.p !== bytes.length) throw new Error(name + '：模块未完整解析');
        const filename = layer.replace(/[\\/]/g, '-') + '-' + key + '.bin';
        if (matches.some(m => m.key === key)) throw new Error(layer + '：同一模块出现多份：' + key);
        fs.writeFileSync(path.join(output, filename), bytes, {flag: 'wx'});
        matches.push({key, layer, entry: i, filename, moduleFilename: code.filename, bytes: bytes.length});
      }
    }
  } finally { for (const {fd} of descriptors.values()) fs.closeSync(fd); }
  console.log(layer + '：扫描 ' + count + ' 条，解码 ' + decoded + ' 条，匹配 ' + matches.length + ' 个模块');
  return matches;
}
function summarize(current, previous) {
  const layouts = dictionary(current.levelLayouts.exports.data);
  const rules = dictionary(current.levelRules.exports.data);
  const groups = dictionary(current.levelLayouts.exports.level_id_to_ids);
  const difficulties = new Map();
  for (const [id, encoded] of rules) {
    const rule = dictionary(encoded);
    if (rule.get('theme') === 999) continue;
    const difficulty = rule.get('difficulty');
    if (difficulty === undefined) continue;
    if (!difficulties.has(difficulty)) difficulties.set(difficulty, new Set());
    for (const layout of groups.get(id) || []) {
      if (!layouts.has(layout)) throw new Error('布局组引用不存在的地图：' + layout);
      difficulties.get(difficulty).add(layout);
    }
  }
  const summary = {
    layoutRecordCount: layouts.size,
    difficulties: [...difficulties].sort((a,b) => a[0]-b[0]).map(([difficulty, ids]) =>
      ({difficulty, count: ids.size, ids: [...ids].sort((a,b) => a-b)})),
    partialModules: Object.entries(current).filter(([,v]) => v.partial).map(([key,v]) =>
      ({key, stoppedBefore: v.stoppedBefore})),
    scope: 'Local installed-client configuration only. No textures, image rendering, game launch or server query.'
  };
  if (previous) {
    const before = dictionary(previous.levelLayouts.exports.data);
    summary.comparison = {
      added: [...layouts.keys()].filter(id => !before.has(id)),
      removed: [...before.keys()].filter(id => !layouts.has(id)),
      changed: [...layouts.keys()].filter(id => before.has(id) &&
        JSON.stringify(canonical(layouts.get(id))) !== JSON.stringify(canonical(before.get(id))))
    };
  }
  return summary;
}
function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log('用法：node extract-map-configs.cjs --game "游戏根目录" [--out "新输出目录"] [--compare "上次的literal-exports.json"]\n只读扫描本地客户端；输出目录必须不存在；需要 Node.js 24 或更高版本。');
    return;
  }
  if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('需要 Node.js 24 或更高版本');
  if (!options.game) throw new Error('请通过 --game 指定包含 res 或 Documents/res 的游戏根目录');
  const gameRoot = fs.realpathSync(path.resolve(options.game));
  const layers = ['Documents/res', 'res'].filter(layer => fs.existsSync(path.join(gameRoot, layer, 'script.idx')));
  if (!layers.length) throw new Error('游戏目录中找不到 Documents/res/script.idx 或 res/script.idx');
  const output = path.resolve(options.out || path.join(__dirname, 'output',
    new Date().toISOString().replace(/[:.]/g, '-') + '-' + process.pid));
  if (inside(gameRoot, output)) throw new Error('输出目录不能位于游戏目录中');
  if (fs.existsSync(output)) throw new Error('输出目录已经存在，请换一个新目录：' + output);
  // Resolve symlinked parent folders before creating the output.
  let parent = path.dirname(output);
  while (!fs.existsSync(parent)) parent = path.dirname(parent);
  if (inside(gameRoot, fs.realpathSync(parent))) throw new Error('输出目录的真实位置位于游戏目录中');
  const previous = options.compare ? JSON.parse(fs.readFileSync(path.resolve(options.compare), 'utf8')) : null;
  if (previous) dictionary(previous.levelLayouts?.exports?.data);
  fs.mkdirSync(output, {recursive: true});
  const write = (name, value) => fs.writeFileSync(path.join(output, name), JSON.stringify(value, null, 2) + '\n', {flag:'wx'});
  const matches = [];
  for (const layer of layers) matches.push(...scan(gameRoot, layer, output));
  write('discovery.json', matches);
  const modules = {};
  for (const key of Object.keys(TARGETS)) {
    const selected = matches.find(m => m.key === key && m.layer === 'Documents/res') ||
      matches.find(m => m.key === key && m.layer === 'res');
    if (!selected) throw new Error('未找到配置模块 ' + key + '；已提取的文件保留在 ' + output);
    modules[key] = selected.filename;
  }
  write('modules.json', modules);
  const current = recoverModules(modules, output);
  write('literal-exports.json', current);
  const summary = summarize(current, previous);
  const versionFile = path.join(gameRoot, 'Documents', 'update.ini');
  summary.installedVersion = fs.existsSync(versionFile) ?
    (fs.readFileSync(versionFile, 'utf8').match(/version\s*=\s*(\S+)/)?.[1] || null) : null;
  summary.checkedAt = new Date().toISOString();
  summary.moduleSources = Object.fromEntries(Object.keys(modules).map(key =>
    [key, matches.find(m => m.filename === modules[key]).layer]));
  write('summary.json', summary);
  console.log('完成：' + output);
  console.log(JSON.stringify(summary.difficulties));
}
try { main(); } catch (error) {
  console.error('提取失败：' + error.message);
  console.error('已生成的输出会保留；修正问题后请使用新输出目录重试。');
  process.exitCode = 1;
}
