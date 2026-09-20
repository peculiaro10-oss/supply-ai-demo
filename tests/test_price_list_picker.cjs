// PM-004 — the price-list picker must offer the file type the endpoint accepts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');

const wrap = html.match(/<div id="price-list-file-wrap"[\s\S]*?<\/div>/);
assert.ok(wrap, 'the price-list upload control is present');

const input = wrap[0].match(/<input id="price-source-file"[^>]*>/);
assert.ok(input, 'the file input is present');
assert.match(input[0], /accept="\.csv,text\/csv"/, 'the picker offers CSV, which is what the endpoint accepts');
assert.ok(!/image\/\*/.test(input[0]), 'it no longer offers images the endpoint refuses');
assert.ok(!/\.pdf/.test(input[0]), 'it no longer offers PDFs the endpoint refuses');

assert.match(wrap[0], /CSV/, 'the label says CSV');
assert.ok(!/PDF or image/.test(wrap[0]), 'the old "PDF or image" label is gone');
assert.match(wrap[0], /SKU, barcode or name/, 'the label says which identifier the first column should hold');

console.log('PM-004 picker checks passed');
