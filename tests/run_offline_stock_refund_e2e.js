// OFFLINE-STOCK-REFUND-001 end to end (G1 stock adjustments/transfers, G2
// refunds): the real app in Chromium against a real local backend. Not for QA or
// production: it edits the local database to play "another device".
//
//   stock:  offline -> two quick adjustments and a transfer (same UI actions as
//           online) -> sealed at rest -> a transfer larger than local stock is
//           refused -> restart offline, unlock, all still queued, "pending" in
//           Sync Details -> another device changes the same stock online ->
//           reconnect -> each applied once as a delta on top, both transfer
//           rows moved -> a transfer whose source was emptied elsewhere is
//           refused whole (no partial move, nothing negative).
//   refund: a synchronized sale and an offline sale -> both refunded offline
//           ("Refund pending", no stock put back locally, cannot refund the same
//           units twice here, needs sales.refund) -> restart offline -> another
//           till refunds most of the synced sale online -> reconnect -> the
//           offline sale syncs, then its refund (restocked once); the synced
//           sale's refund is refused whole with the figures.
//   refund held: an offline sale refused on reconnect holds its refund with
//           a reason.
//
// Same environment as tests/run_business_day_offline_e2e.js (whose helpers it
// uses):
//   CAULDRA_E2E_URL=http://127.0.0.1:8000 CAULDRA_E2E_BIZ_CODE=... CAULDRA_E2E_USER=...
//   CAULDRA_E2E_PASSWORD=... CAULDRA_E2E_LOCAL_DB=postgresql://postgres@127.0.0.1:5433/cauldra_local
//   node tests/run_offline_stock_refund_e2e.js
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { launch, signIn, prepareOnline, unlockOffline, outbox, sellOffline, reconnect, sql, q, check, pause, BASE, PIN, failures } = require('./run_business_day_offline_e2e.js');

const sealedRows = (page) => page.evaluate(() => new Promise((resolve) => {
  const req = indexedDB.open('cauldra_offline');
  req.onsuccess = () => { const all = req.result.transaction('secure_outbox').objectStore('secure_outbox').getAll(); all.onsuccess = () => resolve(all.result); };
}));
const syncDetails = (page) => page.evaluate(async () => {
  await window.CauldraOffline.openSyncDetails();
  const text = { pending: document.getElementById('offline-pending-list').textContent, conflicts: document.getElementById('offline-conflict-list').textContent };
  document.getElementById('offline-sync-dialog').close();
  return text;
});
const reprovision = (page) => page.evaluate(async (pin) => {
  await loadData();
  await window.CauldraOffline.refreshSnapshot({ apiUrl: API_URL, token: authToken });
});
const clearOutbox = async (page) => { for (const r of await outbox(page)) await page.evaluate((id) => window.CauldraOffline.removeOutbox(id), r.op_id); };

async function restartOffline(ctx, dir) {
  await ctx.close();
  const next = await launch(dir, true);
  await unlockOffline(next.page);
  return next;
}

