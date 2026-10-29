// Random-access ZIP reader over a Blob/File. Reads only the parts it needs, so a 1+ GB IPA
// never has to fit in memory. Supports ZIP64 and stored/deflated entries.

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export class Crc32 {
  constructor() { this.c = 0xffffffff; }
  update(u8) {
    let c = this.c;
    for (let i = 0; i < u8.length; i++) c = CRC_TABLE[(c ^ u8[i]) & 0xff] ^ (c >>> 8);
    this.c = c;
    return this;
  }
  digest() { return (this.c ^ 0xffffffff) >>> 0; }
}

export const crc32 = (u8) => new Crc32().update(u8).digest();

async function readRange(blob, start, len) {
  return new Uint8Array(await blob.slice(start, start + len).arrayBuffer());
}

const u16 = (b, o) => b[o] | (b[o + 1] << 8);
const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
const u64 = (b, o) => u32(b, o) + u32(b, o + 4) * 0x100000000;

export async function readZip(blob) {
  const size = blob.size;
  if (size < 22) throw new Error("This file is too small to be a ZIP/IPA/APK.");
  const tailLen = Math.min(size, 65557 + 20);
  const tailStart = size - tailLen;
  const tail = await readRange(blob, tailStart, tailLen);
  let e = -1;
  for (let i = tail.length - 22; i >= 0; i--) {
    if (u32(tail, i) === 0x06054b50) { e = i; break; }
  }
  if (e < 0) throw new Error("Not a valid ZIP file (end of central directory not found).");
  let count = u16(tail, e + 10);
  let cdSize = u32(tail, e + 12);
  let cdOffset = u32(tail, e + 16);
  // ZIP64
  if (e >= 20 && u32(tail, e - 20) === 0x07064b50) {
    const z64off = u64(tail, e - 20 + 8);
    const z = await readRange(blob, z64off, 56);
    if (u32(z, 0) !== 0x06064b50) throw new Error("Broken ZIP64 record.");
    count = u64(z, 32);
    cdSize = u64(z, 40);
    cdOffset = u64(z, 48);
  }
  const cd = await readRange(blob, cdOffset, cdSize);
  const entries = [];
  const dec = new TextDecoder();
  let p = 0;
  for (let i = 0; i < count; i++) {
    if (u32(cd, p) !== 0x02014b50) throw new Error("Broken ZIP central directory.");
    const nameLen = u16(cd, p + 28), extraLen = u16(cd, p + 30), commentLen = u16(cd, p + 32);
    const ent = {
      versionMadeBy: u16(cd, p + 4),
      flags: u16(cd, p + 8),
      method: u16(cd, p + 10),
      time: u16(cd, p + 12),
      date: u16(cd, p + 14),
      crc: u32(cd, p + 16),
      csize: u32(cd, p + 20),
      usize: u32(cd, p + 24),
      extAttr: u32(cd, p + 38),
      lfhOffset: u32(cd, p + 42),
      name: dec.decode(cd.subarray(p + 46, p + 46 + nameLen)),
    };
    // ZIP64 extended information
    let x = p + 46 + nameLen;
    const xEnd = x + extraLen;
    while (x + 4 <= xEnd) {
      const id = u16(cd, x), len = u16(cd, x + 2);
      if (id === 0x0001) {
        let q = x + 4;
        if (ent.usize === 0xffffffff) { ent.usize = u64(cd, q); q += 8; }
        if (ent.csize === 0xffffffff) { ent.csize = u64(cd, q); q += 8; }
        if (ent.lfhOffset === 0xffffffff) { ent.lfhOffset = u64(cd, q); q += 8; }
      }
      x += 4 + len;
    }
    entries.push(ent);
    p += 46 + nameLen + extraLen + commentLen;
  }
  return new ZipArchive(blob, entries);
}

export class ZipArchive {
  constructor(blob, entries) {
    this.blob = blob;
    this.entries = entries;
    this.byName = new Map(entries.map((e) => [e.name, e]));
  }
  get(name) { return this.byName.get(name); }

  async dataOffset(ent) {
    if (ent._dataOffset !== undefined) return ent._dataOffset;
    const h = await readRange(this.blob, ent.lfhOffset, 30);
    if (u32(h, 0) !== 0x04034b50) throw new Error(`Broken local header for ${ent.name}.`);
    ent._dataOffset = ent.lfhOffset + 30 + u16(h, 26) + u16(h, 28);
    return ent._dataOffset;
  }

  // The stored (possibly compressed) bytes exactly as they are in the archive.
  async rawBlob(ent) {
    const off = await this.dataOffset(ent);
    return this.blob.slice(off, off + ent.csize);
  }

  // A ReadableStream of the uncompressed content.
  async stream(ent) {
    const raw = (await this.rawBlob(ent)).stream();
    if (ent.method === 0) return raw;
    if (ent.method === 8) return raw.pipeThrough(new DecompressionStream("deflate-raw"));
    throw new Error(`${ent.name} uses an unsupported compression method (${ent.method}).`);
  }

  async bytes(ent) {
    const buf = new Uint8Array(await new Response(await this.stream(ent)).arrayBuffer());
    if (buf.length !== ent.usize) throw new Error(`${ent.name} has the wrong size.`);
    if (crc32(buf) !== ent.crc) throw new Error(`${ent.name} is damaged (CRC mismatch).`);
    return buf;
  }
}
