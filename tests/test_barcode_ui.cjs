// Barcode hardening (UI): an editable barcode on Edit Product, and no provider
// internals in customer-facing copy.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');

// ---------------------------------------------------------------- Edit Product barcode field
const editModal = html.slice(html.indexOf('id="edit-product-modal"'), html.indexOf('id="edit-product-modal"') + 12000);
const input = editModal.match(/<input[^>]*id="edit-p-barcode"[^>]*>/);
assert.ok(input, 'the Edit Product modal has a barcode input');
assert.match(input[0], /type="text"/, 'it is a text field, so leading zeros survive');
assert.ok(!/required/.test(input[0]), 'a barcode stays optional — a product may have none');
assert.match(editModal, /data-i18n="products\.barcodeOptional"/, 'its label goes through the localization system');
assert.match(app, /barcodeOptional: "Barcode \(optional\)"/, 'the label key exists in the bundle');

// it loads the product's current barcode
assert.match(app, /getElementById\("edit-p-barcode"\)\.value = product\.barcode \|\| ""/,
  'opening the edit modal shows the barcode the product already has');

// it is sent on save, and an empty field clears it
assert.match(app, /const barcode = barcodeEl \? \(barcodeEl\.value\.trim\(\) \|\| null\) : undefined;/,
  'an empty field is sent as null (clear), not as an empty string');
assert.match(app, /const payload = \{ \.\.\.\(barcode !== undefined \? \{ barcode \} : \{\}\), name, sku/,
  'the PATCH payload carries the barcode');

// the edit path must not call the lookup chain
const editFn = app.slice(app.indexOf('async function _submitUpdateProduct()'), app.indexOf('async function applyOfflineEditToPendingProductCreate'));
assert.ok(!/catalog\/barcode-lookup/.test(editFn), 'editing a product never calls the barcode lookup endpoint');

// ---------------------------------------------------------------- customer-facing copy
assert.ok(!/\(source: " \+/.test(app), 'the toast no longer appends the provider/source name');
assert.ok(!/"Product identified: " \+/.test(app), 'the hard-coded English toast is gone');
assert.match(app, /t\("products\.productIdentified", \{ name:/, 'the toast goes through the localization system');
assert.match(app, /productIdentified: "Product identified: \{name\}"/, 'the key exists in the bundle');

assert.ok(!/"Searching database\.\.\."/.test(app), 'the hard-coded sentinel is gone');
assert.match(app, /const SENTINEL = t\("common\.loading"\);/, 'the sentinel uses an existing localized string');

// no provider name anywhere a user could read it: every mention must be in a comment or a console line
for (const line of app.split('\n')) {
  if (!/upcitemdb/i.test(line)) continue;
  const isComment = /^\s*(\/\/|\*|\/\*)/.test(line);
  const isConsole = /console\.(log|warn|error|info)/.test(line);
  const isSourceCompare = /data\.source === "upcitemdb_unavailable"/.test(line);
  assert.ok(isComment || isConsole || isSourceCompare,
    `the provider is named outside a comment/console/source-check: ${line.trim().slice(0, 120)}`);
  assert.ok(!/showToast\(/.test(line), `the provider name must never reach a toast: ${line.trim().slice(0, 120)}`);
}

// ---------------------------------------------------------------- the chains are unchanged
const lookupFn = app.slice(app.indexOf('async function lookupBarcode()'), app.indexOf('function openSaleModal()'));
assert.match(lookupFn, /globalProducts\.find\(p => p\.barcode/, 'the client-side own-inventory hint is still first');
assert.match(lookupFn, /\$\{API_URL\}\/catalog\/barcode-lookup/, 'Add Product still uses the one lookup endpoint');
assert.match(lookupFn, /data\.source === "upcitemdb_unavailable"/, 'provider-unavailable is still distinguished from a miss');
assert.match(lookupFn, /t\("products\.productNotFoundManualEntry"\)/, 'a genuine miss still offers manual entry');

const posFn = app.slice(app.indexOf('function resolveScannedProduct(rawCode)'), app.indexOf('function resolveScannedProduct(rawCode)') + 1200);
assert.ok(!/barcode-lookup|general-catalog|upcitemdb/i.test(posFn), 'POS scanning still matches own products only');

console.log('barcode hardening UI checks passed');