async function scenarioStock() {
  console.log('\n# G1: stock adjustments and transfers offline');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-g1-'));
  let { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page);
  const backName = 'E2E Back ' + Math.random().toString(36).slice(2, 6);
  const created = await page.evaluate(async ({ name, locationId }) => (await fetch(`${API_URL}/warehouses/`, { method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` }, body: JSON.stringify({ name, location_id: locationId }) })).status, { name: backName, locationId: s.locationId });
  check('setup: a second warehouse at the location', created < 300, String(created));
  await reprovision(page);
  const productId = Number(sql(`select id from products where name=${q(s.productName)} order by id desc limit 1`));
  const mainWh = Number(sql(`select id from warehouses where name=${q(s.warehouseName)} and location_id=${s.locationId} order by id limit 1`));
  const backWh = Number(sql(`select id from warehouses where name=${q(backName)}`));
  const stockSql = (wh) => Number(sql(`select coalesce(sum(quantity),0) from warehouse_stocks where product_id=${productId} and warehouse_id=${wh}`));
  const mainBefore = stockSql(mainWh);
  const auditStart = Number(sql(`select coalesce(max(id),0) from audit_logs`)); // earlier runs may have used the same product

  ({ ctx, page } = await restartOffline(ctx, dir));
  const localBefore = await page.evaluate(({ id, wh }) => offlineWarehouseStocks.find((r) => Number(r.product_id) === id && Number(r.warehouse_id) === wh)?.quantity, { id: productId, wh: mainWh });
  check('offline workspace has the synchronized stock', Number(localBefore) === mainBefore, `${localBefore} vs ${mainBefore}`);
  await page.evaluate(async (id) => { await adjustQuickStock(id, -1); await adjustQuickStock(id, -1); await adjustQuickStock(id, 1); }, productId);
  await page.evaluate(async ({ id, from, to }) => {
    openWarehouseModal();
    document.getElementById('transfer-product').value = String(id);
    document.getElementById('transfer-from').value = from; document.getElementById('transfer-to').value = to;
    document.getElementById('transfer-qty').value = '3';
    await handleStockTransfer({ preventDefault() {} });
  }, { id: productId, from: s.warehouseName, to: backName });
  const tooMuch = await page.evaluate(async ({ id, from, to }) => { try { await transferStockOffline(id, from, to, 100000); return null; } catch (e) { return e.message; } }, { id: productId, from: s.warehouseName, to: backName });
  let rows = await outbox(page);
  check('adjustments and the transfer are queued, waiting to sync', rows.filter((r) => r.type === 'stock_adjust').length === 3 && rows.filter((r) => r.type === 'stock_transfer').length === 1 && rows.every((r) => r.status === 'pending'), JSON.stringify(rows));
  check('a transfer larger than the stock on this device is refused, nothing queued', /Only \d+ are recorded/.test(tooMuch || '') && rows.length === 4, tooMuch);
  const local = await page.evaluate(({ id, a, b }) => [a, b].map((wh) => offlineWarehouseStocks.find((r) => Number(r.product_id) === id && Number(r.warehouse_id) === wh)?.quantity ?? 0), { id: productId, a: mainWh, b: backWh });
  check('this device shows the stock after its own pending changes', local[0] === mainBefore - 1 - 3 && local[1] === 3, JSON.stringify(local));
  const raw = await sealedRows(page);
  check('stock changes are sealed at rest', raw.length === 4 && raw.every((r) => r.sealed && !('payload' in r)) && !JSON.stringify(raw).includes('warehouse_id') && !JSON.stringify(raw).includes(s.productName));

  ({ ctx, page } = await restartOffline(ctx, dir));
  rows = await outbox(page);
  const localAfterRestart = await page.evaluate(({ id, a }) => offlineWarehouseStocks.find((r) => Number(r.product_id) === id && Number(r.warehouse_id) === a)?.quantity, { id: productId, a: mainWh });
  check('after a restart offline: all four still queued, local stock kept', rows.length === 4 && localAfterRestart === mainBefore - 4, `${rows.length} ${localAfterRestart}`);
  const details = await syncDetails(page);
  check('Sync Details says pending, not done', /Adjustment pending/.test(details.pending) && /Transfer pending/.test(details.pending) && !/Transferred|Adjusted/.test(details.pending), details.pending.slice(0, 300));

  // Another device changes the same product's stock online meanwhile.
  const other = await fetch(`${BASE}/products/${productId}/stock`, { method: 'PATCH', headers: { Authorization: `Bearer ${s.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ quantity_change: -1 }) });
  check('setup: another device adjusted the same stock online', other.status === 200, String(other.status));
  rows = await reconnect(ctx, page);
  check('reconnect: all stock changes synchronized', rows.length === 0, JSON.stringify(rows));
  check('each adjustment applied once, on top of the other device\'s change; transfer moved both rows', stockSql(mainWh) === mainBefore - 1 - 1 - 3 && stockSql(backWh) === 3, `${stockSql(mainWh)} ${stockSql(backWh)}`);
  const audits = sql(`select action, count(*) from audit_logs where id > ${auditStart} and resource_id=${productId} and action in ('STOCK_ADJUSTED','STOCK_TRANSFER') and metadata_json like '%"offline": true%' group by action order by action`);
  check('audited as offline work', audits === 'STOCK_ADJUSTED|3\nSTOCK_TRANSFER|1', audits);

  // A transfer whose source is emptied elsewhere before this device reconnects.
  await ctx.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await page.evaluate(async ({ id, from, to }) => transferStockOffline(id, from, to, 2), { id: productId, from: backName, to: s.warehouseName });
  sql(`update warehouse_stocks set quantity=1 where product_id=${productId} and warehouse_id=${backWh}`);
  sql(`update products set quantity=(select sum(quantity) from warehouse_stocks where product_id=${productId}) where id=${productId}`);
  const mainNow = stockSql(mainWh);
  rows = await reconnect(ctx, page);
  const refused = rows.find((r) => r.type === 'stock_transfer');
  check('source fell below the transfer: refused whole with a reason', refused?.status === 'conflict' && refused.conflict_code === 'STOCK_CHANGED' && /now has only 1/.test(refused.last_error), JSON.stringify(rows));
  check('no partial transfer and nothing negative', stockSql(backWh) === 1 && stockSql(mainWh) === mainNow, `${stockSql(backWh)} ${stockSql(mainWh)}`);
  check('the refused transfer is kept for review, never dropped', (await syncDetails(page)).conflicts.includes('now has only 1'));
  await clearOutbox(page);
  await ctx.close();
  // Leave the location as found: the test warehouse's stock goes back to the
  // main warehouse and the test warehouse is deactivated.
  sql(`update warehouse_stocks set quantity=quantity+${stockSql(backWh)} where product_id=${productId} and warehouse_id=${mainWh}`);
  sql(`delete from warehouse_stocks where warehouse_id=${backWh}`);
  sql(`update warehouses set is_active=false where id=${backWh}`);
}

async function onlineSale(s, productId, quantity) {
  const ref = `e2e-r-${Date.now()}`;
  const warehouseId = Number(sql(`select id from warehouses where name=${q(s.warehouseName)} and location_id=${s.locationId} order by id limit 1`));
  const r = await fetch(`${BASE}/sales/checkout`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: [{ product_id: productId, quantity, price_mode: 'retail', warehouse_id: warehouseId }], location_id: s.locationId, client_ref: ref }) });
  return { status: r.status, ref, saleId: Number(sql(`select id from sales where client_ref=${q(ref)}`)) };
}

