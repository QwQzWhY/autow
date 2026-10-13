// Render the board, then capture the three interactions the change touched:
// the new delete button, the tick drawing in, and the undo bar.
//
// Usage: node tools/verify_board.mjs <base-url>
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';

const playwrightRoot =
  process.env.PLAYWRIGHT_CORE_ROOT ||
  'C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core';
const { chromium } = await import(pathToFileURL(`${playwrightRoot}/index.mjs`).href);

const base = process.argv[2] || 'http://127.0.0.1:8780';
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

await page.locator('.card').first().screenshot({ path: '.probe/board_card.png' });

// Tick the first box and catch the animation before the card collapses.
await page.locator('.check').first().click();
await page.waitForTimeout(200);
await page.locator('.card').first().screenshot({ path: '.probe/board_tick.png' });
await page.waitForTimeout(900);

// Delete one and capture the undo bar (fixed to the viewport, so not fullPage).
await page.locator('.card__delete').first().click();
await page.waitForTimeout(300);
await page.screenshot({ path: '.probe/board_undo.png' });

console.log('cards now:', await page.locator('.card').count());
console.log('errors:', errors.length ? errors : 'none');
await browser.close();
