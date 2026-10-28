// Python 3 marshal 解析器（精确支持 CPython 3.11+ FLAG_REF 引用机制）
const fs = require('fs');

const T = {
  NULL: 0x30, NONE: 0x4e, FALSE: 0x46, TRUE: 0x54, STOPITER: 0x53, ELLIPSIS: 0x2e,
  INT: 0x69, INT64: 0x49, LONG: 0x6c, FLOAT: 0x66, BINFLOAT: 0x67,
  COMPLEX: 0x78, BINCOMPLEX: 0x79,
  STRING: 0x73, INTERNED: 0x74, UNICODE: 0x75, ASCII: 0x61, ASCII_INTERNED: 0x41,
  SHORT_ASCII: 0x7a, SHORT_ASCII_INTERNED: 0x5a,
  STRINGREF: 0x52, REF: 0x72,
  TUPLE: 0x28, SMALL_TUPLE: 0x29, LIST: 0x5b, DICT: 0x7b,
  SET: 0x3c, FROZENSET: 0x3e, CODE: 0x63,
};
const FLAG_REF = 0x80;

class Unmarshaller {
  constructor(buf) {
    this.b = buf;
    this.p = 0;
    this.refs = [];
  }
  u8() { return this.b[this.p++]; }
  u16() { const v = this.b.readUInt16LE(this.p); this.p += 2; return v; }
  i32() { const v = this.b.readInt32LE(this.p); this.p += 4; return v; }
  u32() { const v = this.b.readUInt32LE(this.p); this.p += 4; return v; }
  i64() { const v = this.b.readBigInt64LE(this.p); this.p += 8; return Number(v); }
  bytes(n) { const v = this.b.subarray(this.p, this.p + n); this.p += n; return v; }

  readObject() {
    const raw = this.u8();
    const flag = (raw & FLAG_REF) !== 0;
    const type = raw & ~FLAG_REF;
    let refIdx = -1;
    if (flag) {
      refIdx = this.refs.length;
      this.refs.push(null); // 先占位
    }
    const setRef = (obj) => {
      if (refIdx >= 0) this.refs[refIdx] = obj;
      return obj;
    };

    switch (type) {
      case T.NULL: return null;
      case T.NONE: return setRef(null);
      case T.FALSE: return setRef(false);
      case T.TRUE: return setRef(true);
      case T.STOPITER: return setRef('StopIteration');
      case T.ELLIPSIS: return setRef('Ellipsis');
      case T.INT: return setRef(this.i32());
      case T.INT64: return setRef(this.i64());
      case T.BINFLOAT: { const v = this.b.readDoubleLE(this.p); this.p += 8; return setRef(v); }
      case T.FLOAT: { const n = this.u8(); const s = this.b.toString('latin1', this.p, this.p + n); this.p += n; return setRef(parseFloat(s)); }
      case T.STRING: { const n = this.u32(); return setRef(this.bytes(n)); }
      case T.INTERNED: case T.ASCII: case T.ASCII_INTERNED: {
        const n = this.u32(); return setRef(this.bytes(n).toString('latin1'));
      }
      case T.SHORT_ASCII: case T.SHORT_ASCII_INTERNED: {
        const n = this.u8(); return setRef(this.bytes(n).toString('latin1'));
      }
      case T.UNICODE: { const n = this.u32(); const s = this.bytes(n).toString('utf8'); return setRef(s); }
      case T.REF: case T.STRINGREF: {
        const i = this.u32();
        return this.refs[i];
      }
      case T.TUPLE: case T.LIST: {
        const n = this.u32();
        const arr = [];
        setRef(arr);
        for (let i = 0; i < n; i++) arr.push(this.readObject());
        return arr;
      }
      case T.SMALL_TUPLE: {
        const n = this.u8();
        const arr = [];
        setRef(arr);
        for (let i = 0; i < n; i++) arr.push(this.readObject());
        return arr;
      }
      case T.DICT: {
        const o = {};
        setRef(o);
        for (;;) {
          const k = this.readObject();
          if (k === null) break;
          o[typeof k === 'object' ? JSON.stringify(k) : String(k)] = this.readObject();
        }
        return o;
      }
      case T.SET: case T.FROZENSET: {
        const n = this.u32();
        const arr = [];
        setRef(arr);
        for (let i = 0; i < n; i++) arr.push(this.readObject());
        return arr;
      }
      case T.LONG: {
        const n = this.i32(); const neg = n < 0; const cnt = Math.abs(n);
        let v = 0n;
        for (let i = 0; i < cnt; i++) v |= BigInt(this.u16()) << BigInt(15 * i);
        if (neg) v = -v;
        return setRef(Number(v));
      }
      case T.CODE: {
        const argcount = this.i32(), posonly = this.i32(), kwonly = this.i32();
        const stacksize = this.i32(), flags = this.i32();
        const co = { __code__: true, argcount, posonly, kwonly, stacksize, flags };
        setRef(co);
        co.code = this.readObject();
        co.consts = this.readObject();
        co.names = this.readObject();
        co.localsplusnames = this.readObject();
        co.localspluskinds = this.readObject();
        co.filename = this.readObject();
        co.name = this.readObject();
        co.qualname = this.readObject();
        co.firstlineno = this.i32();
        co.linetable = this.readObject();
        co.exceptiontable = this.readObject();
        return co;
      }
      default:
        throw new Error(`未知 marshal 类型 0x${type.toString(16)} @${this.p - 1}`);
    }
  }
}

function unmarshal(buf) { const u = new Unmarshaller(buf); return u.readObject(); }

module.exports = { unmarshal, Unmarshaller };

if (require.main === module) {
  const f = process.argv[2];
  const b = fs.readFileSync(f);
  console.log(`文件 ${f}  ${b.length} 字节`);
  try {
    const obj = unmarshal(b);
    console.log('顶层类型: ' + (obj && obj.__code__ ? 'code' : typeof obj));
    if (obj && obj.__code__) {
      console.log(`  模块名 ${obj.qualname}  文件 ${obj.filename}`);
      console.log(`  常量 ${obj.consts ? obj.consts.length : 0} 项`);
    }
  } catch (e) {
    console.log('解析失败: ' + e.message);
  }
}
