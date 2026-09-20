// REFUND-001 (presentation only) — refunds must read as their own line, never as negative sales.
// The posting date is deliberately unchanged; these assertions are about wording and layout only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/js/app.js'), 'utf8');

// --- Profit panel: gross sales / refunds / net sales, and no bare net "Sales" row
assert.ok(/\["Gross sales", grossSales,/.test(appSource), 'the Profit panel shows Gross sales');
assert.ok(/\["Refunds", -refundAmount,/.test(appSource), 'the Profit panel shows Refunds as its own line');
assert.ok(/\["Net sales", summary\.sales,/.test(appSource), 'the Profit panel shows Net sales');
assert.ok(!/\["Sales", summary\.sales,/.test(appSource), 'the old single net "Sales" row must be gone');

// the refunds line only appears when there is something to report
assert.ok(/refundAmount \|\| prevRefund \? \[\["Refunds"/.test(appSource),
  'Refunds is conditional on there being a refund in this period or the comparison period');

// gross falls back to sales + refunds when the API omits gross_sales
assert.ok(/gross_sales === undefined \|\| summary\.gross_sales === null/.test(appSource),
  'gross sales degrades safely when the field is absent');

// --- All-locations view
assert.ok(/Net sales \$\{formatCurrencyAs\(r\.sales, group\.currency\)\}/.test(appSource),
  'per-location figure is labelled Net sales');
assert.ok(/Refunds \$\{formatCurrencyAs\(-Number\(r\.refund_amount\), group\.currency\)\}/.test(appSource),
  'per-location refunds are shown when non-zero');
assert.ok(/const totalRefunds = group\.rows\.reduce/.test(appSource), 'group totals carry refunds');

// --- Export mirrors the screen
assert.ok(/label: "Gross sales", amount:/.test(appSource), 'export has Gross sales');
assert.ok(/label: "Refunds", amount: -Number\(summary\.refund_amount \|\| 0\)/.test(appSource), 'export has Refunds');
assert.ok(/label: "Net sales", amount: summary\.sales/.test(appSource), 'export has Net sales');
assert.ok(!/\{ label: "Sales", amount: summary\.sales \}/.test(appSource), 'the old export "Sales" row is gone');

// --- Dashboard tile
assert.ok(/dashboardRefundsToday \? 'Net sales today' : 'Sales Today'/.test(appSource),
  'the dashboard says Net sales today when the session contains refunds');
assert.ok(/Gross sales \$\{formatCurrency\(Number\(dashboardSalesToday \|\| 0\) \+ dashboardRefundsToday\)\} less refunds/.test(appSource),
  'the dashboard explains the split on hover');

// --- Sales History: a negative day must not render as success green
assert.ok(/Number\(x\.net_sales\) < 0 \? "text-danger" : "text-success"/.test(appSource),
  'a negative day is not shown in success green');
assert.ok(/Refunded \$\{formatCurrency\(x\.refund_total\)\}/.test(appSource),
  'the existing per-day refund line is preserved');

// --- Nothing about refund attribution or dates was touched
assert.ok(!/original_sale_date|refund_business_day_override|moveRefundTo/.test(appSource),
  'no attempt to re-date refunds');

console.log('REFUND_PRESENTATION_PASS profit_panel by_location export dashboard sales_history no_redating');
