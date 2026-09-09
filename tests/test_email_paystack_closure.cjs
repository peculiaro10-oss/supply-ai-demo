const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const emailReturnSource = fs.readFileSync(path.join(root, 'frontend/js/email-return.js'), 'utf8');
const paymentsSource = fs.readFileSync(path.join(root, 'frontend/js/payments.js'), 'utf8');
const appSource = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
const htmlSource = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');

const challenge = 'a'.repeat(64);
const flush = () => new Promise(resolve => setImmediate(resolve));

function emailElements(navigate) {
  const map = {
    'email-return-status': {textContent: ''},
    'email-return-help': {textContent: ''},
    'email-return-open': {hidden: true, textContent: '', href: ''},
  };
  map['email-return-open'].click = () => navigate(map['email-return-open'].href);
  return map;
}

async function runEmailReturn(data, {search, reject = false} = {}) {
  const navigations = [], replacements = [], timers = [];
  const elements = emailElements(url => navigations.push({type: 'click', url}));
  const location = {
    search: search || `?purpose=onboarding&challenge=${challenge}&code=pkce-code`,
    pathname: '/auth/email-verified',
    replace(url) {navigations.push({type: 'replace', url});},
    set href(url) {navigations.push({type: 'href', url});},
  };
  const context = vm.createContext({
    URL, URLSearchParams, location,
    history: {replaceState(_a, _b, url) {replacements.push(url);}},
    document: {title: 'Email verification', getElementById(id) {return elements[id];}},
    fetch: reject ? async () => {throw new Error('network');} : async () => ({
      ok: true, status: 200, json: async () => data,
    }),
    setTimeout(fn) {timers.push(fn); return timers.length;},
  });
  await vm.runInContext(emailReturnSource, context);
  await flush();
  return {elements, navigations, replacements, runTimers() {timers.splice(0).forEach(fn => fn());}};
}

function createStorage() {
  const values = new Map();
  return {
    getItem(key) {return values.has(key) ? values.get(key) : null;},
    setItem(key, value) {values.set(key, String(value));},
    removeItem(key) {values.delete(key);},
    dump(key) {return values.get(key);},
  };
}

async function runNativePayment(width, height) {
  const handlers = {}, domReady = [], windowEvents = {}, diagnostics = [];
  const ids = ['payment-overlay','payment-status','payment-check','payment-resume','payment-spinner',
    'payment-close','payment-retry-init','payment-plan','payment-amount','payment-explanation'];
  const elements = Object.fromEntries(ids.map(id => [id, {
    id, hidden: true, disabled: false, dataset: {}, style: {}, textContent: '',
    focus() {}, addEventListener(type, fn) {handlers[`${id}:${type}`] = fn;},
  }]));
  elements['payment-overlay'].querySelectorAll = () => [];
  const storage = createStorage();
  const listenerNames = [], openArguments = [];
  let openCount = 0, initCount = 0;
  const browser = {
    async addListener(name) {listenerNames.push(name); return {remove: async () => {}};},
    async openInWebView(args) {openArguments.push(args); openCount += 1; if (openCount === 1) throw new Error('simulated launch failure');},
    async close() {},
  };
  const app = {async addListener(name) {listenerNames.push(`App:${name}`); return {remove: async () => {}};}};
  const context = {
    URL, Intl, innerWidth: width, innerHeight: height, sessionStorage: storage,
    crypto: {randomUUID: () => '12345678-1234-1234-1234-123456789abc'},
    authToken: '', currentUserProfile: null, API_URL: 'https://api.example.com',
    onboardingSelectedPlan: 'starter', onboardingSelectedInterval: 'monthly',
    verifiedOnboardingReference: null, publicPlanCatalog: {},
    loadPublicPlanCatalog: async () => {}, openBusinessAuthModal() {}, switchBizAuthView() {}, showToast() {},
    loadData: async () => {}, openBillingModal() {}, loadBillingPanel: async () => {}, checkSubscriptionWarningForDashboard() {},
    document: {
      body: {style: {overflow: ''}}, activeElement: {focus() {}},
      getElementById(id) {return elements[id];},
      addEventListener(type, fn) {if (type === 'DOMContentLoaded') domReady.push(fn);},
    },
    fetch: async (_url, options) => {
      initCount += 1;
      const request = JSON.parse(options.body);
      assert.equal(request.challenge_id, challenge);
      return {ok: true, status: 200, json: async () => ({
        reference: 'cauldra_onboard_ref', access_code: 'access-code',
        authorization_url: 'https://checkout.paystack.com/secure/ref', amount_kobo: 5000,
        currency: 'NGN', plan: 'starter', billing_interval: 'monthly',
        callback_url: 'https://api.example.com/payments/return',
      })};
    },
    console: {info(event, fields) {diagnostics.push([event, fields]);}, warn() {}, error() {}},
    setTimeout, clearTimeout,
  };
  context.window = context;
  context.window.addEventListener = (type, fn) => {windowEvents[type] = fn;};
  context.window.Capacitor = {
    isNativePlatform: () => true,
    isPluginAvailable: name => ['InAppBrowser','App'].includes(name),
    registerPlugin: name => name === 'InAppBrowser' ? browser : app,
  };
  vm.runInContext(paymentsSource, vm.createContext(context));
  domReady.forEach(fn => fn());
  await context.window.CauldraPayments.start('onboarding', {
    email: 'owner@example.com', challenge_id: challenge, plan: 'starter', billing_interval: 'monthly',
  });
  assert.equal(initCount, 1);
  assert.equal(openCount, 1);
  assert.equal(elements['payment-resume'].hidden, false, 'failed launch must expose Resume secure checkout');
  const stored = JSON.parse(storage.dump('cauldra_payment_attempt_v1'));
  assert.equal(stored.reference, 'cauldra_onboard_ref');
  assert.equal(stored.access_code, 'access-code');
  await handlers['payment-resume:click']();
  assert.equal(openCount, 2, 'resume must open the same initialized checkout');
  assert.equal(initCount, 1, 'resume must not create another reference');
  assert.equal(openArguments[1].url, 'https://checkout.paystack.com/secure/ref');
  assert.deepEqual(Object.keys(openArguments[1].options).sort(), [
    'android','clearCache','clearSessionCache','closeButtonText','iOS','leftToRight',
    'mediaPlaybackRequiresUserAction','showNavigationButtons','showToolbar','showURL','toolbarPosition',
  ].sort());
  assert.deepEqual(Object.keys(openArguments[1].options.android).sort(), ['allowZoom','hardwareBack','pauseMedia']);
  assert.ok(listenerNames.includes('browserClosed'));
  assert.ok(listenerNames.includes('browserPageNavigationCompleted'));
  assert.ok(diagnostics.some(([event]) => event.includes('PAYSTACK_NATIVE_OPEN_FAILED')));
  assert.ok(diagnostics.some(([event]) => event.includes('PAYSTACK_NATIVE_OPENED')));
}

