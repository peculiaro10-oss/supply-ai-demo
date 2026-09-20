// Localization parity for the barcode copy: every supported locale must carry both
// keys, with the interpolation convention the bundle already uses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const app = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/js/app.js'), 'utf8');

// Locale bundles are `            <code>: {` at the top level of the translations object.
const starts = [...app.matchAll(/\n {12}([a-z]{2}(?:-[A-Za-z]{2})?): \{\n/g)].map(m => ({ code: m[1], at: m.index }));
assert.ok(starts.length >= 35, `expected the full locale set, found ${starts.length}`);
assert.equal(starts[0].code, 'en', 'English is the default bundle and must come first');

const bodies = starts.map((s, i) => ({
  code: s.code,
  body: app.slice(s.at, i + 1 < starts.length ? starts[i + 1].at : app.length),
}));

// a string literal that may contain escaped characters
const value = (body, key) => {
  const m = body.match(new RegExp(`\\n\\s+${key}: "((?:[^"\\\\]|\\\\.)*)"`));
  return m ? m[1] : null;
};

const missing = [];
const bad = [];
for (const { code, body } of bodies) {
  const optional = value(body, 'barcodeOptional');
  const identified = value(body, 'productIdentified');
  if (!optional || !identified) { missing.push(code); continue; }

  // the placeholder convention is {name}, the same one t()/applyTranslationVars uses
  if (!identified.includes('{name}')) bad.push(`${code}: productIdentified has no {name}`);
  const placeholders = [...identified.matchAll(/\{([a-zA-Z_]+)\}/g)].map(m => m[1]);
  if (placeholders.length !== 1 || placeholders[0] !== 'name') {
    bad.push(`${code}: productIdentified placeholders ${JSON.stringify(placeholders)}`);
  }
  if (/\{/.test(optional)) bad.push(`${code}: barcodeOptional must take no placeholder`);
  // no unresolved English left behind in a non-English bundle, and nothing empty
  if (!optional.trim() || !identified.trim()) bad.push(`${code}: an empty value`);
  if (/%s|%\(|\$\{/.test(identified) || /%s|%\(|\$\{/.test(optional)) {
    bad.push(`${code}: a foreign interpolation style crept in`);
  }
}
assert.deepEqual(missing, [], `locales missing the barcode keys: ${missing.join(', ')}`);
assert.deepEqual(bad, [], `placeholder or value problems: ${bad.join(' | ')}`);

// the keys carry real translations, not copies of the English string
const en = bodies[0];
const enOptional = value(en.body, 'barcodeOptional');
const enIdentified = value(en.body, 'productIdentified');
assert.equal(enOptional, 'Barcode (optional)');
assert.equal(enIdentified, 'Product identified: {name}');

const untranslated = bodies.slice(1).filter(({ body }) => value(body, 'productIdentified') === enIdentified);
// German legitimately shares "Barcode (optional)" as a word, but the sentence must differ everywhere
assert.deepEqual(untranslated.map(b => b.code), [],
  `these locales still show the English sentence: ${untranslated.map(b => b.code).join(', ')}`);

// spot checks the task named explicitly
const expect = {
  fr: ['Code-barres (facultatif)', 'Produit identifié : {name}'],
  es: ['Código de barras (opcional)', 'Producto identificado: {name}'],
  pt: ['Código de barras (opcional)', 'Produto identificado: {name}'],
  ar: ['الباركود (اختياري)', 'تم التعرف على المنتج: {name}'],
};
for (const [code, [optional, identified]] of Object.entries(expect)) {
  const bundle = bodies.find(b => b.code === code);
  assert.ok(bundle, `${code} bundle exists`);
  assert.equal(value(bundle.body, 'barcodeOptional'), optional, `${code} barcodeOptional`);
  assert.equal(value(bundle.body, 'productIdentified'), identified, `${code} productIdentified`);
}

// the UI still reads them through the one localization mechanism
assert.match(app, /t\("products\.productIdentified", \{ name:/, 'the toast resolves through t()');
assert.match(fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/index.html'), 'utf8'),
  /data-i18n="products\.barcodeOptional"/, 'the label resolves through data-i18n');

console.log(`barcode localization parity: ${bodies.length} locales, both keys, {name} intact`);
