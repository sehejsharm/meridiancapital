/*
 * Meridian's service worker. Three jobs, each deliberately narrow:
 *
 *  1. Show an alert the server pushed (trade entered or exited, kill switch, crash).
 *  2. Open at once on a weak network: the build's own hashed files are kept, so a
 *     page needs only its HTML from the network.
 *  3. With no network at all, show the offline page (which marks its figures old)
 *     instead of the browser's error.
 *
 * It never keeps a page or an API answer: a trading desk must not show yesterday's
 * position as if it were live. Pages and /api always go to the network.
 */
const VERSION = "v1";
const STATIC = `meridian-static-${VERSION}`;
const OFFLINE = "/offline";

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC);
    try {
      const res = await fetch(OFFLINE, { cache: "reload" });
      if (res.ok) {
        const html = await res.clone().text();
        await cache.put(OFFLINE, res);
        const files = [...new Set(html.match(/\/_next\/static\/[^"'\\)\s]+/g) || [])];
        await Promise.all(files.map((f) => cache.add(f).catch(() => undefined)));
      }
      await cache.addAll(["/icons/icon-192.png"]).catch(() => undefined);
    } catch {
      /* installed without the offline page; the next visit tries again */
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith("meridian-") && n !== STATIC).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // The build's own files carry a hash in their name: a kept copy can never be out of date.
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith((async () => {
      const cache = await caches.open(STATIC);
      const hit = await cache.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone()).catch(() => undefined);
      return res;
    })());
    return;
  }

  // Pages: always the network. Only when there is none, the offline page.
  if (req.mode === "navigate") {
    event.respondWith((async () => {
      try {
        return await fetch(req);
      } catch {
        const cache = await caches.open(STATIC);
        return (await cache.match(OFFLINE)) || Response.error();
      }
    })());
  }
  // Everything else (the API above all) is left to the browser untouched.
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "Meridian Capital", body: event.data ? event.data.text() : "" };
  }
  const urgent = data.level === "critical" || data.level === "warn";
  event.waitUntil(self.registration.showNotification(data.title || "Meridian Capital", {
    body: data.body || "",
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    tag: data.kind === "test" ? "test" : `${data.kind || "alert"}-${data.ts || Date.now()}`,
    requireInteraction: data.level === "critical",
    renotify: urgent,
    data: { url: data.kind === "crash" || data.level === "critical" ? "/controls" : "/today" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/today";
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of open) {
      if ("focus" in client) {
        await client.focus();
        if ("navigate" in client) await client.navigate(target).catch(() => undefined);
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});
