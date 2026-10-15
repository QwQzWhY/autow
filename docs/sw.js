// Bump the version here and the ?v= on the script/style tags whenever an asset
// changes. GitHub Pages caches assets for ten minutes, so without this a phone
// keeps running the previous version of a file.
const SHELL = 'autow-shell-v11';
const SHELL_FILES = [
  './',
  'index.html',
  'style.css?v=11',
  'app.js?v=11',
  'todo-view.js?v=11',
  'manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Todos must always be fresh; the app shell can come from cache.
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.endsWith('todos.json')) return;
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).catch(() => caches.match('index.html')))
  );
});
