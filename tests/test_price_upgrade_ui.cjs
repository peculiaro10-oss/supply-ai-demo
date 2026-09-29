// PRICE-UPGRADE-001 — the upgrade confirmation shows the server's quote.
//  - an upgrade never goes to Paystack before the Admin has seen the quote;
//  - the amount shown is the quote's amount_due_kobo exactly (no device maths),
//    with the credit and the new paid-through time;
//  - confirming pays that quote (its quote_reference), nothing else;
//  - a refused quote (credit worth more than one new term) shows the server's
//    reason and offers no payment;
//  - the new sentences are catalogued for fr/es/ar/pt.
// Static part always runs; the browser part drives the real app in headless
// Chromium (fetch stubbed, no backend) and is skipped, loudly, without Playwright.
//   node tests/test_price_upgrade_ui.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const app = fs.readFileSync(path.join(frontend, 'js/app.js'), 'utf8');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'i18n/launch_catalog.json'), 'utf8'));
const results = [];
const check = (name, fn) => { fn(); results.push(name); };
const LANGS = ['fr', 'es', 'ar', 'pt'];
const SENTENCES = ["You'll pay the amount below on Paystack's secure checkout. Your plan changes only after the payment is confirmed; a new billing period starts then and renews at {price}.",
    '{plan} {interval} price', 'Credit for the unused part of your current period', 'You pay now',
    'If paid now, your new period runs until {time}.', 'Preparing your upgrade price…', 'The upgrade price could not be prepared. Please try again.'];

