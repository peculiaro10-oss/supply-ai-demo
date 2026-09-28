// SUB-A5-02 — paused wording in every launch language on the workspace banner
// (Admin and Staff) and in subscription reminder notifications. Found on the
// A23: the banner joined the server's paused sentence and "Your business data
// is kept." into one text node, which the whole-text launch catalog could not
// match, and reminder bodies (stored English with the local time written in)
// were not catalogued.
//  - no English paused sentence outside English; English unchanged;
//  - the stored local expiry time stays visible (same wall-clock time, zone
//    label kept), re-shown in the reader's date format;
//  - Admin keeps Choose plan, Staff keep "ask your Admin" and no button;
//  - right-to-left and no horizontal overflow in Arabic.
// Static part always runs; the browser part drives the real app in headless
// Chromium (fetch stubbed, no backend) and is skipped, loudly, without Playwright.
//   node tests/test_subscription_paused_i18n.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const app = fs.readFileSync(path.join(frontend, 'js/app.js'), 'utf8');
const backend = fs.readFileSync(path.join(root, 'backend/main.py'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'i18n/launch_catalog.json'), 'utf8'));
const fnBody = name => { const i = app.indexOf(`function ${name}(`); assert.ok(i > 0, name); return app.slice(i, app.indexOf('\n        }\n', i)); };
const results = [];
const check = (name, fn) => { fn(); results.push(name); };

const PAUSED = 'Your Cauldra subscription is paused because the renewal payment has not been confirmed. Your business data is safe. Renew from Subscription & Billing to continue.';
const KEPT = 'Your business data is kept.';
const REMINDER = 'Your Cauldra subscription is paused because the renewal payment was not confirmed by 28 Sep 2026, 15:33 (Africa/Lagos). Your business data is safe. An Admin can renew from Subscription & Billing at any time.';
const LANGS = ['fr', 'es', 'ar', 'pt'];

