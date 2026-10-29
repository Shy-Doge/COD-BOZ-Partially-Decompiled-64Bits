import { readZip } from "./lib/zip.js";
import { buildApk, estimateSize } from "./lib/build.js";
import { generateSigner, loadStoredSigner, storeSigner, exportSigner, importSigner, fingerprint } from "./lib/keys.js";
import { openSink, prepareDownloads } from "./lib/sinks.js";

const SUPPORTED_BUNDLE_ID = "com.activision.callofduty.blackopszombies";
const SUPPORTED_VERSION = "1.3.5";
const OUTPUT_NAME = "BOZ-64bit.apk";

const $ = (id) => document.getElementById(id);
const logEl = $("log");
const log = (msg) => { logEl.textContent += msg.replace(/\s+$/, "") + "\n"; logEl.scrollTop = logEl.scrollHeight; };
const mb = (n) => `${Math.round(n / 1e6)} MB`;
function status(id, msg, kind = "") { const el = $(id); el.textContent = msg; el.className = "status " + kind; }
function enable(sectionId, on) { $(sectionId).setAttribute("aria-disabled", on ? "false" : "true"); }

// ---- patch worker (Pyodide) ----------------------------------------------------------------------
let worker, nextId = 0;
const pending = new Map();
function call(type, data, transfer = []) {
  if (!worker) {
    worker = new Worker(new URL("./patch-worker.js", import.meta.url), { type: "module" });
    worker.onmessage = (e) => {
      const p = pending.get(e.data.id);
      pending.delete(e.data.id);
      if (e.data.ok) p.resolve(e.data.result);
      else { const err = new Error(e.data.error); err.log = e.data.log; p.reject(err); }
    };
    worker.onerror = (e) => { for (const p of pending.values()) p.reject(new Error(e.message || "worker failed to start")); pending.clear(); };
  }
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, type, data }, transfer);
  });
}

// ---- shell APK (built by GitHub Actions, contains no game files) ---------------------------------
let shellPromise = null;
function loadShell() {
  if (!shellPromise) {
    shellPromise = (async () => {
      const r = await fetch("shell/boz-shell.apk", { cache: "no-cache" });
      if (!r.ok) throw new Error(`Could not download the app from this site (${r.status}).`);
      const shell = await readZip(await r.blob());
      if (!shell.get("AndroidManifest.xml")) throw new Error("The downloaded app is not a valid APK.");
      return shell;
    })();
    shellPromise.catch(() => { shellPromise = null; });
  }
  return shellPromise;
}

fetch("shell/info.json", { cache: "no-cache" })
  .then((r) => (r.ok ? r.json() : Promise.reject()))
  .then((i) => {
    const ref = i.release || (i.commit || "").slice(0, 7);
    $("shell-info").innerHTML = "";
    $("shell-info").append(`App build: ${ref}${i.date ? " · " + i.date.slice(0, 10) : ""}`);
    if (i.repository) {
      const a = Object.assign(document.createElement("a"), { href: `https://github.com/${i.repository}`, textContent: i.repository });
      $("shell-info").append(" · ", a);
    }
  })
  .catch(() => { $("shell-info").textContent = "App build: not published yet."; });

// ---- signing key ---------------------------------------------------------------------------------
let signer = null;
async function showKey() {
  if (!signer) return;
  const created = signer.created ? new Date(signer.created).toLocaleDateString() : "unknown date";
  $("key-info").textContent = `Using the signing key from ${created} (fingerprint ${await fingerprint(signer)}). Builds made with this key install over each other and keep your save data. Save a backup if you want to build updates on another device or browser.`;
  $("key-save").disabled = false;
}
loadStoredSigner().then((s) => { if (s) { signer = s; showKey(); } });

$("key-save").onclick = async () => {
  const text = await exportSigner(signer);
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text], { type: "application/json" })), download: "boz-patcher-key.json" });
  document.body.appendChild(a); a.click(); a.remove();
  status("key-status", "Key backup saved. Keep it private: anyone with it can sign updates for your install.", "ok");
};
$("key-input").onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  try {
    const s = await importSigner(await f.text());
    if (signer && !confirm("Replace the signing key in this browser with the one from the backup?")) return;
    signer = s;
    await storeSigner(signer);
    await showKey();
    status("key-status", "Key backup loaded.", "ok");
  } catch (err) { status("key-status", err.message, "bad"); }
};

// ---- step 1: read and patch the IPA --------------------------------------------------------------
let job = null; // { ipa, appDir, s3e, dzNames }

function patchArgs() {
  const a = [];
  if ($("opt-mobile").checked) a.push("--keep-mobile-check");
  if ($("opt-online").checked) a.push("--no-online");
  if ($("opt-config").checked) a.push("--no-config-hooks");
  return a;
}

