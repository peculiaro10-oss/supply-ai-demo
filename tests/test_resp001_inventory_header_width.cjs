// RESP-001: with the sidebar visible (>= 768 px), the Stock Inventory header
// must keep Add Product (and Export) inside #inventory-section, whose
// overflow:hidden previously clipped the button to "+ Add Pro" at 768-820 px.
//
// Static part (always runs): the 768-1100 px rule lets the header and the
// filter row wrap. Browser part: serves frontend/ from a throwaway local
// server, loads it in headless Chromium (no backend: the connectivity gate
// overlays the page, the layout underneath is what is measured) and checks
// every width in the sidebar-visible range plus the four standard layouts.
// The browser part is skipped, loudly, when Playwright is not installed.
//   node tests/test_resp001_inventory_header_width.cjs
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const css = fs.readFileSync(path.join(frontend, 'css', 'base.css'), 'utf8');

const tablet = css.match(/@media \(min-width:768px\) and \(max-width:1100px\)\{([\s\S]*?)\n        \}/);
assert(tablet, 'the 768-1100 px rule exists');
assert(/\.inventory-header-stack\{[^}]*flex-wrap:wrap/.test(tablet[1]), 'the inventory header wraps in the sidebar-visible tablet range');
assert(/\.inventory-filter-row\{[^}]*flex-wrap:wrap[^}]*max-width:100%|\.inventory-filter-row\{[^}]*max-width:100%[^}]*flex-wrap:wrap/.test(tablet[1]), 'the filter row wraps and never exceeds the card');
assert(!/\.inventory-filter-row select\{[^}]*min-width:220px/.test(tablet[1]), 'the tablet rule does not reimpose a 220px select minimum');
console.log('PASS static: RESP-001 tablet header rule wraps');

let playwright = null;
for (const candidate of ['playwright', 'playwright-core']) {
  try { playwright = require(candidate); break; } catch (_) { /* try next */ }
}
if (!playwright) {
  console.log('SKIP browser: Playwright is not installed (npm i -g playwright, or set NODE_PATH); static checks passed');
  process.exit(0);
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.ico': 'image/x-icon' };
function serve() {
  const server = http.createServer((req, res) => {
    let rel = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (rel === '/') rel = '/index.html';
    const file = path.normalize(path.join(frontend, rel));
    if (!file.startsWith(frontend) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(503, { 'content-type': 'application/json' });
      res.end('{"detail":"no backend in this test"}');
      return;
    }
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

// Every sidebar-visible width from the breakpoint to the end of the tablet
// rule, the widths named in the finding, and the four standard layouts.
const widths = [375, 767, 1024, 1100, 1101, 1280, 1366, 1440];
for (let w = 768; w <= 900; w += 4) widths.push(w);
widths.push(799, 800, 801, 819, 820, 950, 1000, 1050);
const unique = [...new Set(widths)].sort((a, b) => a - b);

(async () => {
  const server = await serve();
  const origin = `http://127.0.0.1:${server.address().port}`;
  const launchOptions = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {};
  const browser = await playwright.chromium.launch(launchOptions);
  const failures = [];
  try {
    const context = await browser.newContext();
    await context.route('**/*', route => (route.request().url().startsWith(origin) ? route.continue() : route.abort()));
    const page = await context.newPage();
    await page.goto(origin + '/', { waitUntil: 'load' });
    await page.waitForSelector('#inventory-section');
    for (const width of unique) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(60);
      const m = await page.evaluate(() => {
        const section = document.getElementById('inventory-section');
        const row = section.querySelector('.inventory-filter-row');
        const add = [...row.querySelectorAll(':scope > button')].find(b => /Add Product/.test(b.textContent));
        const exportBtn = row.querySelector('.export-menu-toggle');
        const select = document.getElementById('warehouse-filter');
        const title = section.querySelector('.inventory-main-header');
        const status = section.querySelector('.inventory-status-row');
        const aside = document.querySelector('aside');
        const r = el => el.getBoundingClientRect();
        const overlaps = (a, b) => !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top);
        const s = r(section);
        const style = getComputedStyle(section);
        const innerRight = s.right - parseFloat(style.borderRightWidth);
        const innerLeft = s.left + parseFloat(style.borderLeftWidth);
        const inside = el => r(el).left >= innerLeft - 0.5 && r(el).right <= innerRight + 0.5;
        return {
          sidebarVisible: !!aside && getComputedStyle(aside).display !== 'none' && r(aside).width > 0,
          addInside: inside(add), exportInside: inside(exportBtn), selectInside: inside(select),
          sectionOverflow: section.scrollWidth - section.clientWidth,
          pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
          titleOverlap: overlaps(r(title), r(row)), statusOverlap: overlaps(r(status), r(row)),
          addRight: Math.round(r(add).right), sectionRight: Math.round(innerRight),
        };
      });
      const problems = [];
      if (width >= 768 && !m.sidebarVisible) problems.push('sidebar not visible');
      if (!m.addInside) problems.push(`Add Product clipped (right ${m.addRight} > card ${m.sectionRight})`);
      if (!m.exportInside) problems.push('Export outside the card');
      if (!m.selectInside) problems.push('warehouse filter outside the card');
      if (m.sectionOverflow > 0) problems.push(`card content overflows by ${m.sectionOverflow}px`);
      if (m.pageOverflow > 0) problems.push(`page scrolls sideways by ${m.pageOverflow}px`);
      if (m.titleOverlap) problems.push('title overlaps the filter row');
      if (m.statusOverlap) problems.push('status filters overlap the filter row');
      if (problems.length) failures.push(`${width}px: ${problems.join('; ')}`);
    }
  } finally {
    await browser.close();
    server.close();
  }
  if (failures.length) {
    console.error('FAIL RESP-001 inventory header:\n  ' + failures.join('\n  '));
    process.exit(1);
  }
  console.log(`PASS browser: Add Product, Export and the warehouse filter stay inside the card at ${unique.length} widths (${unique[0]}-${unique[unique.length - 1]} px, sidebar visible from 768)`);
})().catch(err => { console.error(err); process.exit(1); });
