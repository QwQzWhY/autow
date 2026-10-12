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

const target = process.argv[2] || '/';
const out = process.argv[3] || '.probe/pwa.png';

const page = await browser.newPage({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
});

const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('pageerror', (e) => errors.push(String(e)));

// The setup page needs a repository and token before it renders its config,
// so prime localStorage before the app script runs.
await page.addInitScript(() => {
  localStorage.setItem('autow.qq.repo', 'zhangsan/autow');
  localStorage.setItem('autow.qq.token', 'github_pat_EXAMPLE');
});

await page.goto('http://127.0.0.1:8765' + target, { waitUntil: 'networkidle' });
await page.waitForTimeout(1200);
await page.screenshot({ path: out, fullPage: true });
console.log('url:', page.url());
console.log('meta:', await page.locator('#meta').innerText());
console.log('errors:', errors.length ? errors : 'none');

await browser.close();
