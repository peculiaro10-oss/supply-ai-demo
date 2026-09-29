// POLISH batch (local, 2026-09-29) — six scoped UI / input changes:
//  1. the inventory search lives in the Stock Inventory card, not the header;
//  2. the startup screen says "Starting session…";
//  3. the role badge sits right after the welcome name when there is room;
//  4. the welcome section stays one boxed card at every width;
//  5. a background refresh never turns the Low Stock figure back into
//     "Loading…" (and a failed refresh keeps the last good figure);
//  6. phone fields format and validate for the selected country and send
//     canonical E.164 (no doubled country code, legacy values kept).
// Static + library checks always run; the browser part drives the real app in
// headless Chromium (fetch stubbed, no backend) and is skipped, loudly, without
// Playwright.
//   node tests/test_ui_input_polish.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
const html = read('frontend/index.html');
const app = read('frontend/js/app.js');
const css = read('frontend/css/base.css');
const catalog = JSON.parse(read('i18n/launch_catalog.json'));
const results = [];
const check = (name, fn) => { fn(); results.push(name); };

// ---------------------------------------------------------------- static
check('1. no search box in the global header; the inventory search is inside the Stock Inventory card', () => {
    const header = html.slice(html.indexOf('<header class="h-16'), html.indexOf('</header>'));
    assert.doesNotMatch(header, /<input/);
    assert.doesNotMatch(html, /header-global-search/);
    const card = html.slice(html.indexOf('id="inventory-section"'), html.indexOf('<!-- FULL CONTENT-AREA INVENTORY TABLE -->'));
    assert.match(card, /<div id="inventory-search-wrap" hidden class="hidden inventory-search-row">/);
    assert.match(card, /<input type="search" id="inventory-search" oninput="filterInventoryByWarehouse\(\)"[^>]*aria-label="Search inventory"/);
    assert.equal((html.match(/id="inventory-search"/g) || []).length, 1, 'one inventory-search input (the old hidden mirror is gone)');
    assert.match(read('scripts/verify-native-bundle.js'), /id="inventory-search-wrap"/);
});
check('1. search stays signed-in only and is cleared on sign-out', () => {
    const fn = app.slice(app.indexOf('function updateGuestHeaderState()'), app.indexOf('function renderMobileNav()'));
    assert.match(fn, /getElementById\('inventory-search-wrap'\)/);
    assert.match(fn, /searchInput\.value = ''; filterInventoryByWarehouse\(\);/);
});
check('2. startup screen says "Starting session…" (translated); the old copy is gone', () => {
    assert.match(html, /<span class="cauldra-boot-text">Starting session…<\/span>/);
    assert.doesNotMatch(html, /Restoring your session/);
    assert.equal((catalog['Starting session…'] || []).filter(Boolean).length, 4);
});
check('3/4. below 768px the welcome card keeps its box and the badge is no longer pinned to the far edge', () => {
    assert.doesNotMatch(css, /#dynamic-welcome-banner\{background:transparent/);
    assert.doesNotMatch(css, /#welcome-role-badge\{position:absolute/);
    assert.match(css, /#welcome-heading-text\{min-width:0;overflow-wrap:anywhere;\}/);
});
check('5. Low Stock: no reset on every refresh; newest response wins; failure keeps the last figure', () => {
    const refresh = app.slice(app.indexOf('async function refreshDashboardLocationScopedCards()'), app.indexOf('async function loadDashboardLowStockCount()'));
    assert.match(refresh, /if \(dashboardLowStockScope !== \(selectedBusinessDayLocationId \|\| null\)\) \{\s*dashboardLowStockReady = false;/);
    const load = app.slice(app.indexOf('async function loadDashboardLowStockCount()'), app.indexOf('async function loadInventoryView()'));
    assert.doesNotMatch(load, /^\s*dashboardLowStockReady = false;/m);
    assert.match(load, /if \(!dashboardLowStockReady\) dashboardLowStockCount = 0;/);
});
check('6. new phone messages are catalogued for fr/es/ar/pt', () => {
    for (const s of ['This phone number is too short for the selected country.', 'This phone number is too long for the selected country.',
                     'Search products, SKU or category…', 'Search inventory']) {
        assert.equal((catalog[s] || []).filter(Boolean).length, 4, s);
    }
});
check('6. registration sends E.164, never "<prefix> <typed>"', () => {
    assert.doesNotMatch(app, /`\$\{phonePrefix\} \$\{/);
    assert.match(app, /const phone = phoneE164ForSubmit\(phoneNumberRaw, window\.selectedBusinessContext\);/);
    assert.match(app, /phoneE164ForSubmit\(ownerPhoneRaw, window\.selectedBusinessContext\)/);
});

// ---------------------------------------------------------------- phone library (the app's own helpers + vendored libphonenumber-js)
const phoneSandbox = { console };
phoneSandbox.globalThis = phoneSandbox; phoneSandbox.self = phoneSandbox; phoneSandbox.window = phoneSandbox;
vm.createContext(phoneSandbox);
vm.runInContext(read('frontend/assets/vendor/libphonenumberjs1.11.15.min.js'), phoneSandbox);
// phoneTextForParse … phoneProblemKey, resolvePhoneRegionIso2, sanitizePhoneText, countPhoneDigits, caretAfterDigits.
const helpers = app.slice(app.indexOf('        function phoneTextForParse('), app.indexOf('        function formatPhoneAsTyped('));
vm.runInContext(`var libphonenumber = globalThis.libphonenumber;\n${helpers}\nglobalThis.P = { phoneTextForParse, phoneE164ForSubmit, formatPhoneForDisplay, phoneProblemKey, sanitizePhoneText };`, phoneSandbox);
const P = phoneSandbox.P;
// Example numbers from Google's metadata (Python phonenumbers.example_number_for_type, the backend's library).
const EXAMPLES = {
    NG: { mobile: ['0802 123 4567', '+2348021234567'], fixed: ['02033 12 3456', '+2342033123456'] },
    US: { mobile: ['(201) 555-0123', '+12015550123'], fixed: ['(201) 555-0123', '+12015550123'] },
    GB: { mobile: ['07400 123456', '+447400123456'], fixed: ['0121 234 5678', '+441212345678'] },
    FR: { mobile: ['06 12 34 56 78', '+33612345678'], fixed: ['01 23 45 67 89', '+33123456789'] },
    ES: { mobile: ['612 34 56 78', '+34612345678'], fixed: ['810 12 34 56', '+34810123456'] },
    PT: { mobile: ['912 345 678', '+351912345678'], fixed: ['21 234 5678', '+351212345678'] },
};
check('6. each country: national digits -> E.164; saved E.164 -> national display; valid', () => {
    for (const [iso, kinds] of Object.entries(EXAMPLES)) {
        for (const [national, e164] of Object.values(kinds)) {
            const digits = national.replace(/\D/g, '');
            assert.equal(P.phoneE164ForSubmit(digits, iso), e164, `${iso} ${digits}`);
            assert.equal(P.phoneE164ForSubmit(national, { country_code: iso }), e164, `${iso} formatted ${national}`);
            assert.equal(P.formatPhoneForDisplay(e164, iso), national, `${iso} display ${e164}`);
            assert.equal(P.phoneProblemKey(national, iso), null, `${iso} ${national} valid`);
        }
    }
});
check('6. Nigeria: domestic 0 handled; +234 never keeps the extra 0; lengths not a global rule', () => {
    assert.equal(P.phoneE164ForSubmit('08031234567', 'NG'), '+2348031234567');
    assert.equal(P.phoneE164ForSubmit('+234 0803 123 4567', 'NG'), '+2348031234567');
    assert.equal(P.phoneE164ForSubmit('+2348031234567', 'NG'), '+2348031234567');
    assert.equal(P.phoneE164ForSubmit('0023408031234567', 'NG'), '+2348031234567', '00 international prefix');
    assert.equal(P.phoneProblemKey('02033 12 3456', 'NG'), null, 'an 11-digit fixed line and a 11-digit mobile are both valid');
    assert.equal(P.phoneProblemKey('0803 123', 'NG'), 'common.phoneTooShortForCountry');
    assert.equal(P.phoneProblemKey('0803 123 4567 8901 23', 'NG'), 'common.phoneTooLongForCountry');
    assert.equal(P.phoneProblemKey('0100 000 0000', 'NG'), 'common.invalidPhoneForCountry', 'impossible number (right length, invalid pattern)');
});
check('6. international paste keeps its own code — no duplicated prefix', () => {
    assert.equal(P.phoneE164ForSubmit('+44 7400 123456', 'NG'), '+447400123456');
    assert.equal(P.phoneE164ForSubmit('0044 7400 123456', 'NG'), '+447400123456');
    assert.equal(P.formatPhoneForDisplay('+447400123456', 'NG'), '+44 7400 123456', 'another country is shown internationally');
    assert.equal(P.phoneE164ForSubmit('+234 +44 7400 123456', 'NG'), '+234 +44 7400 123456', 'malformed: sent unchanged so the backend rejects it, never rewritten');
});
check('6. too short / too long per country metadata; US area code rules', () => {
    assert.equal(P.phoneProblemKey('201555', 'US'), 'common.phoneTooShortForCountry');
    assert.equal(P.phoneProblemKey('2015550123999', 'US'), 'common.phoneTooLongForCountry');
    assert.equal(P.phoneProblemKey('1115550123', 'US'), 'common.invalidPhoneForCountry');
    assert.equal(P.phoneProblemKey('06 12 34 56', 'FR'), 'common.phoneTooShortForCountry');
    assert.equal(P.phoneProblemKey('912 345 6789 00', 'PT'), 'common.phoneTooLongForCountry');
});
check('6. legacy values are shown as stored when they cannot be parsed; malformed text is cleaned on entry', () => {
    assert.equal(P.formatPhoneForDisplay('call the shop', 'NG'), 'call the shop');
    assert.equal(P.formatPhoneForDisplay('08031234567', 'NG'), '0803 123 4567', 'old local digits');
    assert.equal(P.formatPhoneForDisplay('+234 803-123-4567', 'NG'), '0803 123 4567', 'old formatted text');
    assert.equal(P.sanitizePhoneText('0803abc123#4567'), '08031234567');
    assert.equal(P.sanitizePhoneText('00 44 7400'), '+44 7400');
});
console.log(`PASS static + library ${results.length} checks`);

// ---------------------------------------------------------------- browser
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
            res.writeHead(503, { 'content-type': 'application/json' }); res.end('{"detail":"no backend in this test"}'); return;
        }
        res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
        fs.createReadStream(file).pipe(res);
    });
    return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}
const OUT = process.env.POLISH_SHOTS || '';

(async () => {
    const server = await serve();
    const origin = `http://127.0.0.1:${server.address().port}`;
    const browser = await playwright.chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {});
    const failures = [];
    const expect = (name, cond, detail) => { if (cond) results.push(name); else failures.push(`${name}\n      ${JSON.stringify(detail).slice(0, 900)}`); };
    async function openApp(width, height, lang = 'en') {
        const context = await browser.newContext({ viewport: { width, height }, timezoneId: 'Africa/Lagos', locale: 'en-GB', serviceWorkers: 'block' });
        await context.route('**/*', route => (route.request().url().startsWith(origin) ? route.continue() : route.abort()));
        const page = await context.newPage();
        await page.goto(origin + '/', { waitUntil: 'load' });
        await page.waitForFunction(() => typeof updateWelcomeBanner === 'function' && typeof wireCountryAwarePhoneInput === 'function' && !!window.CauldraI18n);
        await page.waitForFunction(() => !document.documentElement.classList.contains('cauldra-auth-booting'), null, { timeout: 30000 });
        await page.waitForTimeout(1500);
        await page.evaluate((lang) => {
            document.querySelectorAll('dialog[open]').forEach(d => { d.close(); d.remove(); });
            setLanguage(lang, { persist: false });
        }, lang);
        return { context, page };
    }
    async function signIn(page, firstname) {
        await page.evaluate((firstname) => {
            window.__lowFetch = null;
            window.fetch = async (url) => {
                if (String(url).includes('/products/inventory-summary') && window.__lowFetch) return window.__lowFetch();
                return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
            };
            authToken = 'test-token';
            currentUserProfile = { id: 1, username: 'qa', role: 'admin', firstname, position: 'Admin' };
            businessProfile = { id: 1, business_id: 1, business_code: 'QA-0001-01', company_name: 'QA Polish Ltd', country: 'Nigeria', country_code: 'NG', phone_country_code: '+234', language: 'en', timezone: 'Africa/Lagos', currency: 'NGN (₦)' };
            updateWelcomeBanner(); updateGuestHeaderState();
            document.querySelectorAll('dialog[open]').forEach(d => { d.close(); d.remove(); });
        }, firstname);
        await page.waitForTimeout(300);
    }
    const layout = page => page.evaluate(() => {
        const r = id => { const e = document.getElementById(id); if (!e) return null; const b = e.getBoundingClientRect(); return { x: b.left, y: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height }; };
        const banner = document.getElementById('dynamic-welcome-banner'); const bs = getComputedStyle(banner);
        const wrap = document.getElementById('inventory-search-wrap');
        return {
            heading: r('welcome-heading-text'), badge: r('welcome-role-badge'), bannerBox: r('dynamic-welcome-banner'),
            card: { bg: bs.backgroundImage !== 'none' || bs.backgroundColor !== 'rgba(0, 0, 0, 0)', border: parseFloat(bs.borderTopWidth), radius: parseFloat(bs.borderTopLeftRadius), padding: parseFloat(bs.paddingLeft) },
            headerInputs: document.querySelectorAll('header input').length,
            search: { visible: !!wrap && !wrap.hidden && getComputedStyle(wrap).display !== 'none', inCard: !!wrap && !!wrap.closest('#inventory-section'), box: r('inventory-search'), cardBox: r('inventory-section') },
            overflow: document.documentElement.scrollWidth - innerWidth,
            dir: document.documentElement.dir || 'ltr',
            bootText: document.querySelector('.cauldra-boot-text')?.textContent,
        };
    });
    try {
        // ---- layouts: 1, 3, 4 at every supported width (+ iPad-like), English and Arabic
        for (const [w, h, lang] of [[360, 780, 'en'], [390, 844, 'en'], [412, 915, 'en'], [600, 960, 'en'], [700, 1000, 'en'], [768, 1024, 'en'], [820, 1180, 'en'], [1024, 768, 'en'], [1366, 768, 'en'], [390, 844, 'ar'], [1024, 768, 'ar']]) {
            const { context, page } = await openApp(w, h, lang);
            for (const name of ['Qa', 'Maximiliana-Konstantinopolous Alexandria-Wellington']) {
                await signIn(page, name);
                const L = await layout(page);
                const tag = `${w}px ${lang} ${name.length > 10 ? 'long name' : 'short name'}`;
                const sameLine = Math.abs((L.badge.y + L.badge.h / 2) - (L.heading.y + L.heading.h / 2)) < L.heading.h / 2 + 4 && L.badge.y < L.heading.b;
                const gap = lang === 'ar' ? L.heading.x - L.badge.r : L.badge.x - L.heading.r;
                const wrappedUnder = L.badge.y >= L.heading.b - 2 && (lang === 'ar' ? Math.abs(L.badge.r - L.heading.r) < 4 : Math.abs(L.badge.x - L.heading.x) < 4);
                expect(`${tag}: badge right after the name (≤ 16px gap) or wrapped under it — never pushed to the far edge`, (sameLine && gap >= 0 && gap <= 16) || wrappedUnder, L);
                expect(`${tag}: welcome is a boxed card (background, border, radius, padding)`, L.card.bg && L.card.border >= 1 && L.card.radius >= 12 && L.card.padding >= 12, L.card);
                expect(`${tag}: badge and name inside the card`, L.badge.r <= L.bannerBox.r + 0.5 && L.heading.r <= L.bannerBox.r + 0.5 && L.badge.x >= L.bannerBox.x - 0.5, L);
                expect(`${tag}: header has no search; search is inside the inventory card and fits`, L.headerInputs === 0 && L.search.visible && L.search.inCard
                    && L.search.box.x >= L.search.cardBox.x && L.search.box.r <= L.search.cardBox.r + 0.5, L.search);
                expect(`${tag}: no horizontal scroll`, L.overflow <= 0, L.overflow);
            }
            if (OUT) await page.screenshot({ path: path.join(OUT, `polish_${w}_${lang}.png`), fullPage: false });
            await context.close();
        }

        // ---- 1: the moved search filters exactly as before, clears, and keeps keyboard/focus
        {
            const { context, page } = await openApp(1024, 768);
            await signIn(page, 'Qa');
            await page.evaluate(() => {
                const products = [
                    { id: 1, name: 'Basmati Rice 5kg', sku: 'RICE-5', category: 'Grains', quantity: 40, min_stock_level: 5, warehouse: 'Main Central Warehouse', retail_price: 9000, wholesale_price: 8000, cost_price: 7000 },
                    { id: 2, name: 'Palm Oil 1L', sku: 'OIL-1', category: 'Oils', quantity: 12, min_stock_level: 5, warehouse: 'Main Central Warehouse', retail_price: 2500, wholesale_price: 2200, cost_price: 1900 },
                ];
                window.fetch = async (url) => {
                    const u = String(url);
                    if (u.includes('/products/inventory-summary')) return new Response('{"healthy":2,"low":0,"out":0}', { status: 200, headers: { 'content-type': 'application/json' } });
                    if (u.includes('/products/')) return new Response(JSON.stringify(products), { status: 200, headers: { 'content-type': 'application/json' } });
                    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
                };
            });
            const rows = () => page.evaluate(() => ({ focused: document.activeElement?.id, value: document.getElementById('inventory-search').value,
                names: [...document.querySelectorAll('.inventory-table-container tbody tr')].map(tr => tr.innerText).filter(t => /Rice|Palm/.test(t)).map(t => (t.match(/Basmati Rice 5kg|Palm Oil 1L/) || [''])[0]) }));
            await page.click('#inventory-search');
            await page.keyboard.type('rice');
            await page.waitForTimeout(500);
            const typed = await rows();
            expect('search: typing filters the Stock Inventory rows by name/SKU/category, focus stays in the field', typed.focused === 'inventory-search' && typed.names.join() === 'Basmati Rice 5kg', typed);
            await page.fill('#inventory-search', 'oil-1');
            await page.waitForTimeout(500);
            const bySku = await rows();
            expect('search: SKU match works as before', bySku.names.join() === 'Palm Oil 1L', bySku);
            await page.fill('#inventory-search', '');
            await page.waitForTimeout(500);
            const cleared = await rows();
            expect('search: clearing shows every row again', cleared.value === '' && cleared.names.length === 2, cleared);
            await page.evaluate(() => { document.getElementById('inventory-search').value = 'x'; authToken = null; currentUserProfile = null; updateGuestHeaderState(); });
            const signedOut = await page.evaluate(() => ({ hidden: document.getElementById('inventory-search-wrap').hidden, value: document.getElementById('inventory-search').value }));
            expect('search: hidden and cleared when signed out', signedOut.hidden && signedOut.value === '', signedOut);
            await context.close();
        }

        // ---- 5: Low Stock never flashes "Loading…" on a background refresh
        {
            const { context, page } = await openApp(1024, 768);
            await signIn(page, 'Qa');
            const seq = await page.evaluate(async () => {
                const shown = [];
                const desc = document.getElementById('metric-low-desc');
                new MutationObserver(() => shown.push(desc.textContent)).observe(desc, { childList: true, characterData: true, subtree: true });
                const deferred = () => { let resolve; const p = new Promise(r => { resolve = r; }); return { p, resolve }; };
                const ok = n => new Response(JSON.stringify({ low: n }), { status: 200, headers: { 'content-type': 'application/json' } });
                // A fresh sign-in: nothing loaded yet (sign-in may already have
                // settled a figure against the stub; start again from empty).
                dashboardLowStockCount = null; dashboardLowStockReady = false; dashboardLowStockScope = undefined; dashboardLowStockRequestSeq++;
                updateDashboardMetrics();
                const run = async (answer) => { const d = deferred(); window.__lowFetch = () => d.p; const pending = refreshDashboardLocationScopedCards(); await new Promise(r => setTimeout(r, 120)); const during = desc.textContent; d.resolve(answer); await pending; return { during, after: desc.textContent }; };
                const first = await run(ok(3));
                const same = await run(ok(3));
                const changed = await run(ok(4));
                const failed = await run(new Response('{"detail":"boom"}', { status: 500, headers: { 'content-type': 'application/json' } }));
                // A slow earlier answer must not overwrite a newer one.
                const slow = deferred(), fast = deferred();
                let n = 0; window.__lowFetch = () => (++n === 1 ? slow.p : fast.p);
                const a = loadDashboardLowStockCount(); const b = loadDashboardLowStockCount();
                fast.resolve(ok(7)); await b; slow.resolve(ok(2)); await a;
                return { first, same, changed, failed, race: desc.textContent, shown };
            });
            expect('Low Stock: the very first load may show "Loading…"', seq.first.during === 'Loading…' && seq.first.after === '3 items need attention', seq);
            expect('Low Stock: 3 -> refresh -> 3 with no "Loading…" in between', seq.same.during === '3 items need attention' && seq.same.after === '3 items need attention', seq);
            expect('Low Stock: 3 -> refresh -> 4 updates when the new figure arrives', seq.changed.during === '3 items need attention' && seq.changed.after === '4 items need attention', seq);
            expect('Low Stock: a failed refresh keeps the last good figure', seq.failed.during === '4 items need attention' && seq.failed.after === '4 items need attention', seq);
            expect('Low Stock: a slow older response never overwrites a newer one', seq.race === '7 items need attention', seq);
            expect('Low Stock: "Loading…" appeared only during the first load', seq.shown.filter(s => s === 'Loading…').length <= 1 && seq.shown.indexOf('Loading…') < seq.shown.indexOf('3 items need attention'), seq.shown);
            await context.close();
        }

        // ---- 6: the real registration phone field, as a person types, deletes, pastes and changes country
        for (const lang of ['en', 'ar']) {
            const { context, page } = await openApp(390, 844, lang);
            await page.evaluate(() => {
                let e = document.getElementById('reg-biz-phone');
                while (e && e !== document.body) { e.classList.remove('hidden'); e.hidden = false; e = e.parentElement; }
                selectCountryOption('NG');
            });
            const field = page.locator('#reg-biz-phone');
            const state = () => page.evaluate(() => { const el = document.getElementById('reg-biz-phone'); const er = document.getElementById('reg-biz-phone-error');
                return { value: el.value, error: er.classList.contains('hidden') ? '' : er.textContent, invalid: el.getAttribute('aria-invalid'), e164: phoneE164ForSubmit(el.value, window.selectedBusinessContext),
                         inputmode: el.getAttribute('inputmode'), type: el.type }; });
            await field.click();
            await page.keyboard.type('08031234567', { delay: 15 });
            const typed = await state();
            expect(`${lang} phone: typing 08031234567 shows "0803 123 4567", sends +2348031234567, numeric keyboard`, typed.value === '0803 123 4567' && typed.e164 === '+2348031234567' && !typed.error && typed.inputmode === 'tel' && typed.type === 'tel', typed);
            await page.keyboard.type('9999999', { delay: 15 });
            const long = await state();
            const lengthVerdict = await page.evaluate((v) => libphonenumber.validatePhoneNumberLength(v, 'NG') || 'OK', long.value);
            expect(`${lang} phone: digits past the country's maximum are refused, with the reason shown`, long.value.replace(/\D/g, '').length < 18 && lengthVerdict !== 'TOO_LONG'
                && long.error !== '' && long.invalid === 'true', { long, lengthVerdict });
            await field.fill(''); await field.click();
            await page.keyboard.type('08031234567', { delay: 10 });
            await page.keyboard.press('End');
            const before = (await state()).value.replace(/\D/g, '').length;
            for (let i = 0; i < 4; i++) await page.keyboard.press('Backspace');
            const deleted = await state();
            expect(`${lang} phone: each Backspace removes one digit (never stuck on a space)`, deleted.value.replace(/\D/g, '').length === before - 4 && deleted.value === '0803 123', deleted);
            await field.blur();
            const short = await state();
            expect(`${lang} phone: too short is reported on leaving the field`, short.error !== '' && short.invalid === 'true', short);
            // Paste: formatted national, then an international number (own code kept).
            const paste = async (text) => { await field.fill(''); await field.focus(); await page.evaluate((text) => { const el = document.getElementById('reg-biz-phone'); el.value = text; el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: text })); }, text); return state(); };
            const p1 = await paste('(0803) 123-4567');
            expect(`${lang} phone: pasted formatted national number is re-formatted`, p1.value === '0803 123 4567' && p1.e164 === '+2348031234567', p1);
            const p2 = await paste('+44 7400 123456');
            expect(`${lang} phone: pasted international number keeps its own code (no +234 +44)`, p2.e164 === '+447400123456' && p2.value.startsWith('+44') && !p2.value.startsWith('+234'), p2);
            const p3 = await paste('0803 123 4567 1234 5678');
            expect(`${lang} phone: a pasted number that is too long is kept whole and flagged, never cut`, p3.value.replace(/\D/g, '') === '0803123456712345678' && p3.error !== '', p3);
            await field.fill(''); await field.click();
            await page.keyboard.type('08a03b1', { delay: 10 });
            const letters = await state();
            expect(`${lang} phone: letters are not accepted`, !/[a-z]/i.test(letters.value) && letters.value.replace(/\D/g, '') === '08031', letters);
            // Country change re-evaluates: a Nigerian mobile typed while the United States is selected.
            // (Typed digit by digit, the US field would refuse the 11th digit —
            // too long for a US number — so the number is pasted.)
            await page.evaluate(() => selectCountryOption('US'));
            await paste('08031234567');
            await field.blur();
            const asUs = await state();
            await page.evaluate(() => selectCountryOption('NG'));
            const asNg = await state();
            expect(`${lang} phone: changing the country re-formats and re-checks the number`, asUs.error !== '' && asNg.value === '0803 123 4567' && asNg.error === '' && asNg.e164 === '+2348031234567', { asUs, asNg });
            if (lang === 'ar') {
                await page.evaluate(() => selectCountryOption('NG'));
                await field.fill(''); await field.click(); await page.keyboard.type('0803', { delay: 10 }); await field.blur();
                const ar = await page.evaluate(() => { const el = document.getElementById('reg-biz-phone'); const b = el.getBoundingClientRect(); return { error: document.getElementById('reg-biz-phone-error').textContent, value: el.value, bidi: getComputedStyle(el).unicodeBidi, overflow: document.documentElement.scrollWidth - innerWidth, fits: b.right <= innerWidth }; });
                expect('ar phone: the message is Arabic, the digits stay in order, the field fits', /قصير/.test(ar.error) && ar.value === '0803' && ar.bidi === 'plaintext' && ar.overflow <= 0 && ar.fits, ar);
                if (OUT) await page.screenshot({ path: path.join(OUT, 'polish_phone_ar_390.png') });
            }
            await context.close();
        }

        // ---- 6: saved values are shown formatted and only an edited number is re-sent
        {
            const { context, page } = await openApp(1024, 768);
            await signIn(page, 'Qa');
            const shown = await page.evaluate(() => ({
                e164: formatPhoneForDisplay('+2348031234567', businessProfile),
                local: formatPhoneForDisplay('08031234567', businessProfile),
                foreign: formatPhoneForDisplay('+12015550123', businessProfile),
                legacy: formatPhoneForDisplay('ask for Musa', businessProfile),
            }));
            expect('saved numbers: E.164 and old local digits show nationally; another country internationally; unparseable legacy text unchanged',
                shown.e164 === '0803 123 4567' && shown.local === '0803 123 4567' && shown.foreign === '+1 201 555 0123' && shown.legacy === 'ask for Musa', shown);
            await context.close();
        }
    } finally {
        await browser.close();
        server.close();
    }
    if (failures.length) {
        console.error(`FAIL ${failures.length}:\n  - ${failures.join('\n  - ')}`);
        process.exit(1);
    }
    console.log(`PASS ${results.length} checks (static + library + browser)`);
})().catch(err => { console.error(err); process.exit(1); });
