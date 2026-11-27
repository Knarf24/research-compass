// Keeps the app working offline: files are served from the cache, then refreshed in the background.
const CACHE = "research-compass-v7";
const CORE = ["./", "index.html", "style.css", "main.js", "config.js", "compass.py", "manifest.webmanifest", "icon.svg",
  "pyodide/pyodide.js", "pyodide/pyodide.asm.js", "pyodide/pyodide.asm.wasm", "pyodide/python_stdlib.zip", "pyodide/pyodide-lock.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  // The weekly data: try the network first so new listings show right away; fall back to the cache offline.
  const path = new URL(e.request.url).pathname;
  if (path.includes("/data/") || path.endsWith("/config.js")) {
    e.respondWith(caches.open(CACHE).then((cache) => fetch(e.request).then((res) => { if (res.ok) cache.put(e.request, res.clone()); return res; })
      .catch(() => cache.match(e.request, { ignoreSearch: true }))));
    return;
  }
  e.respondWith(caches.open(CACHE).then(async (cache) => {
    const hit = await cache.match(e.request, { ignoreSearch: true });
    const fresh = fetch(e.request).then((res) => { if (res.ok) cache.put(e.request, res.clone()); return res; }).catch(() => hit);
    return hit || fresh;
  }));
});
