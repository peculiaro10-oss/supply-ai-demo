'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
const base = fs.readFileSync(path.join(root, 'frontend/css/base.css'), 'utf8');

assert.match(index, /name=["']viewport["'][^>]*width=device-width/i, 'responsive viewport metadata must remain present');
assert.match(app, /detail\.message \?\? detail\.error \?\? detail\.detail/, 'structured API detail.message must be rendered');
assert.match(app, /friendlyErrorMessage\(data, disabled \? t\("team\.enableAccountFailed"\)/, 'seat-limit enable errors must use the structured message');

const layouts = [
  ['Mobile 375x812', /@media\s*\(max-width:\s*767px\)/],
  ['iPad portrait 768x1024', /@media\s*\(min-width:\s*768px\)/],
  ['iPad landscape 1024x768', /@media\s*\(min-width:\s*768px\)\s*and\s*\(max-width:\s*1100px\)/],
  ['Desktop 1366x768', /@media\s*\(min-width:\s*1200px\)/],
];
for (const [name, mediaRule] of layouts) {
  assert.match(base, mediaRule, `${name} responsive rule must remain present`);
}

console.log('ENTITLEMENT_FRONTEND_RESPONSIVE_PASS mobile=375x812 ipad_portrait=768x1024 ipad_landscape=1024x768 desktop=1366x768');
