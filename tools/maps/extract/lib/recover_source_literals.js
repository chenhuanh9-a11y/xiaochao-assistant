// Reconstruct static module-level mappings from the supplied custom Python
// bytecode. Imports and the single TD(mapping) literal wrapper are data-only;
// no Python code or constructor is executed.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { Unmarshaller } = require('./marshal.js');

function loadOpcodeNames() {
  const source = fs.readFileSync(path.join(__dirname, 'netease_opmap.py'), 'utf8');
  const opNames = new Map();
  for (const match of source.matchAll(/'([A-Z][A-Z0-9_]*)'\s*:\s*(\d+)/g)) {
    const name = match[1];
    const opcode = Number(match[2]);
    if (opNames.has(opcode)) throw new Error(`Duplicate opcode ${opcode} in netease_opmap.py`);
    opNames.set(opcode, name);
  }
  if (opNames.has(0)) throw new Error('Opcode zero is already assigned in netease_opmap.py');
  opNames.set(0, 'CACHE');
  for (const name of ['CACHE', 'RESUME', 'RETURN_VALUE', 'POP_TOP', 'PUSH_NULL', 'IMPORT_NAME', 'IMPORT_FROM', 'IMPORT_STAR', 'STORE_NAME', 'LOAD_CONST', 'LOAD_NAME', 'BUILD_MAP', 'BUILD_LIST', 'BUILD_TUPLE', 'BUILD_SET', 'BUILD_CONST_KEY_MAP', 'MAP_ADD', 'LIST_EXTEND', 'DICT_UPDATE', 'PRECALL', 'CALL', 'EXTENDED_ARG']) {
    if (![...opNames.values()].includes(name)) throw new Error(`Opcode ${name} missing from netease_opmap.py`);
  }
  return opNames;
}

const opNames = loadOpcodeNames();
const opcodeByName = new Map([...opNames].map(([opcode, name]) => [name, opcode]));
const NULL = Object.freeze({ kind: 'stack_null' });
const TD = Object.freeze({ kind: 'literal_wrapper', name: 'TD' });
const IMPORTED_MODULE = Object.freeze({ kind: 'literal_import_module' });

function keyToken(value) {
  if (Array.isArray(value)) return `array:${JSON.stringify(value)}`;
  if (value === null) return 'null';
  return `${typeof value}:${String(value)}`;
}

function requireMap(value, context) {
  if (!(value instanceof Map)) throw new Error(`${context}: expected a dictionary`);
  return value;
}

function toJson(value) {
  if (value instanceof Map) {
    return {
      kind: 'dict',
      entries: [...value.entries()].map(([key, item]) => [toJson(key), toJson(item)])
    };
  }
  if (Array.isArray(value)) return value.map(toJson);
  if (value === NULL) return { kind: 'stack_null' };
  if (value === TD) return { kind: 'literal_wrapper', name: 'TD' };
  if (value === IMPORTED_MODULE) return { kind: 'literal_import_module' };
  if (typeof value === 'bigint') return { kind: 'bigint', decimal: value.toString() };
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return value;
  throw new Error(`Unsupported value in static export: ${Object.prototype.toString.call(value)}`);
}

function validateInlineCaches(pairs, name) {
  const expectedByName = { PRECALL: 1, CALL: 4 };
  const cacheOpcode = opcodeByName.get('CACHE');
  const errors = [];
  for (let i = 0; i < pairs.length; i++) {
    const opname = opNames.get(pairs[i].opcode);
    if (opname === 'CACHE') continue;
    const expected = expectedByName[opname] || 0;
    for (let offset = 1; offset <= expected; offset++) {
      if (!pairs[i + offset] || pairs[i + offset].opcode !== cacheOpcode) {
        errors.push(`${opname} at ${pairs[i].byteOffset} expected ${expected} CACHE slots`);
        break;
      }
    }
    if (expected) {
      for (let offset = 1; offset <= expected; offset++) pairs[i + offset].isCache = true;
    }
  }
  for (const pair of pairs) {
    if (pair.opcode === cacheOpcode && !pair.isCache) errors.push(`unclaimed CACHE at ${pair.byteOffset}`);
  }
  if (errors.length) throw new Error(`${name}: invalid inline caches: ${errors.join('; ')}`);
}

