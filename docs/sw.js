// Bump the version here and the ?v= on the script/style tags whenever an asset
// changes. GitHub Pages caches assets for ten minutes, so without this a phone
// keeps running the previous version of a file.
const SHELL = 'autow-shell-v18';
const SHELL_FILES = [
  './',
  'index.html',
  'style.css?v=18',
  'app.js?v=18',
  'todo-view.js?v=18',
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
  // 页面本身走网络优先：壳缓存里那份 index.html 可能指着上一版的 app.js，于是
  // 用户刷新多少次都停在旧版本上——这次就是这样，页脚一直显示 v12。
  // 带版本的静态资源仍然走缓存，离线也还能用。
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => caches.match('index.html')));
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).catch(() => caches.match('index.html')))
  );
});
