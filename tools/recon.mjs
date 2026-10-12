// Site reconnaissance for the LZU notifier project.
//
// The LZU sites sit behind a JS-challenge WAF: a bare HTTP client gets a 412,
// and headless Chrome is rejected unless the UA stops advertising "HeadlessChrome"
// and the `--enable-automation` flag is dropped. With those two fixed, the real
// Nuxt-rendered page comes through.
//
// Usage: node tools/recon.mjs <url> [<url> ...] [--text]

import { writeFileSync, mkdirSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const playwrightRoot =
  process.env.PLAYWRIGHT_CORE_ROOT ||
  'C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core';
const { chromium } = await import(pathToFileURL(`${playwrightRoot}/index.mjs`).href);

export const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';

export async function launchStealth(headless = true) {
  const browser = await chromium.launch({
    channel: 'chrome',
    headless,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--disable-blink-features=AutomationControlled', '--no-first-run', '--no-default-browser-check'],
  });
  const context = await browser.newContext({
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    viewport: { width: 1440, height: 900 },
    userAgent: UA,
  });
  return { browser, context };
}

const args = process.argv.slice(2);
const urls = args.filter((a) => !a.startsWith('--'));
const showText = args.includes('--text');
if (urls.length === 0) urls.push('https://ai.lzu.edu.cn/');

mkdirSync('.probe', { recursive: true });

const { browser, context } = await launchStealth(true);
const page = await context.newPage();

const seen = [];
page.on('response', async (res) => {
  const req = res.request();
  const type = req.resourceType();
  if (type !== 'xhr' && type !== 'fetch' && type !== 'document') return;
  const url = res.url();
  if (/\.(css|js|png|jpe?g|svg|ico|woff2?|gif|webp)(\?|$)/i.test(url)) return;
  const entry = { method: req.method(), status: res.status(), type, url, postData: req.postData() };
  try {
    const ct = res.headers()['content-type'] || '';
    if (/json|text|javascript/.test(ct)) {
      const body = await res.text();
      entry.contentType = ct;
      entry.body = body.slice(0, 4000);
      entry.bodyLength = body.length;
    }
  } catch {}
  seen.push(entry);
});

for (const url of urls) {
  console.log('\n################ ' + url);
  seen.length = 0;
  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(6000);
    console.log('# document status:', resp && resp.status(), '| title:', JSON.stringify(await page.title()));
  } catch (e) {
    console.log('# navigation error:', e.message.split('\n')[0]);
    continue;
  }

  const slug = url.replace(/[^a-z0-9]+/gi, '_').slice(0, 60);
  writeFileSync(`.probe/${slug}.html`, await page.content(), 'utf8');

  if (showText) {
    const text = await page.evaluate(() => document.body.innerText || '');
    writeFileSync(`.probe/${slug}.txt`, text, 'utf8');
    console.log('# visible text (2000 chars):');
    console.log(text.slice(0, 2000));
  }

  console.log('# network (documents + xhr + fetch):');
  for (const e of seen) {
    console.log(`  [${e.status}] ${e.type} ${e.method} ${e.url}${e.bodyLength ? ` (${e.bodyLength}b)` : ''}`);
    if (e.body && /json/i.test(e.contentType || '')) {
      console.log('      ' + e.body.replace(/\s+/g, ' ').slice(0, 700));
    }
  }
}

await browser.close();
