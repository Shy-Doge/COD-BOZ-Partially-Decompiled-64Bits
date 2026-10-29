// Service worker that turns a stream produced by the page into a normal file download.
// The page registers a download (with a MessagePort), then opens dl/<id>/<name> in a hidden iframe;
// this worker answers that request with a streamed Response and pulls chunks from the page on demand,
// so the ~900 MB APK is never held in memory.

const downloads = new Map();

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type !== "register") return; // "ping" messages only keep the worker alive
  const port = e.ports[0];
  const stream = new ReadableStream(
    {
      pull(controller) {
        return new Promise((resolve) => {
          port.onmessage = (m) => {
            const x = m.data;
            if (x.done) controller.close();
            else if (x.error) controller.error(new Error(x.error));
            else controller.enqueue(new Uint8Array(x.chunk));
            resolve();
          };
          port.postMessage({ pull: true });
        });
      },
      cancel() { port.postMessage({ cancel: true }); },
    },
    new CountQueuingStrategy({ highWaterMark: 4 }),
  );
  downloads.set(d.id, { stream, name: d.name, size: d.size });
  port.postMessage({ ready: true });
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const m = url.pathname.match(/\/dl\/([\w-]+)\/[^/]*$/);
  if (!m) return;
  const dl = downloads.get(m[1]);
  if (!dl) return;
  downloads.delete(m[1]);
  const headers = {
    "Content-Type": "application/vnd.android.package-archive",
    "Content-Disposition": `attachment; filename="${dl.name.replace(/"/g, "")}"`,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  };
  e.respondWith(new Response(dl.stream, { headers }));
});