async function scenarioRefund() {
  console.log('\n# G2: refunds offline');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-g2-'));
  let { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page, { openDay: true });
  const productId = Number(sql(`select id from products where name=${q(s.productName)} order by id desc limit 1`));
  const synced = await onlineSale(s, productId, 3);
  check('setup: a synchronized sale of 3', synced.status === 200 && synced.saleId > 0, String(synced.status));
  await reprovision(page);

  ({ ctx, page } = await restartOffline(ctx, dir));
  await sellOffline(page, s.productName);
  let rows = await outbox(page);
  const saleOp = rows.find((r) => r.type === 'sale_checkout');
  check('setup: an offline sale on this device', !!saleOp, JSON.stringify(rows));
  const stockBefore = await page.evaluate((id) => globalProducts.find((p) => p.id === id)?.quantity, productId);

  // Refund 2 of the synchronized sale through the Transactions -> Refund UI.
  const listed = await page.evaluate(async (dayId) => {
    await openRefundTransactionsModal(dayId);
    return document.getElementById('refund-transactions-list').textContent;
  }, s.dayId);
  check('Transactions works offline and lists both sales', (listed.match(/Refund/g) || []).length >= 2, listed.slice(0, 300));
  await page.evaluate(async ({ key, saleId }) => {
    await openRefundModal(key); adjustRefundLineQty(saleId, 1); adjustRefundLineQty(saleId, 1); await submitRefund();
  }, { key: synced.ref, saleId: synced.saleId });
  const twice = await page.evaluate(async ({ key, saleId }) => { try { await queueRefundOffline(key, [{ sale_id: saleId, quantity: 2, restock: true }]); return null; } catch (e) { return e.message; } }, { key: synced.ref, saleId: synced.saleId });
  check('the same units cannot be refunded twice on this device', /Only 1 unit\(s\) available/.test(twice || ''), twice);
  // Refund the offline sale (not synchronized yet) through the same UI.
  await page.evaluate(async (key) => { await openRefundModal(key); adjustRefundLineQty(-1, 1); await submitRefund(); }, saleOp.op_id);
  const noPermission = await page.evaluate(async (key) => {
    const saved = currentEffectivePermissions['sales.refund']; delete currentEffectivePermissions['sales.refund'];
    try { await queueRefundOffline(key, [{ sale_id: -1, quantity: 1 }]); return null; } catch (e) { return e.message; } finally { currentEffectivePermissions['sales.refund'] = saved; }
  }, saleOp.op_id);
  check('refunding offline needs sales.refund', /do not have permission to refund/.test(noPermission || ''), noPermission);
  rows = await outbox(page);
  const refunds = rows.filter((r) => r.type === 'sale_refund');
  const localRefund = await page.evaluate(async (key) => (await window.CauldraOffline.listOutbox()).find((r) => r.type === 'sale_refund' && r.payload.transaction_key === key), saleOp.op_id);
  check('two refunds queued; the offline sale\'s refund waits for that sale, by cart line', refunds.length === 2 && localRefund?.depends_on_op_ids.includes(saleOp.op_id) && localRefund.payload.lines[0].item_index === 0, JSON.stringify(localRefund?.payload));
  const stockAfter = await page.evaluate((id) => globalProducts.find((p) => p.id === id)?.quantity, productId);
  check('no stock is put back on this device before the server records the refund', stockAfter === stockBefore, `${stockBefore} ${stockAfter}`);
  const shown = await page.evaluate(async (dayId) => { await openRefundTransactionsModal(dayId); return document.getElementById('refund-transactions-list').textContent; }, s.dayId);
  check('the sale shows "Refund pending", not refunded', /Refund pending/.test(shown), shown.slice(0, 300));
  const raw = await sealedRows(page);
  check('refunds are sealed at rest', raw.filter((r) => r.type === 'sale_refund').length === 2 && raw.every((r) => r.sealed && !('payload' in r)) && !JSON.stringify(raw).includes('transaction_key'));

  ({ ctx, page } = await restartOffline(ctx, dir));
  rows = await outbox(page);
  check('after a restart offline both refunds are still queued', rows.filter((r) => r.type === 'sale_refund').length === 2, JSON.stringify(rows));
  check('Sync Details says "Refund pending"', /Refund pending/.test((await syncDetails(page)).pending));

  // Another till refunds 2 of the synchronized sale's 3 while this one is offline.
  const other = await fetch(`${BASE}/sales/transactions/${synced.ref}/refund`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines: [{ sale_id: synced.saleId, quantity: 2, restock: true }], client_ref: `e2e-other-${Date.now()}` }) });
  check('setup: another till refunded 2 of 3 online', other.status === 200, String(other.status));
  const serverStock = Number(sql(`select quantity from products where id=${productId}`));
  rows = await reconnect(ctx, page);
  const conflict = rows.find((r) => r.type === 'sale_refund');
  check('the synced sale\'s offline refund is refused whole with the figures', rows.length === 1 && conflict?.conflict_code === 'REFUND_CONFLICT' && /only 1 of the 3 sold can still be refunded \(2 already refunded\)/.test(conflict.last_error), JSON.stringify(rows));
  const refundedSynced = Number(sql(`select coalesce(sum(quantity),0) from refund_lines where original_sale_id=${synced.saleId}`));
  check('never refunded more than was sold', refundedSynced === 2, String(refundedSynced));
  const localRow = sql(`select count(*), coalesce(sum(l.quantity),0), bool_and(l.restocked) from refund_transactions t join refund_lines l on l.refund_transaction_id=t.id where t.client_ref=${q(localRefund.op_id)}`);
  check('the offline sale synced first, then its refund, once, restocked', localRow === '1|1|t', localRow);
  const order = sql(`select (select min(timestamp) from sales where client_ref=${q(saleOp.op_id)}) < (select created_at from refund_transactions where client_ref=${q(localRefund.op_id)})`);
  check('sale before refund on the server', order === 't', order);
  const stockFinal = Number(sql(`select quantity from products where id=${productId}`));
  check('stock: the offline sale took 1 and its refund put 1 back, exactly once', stockFinal === serverStock - 1 + 1, `${serverStock} -> ${stockFinal}`);
  check('Sync Details shows the refund conflict reason', (await syncDetails(page)).conflicts.includes('already refunded'));
  await clearOutbox(page);
  await ctx.close();
}

