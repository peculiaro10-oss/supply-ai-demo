// OCR-001 — a server error on an invoice scan must be reported as a server error,
// not as "we couldn't reach the server".
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/js/app.js'), 'utf8');

const calls = appSource.match(/fetch\(`\$\{API_URL\}\/ai\/scan-invoice`[\s\S]{0,1600}?catch \(err\)/g) || [];
assert.equal(calls.length, 2, 'both scan call sites are present (camera snapshot and file upload)');

for (const call of calls) {
  assert.ok(/await res\.json\(\)\.catch\(\(\) => \(\{\}\)\)/.test(call),
    'the response body is parsed defensively, so a non-JSON error cannot throw into the network catch');
  assert.ok(!/const data = await res\.json\(\);/.test(call),
    'the unguarded res.json() that turned a 500 into a connectivity message is gone');
  assert.ok(/if \(res\.ok\)/.test(call), 'the status is still what decides success');
  assert.ok(/scanResultsErrorMessage\(data\)/.test(call),
    'the server\'s own message is shown when it sends one');
}

console.log('OCR-001 UI checks passed');
