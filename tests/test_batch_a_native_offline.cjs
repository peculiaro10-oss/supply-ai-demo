'use strict';
// Batch A (NAT-002, UX-007, AND-003, AND-001, NAT-001) source-level regression guards.
// The behaviour itself is exercised in a real browser by the Batch A harness recorded in
// REMEDIATION_AND_RETEST_LOG.md; these guards stop the fixes being silently undone.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
const app = read('frontend/js/app.js'), offline = read('frontend/js/offline.js');
const index = read('frontend/index.html'), css = read('frontend/css/base.css');

// NAT-002 — retry re-checks the server and recovers without a session.
const retry = app.match(/window\.addEventListener\("cauldra-retry-online", async \(\) => \{[\s\S]+?\n        \}\);/)?.[0] || '';
assert(retry.includes('probeBackend('), 'Try again must perform a fresh server check');
assert(retry.includes('recoverOnline()'), 'a successful retry must dismiss the cold-start gate');
assert(!/currentUserProfile|businessProfile/.test(retry), 'guest recovery must not require a profile or business');
assert(/completeOnlineBoot\(\)/.test(retry), 'a retry that first reaches the server must run the normal post-boot routing');
const recover = offline.match(/function recoverOnline\(\) \{[\s\S]+?\n    \}/)?.[0] || '';
assert(recover.includes('if (active?.offline) return false;'), 'recovery must never touch an unlocked offline workspace');
assert(!/unlock\(|rawDataKey|applyOfflineSnapshot|cacheRead/.test(recover), 'recovery must not decrypt or load cached data');
assert(/probe\.reason === "timeout"\) probe = await probeBackend\(BOOT_SLOW_RETRY_TIMEOUT_MS\)/.test(app), 'only a timed-out boot check gets the slower second chance');
assert(!offline.includes('Internet connection is required for first sign-in on this device.'), 'the gate must not claim there is definitely no internet');
assert(offline.includes('data-mandatory="true"') && offline.includes('if (event.currentTarget.dataset.mandatory === "true") event.preventDefault();'), 'the gate itself stays mandatory');

// UX-007 — "Not Now" snoozes about 24 hours, locally, once per session.
assert(offline.includes('const OPT_IN_SNOOZE_MS = 24 * 60 * 60 * 1000;'), '24 h snooze must be explicit');
assert(/row\?\.value === "declined" && Number\(row\.snooze_until \|\| 0\) > Date\.now\(\)/.test(offline), 'an expired snooze must allow the offer again');
assert(offline.includes('optInOfferedThisSession'), 'offer at most once per session');
assert(offline.includes('anotherOverlayOpen()'), 'never stack the offer over another popup');

// AND-003 — Android Back.
assert(app.includes("app.addListener('backButton'"), 'Android Back must be handled');
for (const id of ['password-change-modal', 'temporary-credential-modal', 'business-id-success-modal']) assert(app.includes(`"${id}"`), `Back must not dismiss ${id}`);
assert(/if \(dialog\.dataset\.mandatory === "true"\) return "blocked";/.test(app), 'Back must not dismiss a mandatory dialog');

// Native bridge has no registerPlugin (no @capacitor/core bundle) — AND-001 and listener wiring.
assert(offline.includes('Promise.resolve(appPlugin.addListener(') && !offline.includes('handleAppActivity(!!isActive)).catch('), 'addListener result must not be assumed to be a Promise');
assert(app.includes('return capacitor.Plugins?.App || null;'), 'App plugin must fall back to the native bridge plugin');
assert(!/window\.Capacitor\.registerPlugin\('App'\)/.test(app), 'app.js must not call registerPlugin directly');

// NAT-001 — one-glyph Naira fallback, local only, after the desktop mono fonts.
assert(/@font-face \{ font-family: "Cauldra Currency"; src: local\([^;]+; unicode-range: U\+20A6; \}/.test(css), 'Naira fallback face must be local-only and limited to U+20A6');
assert(!/Cauldra Currency[^}]*url\(/.test(css), 'no downloaded font');
assert(index.includes(`'"Liberation Mono"', '"Cauldra Currency"', '"Courier New"', 'monospace'`), 'mono stack must place the fallback after the desktop mono fonts');

console.log('PASS: Batch A guards — retry recovery without a session, accurate gate wording, 24 h opt-in snooze, Android Back with protected gates, bridge-safe listeners, local Naira fallback.');
