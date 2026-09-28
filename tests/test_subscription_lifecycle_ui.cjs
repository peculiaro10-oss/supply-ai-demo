// SUB-LIFECYCLE-001 — Billing shows exact paid-through time, a paused notice
// with Renew for the Admin (ask-the-Admin for others), and never treats a
// paused (past-due) subscription as current or upgradeable.
//   node tests/test_subscription_lifecycle_ui.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
const sw = fs.readFileSync(path.join(root, 'frontend/sw.js'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'i18n/launch_catalog.json'), 'utf8'));
const results = [];
const check = (name, fn) => { fn(); results.push(name); };
const fnBody = name => { const i = app.indexOf(`function ${name}(`); assert.ok(i > 0, name); return app.slice(i, app.indexOf('\n        }\n', i)); };

check('paid-through and trial end are shown with date AND time in the business timezone', () => {
    const status = fnBody('renderBillingStatus');
    assert.match(status, /formatBusinessDateTime\(usage\.trial_ends_at\)/);
    assert.match(status, /formatBusinessDateTime\(usage\.next_billing_at\)/);
    assert.match(status, /subscription\.paidThrough/);
    assert.match(status, /renderBillingLifecycleNotice\(usage, isAdmin, saved\)/);
});
check('paused notice: server reason, data safe, Renew for Admin only, ask-the-Admin otherwise', () => {
    const notice = fnBody('renderBillingLifecycleNotice');
    assert.match(notice, /usage\.access_paused/);
    assert.match(notice, /usage\.blocked_message/);
    assert.match(notice, /subscription\.pausedDataSafe/);
    assert.match(notice, /isAdmin && usage\.plans\?\.\[usage\.plan\]/);
    assert.match(notice, /subscription\.askAdmin/);
    assert.match(notice, /openPlanChangeConfirm\(usage\.plan, [^)]*'checkout'\)/, 'Renew reuses the tested Paystack checkout');
    assert.match(notice, /3 \* 86400000/, 'ending-soon warning within 3 days');
});
check('a paused subscription is never "current", downgradeable or upgradeable', () => {
    const cards = fnBody('renderBillingPlanCards');
    assert.match(cards, /const isCurrent = [^;]*\['trialing', 'active'\]\.includes\(usage\.status\);/);
    assert.doesNotMatch(cards, /\['trialing', 'active', 'past_due'\]/);
    assert.match(cards, /const isDowngradeTarget = usage\.status === 'active'/);
    assert.match(cards, /id === currentPlan && usage\.access_paused\) \{ buttonText = t\('subscription\.actionRenew'/);
    assert.match(fnBody('subscribeNow'), /billingUsageCache\?\.status === 'active' && !billingUsageCache\?\.access_paused/);
});
check('new wording is translated for every launch language', () => {
    for (const text of ['Renew {plan}', 'Subscription paused', 'Paid through {time}',
        'Your business data is safe. Nothing has been deleted.',
        'Cauldra unlocks as soon as a renewal payment is confirmed.',
        "Your Cauldra subscription is paused because the renewal payment has not been confirmed. Your business data is safe. Renew from Subscription & Billing to continue."]) {
        assert.equal((catalog[text] || []).length, 4, text);
    }
});
check('service-worker cache bumped so installed web clients take the new Billing screen', () => {
    assert.match(sw, /const SHELL_CACHE = "cauldra-shell-v21-subscription-policy";/);
});
const offline = fs.readFileSync(path.join(root, 'frontend/js/offline.js'), 'utf8');
const payments = fs.readFileSync(path.join(root, 'frontend/js/payments.js'), 'utf8');
check('offline: a device that knows the paid-through time passed refuses new work (runs the real guard)', () => {
    const start = offline.indexOf('    const SUBSCRIPTION_PAUSED_OFFLINE');
    const end = offline.indexOf('    async function enqueue(');
    const guard = new Function(offline.slice(start, end) + '\nreturn subscriptionPausedReason;')();
    const at = Date.parse('2026-10-01T09:00:00Z');
    const snap = usage => ({ cache: { '/subscription/usage': { value: usage } } });
    assert.equal(guard(snap({ status: 'active', current_period_end: '2026-10-01T09:00:00Z' }), at - 1000), null, 'one second before');
    assert.match(guard(snap({ status: 'active', current_period_end: '2026-10-01T09:00:00Z' }), at), /paused/, 'exactly at expiry');
    assert.match(guard(snap({ status: 'trialing', trial_ends_at: '2026-10-01T09:00:00Z' }), at + 1), /paused/);
    assert.match(guard(snap({ status: 'past_due', access_paused: true }), at - 86400000), /paused/, 'server already said paused');
    assert.equal(guard({ cache: {} }, at), null, 'no cached billing: nothing is invented');
    const enqueue = offline.slice(end, offline.indexOf('const sealed', end));
    assert.match(enqueue, /subscriptionPausedReason\(active\.snapshot\)[\s\S]*throw Object\.assign\(new Error\(pausedReason\), \{ code: "SUBSCRIPTION_PAUSED" \}\)/,
        'checked before anything is sealed into the outbox');
});
check('offline: work queued before the pause is kept; a paused sync keeps retrying with the real reason', () => {
    const i = app.indexOf('} else if (res.status === 402) {');
    assert.ok(i > 0);
    const branch = app.slice(i, app.indexOf('} else if ([400, 403, 404, 409, 422]', i));
    assert.match(branch, /scheduleOutboxRetry\(op, friendlyErrorMessage\(data/);
    assert.doesNotMatch(branch, /removeOutboxOp|status: "conflict"/, 'never deleted, never marked refused');
    assert.match(app, /function applyOfflineSubscriptionPause\(snapshot\)[\s\S]{0,600}setSubscriptionBlocked\(reason\)/);
    assert.match(app, /setInterval\(check, 60000\)/);
});
check('paused: notification deep links never open an operational screen', () => {
    assert.match(fnBody('navigateToDeepLink'), /if \(subscriptionBlockedMessage && !\['subscription', 'account_security'\]\.includes\(kind\)\) \{ showSubscriptionBlockedNotice\(\); return; \}/);
});
check('payment in progress: one attempt, truthful "being confirmed", Check status instead of a second payment', () => {
    assert.match(payments, /\['RENEWAL_IN_PROGRESS', 'ALREADY_PAID'\]\.includes\(data\.detail\?\.code\) && data\.detail\?\.reference[\s\S]{0,400}status\('pending'/);
    const notice = fnBody('renderBillingLifecycleNotice');
    assert.match(notice, /usage\.renewal_in_progress/);
    assert.match(notice, /subscription\.renewalConfirming/);
    assert.match(notice, /subscription\.autoRetryNext/);
    for (const text of ['Renewal being confirmed', 'Automatic attempts on your saved card have ended. You can renew at any time.',
        "Your Cauldra subscription is paused, so new work can't be saved on this device. Your business data is safe. Connect to the internet to renew."]) {
        assert.equal((catalog[text] || []).length, 4, text);
    }
});
console.log(`PASS ${results.length} checks:\n - ${results.join('\n - ')}`);
