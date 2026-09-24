// NATIVE-PAY-001 / PAY-001 — runs frontend/js/payments.js in a VM against a
// simulated packaged Android app: the native bridge exposes plugins only as
// Capacitor.Plugins.<Name> and has NO Capacitor.registerPlugin (the packaged app
// loads no @capacitor/core bundle). Before the fix, launchNative() called
// Capacitor.registerPlugin('InAppBrowser') and the checkout could never open.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'js', 'payments.js'), 'utf8');

function element(id) {
    return { id, hidden: true, disabled: false, textContent: '', dataset: {}, listeners: {},
        addEventListener(type, fn) { this.listeners[type] = fn; }, focus() {}, querySelectorAll() { return []; } };
}

function harness({ withRegisterPlugin = false, plugins = true } = {}) {
    const elements = {};
    const el = id => (elements[id] = elements[id] || element(id));
    const calls = { opened: [], fetches: [], registerPlugin: 0, billingOpened: 0 };
    const store = new Map();
    const inAppBrowser = {
        async addListener(name, fn) { return { remove: async () => {} }; },
        async openInWebView(model) { calls.opened.push(model.url); },
        async close() {},
    };
    const appPlugin = { async addListener() { return { remove: async () => {} }; } };
    const capacitor = {
        isNativePlatform: () => true,
        isPluginAvailable: name => plugins && ['InAppBrowser', 'App'].includes(name),
        Plugins: plugins ? { InAppBrowser: inAppBrowser, App: appPlugin } : {},
    };
    if (withRegisterPlugin) capacitor.registerPlugin = name => { calls.registerPlugin++; return capacitor.Plugins[name]; };
    const context = {
        window: { Capacitor: capacitor, addEventListener() {} },
        document: { getElementById: el, addEventListener() {}, body: { style: {} }, activeElement: null,
            head: { appendChild() {} }, createElement: () => ({}) },
        sessionStorage: { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) },
        crypto: { randomUUID: () => 'uuid-' + Math.random().toString(16).slice(2) },
        console: { info() {}, log() {}, error() {} },
        URL, Intl, setTimeout, clearTimeout, JSON, Object, Error, Promise, Number, String, Array,
        API_URL: 'https://qa.example', authToken: 'token', currentUserProfile: { business_id: 7, id: 3 },
        openBillingModal: () => { calls.billingOpened++; },
        fetch: async (url, init) => {
            calls.fetches.push(url);
            if (url.endsWith('/subscription/checkout')) {
                return { ok: true, status: 200, json: async () => ({
                    reference: 'cauldra_ref_1', access_code: 'ac_1', authorization_url: 'https://checkout.paystack.com/ac_1',
                    amount_kobo: 2000000, currency: 'NGN', plan: 'Business', billing_interval: 'monthly',
                    callback_url: 'https://qa.example/payment-return' }) };
            }
            return { ok: false, status: 202, json: async () => ({ status: 'pending' }) };
        },
    };
    context.window.CauldraPayments = undefined;
    vm.createContext(context);
    vm.runInContext(source + '\nthis.__payments = window.CauldraPayments;', context);
    return { payments: context.__payments, calls, el, store };
}

(async () => {
    // 1. Packaged-app bridge without registerPlugin: the checkout opens.
    {
        const { payments, calls, el } = harness();
        await payments.start('checkout', { plan: 'starter', billing_interval: 'monthly' });
        assert.deepStrictEqual(calls.opened, ['https://checkout.paystack.com/ac_1'], 'Paystack TEST checkout opened in the in-app browser');
        assert.notStrictEqual(el('payment-overlay').dataset.state, 'error', 'no "checkout unavailable" error');
    }
    // 2. A bundle that does provide registerPlugin keeps using it.
    {
        const { payments, calls } = harness({ withRegisterPlugin: true });
        await payments.start('checkout', { plan: 'starter', billing_interval: 'monthly' });
        assert.strictEqual(calls.opened.length, 1);
        assert.ok(calls.registerPlugin >= 1, 'registerPlugin used when present');
    }
    // 3. No plugin installed: a clear, recoverable error, never a crash.
    {
        const { payments, calls, el } = harness({ plugins: false });
        await payments.start('checkout', { plan: 'starter', billing_interval: 'monthly' });
        assert.strictEqual(calls.opened.length, 0);
        assert.strictEqual(el('payment-overlay').dataset.state, 'error');
    }
    // 4. PAY-001: an unconfirmed attempt is dismissed for good by closing it,
    //    and "Check payment status" confirms once, without starting a poll.
    {
        const { payments, calls, el, store } = harness();
        store.set('cauldra_payment_attempt_v1', JSON.stringify({ kind: 'method', reference: 'cauldra_method_x', ownerId: '7:3' }));
        await payments.resumeReturn(null);
        const confirms = () => calls.fetches.filter(u => u.endsWith('/confirm')).length;
        assert.strictEqual(confirms(), 1, 'one confirm on load');
        assert.strictEqual(el('payment-overlay').dataset.state, 'pending');
        assert.ok(!/Do not pay again/.test(el('payment-status').textContent), 'no claim that money is in flight');
        await new Promise(r => setTimeout(r, 5200));
        assert.strictEqual(confirms(), 1, 'no automatic re-poll for a stored attempt');
        payments.close();
        assert.strictEqual(store.has('cauldra_payment_attempt_v1'), false, 'the stale latch is cleared on close');
        assert.strictEqual(el('payment-overlay').hidden, true);
    }
    console.log('test_native_paystack_bridge: 4 scenarios passed');
})().catch(err => { console.error(err); process.exit(1); });
