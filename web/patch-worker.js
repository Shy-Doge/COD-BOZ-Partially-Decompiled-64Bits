// Web Worker: loads Pyodide once and runs patch_ipa.py off the main thread.
import { loadPyodide } from "https://cdn.jsdelivr.net/npm/pyodide@314.0.7/pyodide.mjs";
import { setupPatcher, readPlist, runPatch } from "./lib/pypatch.js";

let ready = null;

async function init() {
  const pyodide = await loadPyodide();
  const names = ["patch_ipa.py", "hook.bin", "hook.json", "game_config.icf"];
  const files = {};
  for (const n of names) {
    const r = await fetch(new URL("patch/" + n, self.location.href), { cache: "no-cache" });
    if (!r.ok) throw new Error(`Could not load patch/${n} from this site (${r.status}).`);
    files[n] = new Uint8Array(await r.arrayBuffer());
  }
  await setupPatcher(pyodide, files);
  return pyodide;
}

self.onmessage = async (ev) => {
  const { id, type, data } = ev.data;
  try {
    if (!ready) ready = init();
    const pyodide = await ready;
    let result, transfer = [];
    if (type === "init") result = true;
    else if (type === "plist") result = readPlist(pyodide, data);
    else if (type === "patch") {
      result = runPatch(pyodide, data);
      transfer = [result.s3e.buffer];
    } else throw new Error("unknown request " + type);
    self.postMessage({ id, ok: true, result }, transfer);
  } catch (e) {
    if (type === "init") ready = null; // allow a retry
    self.postMessage({ id, ok: false, error: String(e.message || e), log: e.log || "" });
  }
};
