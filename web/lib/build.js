// Puts the patched APK together: every entry of the game-free "shell" APK (minus its old
// signature), the patched boz_aarch64.s3e, and the .dz data archives streamed straight out of the IPA.

import { ApkWriter } from "./apk.js";
import { crc32 } from "./zip.js";

const OLD_SIGNATURE = /^META-INF\/(MANIFEST\.MF|[^/]+\.(SF|RSA|DSA|EC))$/i;

// Total number of bytes the APK will roughly have (for the progress bar).
export function estimateSize(shell, ipa, appDir, dzNames, s3e) {
  let n = s3e.length;
  for (const e of shell.entries) n += e.csize + 30 + e.name.length;
  for (const name of dzNames) n += ipa.get(appDir + name).usize;
  return n;
}

export async function buildApk({ shell, ipa, appDir, dzNames, s3e, signer, sink, onProgress, log = () => {} }) {
  const newNames = new Set(["assets/boz_aarch64.s3e", ...dzNames.map((n) => "assets/" + n)]);
  const w = new ApkWriter(sink, onProgress);

  log("Copying the app (shell APK)…");
  for (const e of shell.entries) {
    if (e.name.endsWith("/")) continue;
    if (OLD_SIGNATURE.test(e.name)) continue;
    if (newNames.has(e.name)) continue;
    if (/^assets\/.*_aarch64\.s3e$/.test(e.name)) continue; // the loader must find exactly one
    await w.addEntry(
      { name: e.name, method: e.method, crc: e.crc, csize: e.csize, usize: e.usize, time: e.time, date: e.date, extAttr: e.extAttr },
      (await shell.rawBlob(e)).stream(),
    );
  }

  log("Adding boz_aarch64.s3e…");
  await w.addEntry({ name: "assets/boz_aarch64.s3e", method: 0, crc: crc32(s3e), csize: s3e.length, usize: s3e.length }, s3e);

  for (const name of dzNames) {
    const e = ipa.get(appDir + name);
    if (!e) throw new Error(`${name} is missing from the IPA.`);
    log(`Adding ${name} (${Math.round(e.usize / 1e6)} MB)…`);
    // .dz files are stored uncompressed, like the Gradle build does (noCompress "dz").
    await w.addEntry(
      { name: "assets/" + name, method: 0, crc: e.crc, csize: e.usize, usize: e.usize, time: e.time, date: e.date },
      await ipa.stream(e),
      { checkCrc: true },
    );
  }

  log("Signing…");
  return w.finish(signer);
}
