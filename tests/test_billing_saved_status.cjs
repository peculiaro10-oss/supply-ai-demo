// SUB-LIFECYCLE-001 follow-up — Billing while paused and while offline.
//  - Paused (online): the exact paid-through date AND time stays on screen, in
//    the business timezone, from the timestamp access is gated on.
//  - Offline: Billing shows the subscription status saved on this device at its
//    last sync, labelled as saved and not live; Renew says the internet is
//    needed and never starts a payment; nothing is shown as paid.
//  - Staff never get Billing controls; Arabic is right-to-left with no
//    horizontal overflow.
// Static part always runs. Browser part: serves frontend/ from a throwaway local
// server and drives the real Billing code in headless Chromium (no backend;
// fetch and the offline snapshot are stubbed per scenario). Skipped, loudly,
// when Playwright is not installed.
//   node tests/test_billing_saved_status.cjs
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
const fnBody = name => { const i = app.indexOf(`function ${name}(`); assert.ok(i > 0, name); return app.slice(i, app.indexOf('\n        }\n', i)); };
const results = [];
const check = (name, fn) => { fn(); results.push(name); };

check('paused Billing renders the gating timestamp (paid-through, else trial end)', () => {
    assert.match(fnBody('subscriptionPauseAt'), /usage\.paid_through_at \|\| \(usage\.trial_start_at \? usage\.trial_ends_at : null\) \|\| usage\.current_period_end/);
    const status = fnBody('renderBillingStatus');
    assert.match(status, /const pausedAt = usage\.access_paused \? subscriptionPauseAt\(usage\) : null;/);
    assert.match(status, /formatBusinessDateTime\(pausedAt\)/);
});
check('offline Billing reads only the saved /subscription/usage and never the network', () => {
    const load = fnBody('loadBillingPanelOnce');
    assert.ok(load.indexOf('if (offlineWorkspaceUnlocked) { renderBillingSaved(savedBillingUsage(), loadingEl); return; }') < load.indexOf('fetch('), 'offline returns before any fetch');
    assert.match(fnBody('savedBillingUsage'), /currentSnapshot\?\.\(\)\?\.cache\?\.\['\/subscription\/usage'\]/);
    const saved = fnBody('renderBillingSaved');
    assert.doesNotMatch(saved, /billingUsageCache\s*=/, 'the saved copy never becomes the live status');
    assert.doesNotMatch(saved, /CauldraPayments|fetch\(/);
    assert.match(saved, /renewal_in_progress: false, auto_renewal: null, pending_downgrade: null, payment_details_visible: false/);
});
check('offline: Billing itself opens and closes; its payment actions stay online-only', () => {
    assert.match(app, /const offlineAllowed = \["openBillingModal\(\)", "closeBillingModal\(\)"\];/);
    assert.match(fnBody('openBillingModal'), /if \(!\['admin','manager'\]\.includes\(role\)\)/, 'Staff still cannot open Billing');
});
check('new wording is translated for every launch language', () => {
    for (const text of ['Paid through', 'Trial ended', 'Paused', 'Saved subscription status',
        "Saved on this device at {time}. This is not live: payment status can't be checked offline.",
        "The paid-through time saved on this device has passed, so Cauldra is paused on this device.",
        "Subscription details aren't saved on this device yet. Connect to the internet to see them.",
        'Renewing or paying needs an internet connection.']) {
        assert.equal((catalog[text] || []).filter(Boolean).length, 4, text);
    }
    assert.match(html, /id="billing-offline-notice"/);
    assert.match(html, /id="billing-next-billing-label"/);
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

// 09:00 UTC is 10:00 in Africa/Lagos (the test browser's timezone).
const PAUSED_ONLINE = {
    plan: 'starter', plan_label: 'Business', billing_interval: 'monthly', status: 'past_due', effective_status: 'past_due',
    access_paused: true, blocked_message: 'Your Cauldra subscription is paused because the renewal payment has not been confirmed. Your business data is safe. Renew from Subscription & Billing to continue.',
    paid_through_at: '2026-10-01T09:00:00Z', current_period_end: '2026-10-01T09:00:00Z', next_billing_at: '2026-10-01T09:00:00Z',
    trial_start_at: '2026-08-01T09:00:00Z', trial_ends_at: '2026-08-15T09:00:00Z', current_price_naira: 20000, payment_details_visible: true,
    renewal_in_progress: false, auto_renewal: null, card_verified: false, plans: { starter: { label: 'Business', monthly_price: 20000, annual_price: 200000, limits: {} } },
    resources: {}, included_ai_credits: 0, used_ai_credits: 0,
};
// Saved at the last sync while still active; its paid-through time has since passed.
const SAVED_ACTIVE_NOW_PAST = { ...PAUSED_ONLINE, status: 'active', effective_status: 'active', access_paused: false, blocked_message: null,
    paid_through_at: '2026-01-01T09:00:00Z', current_period_end: '2026-01-01T09:00:00Z', next_billing_at: '2026-01-01T09:00:00Z',
    renewal_in_progress: true, card_last4: '4081', card_verified: true };

async function scenario(page, { usage, mode, role = 'admin', lang = 'en', savedAt = '2026-01-01T08:30:00Z', width = 375 }) {
    await page.setViewportSize({ width, height: width >= 1024 ? 768 : 812 });
    return page.evaluate(async ({ usage, mode, role, lang, savedAt }) => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        currentUserProfile = { role, username: 'test' };
        authToken = 'test-token';
        billingUsageCache = null;
        setLanguage(lang, { persist: false });
        const snap = usage ? { cache: { '/subscription/usage': { value: usage, verified_at: savedAt } } } : { cache: {} };
        if (!window.__origOffline) window.__origOffline = window.CauldraOffline;
        window.CauldraOffline = { ...window.__origOffline, currentSnapshot: () => snap };
        offlineWorkspaceUnlocked = mode === 'offline';
        const calls = [];
        window.fetch = async url => {
            calls.push(String(url));
            if (mode === 'offline') throw new TypeError('Failed to fetch');
            if (String(url).includes('/subscription/usage')) return new Response(JSON.stringify(usage), { status: 200, headers: { 'content-type': 'application/json' } });
            return new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } });
        };
        document.querySelectorAll('[data-dynamic-status], #global-status-region').forEach(e => e.classList.add('hidden'));
        document.getElementById('billing-modal').classList.remove('hidden');
        await loadBillingPanel();
        await wait(400);
        const $ = id => document.getElementById(id);
        const vis = e => !!e && !e.closest('.hidden') && e.getClientRects().length > 0;
        const text = e => (e?.innerText || '').replace(/\s+/g, ' ').trim();
        const out = {
            dir: document.documentElement.dir, content: vis($('billing-panel-content')), loading: text($('billing-panel-loading')),
            notice: vis($('billing-offline-notice')) ? text($('billing-offline-notice')) : '',
            plan: text($('billing-plan-label')), badge: text($('billing-status-badge')),
            dateShown: vis($('billing-next-billing-box')), dateLabel: text($('billing-next-billing-label')), dateValue: text($('billing-next-billing-value')),
            box: vis($('billing-lifecycle-box')) ? text($('billing-lifecycle-box')) : '',
            renewNow: vis($('billing-renew-now')), renewOffline: vis($('billing-renew-offline')), refreshStatus: vis($('billing-refresh-status')),
            plans: vis($('billing-plans-section')), usageSection: vis($('billing-usage-section')), card: vis($('billing-card-box')),
            history: vis($('billing-payment-history-section')), cancel: vis($('billing-cancel-row')),
            liveCache: billingUsageCache !== null, calls,
        };
        const panel = document.querySelector('#billing-modal .overflow-y-auto');
        out.overflow = { page: document.documentElement.scrollWidth - window.innerWidth, panel: panel.scrollWidth - panel.clientWidth };
        if (out.renewOffline) {
            const toasts = [];
            const realToast = showToast;
            showToast = (message, type) => { toasts.push(String(message)); return realToast(message, type); };
            const callsBefore = calls.length;
            try { $('billing-renew-offline').click(); await wait(800); } finally { showToast = realToast; }
            out.toast = toasts.join(' | ');
            // showToast writes into the open modal's status line (or the global one).
            out.toastShown = [...document.querySelectorAll('[data-dynamic-status], #global-status-region')].filter(e => !e.classList.contains('hidden')).map(text).filter(Boolean).join(' | ');
            out.callsAfterRenew = calls.length - callsBefore;
            const overlay = $('payment-overlay');
            out.payment = { overlayShown: !!overlay && !overlay.hidden, state: overlay?.dataset.state || null };
        }
        document.getElementById('billing-modal').classList.add('hidden');
        return out;
    }, { usage, mode, role, lang, savedAt });
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
        await page.waitForFunction(() => typeof loadBillingPanel === 'function' && !!window.CauldraOffline);
        await page.waitForTimeout(2500);

        const a = await scenario(page, { usage: PAUSED_ONLINE, mode: 'online' });
        expect('paused (online): Billing shows the exact paid-through date and time in the business timezone',
            a.content && a.dateShown && a.dateLabel === 'Paid through' && /10:00/.test(a.dateValue) && /2026/.test(a.dateValue) && /Oct/.test(a.dateValue), a);
        expect('paused (online): paused, data safe, Renew for the Admin, not labelled as saved',
            /Subscription paused/.test(a.box) && /data is safe/i.test(a.box) && a.renewNow && !a.renewOffline && !a.notice && a.plans, a);

        const b = await scenario(page, { usage: SAVED_ACTIVE_NOW_PAST, mode: 'offline' });
        expect('offline: Billing shows the saved plan and the exact paid-through date and time, with no network request',
            b.content && b.plan === 'Business' && b.dateShown && b.dateLabel === 'Paid through' && /10:00/.test(b.dateValue) && /Jan/.test(b.dateValue) && b.calls.length === 0, b);
        expect('offline: the saved status is labelled as saved on this device and not live',
            /Saved subscription status/.test(b.notice) && /Saved on this device at Jan 1, 2026, 9:30/.test(b.notice) && /not live/.test(b.notice) && !b.liveCache, b);
        expect('offline: a saved paid-through time that has passed shows Paused (the saved "active" is not shown as current)',
            b.badge === 'Paused' && /Subscription paused/.test(b.box) && /data is safe/i.test(b.box), b);
        expect('offline: no live-only state is faked (no "being confirmed", Refresh, card, history, plan changes or cancel)',
            !/being confirmed/i.test(b.box) && !b.refreshStatus && !b.renewNow && !b.card && !b.history && !b.plans && !b.usageSection && !b.cancel, b);
        expect('offline: Renew says the internet is needed, starts no payment and shows nothing as paid',
            b.renewOffline && b.toast === 'Renewing or paying needs an internet connection.' && /internet connection/.test(b.toastShown) && !b.payment.overlayShown && b.payment.state !== 'success' && b.callsAfterRenew === 0 && !/payment confirmed/i.test(`${b.toast} ${b.box}`), b);

        const c = await scenario(page, { usage: null, mode: 'offline' });
        expect('offline with nothing saved: says so plainly, invents nothing', !c.content && /aren't saved on this device/.test(c.loading) && c.calls.length === 0, c);

        const m = await scenario(page, { usage: SAVED_ACTIVE_NOW_PAST, mode: 'offline', role: 'manager' });
        expect('offline Manager: saved status, ask-the-Admin, no Renew control', /Ask your business Admin/.test(m.box) && !m.renewOffline && !m.renewNow, m);
        const s = await scenario(page, { usage: PAUSED_ONLINE, mode: 'online', role: 'staff' });
        expect('Staff: no Renew, Refresh or payment controls even if the Billing panel were rendered', !s.renewNow && !s.renewOffline && /Ask your business Admin/.test(s.box), s);
        const staffGate = await page.evaluate(async () => {
            currentUserProfile = { role: 'staff' }; businessProfile = businessProfile || { id: 1, business_code: 'X', company_name: 'Test' };
            document.getElementById('billing-modal').classList.add('hidden');
            await openBillingModal();
            return { opened: !document.getElementById('billing-modal').classList.contains('hidden') };
        }).catch(e => ({ error: String(e) }));
        expect('Staff: Billing does not open', staffGate.opened === false, staffGate);

        for (const width of [375, 768, 1024, 1366]) {
            const ar = await scenario(page, { usage: SAVED_ACTIVE_NOW_PAST, mode: 'offline', lang: 'ar', width });
            const arabic = s => /[؀-ۿ]/.test(s || '');
            expect(`Arabic offline ${width}px: right-to-left, saved label, paused wording and date label translated`,
                ar.dir === 'rtl' && arabic(ar.notice) && !/Saved on this device/.test(ar.notice) && arabic(ar.box) && !/Subscription paused|data is safe|unlocks as soon/.test(ar.box)
                && ar.dateLabel === 'مدفوع حتى' && ar.dateValue.length > 0 && arabic(ar.toastShown) && !/internet connection/.test(ar.toastShown), ar);
            expect(`Arabic offline ${width}px: no horizontal overflow`, ar.overflow.page <= 0 && ar.overflow.panel <= 0, ar.overflow);
            const arOnline = await scenario(page, { usage: PAUSED_ONLINE, mode: 'online', lang: 'ar', width });
            expect(`Arabic paused online ${width}px: Renew and paid-through label translated, no horizontal overflow`,
                arOnline.dir === 'rtl' && arOnline.dateLabel === 'مدفوع حتى' && arabic(arOnline.box) && !/Subscription paused|data is safe|unlocks as soon/.test(arOnline.box) && arOnline.overflow.page <= 0 && arOnline.overflow.panel <= 0, arOnline);
        }
        const en = await scenario(page, { usage: SAVED_ACTIVE_NOW_PAST, mode: 'offline', width: 375 });
        expect('English offline 375px: no horizontal overflow', en.overflow.page <= 0 && en.overflow.panel <= 0, en.overflow);
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