check('banner: the paused reason and "data is kept" are separate text nodes', () => {
    assert.match(fnBody('renderSubscriptionBlockedBanner'), /<span>\$\{escapeHtml\(subscriptionBlockedMessage\)\}<\/span> <span>\$\{escapeHtml\(t\("subscription\.dataKept"\)\)\}<\/span>/);
    assert.doesNotMatch(app, /`\$\{subscriptionBlockedMessage\} \$\{t\("subscription\.dataKept"\)\}`/, 'no joined, untranslatable paused text remains');
});
check('every reminder sentence the server writes is catalogued for fr/es/ar/pt', () => {
    // The server's own templates, with its placeholders, must be catalogue keys.
    // Messages are built from sentence templates (subscription_reminder_parts);
    // every title and sentence template is one.
    const i = backend.indexOf('def subscription_reminder_parts(');
    const body = backend.slice(i, backend.indexOf('\ndef ', i + 10));
    const templates = [...body.matchAll(/\("([^"]*\s[^"]*)"/g)].map(m => m[1]);
    assert.ok(templates.length > 30, 'templates found');
    for (const t of templates) assert.equal((catalog[t] || []).filter(Boolean).length, 4, t);
    for (const key of [PAUSED, KEPT, REMINDER.replace('28 Sep 2026, 15:33 (Africa/Lagos)', '{when}')]) assert.equal((catalog[key] || []).filter(Boolean).length, 4, key);
});
check('notification bodies: English shown exactly as stored; the stored time is reformatted, not recomputed', () => {
    const f = app.slice(app.indexOf('const REMINDER_TIME_RE'), app.indexOf('async function renderAlerts'));
    assert.match(f, /currentLanguage === DEFAULT_LANGUAGE[^\n]*return text;/);
    assert.match(f, /if \(translated === text\) return text;/);
    assert.match(f, /timeZone: 'UTC'/, 'the stored wall-clock time is shown as written, never shifted');
    assert.match(fnBody('renderAlerts'), /escapeHtml\(notificationMessageText\(n\)\)/);
    assert.match(fnBody('renderAlerts'), /onclick="openNotification\(\$\{n\.id\}\)"/, 'mark-read path unchanged');
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

async function scenario(page, { lang, role, width = 375 }) {
    await page.setViewportSize({ width, height: 812 });
    return page.evaluate(async ({ lang, role, PAUSED, REMINDER }) => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        const text = e => (e?.innerText || '').replace(/\s+/g, ' ').trim();
        currentUserProfile = { role, username: 'test' };
        authToken = 'test-token';
        hasAuthenticatedBusinessContext = () => true;
        renderProductDeletionRequests = async () => '';
        window.fetch = async url => {
            if (String(url).includes('/notifications')) return new Response(JSON.stringify({ unread_count: 1, read_only: true, notifications: [
                { id: 7, type: 'SUBSCRIPTION_REMINDER_PAUSED', category: 'subscription', severity: 'critical', title: 'Subscription paused', message: REMINDER,
                  is_read: false, deep_link: 'subscription', created_at: '2026-09-28T14:33:30Z' }] }), { status: 200, headers: { 'content-type': 'application/json' } });
            return new Response('{}', { status: 402, headers: { 'content-type': 'application/json' } });
        };
        setLanguage(lang, { persist: false });
        subscriptionBlockedMessage = null;
        setSubscriptionBlocked(PAUSED);
        document.getElementById('alerts-modal').classList.remove('hidden');
        await renderAlerts();
        await wait(500);
        const banner = document.getElementById('subscription-blocked-banner');
        const note = document.querySelector('#notifications-container [onclick^="openNotification"]');
        const bannerText = text(banner), bannerButton = !!banner?.querySelector('button');
        const overflow = { page: document.documentElement.scrollWidth - window.innerWidth, banner: banner.scrollWidth - banner.clientWidth };
        const toasts = [];
        const realToast = showToast;
        showToast = m => { toasts.push(String(m)); };
        try { showSubscriptionBlockedNotice(); } finally { showToast = realToast; }
        closeBillingModal?.();
        await wait(400); // the notice re-renders the banner; the catalog runtime translates it on its next mutation pass
        const container = document.getElementById('notifications-container');
        const out = {
            dir: document.documentElement.dir, banner: bannerText, bannerButton, note: text(note), toast: toasts.join(' | '),
            expectedTime: `${new Intl.DateTimeFormat(getBusinessLocale(), { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(Date.UTC(2026, 8, 28, 15, 33)))} (Africa/Lagos)`,
            overflow: { ...overflow, notes: container.scrollWidth - container.clientWidth },
        };
        document.getElementById('alerts-modal').classList.add('hidden');
        subscriptionBlockedMessage = null; banner.classList.add('hidden');
        return out;
    }, { lang, role, PAUSED, REMINDER });
}

(async () => {
    const server = await serve();
    const origin = `http://127.0.0.1:${server.address().port}`;
    const launchOptions = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {};
    const browser = await playwright.chromium.launch(launchOptions);
    const failures = [];
    const expect = (name, cond, detail) => { if (cond) results.push(name); else failures.push(`${name}\n      ${JSON.stringify(detail)}`); };
    const english = /subscription is paused|data is kept|data is safe|renewal payment|Ask your business Admin/i;
    try {
        const context = await browser.newContext({ timezoneId: 'Africa/Lagos', locale: 'en-GB', serviceWorkers: 'block' });
        await context.route('**/*', route => (route.request().url().startsWith(origin) ? route.continue() : route.abort()));
        const page = await context.newPage();
        await page.goto(origin + '/', { waitUntil: 'load' });
        await page.waitForFunction(() => typeof renderAlerts === 'function' && !!window.CauldraI18n);
        await page.waitForTimeout(2500);

        for (const role of ['admin', 'staff']) {
            const en = await scenario(page, { lang: 'en', role });
            expect(`English ${role}: banner, toast and notification exactly as before`,
                en.banner.startsWith(`${PAUSED} ${KEPT}`) && en.toast === `${PAUSED} ${KEPT}` && en.note.includes(REMINDER) && en.bannerButton === (role === 'admin')
                && (role === 'admin' ? /Choose a plan/.test(en.banner) : /Ask your business Admin to renew the subscription\./.test(en.banner)), en);
            for (const lang of LANGS) {
                const tr = src => catalog[src][LANGS.indexOf(lang)];
                const r = await scenario(page, { lang, role });
                expect(`${lang} ${role}: banner has the translated paused reason and "data is kept", no English`,
                    r.banner.includes(tr(PAUSED)) && r.banner.includes(tr(KEPT)) && !english.test(r.banner)
                    && r.bannerButton === (role === 'admin') && (role === 'staff' ? r.banner.includes(tr('Ask your business Admin to renew the subscription.')) : true), r);
                expect(`${lang} ${role}: paused toast translated, no English`, r.toast === `${tr(PAUSED)} ${tr(KEPT)}`, r.toast);
                expect(`${lang} ${role}: paused notification body translated with the exact stored expiry time`,
                    !english.test(r.note) && r.note.includes(r.expectedTime) && r.note.includes(tr('Subscription paused')), { note: r.note, expectedTime: r.expectedTime });
                if (lang === 'ar') {
                    expect(`ar ${role}: right-to-left, no horizontal overflow at 375 px`, r.dir === 'rtl' && r.overflow.page <= 0 && r.overflow.banner <= 0 && r.overflow.notes <= 0, r.overflow);
                    for (const width of [768, 1024, 1366]) {
                        const w = await scenario(page, { lang, role, width });
                        expect(`ar ${role}: no horizontal overflow at ${width} px`, w.dir === 'rtl' && w.overflow.page <= 0 && w.overflow.banner <= 0 && w.overflow.notes <= 0, w.overflow);
                    }
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
