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
check('offline open: button needs business_day.manage, a sale/expense/refund its own permission (as online)',
  /const needed = auto \? \(\{ sale: "sales\.create", expense: "expenses\.record", refund: "sales\.refund" \}\[trigger\]\) : "business_day\.manage";/.test(openFn) && /if \(!needed \|\| !hasPermission\(needed\)\) throw/.test(openFn));
check('a day opened after a local close waits for that close', /depends_on_op_ids: \(await queuedBusinessDayCloses\(locationId\)\)\.map/.test(openFn));
check('offline day is its own queued change with a negative local id', /type: "business_day_open"/.test(openFn) && /const localId = -Date\.now\(\);/.test(openFn) && /meta: \{ local_id: localId \}/.test(openFn));

const pending = between(app, 'async function pendingOfflineBusinessDays', 'async function findOfflineOpenBusinessDay');
check('a refused offline day is not offered for new sales', /op\.status !== "conflict"/.test(pending));

const sale = between(app, 'async function completePOSCheckoutOffline', 'function toggleAIChatWindow');
check('offline sale opens the day only after the cart is validated', sale.indexOf('openBusinessDayOffline(locationId, { auto: true, trigger: "sale" })') > sale.indexOf('Not enough stock for'));
check('offline sale waits for an unsynchronized day', /if \(openDay\.pending_sync && !dependsOnOpIds\.includes\(openDay\.op_id\)\) dependsOnOpIds\.push\(openDay\.op_id\);/.test(sale));
check('a sale opens the day with the sale permission', /openBusinessDayOffline\(locationId, \{ auto: true, trigger: "sale" \}\)/.test(sale));

const expense = between(app, 'async function handleRecordExpenseOffline', 'function populateExpenseHistoryCategoryFilter');
check('an expense opens the day with the expense permission', /openBusinessDayOffline\(locationId, \{ auto: true, trigger: "expense" \}\)/.test(expense));
check('offline expense waits for an unsynchronized day too', /depends_on_op_ids: openDay\.pending_sync \? \[openDay\.op_id\] : \[\]/.test(expense));

const synced = between(app, 'async function handleSyncedOperation', 'async function restorePendingLocalProductsFromOutbox');
check('synced day swaps its local id for the server id in waiting changes', /op\.type === "business_day_open"[\s\S]*resolveOutboxDependenciesOnBusinessDaySynced\(op\.op_id, op\.meta\.local_id, serverId\)/.test(synced));
const remap = between(app, 'async function resolveOutboxDependenciesOnBusinessDaySynced', 'async function holdDependentsOfRefusedBusinessDay');
check('remap rewrites business_day_id and clears the dependency', /business_day_id: serverId/.test(remap) && /filter\(\(id\) => id !== dayOpId\)/.test(remap));

const sync = between(app, 'async function runSync', 'async function scheduleOutboxRetry');
check('runSync still defers a change while its dependency is queued', /stillOutstanding[\s\S]*status: "blocked_dependency"/.test(sync));
check('a refused day change holds what depends on it, with a reason', /if \(op\.type === "business_day_open" \|\| op\.type === "business_day_close"\) await holdDependentsOfRefusedBusinessDay/.test(sync)
  && /if \(dep\?\.status === "conflict"\) await holdForRefusedDependency\(op, dep\);/.test(sync) && /conflict_code: "BUSINESS_DAY_CONFLICT"/.test(app));
