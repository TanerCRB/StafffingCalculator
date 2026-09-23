/* Render the favicon vector at browser-tab and home-screen sizes. */
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/Taner/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const svg = fs.readFileSync(path.join(root, 'assets/favicon.svg'), 'utf8');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 180, height: 180 }, deviceScaleFactor: 1 });
  await page.setContent('<!doctype html><html><head><style>html,body{margin:0;padding:0}svg{display:block}</style></head><body>' + svg + '</body></html>');
  const icon = page.locator('svg');
  await icon.evaluate((element) => { element.style.width = '32px'; element.style.height = '32px'; });
  await icon.screenshot({ path: path.join(root, 'assets/favicon-32.png') });
  await icon.evaluate((element) => { element.style.width = '180px'; element.style.height = '180px'; });
  await icon.screenshot({ path: path.join(root, 'assets/apple-touch-icon.png') });
  await browser.close();
  process.stdout.write('Exported 32px browser icon and 180px shortcut icon.\n');
})().catch((error) => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });
