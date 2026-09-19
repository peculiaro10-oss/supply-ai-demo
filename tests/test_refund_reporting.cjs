// REFUND-002 — the Profit view must not report a refund-only period as empty.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/js/app.js'), 'utf8');

// lift the helper out of app.js and exercise it directly
const match = appSource.match(/function profitPeriodHasActivity\(summary\) \{[\s\S]*?\n {8}\}/);
assert.ok(match, 'profitPeriodHasActivity must exist in app.js');
const context = vm.createContext({});
vm.runInContext(match[0] + '; this.profitPeriodHasActivity = profitPeriodHasActivity;', context);
const hasActivity = context.profitPeriodHasActivity;

const empty = { sales: 0, gross_sales: 0, refund_amount: 0, expenses: 0, cogs: 0, gross_profit: 0, net_profit: 0,
  transaction_count: 0, expense_count: 0, refund_transaction_count: 0, refunded_units: 0, sale_line_count: 0 };

assert.equal(hasActivity(empty), false, 'a genuinely empty period stays empty');
assert.equal(hasActivity(null), false, 'no payload is not activity');
assert.equal(hasActivity({ ...empty, transaction_count: 1 }), true, 'a sale is activity');
assert.equal(hasActivity({ ...empty, expense_count: 1 }), true, 'an expense is activity');

// the audit's exact case: today's only event is a refund of 46,000
const refundOnly = { ...empty, sales: -46000, cogs: -38000, gross_profit: -8000, net_profit: -8000,
  refund_amount: 46000, refunded_units: 1, refund_transaction_count: 1 };
assert.equal(hasActivity(refundOnly), true, 'a refund-only period must render, not read as empty');
assert.equal(hasActivity({ ...empty, refund_transaction_count: 1 }), true, 'refund count alone is activity');
assert.equal(hasActivity({ ...empty, net_profit: -8000 }), true, 'a negative figure alone is activity');
assert.equal(hasActivity({ ...empty, sales: -46000 }), true, 'negative sales alone is activity');

// both Profit surfaces must use it — the single-period panel and the all-locations view
assert.ok(/if \(!profitPeriodHasActivity\(summary\)\) \{/.test(appSource), 'the period panel uses the shared test');
assert.ok(/rows\.filter\(r => profitPeriodHasActivity\(r\)\)/.test(appSource), 'the by-location view uses the shared test');
assert.ok(!/summary\.transaction_count === 0 && summary\.expense_count === 0/.test(appSource),
  'the old sales-only empty test must be gone');

console.log('REFUND_REPORTING_PASS refund_only_period_renders negative_values_count both_profit_surfaces');
