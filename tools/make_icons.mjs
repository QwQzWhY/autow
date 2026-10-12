// Render docs/icon.svg to the PNG sizes the web app manifest asks for.
// Uses the installed Chrome, so no image library is needed.
import { pathToFileURL } from 'node:url';
import { readFileSync, writeFileSync } from 'node:fs';

const playwrightRoot =
  process.env.PLAYWRIGHT_CORE_ROOT ||
  'C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core';
const { chromium } = await import(pathToFileURL(`${playwrightRoot}/index.mjs`).href);

const svg = readFileSync('docs/icon.svg', 'utf8');
const browser = await chromium.launch({
  channel: 'chrome',
  headless: true,
  ignoreDefaultArgs: ['--enable-automation'],
});

for (const size of [192, 512]) {
  const page = await browser.newPage({
    viewport: { width: size, height: size },
    deviceScaleFactor: 1,
  });
  await page.setContent(
    `<html><body style="margin:0;background:transparent">${svg
      .replace('<svg', `<svg width="${size}" height="${size}"`)
      .replace('viewBox="0 0 192 192"', 'viewBox="0 0 192 192"')}</body></html>`
  );
  const buffer = await page.screenshot({ omitBackground: true });
  writeFileSync(`docs/icon-${size}.png`, buffer);
  console.log(`docs/icon-${size}.png ${buffer.length} bytes`);
  await page.close();
}

await browser.close();
