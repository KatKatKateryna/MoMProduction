// Screenshots every figure page with the Playwright already inside the mcp/playwright
// image (its Chromium has WebGL, which MapLibre needs).
const { chromium } = require('playwright');
const fs = require('fs');
const WEB = '/work/web', OUT = '/out';
const BASE = 'http://127.0.0.1:8899/fig_page.html?fig=';
(async () => {
  const man = JSON.parse(fs.readFileSync(WEB + '/manifest.json', 'utf8'));
  const only = process.argv.slice(2);
  const b = await chromium.launch({ channel: 'chromium', args: ['--no-sandbox'] });
  const pg = await b.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1.5 });
  for (const f of man) {
    if (only.length && !only.includes(f.id)) continue;
    await pg.goto(BASE + f.id, { waitUntil: 'networkidle' });
    try { await pg.waitForFunction('window.FIG_READY === true', { timeout: 45000 }); }
    catch (e) { console.log('  idle timeout: ' + f.id); }
    await pg.waitForTimeout(900);
    await pg.screenshot({ path: `${OUT}/${f.id}.png` });
    console.log('shot ' + f.id);
  }
  await b.close();
})();
