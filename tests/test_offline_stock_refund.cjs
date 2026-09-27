// OFFLINE-STOCK-REFUND-001 (G1 stock adjustments/transfers, G2 refunds): static
// regression checks. Behaviour is exercised end to end by
// tests/run_offline_stock_refund_e2e.js and on the server by
// tests/test_offline_stock_refund_postgres.py.
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
const main = read('backend/main.py');
const catalog = JSON.parse(read('i18n/launch_catalog.json'));

// ---- G1 -------------------------------------------------------------------
const adjust = between(app, 'async function adjustStockOffline', 'async function transferStockOffline');
check('offline adjustment needs inventory.adjust_stock, as online', /hasPermission\("inventory\.adjust_stock"\)/.test(adjust));
check('offline adjustment is a delta on one named warehouse, never below zero', /quantity_change: change/.test(adjust) && /warehouse_id: warehouse\.id/.test(adjust) && /before \+ change < 0\) throw/.test(adjust));
const transfer = between(app, 'async function transferStockOffline', 'function withCartPositions');
check('offline transfer needs inventory.transfer_stock and enough local source stock', /hasPermission\("inventory\.transfer_stock"\)/.test(transfer) && /available < quantity\) throw/.test(transfer));
check('stock change and local stock are written in one sealed step', /\[\{ kind: "products", value: nextProducts \}, \{ kind: "warehouse_stocks", value: nextStocks \}\]/.test(between(app, 'async function queueStockChange', 'async function adjustStockOffline')));
check('offline path only when already offline (never after a request that may have reached the server)',
  /function offlineWorkspaceActive\(\) \{\s*return !!\(window\.CauldraOffline\?\.isUnlocked\(\) && \(\(offlineWorkspaceUnlocked && !authToken\) \|\| !navigator\.onLine\)\);/.test(app)
  && /async function adjustQuickStock\(productId, change\) \{\s*if \(offlineWorkspaceActive\(\)\)/.test(app) && /if \(offlineWorkspaceActive\(\)\) \{\s*try \{\s*await transferStockOffline/.test(app));
check('a stock change on an unsynced product waits for it and gets its server id', /dependent\.type === "stock_adjust" \|\| dependent\.type === "stock_transfer"/.test(app));
check('server: stock changes recheck their permission', /"stock_adjust": "inventory\.adjust_stock", "stock_transfer": "inventory\.transfer_stock"/.test(server));
check('server: adjustment is a delta, refused below zero with the current figure', /available \+ change < 0:[\s\S]{0,80}failure\("STOCK_CHANGED"/.test(server) && /_apply_stock_adjustment/.test(server));
check('server: transfer locks both rows in a fixed order and refuses whole', /sorted\(\(source_wh, target_wh\), key=lambda w: w\.id\)/.test(server) && /if available < quantity:[\s\S]{0,80}failure\("STOCK_CHANGED"/.test(server));
check('online and offline share one adjustment and one transfer core', /_apply_stock_adjustment\(db, user, p, stock, warehouse_name, data\.quantity_change\)/.test(main) && /_apply_stock_transfer\(db, user, p, source, target, from_warehouse_row, to_warehouse_row, data\.quantity\)/.test(main));

// ---- G2 -------------------------------------------------------------------
check('offline refunds are no longer blocked as online-only', !/"PriceMonitor", "Refund",/.test(app));
const queue = between(app, 'async function queueRefundOffline', 'async function rememberSyncedRefund');
check('offline refund needs sales.refund and checks what is still refundable here', /hasPermission\("sales\.refund"\)/.test(queue) && /line\.quantity > item\.available_quantity\) throw/.test(queue));
check('a refund of an unsynced sale waits for that sale; a refused sale blocks it', /if \(txn\.sale_op\) depends\.push\(txn\.sale_op\.op_id\)/.test(queue) && /sale_op\?\.status === "conflict"\) throw/.test(queue));
check('a refund is recorded in the open day at the original sale\'s location', /openBusinessDayOffline\(txn\.location_id, \{ auto: true, trigger: "refund" \}\)/.test(queue));
const refundable = between(app, 'async function offlineRefundableTransactions', 'function businessDayIdAliases');
check('refundable here = trusted snapshot + this device\'s own sales, minus refunds already queued', /snapshot\.refundable_sales/.test(refundable) && /snapshot\.sales/.test(refundable) && /op\.type !== "sale_refund" \|\| op\.status === "conflict"/.test(refundable));
check('a legacy sale with no known location is not refunded offline', /txn\.location_id == null\) continue/.test(refundable));
check('no stock is restored on the device before the server records the refund', !/offlineWarehouseStocks/.test(queue));
check('the Business Day close waits for refunds recorded in it', /const BUSINESS_DAY_WORK_TYPES = \["sale_checkout", "expense_create", "sale_refund"\];/.test(app));
check('a refund waiting on a refused sale is held with a reason', /op\.type === "sale_refund" && dep\.type === "sale_checkout"\) return `The sale this refund is for could not be synchronized/.test(app));
check('UI says pending, not refunded', /Refund pending: saved offline\. It is not complete until it syncs\./.test(app) && /Refund pending<\/span>/.test(app));
check('server: refund permission rechecked; auto-open with the refund permission', /"sale_refund": "sales\.refund"/.test(server) && /"refund": "sales\.refund"\}/.test(server));
check('server: sale rows locked, remaining quantity rechecked, refused whole on conflict', /with_for_update\(\)\.all\(\)[\s\S]*failure\("REFUND_CONFLICT"[\s\S]*Nothing from this refund was applied/.test(between(server, 'def refund_sale', '@app.post("/offline/replay")')));
check('server: the online refund itself runs with the op id as its idempotency key', /g\["create_refund"\]\(key, body, user, DeferredCommit\(db\)\)/.test(server) && /client_ref=ref\)/.test(server));
check('online refunds lock the sale rows too (no concurrent over-refund)', /order_by\(SaleModel\.id\)\.with_for_update\(\)\.all\(\)/.test(between(main, 'def create_refund', 'already_refunded = db.query')));
check('sale refunds are internal: no payment provider in the refund path', !/paystack/i.test(between(main, 'def create_refund', '@app.get("/sales/current-day")')));
check('snapshot carries refundable sales only with sales.refund', /if permissions\(user\)\.get\("sales\.refund"\):\s*refundable = g\["list_sale_transactions"\]/.test(server));

// ---- Sync Details -----------------------------------------------------------
check('Sync Details lists waiting changes with truthful statuses', /sale_refund: "Refund pending", stock_transfer: "Transfer pending", stock_adjust: "Adjustment pending"/.test(offline) && /offline-pending-list/.test(offline));
check('Sync Details explains a refund conflict', /REFUND_CONFLICT: "/.test(offline));
for (const text of ['Refund pending', 'Transfer pending', 'Adjustment pending', 'Waiting to sync', 'Needs attention',
  'Refund pending: saved offline. It is not complete until it syncs.', 'Transfer pending: saved offline. It will be applied when it syncs.',
  'Stock adjustment saved offline. It is pending until it syncs.', 'The sale this refund is for could not be synchronized, so the refund was not sent: {reason}'])
  check(`translated in all launch languages: ${text.slice(0, 50)}`, Array.isArray(catalog[text]) && catalog[text].length === 4 && catalog[text].every(Boolean));

console.log(failures ? `${failures} FAILED` : 'ALL PASS');
process.exit(failures ? 1 : 0);
