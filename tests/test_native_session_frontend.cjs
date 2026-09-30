// NATIVE-SESSION-001 frontend + native wiring. On Android the WebView writes cookies to disk in ~30 s
// batches; every auth response that sets, rotates or clears the refresh cookie must be followed by
// CauldraSession.flushCookies() so a process death cannot leave a revoked predecessor on disk.
// Static checks on frontend/js/app.js and the Android sources, plus the helper run in a sandbox.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const root = path.join(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'frontend', 'js', 'app.js'), 'utf8');
const javaDir = path.join(root, 'android', 'app', 'src', 'main', 'java', 'com', 'example', 'cauldra');
const activity = fs.readFileSync(path.join(javaDir, 'MainActivity.java'), 'utf8');
const plugin = fs.readFileSync(path.join(javaDir, 'CauldraSessionPlugin.java'), 'utf8');

function body(name) {
    const start = app.search(new RegExp(`(async\\s+)?function ${name}\\(`));
    assert.ok(start >= 0, `${name} exists`);
    const open = /\)\s*\{/g; open.lastIndex = start; const m = open.exec(app);
    let depth = 0;
    for (let j = m.index + m[0].length - 1; j < app.length; j++) {
        if (app[j] === '{') depth++;
        else if (app[j] === '}' && --depth === 0) return app.slice(start, j + 1);
    }
    throw new Error(`unbalanced ${name}`);
}

const FLUSH = 'await flushNativeSessionCookies()';
const checks = [];
async function check(label, fn) { await fn(); checks.push(label); }

(async () => {
    await check('the native plugin flushes the WebView cookie store and is registered', () => {
        assert.match(plugin, /@CapacitorPlugin\(name = "CauldraSession"\)/);
        assert.match(plugin, /@PluginMethod\s+public void flushCookies\(PluginCall call\)\s*\{[\s\S]*?CookieManager\.getInstance\(\)\.flush\(\);[\s\S]*?call\.resolve\(\);/);
        assert.doesNotMatch(plugin, /getCookie|setCookie|Log\./, 'never reads, writes or logs a cookie');
        assert.match(activity, /registerPlugin\(CauldraSessionPlugin\.class\);/);
        assert.match(activity, /public void onPause\(\)[\s\S]*?CookieManager\.getInstance\(\)\.flush\(\);/, 'onPause flush kept');
    });

    await check('a successful refresh flushes the rotated cookie before the session is used', () => {
        const b = body('refreshAccessToken');
        const ok = b.indexOf('if (!data.access_token) return false;');
        const flush = b.indexOf(FLUSH, ok);
        const outcome = b.indexOf('refreshAccessTokenLastOutcome = "ok"');
        assert.ok(ok > 0 && flush > ok && outcome > flush, 'access_token check -> flush -> ok outcome');
    });

    await check('a rejected refresh (204/401) flushes the cleared cookie', () => {
        assert.match(body('refreshAccessToken'),
            /if \(res\.status === 204 \|\| res\.status === 401\) \{ refreshAccessTokenLastOutcome = "no-session"; await flushNativeSessionCookies\(\); return false; \}/);
    });

    await check('sign-in (Admin and employee) flushes right after the token is accepted', () => {
        const b = body('handleSecureEmployeeAuthSubmit');
        assert.match(b, /auth\/admin-login/); assert.match(b, /auth\/employee-login/);
        assert.match(b, /authToken = data\.access_token; rememberSignedInSession\(\);\s*await flushNativeSessionCookies\(\);/);
    });

    await check('business registration flushes after the new session', () => {
        const at = app.indexOf('fetch(`${API_URL}/auth/register-business`');
        assert.ok(at > 0);
        assert.match(app.slice(at, at + 2500), /if \(response\.ok\) \{\s*authToken = data\.access_token; rememberSignedInSession\(\);\s*await flushNativeSessionCookies\(\);/);
    });

    await check('password change flushes the replacement cookie', () => {
        const at = app.indexOf('fetch(`${API_URL}/auth/change-password`');
        assert.ok(at > 0);
        assert.match(app.slice(at, at + 1800), /if \(data\.access_token\) authToken = data\.access_token;[\s\S]{0,120}await flushNativeSessionCookies\(\);/);
    });

    await check('sign-out, app reset and business deletion flush the cleared cookie', () => {
        const signOut = body('handleSignOut');
        const logout = signOut.indexOf('fetch(`${API_URL}/auth/logout`');
        const flush = signOut.indexOf(FLUSH, logout);
        assert.ok(logout > 0 && flush > logout && flush < signOut.indexOf('releaseDevicePushOnSignOut()', logout));
        assert.match(body('confirmFullAppReset'), /auth\/clear-client-session[\s\S]{0,200}await flushNativeSessionCookies\(\);/);
        const del = app.indexOf('fetch(`${API_URL}/business-profile/`, { method: "DELETE"');
        assert.match(app.slice(del, del + 600), /if \(!res\.ok\) throw[^\n]*\n\s*await flushNativeSessionCookies\(\);/);
    });

    await check('every endpoint that sets or clears the refresh cookie is followed by a flush', () => {
        for (const endpoint of ['auth/refresh', 'auth/admin-login', 'auth/employee-login', 'auth/register-business',
            'auth/change-password', 'auth/logout', 'auth/clear-client-session']) {
            const call = app.indexOf('`${API_URL}/' + endpoint + '`');
            assert.ok(call > 0, endpoint);
            assert.ok(app.indexOf(FLUSH, call) - call < 2500, `${endpoint} is followed by a flush`);
        }
    });

    // ---- the helper itself, run in a sandbox
    const helper = body('flushNativeSessionCookies');
    function load(windowObj, timers) {
        const ctx = { window: windowObj, setTimeout: timers?.setTimeout || setTimeout, Promise };
        vm.createContext(ctx);
        vm.runInContext(`${helper}; this.flushNativeSessionCookies = flushNativeSessionCookies;`, ctx);
        return ctx.flushNativeSessionCookies;
    }

    await check('native: calls CauldraSession.flushCookies once', async () => {
        let calls = 0;
        const f = load({ Capacitor: { isNativePlatform: () => true, Plugins: { CauldraSession: { flushCookies: async () => { calls++; } } } } });
        await f();
        assert.strictEqual(calls, 1);
    });

    await check('web, or an app build without the plugin: no-op', async () => {
        let calls = 0;
        const plug = { CauldraSession: { flushCookies: async () => { calls++; } } };
        await load({})();
        await load({ Capacitor: { isNativePlatform: () => false, Plugins: plug } })();
        await load({ Capacitor: { isNativePlatform: () => true, Plugins: {} } })();
        assert.strictEqual(calls, 0);
    });

    await check('a failing or hanging plugin never breaks or stalls authentication', async () => {
        const failing = load({ Capacitor: { isNativePlatform: () => true, Plugins: { CauldraSession: { flushCookies: async () => { throw new Error('boom'); } } } } });
        await failing();
        let capped = null;
        const fakeTimers = { setTimeout: (fn, ms) => { capped = ms; fn(); return 0; } };
        const hanging = load({ Capacitor: { isNativePlatform: () => true, Plugins: { CauldraSession: { flushCookies: () => new Promise(() => {}) } } } }, fakeTimers);
        await hanging();
        assert.strictEqual(capped, 2000);
    });

    console.log(`NATIVE-SESSION-001 frontend: ${checks.length} checks passed`);
    checks.forEach(c => console.log('  ok -', c));
})().catch(e => { console.error(e); process.exit(1); });
