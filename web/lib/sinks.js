// Where the finished APK goes. Every sink takes ownership of the chunks it is given.
//  - "picker": desktop Chrome/Edge save dialog, written straight to disk
//  - "stream": service-worker download, works on Android Chrome/Firefox (low memory)
//  - "memory": last resort, keeps the whole APK in memory (desktop browsers without the above)

const CHUNK = 1 << 20;

// Groups small writes into 1 MB chunks that own their buffers (so they can be transferred).
function coalesce(inner) {
  let buf = new Uint8Array(CHUNK), fill = 0;
  return {
    kind: inner.kind,
    async write(u8) {
      let p = 0;
      while (p < u8.length) {
        const n = Math.min(CHUNK - fill, u8.length - p);
        buf.set(u8.subarray(p, p + n), fill);
        fill += n; p += n;
        if (fill === CHUNK) { const out = buf; buf = new Uint8Array(CHUNK); fill = 0; await inner.write(out); }
      }
    },
    async close() {
      if (fill) await inner.write(buf.slice(0, fill));
      fill = 0;
      await inner.close();
    },
    abort: (err) => inner.abort(err),
  };
}

async function pickerSink(name) {
  const handle = await window.showSaveFilePicker({
    suggestedName: name,
    types: [{ description: "Android app", accept: { "application/vnd.android.package-archive": [".apk"] } }],
  });
  const w = await handle.createWritable();
  return { kind: "picker", write: (u8) => w.write(u8), close: () => w.close(), abort: () => w.abort().catch(() => {}) };
}

async function streamSink(name) {
  const reg = await navigator.serviceWorker.register("sw.js", { scope: "./" });
  await navigator.serviceWorker.ready;
  const sw = reg.active;
  if (!sw) throw new Error("service worker not active");
  const id = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
  const ch = new MessageChannel();
  const queue = [];
  let wantPull = false, failed = null, readyResolve;
  const ready = new Promise((r) => (readyResolve = r));
  const send = (item) => {
    if (item.done) ch.port1.postMessage({ done: true });
    else if (item.error) ch.port1.postMessage({ error: item.error });
    else ch.port1.postMessage({ chunk: item.u8.buffer }, [item.u8.buffer]);
    item.resolve();
  };
  ch.port1.onmessage = (m) => {
    const x = m.data;
    if (x.ready) readyResolve();
    else if (x.pull) { if (queue.length) send(queue.shift()); else wantPull = true; }
    else if (x.cancel) {
      failed = new Error("The download was cancelled.");
      for (const it of queue.splice(0)) it.reject(failed);
    }
  };
  const push = (item) => new Promise((resolve, reject) => {
    if (failed) return reject(failed);
    Object.assign(item, { resolve, reject });
    if (wantPull) { wantPull = false; send(item); } else queue.push(item);
  });
  const ping = setInterval(() => sw.postMessage({ type: "ping" }), 10000);
  sw.postMessage({ type: "register", id, name }, [ch.port2]);
  await ready;
  const frame = document.createElement("iframe");
  frame.hidden = true;
  frame.src = `dl/${id}/${encodeURIComponent(name)}`;
  document.body.appendChild(frame);
  const cleanup = () => { clearInterval(ping); setTimeout(() => frame.remove(), 60000); };
  return {
    kind: "stream",
    write: (u8) => push({ u8 }),
    close: async () => { await push({ done: true }); cleanup(); },
    abort: (err) => { if (!failed) push({ error: String(err?.message || err) }).catch(() => {}); cleanup(); },
  };
}

function memorySink(name) {
  const parts = [];
  return {
    kind: "memory",
    write: async (u8) => { parts.push(u8); },
    close: async () => {
      const url = URL.createObjectURL(new Blob(parts, { type: "application/vnd.android.package-archive" }));
      parts.length = 0;
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 120000);
    },
    abort: () => { parts.length = 0; },
  };
}

const isMobile = () => /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);

// Must be called directly from a click handler (the save dialog needs the user gesture).
export async function openSink(name) {
  if (window.showSaveFilePicker && !isMobile()) return coalesce(await pickerSink(name)); // a cancelled dialog throws
  if ("serviceWorker" in navigator && window.isSecureContext) {
    try { return coalesce(await streamSink(name)); } catch (e) { console.warn("stream download unavailable", e); }
  }
  return coalesce(memorySink(name));
}

// Registers the worker early so it is active by the time the user saves.
export function prepareDownloads() {
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
}
