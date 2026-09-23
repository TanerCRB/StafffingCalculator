/* Export the UI-15 product screen and focused dictionary panel artwork. */
const path = require('path');
const { pathToFileURL } = require('url');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/Taner/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const panels = [
  ['roles', '15-catalog-roles.png'],
  ['seniorities', '15-catalog-seniorities.png'],
  ['locations', '15-catalog-locations.png'],
  ['engagement-types', '15-catalog-engagement-types.png'],
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(path.join(root, 'index.html')).href + '#catalog');
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => document.body.classList.add('export-mode'));
  await page.screenshot({ path: path.join(root, 'screens/15-catalog.png'), fullPage: true });

  for (const [dimension, filename] of panels) {
    const panel = page.locator('[data-catalog-dimension="' + dimension + '"]');
    await panel.evaluate((element) => {
      element.classList.add('catalog-dictionary--focus');
      const title = element.querySelector('h2').textContent.toLowerCase();
      const fieldName = element.dataset.catalogDimension === 'seniorities'
        ? 'Seniority'
        : element.dataset.catalogDimension === 'engagement-types' ? 'Engagement type'
        : element.dataset.catalogDimension === 'roles' ? 'Role' : 'Location';
      const form = document.createElement('div');
      form.className = 'dimension-form';
      form.innerHTML = '<h3>Add an entry to the ' + title + ' dictionary</h3><div class="formfield"><label>' + fieldName + ' name<input type="text" placeholder="Enter ' + fieldName.toLowerCase() + ' name"></label></div><div class="actions"><button class="btn small" type="button">Cancel</button><button class="btn small primary" type="button">Save new entry</button></div>';
      element.append(form);
    });
    await panel.screenshot({ path: path.join(root, 'screens', filename) });
  }
  await browser.close();
  process.stdout.write('Exported UI-15 screen and four dictionary-panel illustrations.\n');
})().catch((error) => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });
