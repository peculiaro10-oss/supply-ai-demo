// AI-004 — both Margin Advisor call sites must send the field the API asks for.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/js/app.js'), 'utf8');

const calls = appSource.match(/fetch\(`\$\{API_URL\}\/ai\/suggest-margin`[^;]*?\}\)/gs) || [];
assert.equal(calls.length, 2, 'both advisor call sites are present (Add Product and Edit Product)');

for (const call of calls) {
  const body = call.match(/body:JSON\.stringify\(\{([^}]*)\}\)/);
  assert.ok(body, 'the call sends a JSON body');
  assert.match(body[1], /(^|[{,\s])name([,\s]|$)/, 'it sends the canonical `name` field');
  assert.ok(!/product_name/.test(body[1]), 'the mismatched `product_name` spelling is gone');
  assert.match(body[1], /cost_price:cost/, 'it still sends the cost price');
  assert.match(body[1], /category/, 'it still sends the category');
}

// The UI renders the structured figures, so they must still be read from the payload.
assert.ok(/wholesale: formatCurrency\(data\.suggested_wholesale\)/.test(appSource),
  'the advisor still shows the wholesale figure the API returns');
assert.ok(/retail: formatCurrency\(data\.suggested_retail\)/.test(appSource),
  'the advisor still shows the retail figure the API returns');

// The machine-readable line the server strips must never be displayed by the client.
assert.ok(!/CAULDRA_PRICES/.test(appSource), 'the machine-readable price line never reaches the UI');

console.log('AI-004 UI checks passed');
