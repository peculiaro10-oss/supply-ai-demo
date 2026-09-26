// Batch F frontend regressions (web bundle). Each section names its finding.
//   node tests/test_batch_f_frontend.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');

function extract(name, source = appSource) {
  let start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} exists`);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  let depth = 0;
  // Body starts after the parameter list (which may hold `= {}` defaults).
  for (let i = source.indexOf(') {', start) + 2; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`could not extract ${name}`);
}
function extractIife(marker) {
  const start = appSource.indexOf(marker);
  assert.ok(start >= 0, `${marker} exists`);
  const open = appSource.lastIndexOf('(function', start);
  const end = appSource.indexOf('})();', start);
  return appSource.slice(open, end + 5);
}
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

// --- ERR-001: readable errors when a response body is not JSON --------------
test('ERR-001 non-JSON bodies reject with a readable sentence and the status', async () => {
  const nativeJson = Response.prototype.json;
  const ctx = { Response, JSON, Error, String };
  vm.createContext(ctx);
  vm.runInContext(`${extract('unreadableResponseMessage')}\n${extractIife('function installReadableJsonErrors')}`, ctx);
  try {
    const cases = [
      [new Response('Internal Server Error', { status: 500 }), 500, "We couldn't complete that request right now. Please try again."],
      [new Response('<html><body>502 Bad Gateway</body></html>', { status: 502 }), 502, "We couldn't complete that request right now. Please try again."],
      [new Response('', { status: 401 }), 401, 'Your session could not be verified. Please sign in again.'],
      [new Response('Too Many Requests', { status: 429 }), 429, 'Too many attempts right now. Please wait a moment and try again.'],
      [new Response('oops', { status: 400 }), 400, 'Something went wrong. Please try again.'],
    ];
    for (const [response, status, message] of cases) {
      await assert.rejects(response.json(), err => {
        assert.equal(err.message, message);
        assert.equal(err.status, status);
        assert.doesNotMatch(err.message, /Unexpected|JSON|token/i);
        return true;
      });
    }
    assert.deepEqual(await new Response('{"detail":"Probe conflict."}', { status: 409 }).json(), { detail: 'Probe conflict.' });
    assert.deepEqual(await new Response('[1,2]').json(), [1, 2]);
  } finally {
    // Leave the global Response untouched for the rest of this process.
    Response.prototype.json = nativeJson;
  }
});

test('ERR-001 friendlyErrorMessage never shows parser text, even as its own fallback', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(`${extract('friendlyErrorMessage')}\n${extract('isTechnicalErrorText')}\nthis.f = friendlyErrorMessage;`, ctx);
  const parserTexts = [
    `Unexpected token 'I', "Internal S"... is not valid JSON`,
    'Unexpected end of JSON input',
    'JSON.parse: unexpected character at line 1 column 1 of the JSON data',
    'JSON Parse error: Unrecognized token \'<\'',
    'SyntaxError: Unexpected token < in JSON at position 0',
    '<!DOCTYPE html><html>',
  ];
  for (const text of parserTexts) {
    assert.equal(ctx.f(text, 'Export failed. Please try again.'), 'Export failed. Please try again.', text);
    assert.equal(ctx.f(text, text), 'Something went wrong. Please try again.', `self-fallback: ${text}`);
    assert.equal(ctx.f(text), 'Something went wrong. Please try again.');
  }
  assert.equal(ctx.f('Supplier email is not valid.', 'x'), 'Supplier email is not valid.', 'real messages pass');
  assert.equal(ctx.f('Upload failed because the file is too large.', 'x'), 'Upload failed because the file is too large.');
  assert.equal(ctx.f({ detail: 'Probe conflict.' }, 'x'), 'Probe conflict.');
  assert.equal(ctx.f('Internal Server Error', ''), '', 'an empty fallback stays empty for callers that test it');
});

// --- UI-001: no "Guest Hub" flash right after sign-in -----------------------
test('UI-001 role labels are derived from the role, never re-stamped by the language pass', () => {
  for (const id of ['role-badge-title', 'mobile-role-title']) {
    const tag = indexHtml.match(new RegExp(`<[^>]+id="${id}"[^>]*>`));
    assert.ok(tag, `${id} exists`);
    assert.doesNotMatch(tag[0], /data-i18n=/, `${id} must not carry a static data-i18n label`);
  }
  const setLanguage = extract('setLanguage');
  assert.ok(setLanguage.indexOf('syncRoleHubLabels()') > setLanguage.indexOf('applyStaticTranslations()'), 'language changes re-derive the role labels after the static pass');
  const els = { 'role-badge-title': { textContent: 'Guest Hub' }, 'mobile-role-title': { textContent: 'Guest Hub' } };
  const ctx = { document: { getElementById: id => els[id] || null }, role: 'admin', t: k => ({ 'navigation.adminHub': 'Admin Hub', 'navigation.guestHub': 'Guest Hub', 'navigation.staffHub': 'Staff Hub', 'navigation.managerHub': 'Manager Hub' })[k] };
  vm.createContext(ctx);
  vm.runInContext(`${extract('hubRoleLabel')}\nfunction getCurrentRole(){ return role; }\n${extract('syncRoleHubLabels')}\nthis.sync = syncRoleHubLabels;`, ctx);
  ctx.sync();
  assert.equal(els['role-badge-title'].textContent, 'Admin Hub');
  assert.equal(els['mobile-role-title'].textContent, 'Admin Hub');
  ctx.role = '';
  ctx.sync();
  assert.equal(els['role-badge-title'].textContent, 'Guest Hub');
});

// --- PLAN-006: the password dialog closes after a successful change ----------
test('PLAN-006 closing the password dialog clears the inline display:flex and the typed values', () => {
  const cls = () => { const set = new Set(); return { add: c => set.add(c), remove: c => set.delete(c), contains: c => set.has(c) }; };
  const els = { 'password-change-modal': { classList: cls(), style: { display: 'flex' } }, 'pw-change-current': { value: 'a' }, 'pw-change-new': { value: 'b' }, 'pw-change-confirm': { value: 'b' }, 'password-change-message': { classList: cls() }, 'pw-change-cancel-btn': { classList: cls() } };
  const ctx = { document: { getElementById: id => els[id] || null } };
  vm.createContext(ctx);
  vm.runInContext(`${extract('closePasswordChangeDialog')}\nthis.close = closePasswordChangeDialog;`, ctx);
  ctx.close();
  assert.equal(els['password-change-modal'].style.display, '', 'inline display removed');
  assert.ok(els['password-change-modal'].classList.contains('hidden'));
  for (const id of ['pw-change-current', 'pw-change-new', 'pw-change-confirm']) assert.equal(els[id].value, '');
  const handler = extract('handleMandatoryPasswordChange');
  assert.match(handler, /closePasswordChangeDialog\(\)/);
  assert.ok(handler.indexOf('await loadData()') > handler.indexOf('closePasswordChangeDialog()'), 'data reload happens after the dialog is closed');
  assert.match(handler, /try \{ await loadData\(\); \}\s*catch/, 'a reload failure is not reported as a password failure');
});

// --- SESS-001 / UX-004: an expired session is noticed and announced ----------
function detectorContext(currentToken) {
  const calls = [];
  const ctx = {
    API_URL: 'https://api.example', authToken: currentToken, Headers, Reflect, String,
    recheckSessionAfterUnauthorized: (token, mutating) => calls.push({ token, mutating }),
    window: {},
  };
  ctx.window.fetch = (input, init) => Promise.resolve({ status: ctx.nextStatus || 401 });
  vm.createContext(ctx);
  vm.runInContext(extractIife('function installSessionExpiryDetector'), ctx);
  return { ctx, calls };
}
test('SESS-001 a 401 on an API request made with the current token triggers one session re-check', async () => {
  const { ctx, calls } = detectorContext('tok-1');
  const auth = { headers: { Authorization: 'Bearer tok-1' } };
  await ctx.window.fetch('https://api.example/products/7', { method: 'DELETE', ...auth });
  assert.deepEqual(calls, [{ token: 'tok-1', mutating: true }], 'destructive request');
  await ctx.window.fetch('https://api.example/products/', auth);
  assert.equal(calls[1].mutating, false, 'reads re-check too, without the retry hint');
  await ctx.window.fetch('https://api.example/auth/admin-login', { method: 'POST', ...auth });
  await ctx.window.fetch('https://api.example/auth/change-password', { method: 'POST', ...auth });
  await ctx.window.fetch('https://api.example/products/', { headers: { Authorization: 'Bearer old-token' } });
  await ctx.window.fetch('https://api.example/products/');
  await ctx.window.fetch('https://elsewhere.example/x', auth);
  ctx.nextStatus = 403;
  await ctx.window.fetch('https://api.example/products/', auth);
  assert.equal(calls.length, 2, 'wrong-password endpoints, stale tokens, no token, other hosts and 403 never end a session');
});

test('SESS-001 only a backend "no session" answer ends the session; other failures leave it', async () => {
  for (const [outcome, ends] of [['no-session', true], ['unreachable', false], ['error', false], ['skipped', false]]) {
    const ended = [];
    const ctx = { authToken: 'tok', signOutInProgress: false, businessDeletionInProgress: false, refreshAccessTokenLastOutcome: outcome,
      SESSION_EXPIRED_MESSAGE: 'Your session has expired. Please sign in again.',
      refreshAccessToken: async () => false, handleAuthenticationFailure: m => ended.push(m) };
    vm.createContext(ctx);
    vm.runInContext(`${extract('refreshSessionOrEnd')}\nthis.run = refreshSessionOrEnd;`, ctx);
    await ctx.run();
    assert.equal(ended.length, ends ? 1 : 0, outcome);
    if (ends) assert.equal(ended[0], 'Your session has expired. Please sign in again.');
  }
  const refresh = extract('refreshAccessToken');
  assert.match(refresh, /res\.status === 204 \|\| res\.status === 401\) \{ refreshAccessTokenLastOutcome = "no-session"/);
  const failure = extract('handleAuthenticationFailure');
  assert.match(failure, /const hadSession = !!authToken;/);
  assert.match(failure, /if \(hadSession\) showSessionEndedNotice\(/, 'losing a real session is announced, a guest load is not');
  assert.match(extract('startAuthRefreshHeartbeat'), /refreshSessionOrEnd\(\)/, 'the heartbeat ends a refused session');
  assert.match(extract('handleSignOut'), /forgetSignedInSession\(\)/, 'a deliberate sign-out is never announced as an expiry');
});

test('SESS-001 the notice closes app dialogs and opens Sign In with the reason', () => {
  const notice = extract('showSessionEndedNotice');
  assert.match(notice, /\.fixed\[id\$="-modal"\]:not\(\.hidden\)/);
  assert.match(notice, /switchBizAuthView\("signin"\)/);
  assert.match(notice, /auth-session-message/);
  assert.match(extract('openBusinessAuthModal'), /auth-session-message"\)\?\.classList\.add\("hidden"\)/, 'an ordinary open does not show a stale reason');
});

// --- PLAN-003: a usable billing Retry ---------------------------------------
test('PLAN-003 billing loads one at a time and every attempt restores the spinner', async () => {
  let runs = 0; let release;
  const ctx = { loadBillingPanelOnce: () => { runs++; return new Promise(r => { release = r; }); } };
  vm.createContext(ctx);
  vm.runInContext(`let billingPanelLoadInFlight = null;\n${extract('loadBillingPanel')}\nthis.load = loadBillingPanel;`, ctx);
  const a = ctx.load(); const b = ctx.load();
  assert.equal(runs, 1, 'a second Retry while loading does not start another request');
  assert.equal(a, b);
  release(); await a;
  ctx.load();
  assert.equal(runs, 2, 'after it settles, Retry works again');
  const once = extract('loadBillingPanelOnce');
  assert.match(once, /loadingEl\.innerHTML = BILLING_LOADING_HTML/, 'the spinner is restored on every attempt');
  assert.match(once, /e\.status === 401/, 'an expired session is not offered a Retry that cannot work');
  assert.match(once, /escapeHtml\(message\)/);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`PASS ${name}`); }
    catch (err) { failed++; console.error(`FAIL ${name}\n  ${err.stack || err}`); }
  }
  if (failed) { console.error(`${failed} of ${tests.length} failed`); process.exit(1); }
  console.log(`All ${tests.length} Batch F frontend checks passed`);
})();