check('an upgrade opens the quote confirmation; it never starts a checkout without it', () => {
    const body = app.slice(app.indexOf('async function subscribeNow('), app.indexOf('function formatQuoteNaira('));
    assert.match(body, /return openPlanChangeConfirm\(plan, interval, 'upgrade'\);/);
    assert.doesNotMatch(body, /CauldraPayments\.start\('upgrade'/);
    assert.equal((app.match(/CauldraPayments\.start\('upgrade'/g) || []).length, 1);
    assert.match(app, /if \(selection\.kind === 'upgrade'\) \{\s*if \(!selection\.quote\?\.quote_reference\)/);
});
check('the quote box exists in the confirmation dialog', () => {
    assert.match(html, /id="plan-change-confirm-quote"[^>]*aria-live="polite"/);
});
check('the amount shown is the server amount_due_kobo', () => {
    assert.match(app, /data-quote-amount-kobo="\$\{Number\(quote\.amount_due_kobo\)\}"[^>]*>\$\{escapeHtml\(formatQuoteNaira\(quote\.amount_due_kobo\)\)\}/);
});
check('every new sentence is catalogued for fr/es/ar/pt', () => {
    for (const s of SENTENCES) assert.equal((catalog[s] || []).filter(Boolean).length, 4, s);
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

const end = new Date(Date.now() + 3 * 3600000).toISOString().replace(/\.\d+Z$/, 'Z');
const USAGE = {
    plan: 'starter', plan_label: 'Business', billing_interval: 'monthly', status: 'active', effective_status: 'active',
    access_paused: false, blocked_message: null, paid_through_at: end, current_period_end: end, next_billing_at: end,
    current_price_naira: 20000, payment_details_visible: true, renewal_in_progress: false, card_verified: true, card_last4: '4081',
    pay_now_available: false,
    plans: { starter: { label: 'Business', monthly_price: 20000, annual_price: 200000, limits: {} },
             enterprise: { label: 'Enterprise', monthly_price: 200000, annual_price: 2100000, limits: {} } },
    resources: {}, included_ai_credits: 0, used_ai_credits: 0,
};
// Business Monthly -> Enterprise Annual with ~2.9 h left (the preflight's QA case).
const QUOTE = {
    quote_reference: 'upgquote_test_0001', current_plan: 'starter', current_plan_label: 'Business', current_interval: 'monthly',
    new_plan: 'enterprise', new_plan_label: 'Enterprise', new_interval: 'annual',
    current_price: 20000, new_price: 2100000, unused_credit: 80.61, amount_due: 2099919.39, amount_due_kobo: 209991939,
    currency: 'NGN', current_period_end: end, new_period_starts: 'at_payment_confirmation',
    new_paid_through_if_paid_now: '2027-09-29T12:00:00Z', next_renewal_amount: 2100000, expires_at: end,
};

async function scenario(page, { lang, quoteStatus = 200, quoteBody = QUOTE }) {
    return page.evaluate(async ({ usage, lang, quoteStatus, quoteBody }) => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        const text = e => (e?.innerText || '').replace(/\s+/g, ' ').trim();
        currentUserProfile = { role: 'admin', username: 'test' };
        authToken = 'test-token';
        billingUsageCache = usage;
        setLanguage(lang, { persist: false });
        const calls = [];
        window.fetch = async (url, init) => {
            calls.push({ url: String(url), body: init?.body || null });
            if (String(url).includes('/subscription/upgrade-quote')) return new Response(JSON.stringify(quoteBody), { status: quoteStatus, headers: { 'content-type': 'application/json' } });
            return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
        };
        const started = [];
        CauldraPayments.start = async (kind, payload) => { started.push({ kind, payload }); };
        await subscribeNow('enterprise', 'annual');
        await wait(400);
        const box = document.getElementById('plan-change-confirm-quote');
        const submit = document.getElementById('plan-change-confirm-submit');
        const shown = { text: text(box), kobo: box.querySelector('[data-quote-amount-kobo]')?.getAttribute('data-quote-amount-kobo') || null,
                        amountText: text(box.querySelector('[data-quote-amount-kobo]')), disabled: submit.disabled,
                        body: text(document.getElementById('plan-change-confirm-body')), startedBeforeConfirm: started.length };
        if (!submit.disabled) { await confirmPlanChange(); await wait(50); }
        closePlanChangeConfirm();
        return { shown, started, calls };
    }, { usage: USAGE, lang, quoteStatus, quoteBody });
}

(async () => {
    const server = await serve();
    const origin = `http://127.0.0.1:${server.address().port}`;
    const launchOptions = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {};
    const browser = await playwright.chromium.launch(launchOptions);
    const failures = [];
    const expect = (name, cond, detail) => { if (cond) results.push(name); else failures.push(`${name}\n      ${JSON.stringify(detail)}`); };
    try {
        const context = await browser.newContext({ timezoneId: 'Africa/Lagos', locale: 'en-GB', serviceWorkers: 'block' });
        await context.route('**/*', route => (route.request().url().startsWith(origin) ? route.continue() : route.abort()));
        const page = await context.newPage();
        await page.goto(origin + '/', { waitUntil: 'load' });
        await page.waitForFunction(() => typeof subscribeNow === 'function' && typeof openPlanChangeConfirm === 'function' && !!window.CauldraI18n);
        await page.waitForTimeout(2500);

        const en = await scenario(page, { lang: 'en' });
        expect('English: nothing is paid before the quote is shown', en.shown.startedBeforeConfirm === 0, en.shown);
        expect('English: the amount shown is the server amount, to the kobo', en.shown.kobo === '209991939' && en.shown.amountText === '₦2,099,919.39', en.shown);
        expect('English: credit and new list price shown', en.shown.text.includes('−₦80.61') && en.shown.text.includes('₦2,100,000') && en.shown.text.includes('Enterprise Annual price'), en.shown.text);
        expect('English: the new paid-through time is shown', /If paid now, your new period runs until/.test(en.shown.text) && /2027/.test(en.shown.text), en.shown.text);
        expect('English: exactly one quote request, for the chosen plan', en.calls.filter(c => c.url.includes('/subscription/upgrade-quote')).length === 1
            && JSON.parse(en.calls.find(c => c.url.includes('upgrade-quote')).body).plan === 'enterprise', en.calls);
        expect('English: confirming pays that quote and nothing else', en.started.length === 1 && en.started[0].kind === 'upgrade'
            && en.started[0].payload.quote_reference === QUOTE.quote_reference && Object.keys(en.started[0].payload).length === 1, en.started);

        const refused = await scenario(page, { lang: 'en', quoteStatus: 409, quoteBody: { detail: { code: 'UPGRADE_CREDIT_EXCEEDS_TERM', message: 'The unused part of your current paid period (₦500,000.00) is worth more than one monthly term of Enterprise (₦200,000.00). Choose Enterprise Annual, or change plan when your current period ends on 1 Oct 2027.' } } });
        expect('refused quote: the server reason is shown and no payment is offered', refused.shown.disabled && refused.started.length === 0
            && refused.shown.text.includes('Choose Enterprise Annual'), refused.shown);

        for (const lang of LANGS) {
            const r = await scenario(page, { lang });
            const tr = s => catalog[s][LANGS.indexOf(lang)];
            expect(`${lang}: quote labels translated, amount unchanged`, r.shown.kobo === '209991939' && r.shown.text.includes(tr('You pay now'))
                && r.shown.text.includes(tr('Credit for the unused part of your current period')) && !/You pay now|Credit for the unused/.test(r.shown.text), r.shown.text);
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