const reasons = between(app, 'function refusedDependencyReason', 'async function holdForRefusedDependency');
check('a close waiting on refused work in its day is held, never sent', /op\.type === "business_day_close"\) return `This Business Day was closed offline, but a change recorded in it needs review first/.test(reasons));
check('a change saved just after its day synced uses the server id', /Number\(op\.payload\?\.business_day_id\) < 0/.test(sync) && /day\.local_id/.test(sync));

const header = between(app, 'async function loadBusinessDayControl', 'async function loadDailySales');
check('Dashboard shows an unsynchronized offline day as open, waiting to sync', /localDay && \(localDay\.pending_sync \|\| offlineNow\)/.test(header) && /Opened offline at \$\{formatBusinessTime\(b\.opened_at\)\} · waiting to sync/.test(header));
check('Close is offered for an offline day too', !/canControl && !b\?\.pending_sync/.test(header));
check('a day closed here shows as closed, waiting to sync', /closedIds\.has\(Number\(d\.business_day\.id\)\)/.test(header) && /Closed offline at \$\{formatBusinessTime\(d\.closed_offline_at\)\} · waiting to sync/.test(header));
check('Close works offline, for an unsynced day, and after a network failure', /if \(\(offlineWorkspaceUnlocked && !authToken\) \|\| Number\(currentBusinessDayId\) < 0\) await closeOffline\(\);/.test(header));
const closeFn = between(app, 'async function closeBusinessDayOffline', 'async function resolveOutboxDependenciesOnBusinessDaySynced');
check('offline close needs business_day.manage, like online', /if \(!hasPermission\("business_day\.manage"\)\) throw/.test(closeFn));
check('offline close waits for the day\'s open and all work recorded in it', /depends_on_op_ids: \[\.\.\.\(day\.pending_sync \? \[day\.op_id\] : \[\]\), \.\.\.work\.map/.test(closeFn) && /own_refs: work\.map/.test(closeFn));
const find = between(app, 'async function findOfflineOpenBusinessDay', 'async function queuedBusinessDayCloses');
check('no new work attaches to a day closed on this device', /!closed\.has\(Number\(day\.id\)\)/.test(find));
check('a synchronized close is remembered in the sealed snapshot', /op\.type === "business_day_close" && serverData\?\.business_day_id\)[\s\S]{0,200}is_open: false/.test(synced) && /cacheWrite\("snapshot", offlineSnapshot\)/.test(app));
check('Open Business Day works offline and after a network failure', /if \(offlineWorkspaceUnlocked && !authToken\) await openOffline\(\);/.test(header) && /isNetworkFailure\(networkError\)/.test(header));

check('Sync Details explains a Business Day conflict', /BUSINESS_DAY_CONFLICT: "/.test(offline));
check('quarantine still keeps earlier refusal reasons (row 78)', /if \(row\.status === "conflict"\) continue;/.test(offline));

check('server: open/close need business_day.manage; an auto open needs its sale/expense permission',
  /"business_day_open": "business_day\.manage", "business_day_close": "business_day\.manage"/.test(server) && /\{"sale": "sales\.create", "expense": "expenses\.record", "refund": "sales\.refund"\}/.test(server));
check('server: close is by id, already-closed is answered, later work by others refuses', /BUSINESS_DAY_OFFLINE_CLOSE_ALREADY_CLOSED/.test(server) && /records_after_offline_close/.test(server));
check('server: same-date open day is joined, other dates refused', /if active\.date != local_date:[\s\S]*BUSINESS_DAY_CONFLICT[\s\S]*BUSINESS_DAY_OFFLINE_OPEN_JOINED/.test(server));

for (const text of ['No Business Day is open for this location. Ask someone who can open the Business Day to start it.',
  "Business Day opened offline. It will sync when you're back online.", 'Opened offline at {time} · waiting to sync',
  'The Business Day this change was recorded in could not be synchronized: {reason}', 'Closed offline at {time} · waiting to sync',
  "Business Day closed offline. It will sync when you're back online.",
  'This Business Day was closed offline, but a change recorded in it needs review first, so the day was not closed on the server: {reason}'])
  check(`translated in all launch languages: ${text.slice(0, 50)}`, Array.isArray(catalog[text]) && catalog[text].length === 4 && catalog[text].every(Boolean));

console.log(failures ? `${failures} FAILED` : 'ALL PASS');
process.exit(failures ? 1 : 0);
