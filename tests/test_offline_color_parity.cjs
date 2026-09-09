'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const app = read('frontend/js/app.js');
const offline = read('frontend/js/offline.js');
const css = read('frontend/css/offline.css');

const enabledStatus = app.match(/<p[^>]*>Enabled on this device<\/p>/)?.[0] || '';
assert(enabledStatus.includes('text-primary'), 'enabled status must use Cauldra primary text');
assert(!enabledStatus.includes('text-success'), 'enabled status must not use success green');

for (const marker of [
    'id="offline-opt-in-enable" class="offline-primary"',
    'id="offline-pin-unlock" class="offline-primary"',
    'type="submit" class="offline-primary">Enable on this device',
    'type="submit" class="offline-primary">Change Offline PIN',
    'type="submit" class="offline-primary">Enable Biometrics',
    'id="offline-sync-retry" class="offline-primary"',
]) assert(offline.includes(marker), `primary action styling missing: ${marker}`);

assert(offline.includes('id="offline-remove-confirm" class="offline-danger"'), 'Remove Anyway confirmation must use danger styling');
assert(/\.offline-settings-actions button[^}]*background:#0d1322/i.test(css), 'profile management actions must use neutral card surface');
assert(/\.offline-settings-secondary button[^}]*color:#7c8ba1/i.test(css), 'Disable Offline Access must retain quiet secondary styling');
assert(/\.offline-settings-danger button[^}]*color:#fca5a5/i.test(css), 'Remove Offline Data must retain scoped danger styling');
assert(/\.offline-dialog \.offline-primary[^}]*background:#436bee/i.test(css), 'dialog primary actions must use Cauldra primary');
assert(/\.offline-dialog \.offline-danger[^}]*background:#d94141/i.test(css), 'dialog destructive actions must use Cauldra danger');
assert(!/(?:green|teal|emerald|cyan|lime)|#(?:0f|10b|14b|16a|22c)[0-9a-f]{3,6}/i.test(css), 'Offline CSS must not define a green/teal color family');

for (const [width, height] of [[375,812],[768,1024],[1024,768],[1366,768]]) {
    assert(css.includes('@media(max-width:600px)') || width > 600, `${width}x${height}: mobile rules missing`);
    assert(css.includes('@media(min-width:601px) and (max-width:900px)') || width < 601 || width > 900, `${width}x${height}: portrait tablet rules missing`);
    assert(css.includes('@media(min-width:901px) and (max-width:1100px)') || width < 901 || width > 1100, `${width}x${height}: landscape tablet rules missing`);
    assert(css.includes('@media(min-width:1101px)') || width < 1101, `${width}x${height}: desktop rules missing`);
}

console.log('PASS: Offline status/action colors and four-layout responsive rules use Cauldra primary, neutral, and danger hierarchy without green/teal styling.');
