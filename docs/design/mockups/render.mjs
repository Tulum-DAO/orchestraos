import puppeteer from 'puppeteer-core';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CHROME = process.env.CHROME_BIN || 'google-chrome';

const ids = ['S1','S2','S3','S4','S5','S6'];

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-gpu'],
    headless: 'new',
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 2600, height: 1000, deviceScaleFactor: 2 });
  await page.goto('file://' + path.join(__dirname, 'mockup.html'), { waitUntil: 'networkidle0' });
  await new Promise(r => setTimeout(r, 300));

  for (const id of ids) {
    const el = await page.$('#' + id);
    await el.screenshot({ path: path.join(__dirname, id + '.png') });
    console.log('wrote', id + '.png');
  }

  // contact sheet: screenshot the whole body
  await page.setViewport({ width: 2600, height: 900, deviceScaleFactor: 1 });
  const body = await page.$('body');
  await body.screenshot({ path: path.join(__dirname, 'all.png') });
  console.log('wrote all.png');

  await browser.close();
})();
