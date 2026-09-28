// Pre-freeze subscription policy — Billing and notifications in the app.
//  - while the paid period is active nothing on Billing starts a payment for
//    the next period: a same-plan interval switch is shown as "Available after
//    this period ends" (disabled); an upgrade (prorated) is still offered;
//  - ending soon with a saved card says the card is charged automatically;
//  - Pay Now refused by the server (RENEWAL_NOT_DUE) shows the server's reason;
//  - new reminder messages (built from catalogued sentences) are translated
//    sentence by sentence: no English in Arabic, the stored time kept, RTL,
//    no horizontal overflow; English unchanged.
// Static part always runs; the browser part drives the real app in headless
// Chromium (fetch stubbed, no backend) and is skipped, loudly, without Playwright.
//   node tests/test_subscription_prefreeze_ui.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const app = fs.readFileSync(path.join(frontend, 'js/app.js'), 'utf8');
const payments = fs.readFileSync(path.join(frontend, 'js/payments.js'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'i18n/launch_catalog.json'), 'utf8'));
const results = [];
const check = (name, fn) => { fn(); results.push(name); };
const LANGS = ['fr', 'es', 'ar', 'pt'];

const FAILED_EN = 'Cauldra could not renew your subscription automatically at 29 Sep 2026, 12:35 (Africa/Lagos). The card issuer declined the payment. '
    + 'Cauldra stays paused until a renewal payment is confirmed. Cauldra will try your saved card again every 12 hours until 02 Oct 2026, 12:35 (Africa/Lagos). '
    + 'An Admin can renew now with Pay Now, or change the card, in Subscription & Billing. Your business data is safe.';

check('Pay Now refused as not yet due shows the server reason, never a checkout', () => {
    const i = payments.indexOf("data.detail?.code === 'RENEWAL_NOT_DUE'");
    assert.ok(i > 0 && i < payments.indexOf("['RENEWAL_IN_PROGRESS', 'ALREADY_PAID'].includes(data.detail?.code)"));
    assert.match(payments.slice(i, i + 400), /status\('error', data\.detail\.message/);
});
check('plan cards: an active paid period offers no same-plan payment; upgrades stay', () => {
    assert.match(app, /usage\.status === 'active' && !isUpgrade && usage\.pay_now_available === false\) \{ buttonText = null;/);
    assert.match(app, /data-early-renewal="blocked"/);
});
check('every sentence of the new reminder messages is catalogued for fr/es/ar/pt', () => {
    for (const s of ['Available after this period ends', 'Your subscription is paid through {time}. Cauldra will charge your saved card automatically at that time; please make sure it can be charged. Your data stays safe.',
        'Cauldra could not renew your subscription automatically at {when}.', 'The card issuer declined the payment.', 'Cauldra stays paused until a renewal payment is confirmed.',
        'Cauldra will try your saved card again every 12 hours until {end}.', 'An Admin can renew now with Pay Now, or change the card, in Subscription & Billing.', 'Your business data is safe.',
        'Automatic renewal failed']) {
        assert.equal((catalog[s] || []).filter(Boolean).length, 4, s);
    }
});
console.log(`PASS static ${results.length} checks:\n - ${results.join('\n - ')}`);

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

const end = new Date(Date.now() + 2 * 86400000).toISOString().replace(/\.\d+Z$/, 'Z');
const ACTIVE = {
    plan: 'starter', plan_label: 'Business', billing_interval: 'monthly', status: 'active', effective_status: 'active',
    access_paused: false, blocked_message: null, paid_through_at: end, current_period_end: end, next_billing_at: end,
    trial_start_at: '2026-08-01T09:00:00Z', trial_ends_at: '2026-08-15T09:00:00Z', current_price_naira: 20000, payment_details_visible: true,
    renewal_in_progress: false, auto_renewal: { next_attempt_at: end, ends_at: end, exhausted: false }, card_verified: true, card_last4: '4081',
    pay_now_available: false,
    plans: { starter: { label: 'Business', monthly_price: 20000, annual_price: 200000, limits: {} },
             business: { label: 'Premium', monthly_price: 50000, annual_price: 500000, limits: {} } },
    resources: {}, included_ai_credits: 0, used_ai_credits: 0,
};

async function scenario(page, { lang, width = 375 }) {
    await page.setViewportSize({ width, height: width >= 1024 ? 768 : 812 });
    return page.evaluate(async ({ usage, lang, FAILED_EN }) => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        const text = e => (e?.innerText || '').replace(/\s+/g, ' ').trim();
        currentUserProfile = { role: 'admin', username: 'test' };
        authToken = 'test-token';
        billingUsageCache = null;
        setLanguage(lang, { persist: false });
        offlineWorkspaceUnlocked = false;
        window.fetch = async url => {
            if (String(url).includes('/subscription/usage')) return new Response(JSON.stringify(usage), { status: 200, headers: { 'content-type': 'application/json' } });
            if (String(url).includes('/notifications')) return new Response(JSON.stringify({ unread_count: 1, read_only: false, notifications: [
                { id: 9, type: 'SUBSCRIPTION_REMINDER_RENEWAL_FAILED', category: 'subscription', severity: 'critical', title: 'Automatic renewal failed',
                  message: FAILED_EN, is_read: false, deep_link: 'subscription', created_at: '2026-09-29T11:36:00Z' }] }), { status: 200, headers: { 'content-type': 'application/json' } });
            return new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } });
        };
        document.getElementById('billing-modal').classList.remove('hidden');
        await loadBillingPanel();
        setBillingIntervalChoice('annual');                         // the same plan, the other interval
        await wait(500);
        const cards = [...document.querySelectorAll('#billing-plan-cards > div')].map(c => ({
            text: text(c), blocked: !!c.querySelector('[data-early-renewal="blocked"]'),
            clickable: [...c.querySelectorAll('button')].filter(b => !b.disabled).map(b => b.getAttribute('onclick') || ''),
        }));
        const box = text(document.getElementById('billing-lifecycle-box'));
        const panel = document.querySelector('#billing-modal .overflow-y-auto');
        const overflow = { page: document.documentElement.scrollWidth - window.innerWidth, panel: panel.scrollWidth - panel.clientWidth };
        document.getElementById('billing-modal').classList.add('hidden');
        document.getElementById('alerts-modal').classList.remove('hidden');
        await renderAlerts();
        await wait(500);
        const note = text(document.querySelector('#notifications-container [onclick^="openNotification"]'));
        const notes = document.getElementById('notifications-container');
        overflow.notes = notes.scrollWidth - notes.clientWidth;
        document.getElementById('alerts-modal').classList.add('hidden');
        const expectedTime = `${new Intl.DateTimeFormat(getBusinessLocale(), { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(Date.UTC(2026, 8, 29, 12, 35)))} (Africa/Lagos)`;
        return { dir: document.documentElement.dir, cards, box, note, expectedTime, overflow };
    }, { usage: ACTIVE, lang, FAILED_EN });
}

