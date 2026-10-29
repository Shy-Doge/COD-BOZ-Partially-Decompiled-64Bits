// Signing key for the patched APK: an RSA-2048 key and a self-signed X.509 certificate, created in
// the browser with WebCrypto and kept in this browser's IndexedDB. Reusing the same key lets a newer
// patched build install over an older one without losing save data. The key never leaves the device
// unless the user saves a backup.

const subtle = globalThis.crypto.subtle;
const ALG = { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" };

// ---- minimal DER encoder ---------------------------------------------------------------------
function derLen(n) {
  if (n < 0x80) return [n];
  const b = [];
  while (n > 0) { b.unshift(n & 0xff); n = Math.floor(n / 256); }
  return [0x80 | b.length, ...b];
}
function tlv(tag, content) {
  const c = content instanceof Uint8Array ? content : new Uint8Array(content);
  const h = [tag, ...derLen(c.length)];
  const out = new Uint8Array(h.length + c.length);
  out.set(h); out.set(c, h.length);
  return out;
}
function cat(...parts) {
  const len = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(len);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}
const seq = (...p) => tlv(0x30, cat(...p));
const set = (...p) => tlv(0x31, cat(...p));
function oid(str) {
  const a = str.split(".").map(Number);
  const out = [40 * a[0] + a[1]];
  for (const v of a.slice(2)) {
    const b = [v & 0x7f];
    let x = v >>> 7;
    while (x > 0) { b.unshift(0x80 | (x & 0x7f)); x >>>= 7; }
    out.push(...b);
  }
  return tlv(0x06, out);
}
const NULL = new Uint8Array([0x05, 0x00]);
function integer(bytes) {
  let b = [...bytes];
  while (b.length > 1 && b[0] === 0 && !(b[1] & 0x80)) b.shift();
  if (b[0] & 0x80) b.unshift(0);
  return tlv(0x02, b);
}
const ascii = (s) => new TextEncoder().encode(s);
const pad2 = (n) => String(n).padStart(2, "0");
function utcTime(d) {
  return tlv(0x17, ascii(`${pad2(d.getUTCFullYear() % 100)}${pad2(d.getUTCMonth() + 1)}${pad2(d.getUTCDate())}${pad2(d.getUTCHours())}${pad2(d.getUTCMinutes())}${pad2(d.getUTCSeconds())}Z`));
}
const generalizedTime = (s) => tlv(0x18, ascii(s));

async function selfSignedCert(privateKey, spkiDer, commonName) {
  const sigAlg = seq(oid("1.2.840.113549.1.1.11"), NULL); // sha256WithRSAEncryption
  const name = seq(set(seq(oid("2.5.4.3"), tlv(0x0c, ascii(commonName)))));
  const serial = crypto.getRandomValues(new Uint8Array(12));
  serial[0] &= 0x7f; serial[0] |= 0x01;
  const notBefore = new Date(Date.now() - 86400000);
  const tbs = seq(
    tlv(0xa0, integer([2])), // v3
    integer(serial),
    sigAlg,
    name,
    seq(utcTime(notBefore), generalizedTime("20751231235959Z")),
    name,
    spkiDer,
  );
  const sig = new Uint8Array(await subtle.sign(ALG, privateKey, tbs));
  return seq(tbs, sigAlg, tlv(0x03, cat(new Uint8Array([0]), sig)));
}

export async function generateSigner() {
  const kp = await subtle.generateKey({ ...ALG, modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) }, true, ["sign", "verify"]);
  const spkiDer = new Uint8Array(await subtle.exportKey("spki", kp.publicKey));
  const certDer = await selfSignedCert(kp.privateKey, spkiDer, "BOZ 64-bit patcher");
  return { privateKey: kp.privateKey, spkiDer, certDer, created: new Date().toISOString() };
}

export async function fingerprint(signer) {
  const d = new Uint8Array(await subtle.digest("SHA-256", signer.certDer));
  return [...d.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join(":").toUpperCase();
}

// ---- backup file ---------------------------------------------------------------------------------
const b64 = (u8) => { let s = ""; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000)); return btoa(s); };
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export async function exportSigner(signer) {
  const pkcs8 = new Uint8Array(await subtle.exportKey("pkcs8", signer.privateKey));
  return JSON.stringify({ format: "boz-patcher-key-1", created: signer.created, pkcs8: b64(pkcs8), spki: b64(signer.spkiDer), cert: b64(signer.certDer) }, null, 1);
}

export async function importSigner(text) {
  const j = JSON.parse(text);
  if (j.format !== "boz-patcher-key-1") throw new Error("This is not a key backup from this page.");
  const privateKey = await subtle.importKey("pkcs8", unb64(j.pkcs8), ALG, true, ["sign"]);
  return { privateKey, spkiDer: unb64(j.spki), certDer: unb64(j.cert), created: j.created };
}

// ---- IndexedDB storage ---------------------------------------------------------------------------
function db() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("boz-patcher", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("kv");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
async function kv(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const tx = d.transaction("kv", mode);
    const req = fn(tx.objectStore("kv"));
    tx.oncomplete = () => resolve(req.result);
    tx.onerror = () => reject(tx.error);
  });
}
export const loadStoredSigner = () => kv("readonly", (s) => s.get("signer")).catch(() => undefined);
export const storeSigner = (signer) => kv("readwrite", (s) => s.put(signer, "signer"));
