// BUSINESS-DAY-OFFLINE-001: static regression checks for opening a Business Day
// while offline. The behaviour itself is exercised end to end by
// tests/run_business_day_offline_e2e.js and, on the server, by
// tests/test_business_day_offline_postgres.py.
'use strict';
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${!ok && detail ? ' — ' + detail : ''}`); if (!ok) failures++; };
const between = (text, start, end) => { const i = text.indexOf(start); return i < 0 ? '' : text.slice(i, end ? text.indexOf(end, i + start.length) : undefined); };

const app = read('frontend/js/app.js');
const offline = read('frontend/js/offline.js');
const server = read('backend/offline_access.py');
const catalog = JSON.parse(read('i18n/launch_catalog.json'));

check('sales and expenses no longer refuse for lack of a previously synchronized day',
  !/Internet required: no previously synchronized open Business Day/.test(app));

const openFn = between(app, 'async function openBusinessDayOffline', '\n        }\n');
check('offline open reuses an open day before creating one', /findOfflineOpenBusinessDay\(locationId\)[\s\S]*if \(existing\) return existing;/.test(openFn));
check('offline open needs business_day.manage, like the online button', /if \(!hasPermission\("business_day\.manage"\)\) throw/.test(openFn));
check('offline day is its own queued change with a negative local id', /type: "business_day_open"/.test(openFn) && /const localId = -Date\.now\(\);/.test(openFn) && /meta: \{ local_id: localId \}/.test(openFn));

const pending = between(app, 'async function pendingOfflineBusinessDays', 'async function findOfflineOpenBusinessDay');
check('a refused offline day is not offered for new sales', /op\.status !== "conflict"/.test(pending));

const sale = between(app, 'async function completePOSCheckoutOffline', 'function toggleAIChatWindow');
check('offline sale opens the day only after the cart is validated', sale.indexOf('openBusinessDayOffline(locationId, { auto: true })') > sale.indexOf('Not enough stock for'));
check('offline sale waits for an unsynchronized day', /if \(openDay\.pending_sync && !dependsOnOpIds\.includes\(openDay\.op_id\)\) dependsOnOpIds\.push\(openDay\.op_id\);/.test(sale));
check('user without permission is told who can open the day', /!hasPermission\("business_day\.manage"\)[\s\S]{0,80}Ask someone who can open the Business Day/.test(sale));

const expense = between(app, 'async function handleRecordExpenseOffline', 'function populateExpenseHistoryCategoryFilter');
check('offline expense waits for an unsynchronized day too', /depends_on_op_ids: openDay\.pending_sync \? \[openDay\.op_id\] : \[\]/.test(expense));

const synced = between(app, 'async function handleSyncedOperation', 'async function restorePendingLocalProductsFromOutbox');
check('synced day swaps its local id for the server id in waiting changes', /op\.type === "business_day_open"[\s\S]*resolveOutboxDependenciesOnBusinessDaySynced\(op\.op_id, op\.meta\.local_id, serverId\)/.test(synced));
const remap = between(app, 'async function resolveOutboxDependenciesOnBusinessDaySynced', 'async function holdDependentsOfRefusedBusinessDay');
check('remap rewrites business_day_id and clears the dependency', /business_day_id: serverId/.test(remap) && /filter\(\(id\) => id !== dayOpId\)/.test(remap));

const sync = between(app, 'async function runSync', 'async function scheduleOutboxRetry');
check('runSync still defers a change while its dependency is queued', /stillOutstanding[\s\S]*status: "blocked_dependency"/.test(sync));
check('a refused day holds its sales with their own reason', /if \(op\.type === "business_day_open"\) await holdDependentsOfRefusedBusinessDay\(op, refusal\);/.test(sync)
  && /conflict_code: "BUSINESS_DAY_CONFLICT"/.test(app));
check('a change saved just after its day synced uses the server id', /Number\(op\.payload\?\.business_day_id\) < 0/.test(sync) && /day\.local_id/.test(sync));

const header = between(app, 'async function loadBusinessDayControl', 'async function openBusinessDayHeaderClose');
check('Dashboard shows an unsynchronized offline day as open, waiting to sync', /localDay\?\.pending_sync/.test(header) && /Opened offline at \$\{formatBusinessTime\(b\.opened_at\)\} · waiting to sync/.test(header));
check('no Close control for a day that has not synchronized', /if \(canControl && !b\?\.pending_sync\)/.test(header));
check('Open Business Day works offline and after a network failure', /if \(offlineWorkspaceUnlocked && !authToken\) await openOffline\(\);/.test(header) && /isNetworkFailure\(networkError\)/.test(header));

check('Sync Details explains a Business Day conflict', /BUSINESS_DAY_CONFLICT: "/.test(offline));
check('quarantine still keeps earlier refusal reasons (row 78)', /if \(row\.status === "conflict"\) continue;/.test(offline));

check('server: opening offline needs business_day.manage', /"business_day_open": "business_day\.manage"/.test(server));
check('server: same-date open day is joined, other dates refused', /if active\.date != local_date:[\s\S]*BUSINESS_DAY_CONFLICT[\s\S]*BUSINESS_DAY_OFFLINE_OPEN_JOINED/.test(server));

for (const text of ['No Business Day is open for this location. Ask someone who can open the Business Day to start it.',
  "Business Day opened offline. It will sync when you're back online.", 'Opened offline at {time} · waiting to sync',
  'The Business Day this change was recorded in could not be synchronized: {reason}'])
  check(`translated in all launch languages: ${text.slice(0, 50)}`, Array.isArray(catalog[text]) && catalog[text].length === 4 && catalog[text].every(Boolean));

console.log(failures ? `${failures} FAILED` : 'ALL PASS');
process.exit(failures ? 1 : 0);