(async () => {
    const server = await serve();
    const origin = `http://127.0.0.1:${server.address().port}`;
    const launchOptions = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {};
    const browser = await playwright.chromium.launch(launchOptions);
    const failures = [];
    const expect = (name, cond, detail) => { if (cond) results.push(name); else failures.push(`${name}\n      ${JSON.stringify(detail)}`); };
    const english = /could not renew|card issuer|stays paused|every 12 hours|Pay Now|business data|Available after|charged automatically|saved card/i;
    try {
        const context = await browser.newContext({ timezoneId: 'Africa/Lagos', locale: 'en-GB', serviceWorkers: 'block' });
        await context.route('**/*', route => (route.request().url().startsWith(origin) ? route.continue() : route.abort()));
        const page = await context.newPage();
        await page.goto(origin + '/', { waitUntil: 'load' });
        await page.waitForFunction(() => typeof loadBillingPanel === 'function' && typeof renderAlerts === 'function' && !!window.CauldraI18n);
        await page.waitForTimeout(2500);

        const en = await scenario(page, { lang: 'en' });
        const same = en.cards.find(c => /Business/.test(c.text));
        const upgrade = en.cards.find(c => /Premium/.test(c.text));
        expect('English: the same plan on the other interval is not payable while the period is paid', same && same.blocked && /Available after this period ends/.test(same.text)
            && !same.clickable.some(o => /checkout/.test(o)), en.cards);
        expect('English: an upgrade (prorated, not a renewal) is still offered', upgrade && upgrade.clickable.some(o => /'upgrade'/.test(o)), en.cards);
        expect('English: ending soon says the saved card is charged automatically at that time', /charge your saved card automatically at that time/.test(en.box), en.box);
        expect('English: the new reminder message is shown exactly as stored', en.note.includes(FAILED_EN), en.note);
        for (const lang of LANGS) {
            const r = await scenario(page, { lang });
            const tr = s => catalog[s][LANGS.indexOf(lang)];
            expect(`${lang}: "Available after this period ends" translated, still not payable`, r.cards.some(c => c.blocked && c.text.includes(tr('Available after this period ends'))), r.cards);
            expect(`${lang}: failed-renewal notification translated sentence by sentence, stored time kept`,
                !english.test(r.note) && r.note.includes(tr('The card issuer declined the payment.')) && r.note.includes(r.expectedTime), { note: r.note, expectedTime: r.expectedTime });
            expect(`${lang}: ending-soon notice has no English`, r.box && !english.test(r.box), r.box);
            if (lang === 'ar') {
                for (const width of [375, 768, 1024, 1366]) {
                    const w = width === 375 ? r : await scenario(page, { lang, width });
                    expect(`ar: right-to-left, no horizontal overflow at ${width} px`, w.dir === 'rtl' && w.overflow.page <= 0 && w.overflow.panel <= 0 && w.overflow.notes <= 0, w.overflow);
                }
            }
        }
    } finally {
        await browser.close();
        server.close();
    }
    if (failures.length) {
        console.error(`FAIL ${failures.length}:\n  - ${failures.join('\n  - ')}`);
        process.exit(1);
    }
    console.log(`PASS ${results.length} checks (static + browser)`);
})().catch(err => { console.error(err); process.exit(1); });
