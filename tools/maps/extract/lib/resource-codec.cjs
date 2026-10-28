'use strict';
// Adapted from MiniMap_Creator/tools/extract_doors.js and check-layouts.cjs.
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const M64 = 0xFFFFFFFFFFFFFFFFn;
function deriveKey(length, t) {
  const L = BigInt(length >>> 0);
  const v10 = Number((BigInt(t) + L) & 0xFFn);
  const v28 = 0x7C2E6B6A00000000n | ((L << 8n) & 0xFFFF0000n) | (BigInt(v10) << 8n) | BigInt((length >>> 0) % 0xFD);
  const v29 = 0x5C74656E00003630n | ((BigInt(v10 ^ 0x33) << 16n) & 0xFFFFFFFF00FFFFFFn) | (BigInt(v10 | 0x2E) << 24n);
  const b = Buffer.alloc(16); b.writeBigUInt64LE(v28 & M64, 0); b.writeBigUInt64LE(v29 & M64, 8); return b;
}
function aesDecPrefix(buf, length, k) {
  const done = Math.floor(length / 16) * 16; if (done <= 0) return 0;
  const d = crypto.createDecipheriv('aes-128-ecb', k, null); d.setAutoPadding(false);
  Buffer.concat([d.update(buf.subarray(0, done)), d.final()]).copy(buf, 0); return done;
}
function xorOffset(buf, off, want, seed) {
  if (want <= 0) return; const m = Math.min(off, want);
  for (let i = 0; i < m; i++) buf[off + i] ^= ((seed + i) + buf[i]) & 0xFF;
  for (let i = 0; i < want - m; i++) buf[off + m + i] ^= (seed + m + i) & 0xFF;
}
function headerDecode(buf) {
  const n = Math.min(64, buf.length); let i = 0, j = n - 1;
  while (i < j) { const bi = buf[i] ^ 0x5A, bj = buf[j] ^ 0x5A; buf[i] = bj; buf[j] = bi; i++; j--; }
  if (i === j) buf[i] ^= 0x5A;
}
function stage1(pl) {
  if (pl.length < 8) return null;
  const tag = pl.readUInt16LE(0), p = pl[2], t = pl[3];
  const body = Buffer.from(pl.subarray(8)); const bl = body.length;
  const pxl = (bl > 0 && p !== 0) ? Math.min(bl, 128 << (p - 1)) : 0; const seed = (t + bl) >>> 0;
  if (tag === 0x4341 || tag === 0x4350) {
    const done = aesDecPrefix(body, pxl, deriveKey(bl, t));
    const rem = Math.max(0, pxl - done); if (rem > 0) xorOffset(body, done, rem, seed);
  } else if (tag === 0x4358) { for (let i = 0; i < pxl; i++) body[i] ^= (seed + i) & 0xFF; } else return null;
  headerDecode(body); return body;
}
function stripW(d) {
  let x = d;
  for (let i = 0; i < 8; i++) {
    if (x.length >= 4 && x.subarray(0, 4).toString('latin1') === 'ENON') { x = x.subarray(4); continue; }
    if (x.length >= 8 && x.subarray(0, 4).toString('latin1') === 'NONE') { x = x.subarray(8); continue; }
    if (x.length >= 8 && x.subarray(0, 4).toString('latin1') === 'DTSZ') { try { x = zlib.zstdDecompressSync(x.subarray(4)); continue; } catch (error) { throw new Error('DTSZ 解压失败：' + error.message); } }
    break;
  }
  return x;
}

const publicKey=`-----BEGIN RSA PUBLIC KEY-----
MIGJAoGBAOZAaZe2qB7dpT9Y8WfZIdDv+ooS1HsFEDW2hFnnvcuFJ4vIuPgKhISm
pY4/jT3aipwPNVTjM6yHbzOLhrnGJh7Ec3CQG/FZu6VKoCqVEtCeh15hjcu6QYtn
YWIEf8qgkylqsOQ3IIn76udV6m0AWC2jDlmLeRcR04w9NNw7+9t9AgMBAAE=
-----END RSA PUBLIC KEY-----`;
function unwrapNxs(bytes){
  if(bytes.subarray(0,8).toString('hex')!=='4e58533303000001')return bytes;
  let key=crypto.publicDecrypt({key:publicKey,padding:crypto.constants.RSA_PKCS1_PADDING},bytes.subarray(20,148)).readUInt32LE(0);
  const compressed=Buffer.from(bytes.subarray(148));
  for(let i=0;i<compressed.length;i++){
    compressed[i]^=(key>>>((i%4)*8))&255;
    if(i%4===3){const ror=((key>>>19)|(key<<13))>>>0;key=(ror+((ror<<2)>>>0)+0xe6546b64)>>>0;}
  }
  const expected=bytes.readUInt32LE(8),out=Buffer.alloc(expected);let p=0,q=0;
  const extend=n=>{if(n!==15)return n;let t;do{if(p>=compressed.length)throw Error('LZ4 extension bounds');t=compressed[p++];n+=t;}while(t===255);return n;};
  while(p<compressed.length){
    const token=compressed[p++],literal=extend(token>>>4);
    if(p+literal>compressed.length||q+literal>out.length)throw Error('LZ4 literal bounds');
    compressed.copy(out,q,p,p+literal);p+=literal;q+=literal;
    if(p===compressed.length)break;
    if(p+2>compressed.length)throw Error('LZ4 offset bounds');
    const offset=compressed.readUInt16LE(p);p+=2;const length=extend(token&15)+4;
    if(!offset||offset>q||q+length>out.length)throw Error('LZ4 match bounds');
    for(let j=0;j<length;j++)out[q+j]=out[q+j-offset];q+=length;
  }
  if(q!==expected)throw Error('NXS3 output length mismatch');
  return out;
}

function decodeResource(payload) { const decoded = stage1(payload); return decoded ? unwrapNxs(stripW(decoded)) : null; }
module.exports = { decodeResource };