async function scenarioRefundHeld() {
  console.log('\n# G2: a refund of an offline sale that is refused stays held');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-g2h-'));
  let { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page, { openDay: true });
  const productId = Number(sql(`select id from products where name=${q(s.productName)} order by id desc limit 1`));
  ({ ctx, page } = await restartOffline(ctx, dir));
  await sellOffline(page, s.productName);
  const saleOp = (await outbox(page)).find((r) => r.type === 'sale_checkout');
  await page.evaluate(async (key) => queueRefundOffline(key, [{ sale_id: -1, quantity: 1, restock: true }]), saleOp.op_id);
  // The server price changed, so the offline sale is refused on reconnect.
  const price = sql(`select retail_price from products where id=${productId}`);
  sql(`update products set retail_price=retail_price+1 where id=${productId}`);
  const rows = await reconnect(ctx, page);
  sql(`update products set retail_price=${Number(price)} where id=${productId}`);
  const sale = rows.find((r) => r.type === 'sale_checkout'), refund = rows.find((r) => r.type === 'sale_refund');
  check('the sale is refused with its own reason', sale?.status === 'conflict' && sale.conflict_code === 'STOCK_CHANGED', JSON.stringify(sale));
  check('its refund is held, never sent, with a reason', refund?.status === 'conflict' && /The sale this refund is for could not be synchronized/.test(refund.last_error)
    && sql(`select count(*) from refund_transactions where client_ref=${q(refund.op_id)}`) === '0', JSON.stringify(refund));
  await clearOutbox(page);
  await ctx.close();
}

(async () => {
  const only = process.env.CAULDRA_E2E_ONLY;
  for (const run of [scenarioStock, scenarioRefund, scenarioRefundHeld].filter((r) => !only || only.split(',').includes(r.name))) {
    try { await run(); } catch (e) { check(`${run.name} completed`, false, e.stack); }
  }
  console.log(failures() ? `\n${failures()} FAILED` : '\nALL PASS');
  process.exit(failures() ? 1 : 0);
})();
