// Streaming APK writer with APK Signature Scheme v2.
//
// Entries are written to the sink one after another while the v2 content digest (1 MB chunks,
// SHA-256) is computed on the fly. At the end the APK Signing Block is inserted between the last
// entry and the central directory, as the scheme requires. Nothing larger than one chunk is held in
// memory, so an ~900 MB APK can be produced on a phone.
//
// Spec: https://source.android.com/docs/security/features/apksigning/v2

import { Crc32 } from "./zip.js";

const subtle = globalThis.crypto.subtle;
const CHUNK = 1 << 20;
const SIG_RSA_PKCS1_SHA256 = 0x0103;
const V2_BLOCK_ID = 0x7109871a;
const PAGE_ALIGN = 16384; // uncompressed .so files: 16 KB page alignment (also fine for 4 KB pages)

function le(n, bytes) {
  const out = new Uint8Array(bytes);
  let v = BigInt(n);
  for (let i = 0; i < bytes; i++) { out[i] = Number(v & 0xffn); v >>= 8n; }
  return out;
}
function concat(parts) {
  let len = 0;
  for (const p of parts) len += p.length;
  const out = new Uint8Array(len);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}
const lp = (u8) => concat([le(u8.length, 4), u8]); // uint32 length-prefixed
const lpSeq = (items) => lp(concat(items.map(lp)));

async function sha256(u8) { return new Uint8Array(await subtle.digest("SHA-256", u8)); }

// Collects the 1 MB chunk digests of one contiguous section.
class ChunkDigester {
  constructor() { this.buf = new Uint8Array(CHUNK + 5); this.fill = 0; this.digests = []; }
  async update(u8) {
    let p = 0;
    while (p < u8.length) {
      const n = Math.min(CHUNK - this.fill, u8.length - p);
      this.buf.set(u8.subarray(p, p + n), 5 + this.fill);
      this.fill += n; p += n;
      if (this.fill === CHUNK) await this._flush();
    }
  }
  async _flush() {
    this.buf[0] = 0xa5;
    this.buf.set(le(this.fill, 4), 1);
    this.digests.push(await sha256(this.buf.subarray(0, 5 + this.fill)));
    this.fill = 0;
  }
  async finish() { if (this.fill > 0) await this._flush(); return this.digests; }
}

export class ApkWriter {
  // sink: async (Uint8Array) => void. Bytes passed to the sink are never reused by the writer.
  constructor(sink, onProgress) {
    this.sink = sink;
    this.onProgress = onProgress || (() => {});
    this.offset = 0;
    this.central = [];
    this.names = new Set();
    this.section1 = new ChunkDigester();
  }

  async _emit(u8) {
    await this.section1.update(u8);
    await this.sink(u8);
    this.offset += u8.length;
    this.onProgress(this.offset);
  }

  // e: { name, method (0|8), crc, csize, usize, time, date, extAttr }
  // source: ReadableStream of the bytes to store (already compressed when method is 8),
  //         or a Uint8Array. When checkCrc is true the stored bytes (method 0) are CRC-checked.
  async addEntry(e, source, { checkCrc = false } = {}) {
    if (this.names.has(e.name)) throw new Error(`duplicate entry ${e.name}`);
    if (e.csize >= 0xffffffff || this.offset >= 0xffffffff) throw new Error("APK larger than 4 GB is not supported");
    this.names.add(e.name);
    const name = new TextEncoder().encode(e.name);
    const time = e.time ?? 0, date = e.date ?? 0x21; // 1980-01-01 00:00 for new entries
    let extra = new Uint8Array(0);
    if (e.method === 0) {
      const align = e.name.endsWith(".so") ? PAGE_ALIGN : 4;
      const base = this.offset + 30 + name.length + 6; // 0xD935 extra = id, size, alignment
      const pad = (align - (base % align)) % align;
      extra = concat([le(0xd935, 2), le(2 + pad, 2), le(align, 2), new Uint8Array(pad)]);
    }
    const flags = 0x0800; // UTF-8 names, sizes in the local header (no data descriptor)
    const lfh = concat([
      le(0x04034b50, 4), le(e.method === 0 ? 10 : 20, 2), le(flags, 2), le(e.method, 2),
      le(time, 2), le(date, 2), le(e.crc, 4), le(e.csize, 4), le(e.usize, 4),
      le(name.length, 2), le(extra.length, 2), name, extra,
    ]);
    const lfhOffset = this.offset;
    await this._emit(lfh);

    let written = 0;
    const crc = checkCrc && e.method === 0 ? new Crc32() : null;
    if (source instanceof Uint8Array) {
      if (crc) crc.update(source);
      await this._emit(source.slice());
      written = source.length;
    } else {
      const reader = source.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (crc) crc.update(value);
        written += value.length;
        await this._emit(value);
      }
    }
    if (written !== e.csize) throw new Error(`${e.name}: expected ${e.csize} bytes, got ${written}`);
    if (crc && crc.digest() !== e.crc) throw new Error(`${e.name} is damaged in the IPA (CRC mismatch)`);

