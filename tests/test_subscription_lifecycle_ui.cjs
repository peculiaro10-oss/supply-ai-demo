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
    assert.match(status, /renderBillingLifecycleNotice\(usage, isAdmin\)/);
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
    assert.match(sw, /const SHELL_CACHE = "cauldra-shell-v18-subscription-lifecycle";/);
});
console.log(`PASS ${results.length} checks:\n - ${results.join('\n - ')}`);