$("ipa-input").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  job = null;
  $("build-button").disabled = true;
  enable("step-build", false);
  const prog = $("ipa-progress");
  prog.hidden = false; prog.removeAttribute("value");
  log(`IPA: ${file.name} (${mb(file.size)})`);
  try {
    status("ipa-status", "Reading the IPA…");
    loadShell().catch(() => {}); // start downloading the app in the background
    const workerReady = call("init");
    const ipa = await readZip(file);
    const plistEntry = ipa.entries.find((x) => /^Payload\/[^/]+\.app\/Info\.plist$/.test(x.name));
    if (!plistEntry) throw new Error("This doesn't look like an IPA (no Payload/*.app/Info.plist inside).");
    const appDir = plistEntry.name.slice(0, -"Info.plist".length);
    const appName = appDir.split("/")[1];
    const plist = await ipa.bytes(plistEntry);

    status("ipa-status", "Loading the patcher (first time: about 10 MB)…");
    await workerReady;
    const info = await call("plist", plist);
    log(`app: ${appName}  bundle: ${info.CFBundleIdentifier}  version: ${info.CFBundleVersion}`);
    if (info.CFBundleIdentifier !== SUPPORTED_BUNDLE_ID) throw new Error("This IPA is not Black Ops Zombies.");
    if (info.CFBundleVersion !== SUPPORTED_VERSION) throw new Error(`This IPA is version ${info.CFBundleVersion}; only ${SUPPORTED_VERSION} is supported.`);
    const exeEntry = ipa.get(appDir + info.CFBundleExecutable);
    if (!exeEntry) throw new Error("The game executable is missing from the IPA.");

    status("ipa-status", "Patching the game code…");
    const exe = await ipa.bytes(exeEntry);
    const res = await call("patch", { appName, plist, exeName: info.CFBundleExecutable, exe, args: patchArgs() }, [exe.buffer]);
    log(res.log);
    for (const n of res.dzNames) {
      if (!ipa.get(appDir + n)) throw new Error(`${n} is missing from the IPA.`);
    }

    status("ipa-status", "Downloading the app…");
    const shell = await loadShell();
    job = { ipa, appDir, s3e: res.s3e, dzNames: res.dzNames };
    const size = estimateSize(shell, ipa, appDir, res.dzNames, res.s3e);
    job.size = size;
    status("ipa-status", "IPA patched. Ready to build.", "ok");
    $("build-info").textContent = `The APK will be about ${mb(size)}. Keep this page open and the screen on until it finishes.`;
    $("build-button").disabled = false;
    enable("step-build", true);
    prepareDownloads();
  } catch (err) {
    if (err.log) log(err.log);
    log("ERROR: " + err.message);
    status("ipa-status", err.message, "bad");
  } finally {
    prog.hidden = true;
  }
};

// ---- step 2: build, sign and save ----------------------------------------------------------------
$("build-button").onclick = async () => {
  if (!job) return;
  const btn = $("build-button");
  btn.disabled = true;
  let sink;
  try {
    sink = await openSink(OUTPUT_NAME); // first, while the click still counts as a user gesture
  } catch (err) {
    btn.disabled = false;
    if (err.name !== "AbortError") status("build-status", err.message, "bad");
    return;
  }
  const prog = $("build-progress");
  prog.hidden = false; prog.max = job.size; prog.value = 0;
  let wake = null;
  try { wake = await navigator.wakeLock?.request("screen"); } catch { /* not supported */ }
  try {
    if (!signer) {
      status("build-status", "Creating your signing key…");
      signer = await generateSigner();
      await storeSigner(signer);
      navigator.storage?.persist?.().catch(() => {});
      await showKey();
    }
    const shell = await loadShell();
    if (sink.kind === "stream") log("Saving through the browser's download manager.");
    if (sink.kind === "memory") log("This browser can't stream downloads; the APK is assembled in memory first.");
    const t0 = Date.now();
    let last = 0;
    const total = await buildApk({
      ...job, shell, signer,
      sink: (u8) => sink.write(u8),
      onProgress: (n) => {
        const now = Date.now();
        if (now - last < 250) return;
        last = now;
        prog.value = Math.min(n, job.size);
        status("build-status", `Building… ${mb(n)} of ${mb(job.size)}`);
      },
      log: (m) => { log(m); status("build-status", m); },
    });
    await sink.close();
    prog.value = prog.max;
    log(`Done: ${mb(total)} in ${Math.round((Date.now() - t0) / 1000)} s.`);
    status("build-status", sink.kind === "stream"
      ? `Done. ${OUTPUT_NAME} (${mb(total)}) is in your downloads; wait for the browser to show it as complete.`
      : `Done. ${OUTPUT_NAME} (${mb(total)}) is saved.`, "ok");
    enable("step-install", true);
  } catch (err) {
    sink?.abort(err);
    log("ERROR: " + err.message);
    status("build-status", err.message, "bad");
  } finally {
    wake?.release().catch(() => {});
    btn.disabled = false;
  }
};
