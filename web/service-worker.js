'use strict';

const CACHE = 'ii-shell-v0.4.0';
const SHELL = [
  '/',
  '/index.html',
  '/app.html',
  '/app.js',
  '/secure-store.js',
  '/offline.html',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(Promise.all([
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))),
    self.clients.claim(),
  ]));
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  const shellPath = SHELL.includes(url.pathname) || (url.pathname === '' && SHELL.includes('/'));
  if (request.mode === 'navigate' && !['/', '/index.html', '/app.html', '/offline.html'].includes(url.pathname)) return;
  if (request.mode !== 'navigate' && !shellPath) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          event.waitUntil(caches.open(CACHE).then((cache) => cache.put(request, copy)));
        }
        return response;
      })
      .catch(async () => (
        (await caches.match(request))
        || (url.pathname.startsWith('/app') ? await caches.match('/app.html') : await caches.match('/offline.html'))
      )));
    return;
  }
  event.respondWith(caches.match(request).then((cached) => cached || fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      event.waitUntil(caches.open(CACHE).then((cache) => cache.put(request, copy)));
    }
    return response;
  })));
});

self.addEventListener('sync', (event) => {
  if (event.tag !== 'ii-resume-job-poll') return;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
    for (const client of clients) client.postMessage({ type: 'II_RESUME_JOB_POLL' });
  }));
});

self.addEventListener('push', (event) => {
  let payload = { title: 'II job update', body: 'Open II to check your background job.', url: '/app.html' };
  try { payload = { ...payload, ...event.data.json() }; } catch { /* Use a non-sensitive generic message. */ }
  event.waitUntil(self.registration.showNotification(payload.title, {
    body: payload.body,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    tag: payload.tag || 'ii-job-status',
    data: { url: payload.url || '/app.html' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  let target = new URL('/app.html', self.location.origin);
  try {
    const requested = new URL(event.notification.data?.url || '/app.html', self.location.origin);
    if (requested.origin === self.location.origin && requested.pathname === '/app.html') target = requested;
  } catch { /* Keep the same-origin app target. */ }
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (clients) => {
    const existing = clients.find((client) => new URL(client.url).pathname === target.pathname);
    if (existing) {
      await existing.focus();
      existing.postMessage({ type: 'II_RESUME_JOB_POLL' });
      return;
    }
    await self.clients.openWindow(`${target.pathname}${target.search}`);
  }));
});