    this.central.push({ ...e, nameBytes: name, flags, time, date, lfhOffset });
  }

  _centralDirectory() {
    return concat(this.central.flatMap((c) => [
      le(0x02014b50, 4), le(0x0314, 2), le(c.method === 0 ? 10 : 20, 2), le(c.flags, 2), le(c.method, 2),
      le(c.time, 2), le(c.date, 2), le(c.crc, 4), le(c.csize, 4), le(c.usize, 4),
      le(c.nameBytes.length, 2), le(0, 2), le(0, 2), le(0, 2), le(0, 2),
      le(c.extAttr ?? 0, 4), le(c.lfhOffset, 4), c.nameBytes,
    ]));
  }

  _eocd(cdSize, cdOffset) {
    const n = this.central.length;
    if (n > 0xffff) throw new Error("too many entries");
    return concat([le(0x06054b50, 4), le(0, 2), le(0, 2), le(n, 2), le(n, 2), le(cdSize, 4), le(cdOffset, 4), le(0, 2)]);
  }

  // signer: { privateKey: CryptoKey (RSASSA-PKCS1-v1_5/SHA-256), certDer: Uint8Array, spkiDer: Uint8Array }
  async finish(signer) {
    const sbOffset = this.offset;
    const cd = this._centralDirectory();
    // For the digest, the EOCD's central-directory offset points at the start of the signing block.
    const eocdForDigest = this._eocd(cd.length, sbOffset);

    const chunkDigests = [...(await this.section1.finish())];
    for (const section of [cd, eocdForDigest]) {
      const d = new ChunkDigester();
      await d.update(section);
      chunkDigests.push(...(await d.finish()));
    }
    const contentDigest = await sha256(concat([new Uint8Array([0x5a]), le(chunkDigests.length, 4), ...chunkDigests]));

    const signedData = concat([
      lpSeq([concat([le(SIG_RSA_PKCS1_SHA256, 4), lp(contentDigest)])]), // digests
      lpSeq([signer.certDer]),                                          // certificates
      lp(new Uint8Array(0)),                                             // additional attributes
    ]);
    const signature = new Uint8Array(await subtle.sign({ name: "RSASSA-PKCS1-v1_5" }, signer.privateKey, signedData));
    const signerBlock = concat([
      lp(signedData),
      lpSeq([concat([le(SIG_RSA_PKCS1_SHA256, 4), lp(signature)])]),
      lp(signer.spkiDer),
    ]);
    const v2Value = lpSeq([signerBlock]);
    const pair = concat([le(4 + v2Value.length, 8), le(V2_BLOCK_ID, 4), v2Value]);
    const blockSize = pair.length + 8 + 16;
    const magic = new TextEncoder().encode("APK Sig Block 42");
    const block = concat([le(blockSize, 8), pair, le(blockSize, 8), magic]);

    const tailBytes = concat([block, cd, this._eocd(cd.length, sbOffset + block.length)]);
    await this.sink(tailBytes);
    this.offset += tailBytes.length;
    this.onProgress(this.offset);
    return this.offset;
  }
}