function executeLiteralModule(code, moduleKey) {
  if (!code || code.__code__ !== true) throw new Error(`${moduleKey}: top-level object is not code`);
  const constants = code.consts;
  const names = code.names;
  if (!Array.isArray(constants) || !Array.isArray(names)) throw new Error(`${moduleKey}: malformed const/name tables`);
  if (!(code.code instanceof Buffer) && !(code.code instanceof Uint8Array)) throw new Error(`${moduleKey}: malformed bytecode field`);
  if (code.code.length % 2 !== 0) throw new Error(`${moduleKey}: bytecode has an incomplete instruction`);

  const pairs = [];
  for (let i = 0; i < code.code.length; i += 2) {
    pairs.push({ opcode: code.code[i], arg: code.code[i + 1], byteOffset: i });
  }
  validateInlineCaches(pairs, moduleKey);

  const stack = [];
  const globals = new Map();
  const overwriteHistory = [];
  let extendedArg = 0;
  let calls = 0;
  const sourceOnlyImports = [];
  const setMap = (target, key, value, byteOffset) => {
    requireMap(target, `${moduleKey} byte ${byteOffset}`);
    const token = keyToken(key);
    const previous = [...target.keys()].find(candidate => keyToken(candidate) === token);
    if (previous !== undefined || target.has(undefined)) {
      overwriteHistory.push({ byteOffset, key: toJson(key) });
      target.delete(previous);
    }
    target.set(key, value);
  };

  for (let index = 0; index < pairs.length; index++) {
    const instruction = pairs[index];
    const name = opNames.get(instruction.opcode);
    if (!name) throw new Error(`${moduleKey}: unknown opcode ${instruction.opcode} at byte ${instruction.byteOffset}`);
    const arg = (extendedArg << 8) | instruction.arg;
    if (name === 'EXTENDED_ARG') {
      extendedArg = arg;
      continue;
    }
    extendedArg = 0;
    if (instruction.isCache || name === 'CACHE') continue;

    const pop = (context) => {
      if (!stack.length) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: stack underflow in ${context}`);
      return stack.pop();
    };
    const popMany = (count, context) => {
      if (!Number.isSafeInteger(count) || count < 0 || stack.length < count) {
        throw new Error(`${moduleKey} byte ${instruction.byteOffset}: invalid ${context} count ${count}`);
      }
      return stack.splice(stack.length - count, count);
    };
    const targetAtDepth = (depth, context) => {
      if (!Number.isSafeInteger(depth) || depth < 1 || stack.length < depth) {
        throw new Error(`${moduleKey} byte ${instruction.byteOffset}: invalid ${context} stack depth ${depth}`);
      }
      return stack[stack.length - depth];
    };

    switch (name) {
      case 'RESUME':
        break;
      case 'LOAD_CONST':
        if (arg >= constants.length) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: const index ${arg} out of range`);
        stack.push(constants[arg]);
        break;
      case 'LOAD_NAME':
        if (arg >= names.length) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: name index ${arg} out of range`);
        if (!globals.has(names[arg])) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: unresolved name ${names[arg]}`);
        stack.push(globals.get(names[arg]));
        break;
      case 'STORE_NAME':
        if (arg >= names.length) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: name index ${arg} out of range`);
        globals.set(names[arg], pop(name));
        break;
      case 'PUSH_NULL':
        stack.push(NULL);
        break;
      case 'POP_TOP':
        pop(name);
        break;
      case 'IMPORT_NAME': {
        const fromlist = pop(name);
        const level = pop(name);
        if (typeof level !== 'number' || !Array.isArray(fromlist)) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: nonliteral import operands`);
        stack.push(IMPORTED_MODULE);
        break;
      }
      case 'IMPORT_FROM':
        if (arg >= names.length || stack[stack.length - 1] !== IMPORTED_MODULE || names[arg] !== 'taggeddict') {
          throw new Error(`${moduleKey} byte ${instruction.byteOffset}: unsupported import target`);
        }
        stack.push(TD);
        break;
      case 'IMPORT_STAR':
        if (arg >= names.length || pop(name) !== IMPORTED_MODULE) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: unsupported star import`);
        sourceOnlyImports.push(names[arg]);
        break;
      case 'BUILD_MAP': {
        const items = popMany(arg * 2, name);
        const map = new Map();
        for (let i = 0; i < items.length; i += 2) setMap(map, items[i], items[i + 1], instruction.byteOffset);
        stack.push(map);
        break;
      }
      case 'BUILD_LIST':
      case 'BUILD_TUPLE':
        stack.push(popMany(arg, name));
        break;
      case 'BUILD_SET':
        stack.push(new Set(popMany(arg, name)));
        break;
      case 'BUILD_CONST_KEY_MAP': {
        const keys = pop(name);
        if (!Array.isArray(keys) || keys.length !== arg) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: key tuple length does not match arg ${arg}`);
        const values = popMany(arg, name);
        const map = new Map();
        keys.forEach((key, i) => setMap(map, key, values[i], instruction.byteOffset));
        stack.push(map);
        break;
      }
      case 'LIST_EXTEND': {
        const values = pop(name);
        const target = targetAtDepth(arg, name);
        if (!Array.isArray(target) || !Array.isArray(values)) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: LIST_EXTEND requires literal lists`);
        target.push(...values);
        break;
      }
      case 'MAP_ADD': {
        const value = pop(name);
        const key = pop(name);
        setMap(targetAtDepth(arg, name), key, value, instruction.byteOffset);
        break;
      }
      case 'DICT_UPDATE': {
        const source = requireMap(pop(name), `${moduleKey} byte ${instruction.byteOffset} DICT_UPDATE source`);
        const target = requireMap(targetAtDepth(arg, name), `${moduleKey} byte ${instruction.byteOffset} DICT_UPDATE target`);
        for (const [key, value] of source) setMap(target, key, value, instruction.byteOffset);
        break;
      }
      case 'PRECALL':
        if (arg !== 1) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: unsupported call arity ${arg}`);
        break;
      case 'CALL': {
        const args = popMany(arg, name);
        const callable = pop(name);
        const nullOrCallable = pop(name);
        if (nullOrCallable !== NULL || callable !== TD || args.length !== 1 || !(args[0] instanceof Map)) {
          throw new Error(`${moduleKey} byte ${instruction.byteOffset}: only literal TD(mapping) is permitted`);
        }
        stack.push(args[0]);
        calls++;
        break;
      }
      case 'MAKE_FUNCTION': {
        if (moduleKey !== 'dungeonParams') throw new Error('Function not permitted in ' + moduleKey);
        return { exports: Object.fromEntries([...globals.entries()].map(([key, value]) => [key, toJson(value)])),
          partial: true, stoppedBefore: 'MAKE_FUNCTION' };
      }
      case 'RETURN_VALUE': {
        pop(name);
        if (stack.length) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: nonempty stack at return (${stack.length})`);
        if (extendedArg) throw new Error(`${moduleKey}: dangling EXTENDED_ARG`);
        return {
          exports: Object.fromEntries([...globals.entries()].map(([key, value]) => [key, toJson(value)])),
          instructionCount: pairs.length,
          literalWrapperCalls: calls,
          sourceOnlyImports,
          overwrittenKeys: overwriteHistory
        };
      }
      default:
        throw new Error(`${moduleKey}: unsupported opcode ${name} (${instruction.opcode}) at byte ${instruction.byteOffset}`);
    }
    if (stack.length > code.stacksize) throw new Error(`${moduleKey} byte ${instruction.byteOffset}: stack exceeds declared size ${code.stacksize}`);
  }
  throw new Error(`${moduleKey}: bytecode ended without RETURN_VALUE`);
}

function recoverModules(MODULES, ROOT) {
  const output = {};
  for (const [key, relative] of Object.entries(MODULES)) {
    const file = path.resolve(ROOT, relative);
    const bytes = fs.readFileSync(file);
    const reader = new Unmarshaller(bytes);
    const code = reader.readObject();
    if (reader.p !== bytes.length) throw new Error(`${relative}: parser consumed ${reader.p}/${bytes.length} bytes`);
    if (code.filename !== path.basename(relative).replace('export_configs.pyc', 'goldrush_random_room_export_configs.py')) {
      // Module filename uses the game's original package path, independent of the local asset name.
      const expectedFragments = {
        levelLayouts: 'goldrush_random_room_export_configs.py',
        mapImages: 'goldrush_minimap_configs.py',
        levelRules: 'goldrush_random_room_level_configs.py',
        roomDefinitions: 'random_advanture_room_configs.py',
        globalRules: 'goldrush_random_room_global_configs.py',
        outdoorMaps: 'goldrush_outdoor_scn_configs.py',
        roomArrange: 'goldrush_room_arrange.py',
        scenes: 'gold_rush_scn.py', sceneGroups: 'gold_rush_scn_group.py',
        renderGlobalData: 'goldrush_global_data.py', dungeonParams: 'goldrush_dungeon_params.py'
      };
      if (!code.filename.endsWith(expectedFragments[key])) throw new Error(`${relative}: unexpected module filename ${code.filename}`);
    }
    output[key] = {
      file: relative.replace(/\\/g, '/'),
      sourceBytes: bytes.length,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
      moduleFilename: code.filename,
      instructionCount: code.code.length / 2,
      ...executeLiteralModule(code, key)
    };
  }
  return output;
}
module.exports = { recoverModules };