(async () => {
  const web = await runEmailReturn({
    status: 'verified', platform: 'web', return_target: 'https://app.example.com/',
    expected_return_origin: 'https://app.example.com',
  });
  assert.equal(web.elements['email-return-open'].textContent, 'Return to Cauldra');
  assert.equal(web.elements['email-return-open'].hidden, false);
  web.elements['email-return-open'].click();
  assert.equal(web.navigations[0].type, 'click');
  assert.match(web.navigations[0].url, /^https:\/\/app\.example\.com\/\?/);
  assert.match(web.navigations[0].url, /challenge=/);
  web.runTimers();
  assert.ok(web.navigations.some(item => item.type === 'replace'));
  assert.equal(web.replacements.length, 1, 'query must clear only after successful confirmation/target validation');

  const native = await runEmailReturn({
    status: 'verified', platform: 'native_android', return_target: 'cauldra://auth/email-verified',
  });
  assert.equal(native.elements['email-return-open'].textContent, 'Open Cauldra');
  native.elements['email-return-open'].click();
  assert.match(native.navigations[0].url, /^cauldra:\/\/auth\/email-verified\?/);

  const transient = await runEmailReturn({}, {reject: true});
  assert.equal(transient.replacements.length, 0, 'transient failure must retain recoverable PKCE query');

  const unsafe = await runEmailReturn({
    status: 'verified', platform: 'web', return_target: 'https://evil.example/',
    expected_return_origin: 'https://app.example.com',
  });
  assert.equal(unsafe.elements['email-return-open'].hidden, true);

  assert.match(appSource, /platform:evPlatform\(\)/);
  assert.match(appSource, /data\?\.detail\?\.reason === 'platform_mismatch'/);
  assert.match(appSource, /JSON\.stringify\(\{challenge_id:evChallengeId, platform:evPlatform\(\)\}\)/);
  assert.match(htmlSource, /name=["']viewport["'][^>]*width=device-width/i);

  for (const [width, height] of [[375,812],[768,1024],[1024,768],[1366,768]]) {
    await runNativePayment(width, height);
  }
  console.log('EMAIL_PAYSTACK_CLOSURE_PASS web_auto_return fallback_click exact_challenge native_resume_same_reference mobile=375x812 ipad_portrait=768x1024 ipad_landscape=1024x768 desktop=1366x768');
})().catch(error => {console.error(error); process.exitCode = 1;});
