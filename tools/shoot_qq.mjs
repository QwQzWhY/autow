// Screenshot the QQ setup page with a spotlight on a given element, so the
// instructions can point at something real instead of describing it.
//
// Usage: node tools/shoot_qq.mjs <base-url> <selector> <out.png> <label>
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';

const playwrightRoot =
  process.env.PLAYWRIGHT_CORE_ROOT ||
  'C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core';
const { chromium } = await import(pathToFileURL(`${playwrightRoot}/index.mjs`).href);

const [base = 'http://127.0.0.1:8770', selector = '#test', out = '.probe/shot.png'] =
  process.argv.slice(2);

mkdirSync('.probe', { recursive: true });

const browser = await chromium.launch({
  channel: 'chrome',
  headless: true,
  ignoreDefaultArgs: ['--enable-automation'],
});
const page = await browser.newPage({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
});
await page.goto(`${base}/qq.html`, { waitUntil: 'networkidle' });
await page.waitForTimeout(900);

const box = await page.evaluate((sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
}, selector);

if (!box) {
  console.log('selector not found:', selector);
  await browser.close();
  process.exit(1);
}

// Dim everything except the target, then drop a ring around it.
await page.evaluate((sel) => {
  const el = document.querySelector(sel);
  const r = el.getBoundingClientRect();
  const ring = document.createElement('div');
  Object.assign(ring.style, {
    position: 'absolute',
    left: `${r.left + window.scrollX - 7}px`,
    top: `${r.top + window.scrollY - 7}px`,
    width: `${r.width + 14}px`,
    height: `${r.height + 14}px`,
    border: '3px solid #e11d48',
    borderRadius: '14px',
    boxShadow: '0 0 0 9999px rgba(8, 10, 14, .45)',
    zIndex: 9999,
    pointerEvents: 'none',
  });
  document.body.appendChild(ring);
}, selector);

const pad = { x: 26, top: 120, bottom: 70 };
await page.screenshot({
  path: out,
  // The target is usually below the fold, so capture the whole page and crop.
  fullPage: true,
  clip: {
    x: Math.max(0, box.x - pad.x),
    y: Math.max(0, box.y - pad.top),
    width: Math.min(390, box.w + pad.x * 2),
    height: box.h + pad.top + pad.bottom,
  },
});

console.log(`${out} <- ${selector} @ (${Math.round(box.x)}, ${Math.round(box.y)})`);
await browser.close();
