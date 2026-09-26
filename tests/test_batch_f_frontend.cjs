// Batch F frontend regressions (web bundle). Each section names its finding.
//   node tests/test_batch_f_frontend.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');

function extract(name, source = appSource) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} exists`);
  let depth = 0;
  for (let i = source.indexOf('{', start); i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`could not extract ${name}`);
}
function extractIife(marker) {
  const start = appSource.indexOf(marker);
  assert.ok(start >= 0, `${marker} exists`);
  const open = appSource.lastIndexOf('(function', start);
  const end = appSource.indexOf('})();', start);
  return appSource.slice(open, end + 5);
}
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

// --- ERR-001: readable errors when a response body is not JSON --------------
test('ERR-001 non-JSON bodies reject with a readable sentence and the status', async () => {
  const nativeJson = Response.prototype.json;
  const ctx = { Response, JSON, Error, String };
  vm.createContext(ctx);
  vm.runInContext(`${extract('unreadableResponseMessage')}\n${extractIife('function installReadableJsonErrors')}`, ctx);
  try {
    const cases = [
      [new Response('Internal Server Error', { status: 500 }), 500, "We couldn't complete that request right now. Please try again."],
      [new Response('<html><body>502 Bad Gateway</body></html>', { status: 502 }), 502, "We couldn't complete that request right now. Please try again."],
      [new Response('', { status: 401 }), 401, 'Your session could not be verified. Please sign in again.'],
      [new Response('Too Many Requests', { status: 429 }), 429, 'Too many attempts right now. Please wait a moment and try again.'],
      [new Response('oops', { status: 400 }), 400, 'Something went wrong. Please try again.'],
    ];
    for (const [response, status, message] of cases) {
      await assert.rejects(response.json(), err => {
        assert.equal(err.message, message);
        assert.equal(err.status, status);
        assert.doesNotMatch(err.message, /Unexpected|JSON|token/i);
        return true;
      });
    }
    assert.deepEqual(await new Response('{"detail":"Probe conflict."}', { status: 409 }).json(), { detail: 'Probe conflict.' });
    assert.deepEqual(await new Response('[1,2]').json(), [1, 2]);
  } finally {
    // Leave the global Response untouched for the rest of this process.
    Response.prototype.json = nativeJson;
  }
});

test('ERR-001 friendlyErrorMessage never shows parser text, even as its own fallback', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(`${extract('friendlyErrorMessage')}\n${extract('isTechnicalErrorText')}\nthis.f = friendlyErrorMessage;`, ctx);
  const parserTexts = [
    `Unexpected token 'I', "Internal S"... is not valid JSON`,
    'Unexpected end of JSON input',
    'JSON.parse: unexpected character at line 1 column 1 of the JSON data',
    'JSON Parse error: Unrecognized token \'<\'',
    'SyntaxError: Unexpected token < in JSON at position 0',
    '<!DOCTYPE html><html>',
  ];
  for (const text of parserTexts) {
    assert.equal(ctx.f(text, 'Export failed. Please try again.'), 'Export failed. Please try again.', text);
    assert.equal(ctx.f(text, text), 'Something went wrong. Please try again.', `self-fallback: ${text}`);
    assert.equal(ctx.f(text), 'Something went wrong. Please try again.');
  }
  assert.equal(ctx.f('Supplier email is not valid.', 'x'), 'Supplier email is not valid.', 'real messages pass');
  assert.equal(ctx.f('Upload failed because the file is too large.', 'x'), 'Upload failed because the file is too large.');
  assert.equal(ctx.f({ detail: 'Probe conflict.' }, 'x'), 'Probe conflict.');
  assert.equal(ctx.f('Internal Server Error', ''), '', 'an empty fallback stays empty for callers that test it');
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`PASS ${name}`); }
    catch (err) { failed++; console.error(`FAIL ${name}\n  ${err.stack || err}`); }
  }
  if (failed) { console.error(`${failed} of ${tests.length} failed`); process.exit(1); }
  console.log(`All ${tests.length} Batch F frontend checks passed`);
})();
