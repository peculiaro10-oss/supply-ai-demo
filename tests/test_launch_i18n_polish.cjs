// Launch UI polish + five launch languages (UI-POLISH / I18N stage).
//   node tests/test_launch_i18n_polish.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const os = require('node:os');

const root = path.resolve(__dirname, '..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
const app = read('frontend/js/app.js');
const html = read('frontend/index.html');
const offline = read('frontend/js/offline.js');
const baseCss = read('frontend/css/base.css');
const results = [];
const check = (name, fn) => { fn(); results.push(name); };

// ---------------------------------------------------------------- languages
check('exactly five launch languages are offered', () => {
    const m = app.match(/const LAUNCH_LANGUAGES = \[([^\]]*)\]/);
    assert.ok(m, 'LAUNCH_LANGUAGES declared');
    assert.deepEqual(JSON.parse(`[${m[1]}]`).sort(), ['ar', 'en', 'es', 'fr', 'pt']);
    assert.match(app, /const SUPPORTED_LANGUAGES = languageDatabase\.map\(l => l\.code\)\.filter\(code => LAUNCH_LANGUAGES\.includes\(code\)\)/);
    assert.match(app, /function populateLanguageSelects[\s\S]{0,120}launchLanguageOptions\(\)/);
    assert.match(app, /langSel\.innerHTML = launchLanguageOptions\(\)/);
});
check('non-launch languages remain in source (hidden, not deleted)', () => {
    const db = app.slice(app.indexOf('const languageDatabase = ['), app.indexOf('];', app.indexOf('const languageDatabase = [')));
    assert.equal((db.match(/\{ code: "/g) || []).length, 35, 'all 35 languageDatabase entries kept');
    const bundles = app.slice(app.indexOf('const TRANSLATIONS = {'));
    for (const code of ['en', 'fr', 'es', 'zh', 'ar', 'pt', 'de', 'ja', 'ko', 'it', 'nl', 'tr', 'ru', 'hi', 'bn', 'sw', 'yo', 'ig', 'ha', 'uk', 'pl', 'vi', 'th', 'id', 'ms', 'fa', 'he', 'ro', 'cs', 'sv', 'da', 'no', 'fi', 'el', 'hu']) {
        assert.ok(new RegExp(`\\n            ${code}: \\{`).test(bundles), `TRANSLATIONS.${code} kept`);
    }
});
check('automatic language fallback never overwrites a saved preference', () => {
    assert.match(app, /function setLanguage\(lang, options = \{\}\)/);
    assert.match(app, /if \(options\.persist !== false\) \{ try \{ persistPreferredLanguage\(resolved\); \} catch \(_\) \{\} \}/);
    assert.match(app, /setLanguage\(bizLang, \{ persist: false \}\)/);
    assert.match(app, /setLanguage\(currentLanguage, \{ persist: false \}\)/);
});

// ---------------------------------------------------------------- catalog
check('generated catalog is in sync with i18n/launch_catalog.json', () => {
    const builder = require(path.join(root, 'scripts/build-i18n-catalog.js'));
    const committed = read('frontend/js/i18n-catalog.js');
    const tmp = path.join(os.tmpdir(), `i18n-catalog-${process.pid}.js`);
    const origOut = builder.OUT;
    // Build into memory by temporarily redirecting the output file.
    const src = JSON.parse(read('i18n/launch_catalog.json'));
    assert.ok(Object.keys(src).length > 1800, 'catalog covers the launch UI');
    for (const [k, v] of Object.entries(src)) {
        assert.equal(v.length, 4, `4 translations for "${k}"`);
        v.forEach((t, i) => assert.ok(typeof t === 'string' && t.trim(), `non-empty ${['fr', 'es', 'ar', 'pt'][i]} for "${k}"`));
    }
    fs.copyFileSync(origOut, tmp);
    try { builder.build(); assert.equal(read('frontend/js/i18n-catalog.js'), committed, 'rebuild changes nothing'); }
    finally { fs.copyFileSync(tmp, origOut); fs.unlinkSync(tmp); }
});
check('runtime and catalog load before the app in <head>', () => {
    const head = html.slice(0, html.indexOf('</head>'));
    assert.ok(head.includes('<script src="/js/i18n-catalog.js"></script>') && head.includes('<script src="/js/i18n-runtime.js"></script>'));
    assert.ok(html.indexOf('src="/js/i18n-runtime.js"') < html.indexOf('src="/js/app.js"'));
    const sw = read('frontend/sw.js');
    assert.ok(sw.includes('"/js/i18n-catalog.js"') && sw.includes('"/js/i18n-runtime.js"') && sw.includes('"/assets/cauldra-mark-96.png"'), 'offline shell caches the new files');
});

function loadRuntime(stored) {
    const attrs = {};
    const documentElement = { lang: 'en', dir: 'ltr', classList: { toggle(c, on) { attrs[c] = on; } } };
    const sandbox = {
        window: {}, navigator: { language: 'en-US' },
        localStorage: { getItem: () => stored },
        document: { documentElement, readyState: 'loading', addEventListener() {}, createTreeWalker: () => ({ nextNode: () => null }) },
        MutationObserver: class { observe() {} },
        Intl, Map, WeakMap, RegExp, Object, String, Number, Array, JSON,
    };
    sandbox.window.CAULDRA_COUNTRY_ISO = { Nigeria: 'NG', France: 'FR' };
    vm.createContext(sandbox);
    vm.runInContext(read('frontend/js/i18n-catalog.js'), sandbox);
    sandbox.window.CAULDRA_I18N_CATALOG = sandbox.window.CAULDRA_I18N_CATALOG || sandbox.CAULDRA_I18N_CATALOG;
    vm.runInContext(read('frontend/js/i18n-runtime.js'), sandbox);
    return { api: sandbox.window.CauldraI18n, documentElement, attrs };
}
check('runtime translates exact copy, patterns and country names; leaves data alone', () => {
    const { api, documentElement } = loadRuntime('fr');
    assert.equal(api.currentLanguage(), 'fr');
    assert.equal(api.translate('Business Day Closed'), 'Journée commerciale clôturée');
    assert.equal(api.translate('  Save Changes '), '  Enregistrer les modifications ');
    assert.equal(api.translate('Showing 1 – 20'), 'Affichage 1 – 20');
    assert.equal(api.translate('₦20,000/month'), '₦20,000/mois');
    assert.equal(api.translate('Peak Evaporated Milk 160g'), 'Peak Evaporated Milk 160g', 'product names are data');
    assert.equal(api.translate('AP-1186-31'), 'AP-1186-31');
    assert.equal(api.translate('Main Warehouse'), 'Main Warehouse', 'a location name is never rewritten by a count pattern');
    assert.equal(api.translate('2 Warehouses'), '2 entrepôts');
    assert.equal(api.translate('2,500 Products'), '2,500 produits');
    assert.equal(documentElement.dir, 'ltr');
    api.setLanguage('ar');
    assert.equal(documentElement.lang, 'ar');
    assert.equal(documentElement.dir, 'rtl', 'Arabic switches the page to RTL');
    assert.equal(api.translate('Open Business Day'), 'فتح يوم العمل');
    assert.equal(api.translate('Nigeria'), 'نيجيريا', 'country names come from the locale data');
    api.setLanguage('de');
    assert.equal(api.currentLanguage(), 'en', 'a hidden language falls back to English');
    assert.equal(api.translate('Open Business Day'), 'Open Business Day');
});
check('stored non-launch language starts in English', () => {
    const { api, documentElement } = loadRuntime('de');
    assert.equal(api.currentLanguage(), 'en');
    assert.equal(documentElement.dir, 'ltr');
});

// ---------------------------------------------------------------- polish
check('Cauldra mark replaces the generic "C" and plain-text brand treatments', () => {
    assert.ok(fs.existsSync(path.join(root, 'frontend/assets/cauldra-mark-96.png')));
    assert.ok(!offline.includes('<span aria-hidden="true">C</span>'), 'no generic C placeholder');
    assert.equal((offline.match(/cauldra-mark-96\.png/g) || []).length, 2, 'unlock + opt-in dialogs carry the mark');
    assert.match(html, /<header class="payment-header"><span class="payment-brand"><img src="\/assets\/cauldra-mark-96\.png"/);
    assert.match(read('frontend/email-verified.html'), /cauldra-mark-96\.png/);
    assert.match(read('backend/main.py'), /"Content-Security-Policy":"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self';/, 'the email-return page may load its own (same-origin) images');
    assert.match(html, /id="cauldra-auth-boot-screen"[^>]*><img src="\/assets\/cauldra-mark-96\.png"/);
});
check('offline Access action hierarchy markers are intact (verify-native-bundle)', () => {
    for (const marker of ['id="offline-opt-in-enable" class="offline-primary"', 'id="offline-pin-unlock" class="offline-primary"', 'type="submit" class="offline-primary">Enable on this device', 'type="submit" class="offline-primary">Enable Biometrics', 'id="offline-remove-confirm" class="offline-danger"']) {
        assert.ok(offline.includes(marker), marker);
    }
    assert.match(offline, /function setUnlockMode\(mode\)/);
    assert.match(offline, /retry\.classList\.toggle\("offline-primary", mode === "connection"\)/);
});
check('chat launcher steps aside for dialogs; focus ring; RTL rules', () => {
    assert.match(html, /id="chat-launcher-root"/);
    assert.match(baseCss, /body:has\(\[id\$="-modal"\]:not\(\.hidden\), dialog\[open\], #payment-overlay:not\(\[hidden\]\), #mobile-nav-overlay:not\(\.hidden\)\) #chat-launcher-root \{ display: none; \}/);
    assert.match(baseCss, /:focus-visible \{\s*outline: 2px solid #436BEE;/);
    assert.match(baseCss, /html\[dir="rtl"\] #mobile-nav-drawer\.-translate-x-full \{ --tw-translate-x: 100%; \}/);
    assert.match(baseCss, /html\[dir="rtl"\] #chat-launcher-root/);
});
check('onboarding: one name for the Business ID, step markers, phone row stacks on phones', () => {
    assert.ok(!html.includes('Business ID / Code'), 'no "Business ID / Code"');
    assert.ok(!app.includes('Employee Authentication`'), 'no implementation-style title');
    for (const s of ['Step 1 of 3', 'Step 2 of 3', 'Step 3 of 3', 'Your Position / Job Title', 'Business Phone']) assert.ok(html.includes(s), s);
    assert.match(html, /<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">\s*<div>\s*<label class="block font-medium text-textSec mb-1">Business Email/);
    assert.match(app, /const greetingName = String\(currentUserProfile\.firstname \|\| ""\)\.trim\(\) \|\| \(currentUserProfile\.username \|\| ""\)\.toUpperCase\(\);/);
});
check('no provider or server-variable names in user-facing errors', () => {
    const main = read('backend/main.py');
    for (const bad of ['Add GEMINI_API_KEY to the server environment', 'Add OPENAI_API_KEY to the server environment', 'Add RESEND_API_KEY to the server environment', 'Set RESEND_FROM on the server']) {
        assert.ok(!main.includes(`detail="${bad}`) && !main.includes(bad + '."'), bad);
    }
    for (const lang of ['en', 'fr', 'es', 'ar', 'pt']) {
        const start = app.indexOf(`\n            ${lang}: {`), end = app.indexOf('\n            }', start);
        assert.ok(start > 0);
    }
    assert.ok(!/enterCostRetailForAdvice: "Enter cost and retail price to get a Gemini/.test(app));
});

console.log(`LAUNCH_I18N_POLISH_PASS ${results.length}/${results.length}`);
results.forEach(r => console.log('  ok  ' + r));
