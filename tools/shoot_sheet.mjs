// Screenshot the board with the "new todo" sheet open, in both modes.
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';

const playwrightRoot =
  process.env.PLAYWRIGHT_CORE_ROOT ||
  'C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core';
const { chromium } = await import(pathToFileURL(`${playwrightRoot}/index.mjs`).href);

const base = process.argv[2] || 'http://127.0.0.1:8810';
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

const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(`${base}/`, { waitUntil: 'networkidle' });
await page.waitForTimeout(900);
await page.locator('#fab').click();
await page.waitForTimeout(300);
await page.screenshot({ path: '.probe/sheet_note.png' });

await page.locator('#compose-mode .seg__item[data-mode="manual"]').click();
await page.waitForTimeout(200);
await page.locator('#manual-title').fill('去图书馆还书');
await page.locator('#manual-due').fill('2026-10-20T18:00');
await page.locator('#manual-priority').selectOption('high');
await page.waitForTimeout(150);
await page.screenshot({ path: '.probe/sheet_manual.png' });

console.log('errors:', errors.length ? errors : 'none');
await browser.close();
