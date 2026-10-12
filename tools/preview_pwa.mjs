// Render the phone dashboard to a PNG so the layout can be eyeballed.
// Expects a static server for docs/ on 127.0.0.1:8765.
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';

const playwrightRoot =
  process.env.PLAYWRIGHT_CORE_ROOT ||
  'C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core';
const { chromium } = await import(pathToFileURL(`${playwrightRoot}/index.mjs`).href);

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

await page.goto('http://127.0.0.1:8765/', { waitUntil: 'networkidle' });
await page.waitForTimeout(1200);
await page.screenshot({ path: '.probe/pwa.png', fullPage: true });
console.log('cards:', await page.locator('.card').count());
console.log('meta:', await page.locator('#meta').innerText());
console.log('errors:', errors.length ? errors : 'none');

await browser.close();
