// BUSINESS-DAY-OFFLINE-001 end to end: the real app in Chromium against a real
// local backend. Not for QA or production: scenario 3 edits the local database.
//
//   1. no open Business Day -> offline -> open it from the Dashboard control ->
//      offline sale -> restart the app while still offline -> unlock -> the day
//      is still there -> second sale reuses it -> reconnect -> the day syncs
//      first, both sales land in it, one open day.
//   2. another device opens today's day while this one is offline -> reconnect
//      -> the offline day is joined to it, no second open day, sales kept.
//   3. the location's open day is from another date -> the offline day and its
//      sale are refused with their own reason (nothing added to that day) ->
//      a later permission change does not overwrite those reasons (row 78).
//   4. closing offline: open -> sales -> close offline -> restart offline -> the
//      day is still closed -> a new sale opens a new day that waits for the
//      close -> reconnect -> open, sales, close, next open reach the server in
//      that order; a synced day closed offline; a day closed on the server
//      first (its sale and the close are held with reasons); another device
//      opened the day (joined and closed) or kept selling after the offline
//      close (close refused, day left open).
//   Also: the queue stays sealed at rest; without business_day.manage the
//   Open/Close controls are refused, while a sale still opens a day with the
//   sale permission, as online.
//
// Needs: a backend with CAULDRA_OFFLINE_SIGNING_KEY, `playwright` resolvable,
// psql, and an admin account whose business has one location with a currency.
//   CAULDRA_E2E_URL=http://127.0.0.1:8000 CAULDRA_E2E_BIZ_CODE=... CAULDRA_E2E_USER=...
//   CAULDRA_E2E_PASSWORD=... CAULDRA_E2E_LOCAL_DB=postgresql://postgres@127.0.0.1:5433/cauldra_local
//   node tests/run_business_day_offline_e2e.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE = process.env.CAULDRA_E2E_URL || 'http://127.0.0.1:8000';
const DB = process.env.CAULDRA_E2E_LOCAL_DB || '';
const PIN = '246810';
for (const name of ['CAULDRA_E2E_BIZ_CODE', 'CAULDRA_E2E_USER', 'CAULDRA_E2E_PASSWORD'])
  if (!process.env[name]) { console.error(`${name} is required`); process.exit(2); }
if (!/^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(BASE + '/') || (DB && !/@(127\.0\.0\.1|localhost)[:/]/.test(DB))) {
  console.error('Local backend and local database only.'); process.exit(2);
}
const sql = (query) => execFileSync('psql', [DB, '-Atc', query], { encoding: 'utf8' }).trim();

let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${!ok && detail ? ' — ' + detail : ''}`); if (!ok) failures++; };
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function launch(dir, offline) {
  const ctx = await chromium.launchPersistentContext(dir, { viewport: { width: 1366, height: 768 } });
  await ctx.route(/flagcdn/, (r) => r.abort());
  if (offline) await ctx.setOffline(true);
  const page = ctx.pages()[0] || await ctx.newPage();
  page.on('pageerror', (e) => console.log('  [pageerror]', e.message));
  return { ctx, page };
}

async function signIn(page) {
  await page.waitForFunction(() => typeof openBusinessAuthModal === 'function');
  await page.evaluate(() => { openBusinessAuthModal(); switchBizAuthView('signin'); });
  await page.fill('#signin-biz-code', process.env.CAULDRA_E2E_BIZ_CODE);
  await page.evaluate(() => verifyBusinessCodeServerSide());
  await page.waitForSelector('#biz-auth-view-signin-step2:not(.hidden)');
  await page.evaluate(() => { selectSignInRole('admin'); selectRoleActionMode('signin'); });
  await page.fill('#employee-login-username', process.env.CAULDRA_E2E_USER);
  await page.fill('#employee-login-password', process.env.CAULDRA_E2E_PASSWORD);
  await page.evaluate(() => document.getElementById('role-credentials-form').requestSubmit());
  await page.waitForFunction(() => typeof authToken !== 'undefined' && !!authToken, null, { timeout: 20000 });
  await pause(2500);
  if (await page.$('#offline-opt-in-dialog[open]')) await page.click('#offline-opt-in-not-now');
  await page.evaluate(() => setLanguage('en'));
}

// Online: one location with a warehouse and a product in stock, NO open day,
// app shell cached, Offline Access provisioned with a PIN.
async function prepareOnline(page, { openDay = false } = {}) {
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await signIn(page);
  const setup = await page.evaluate(async () => {
    const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` };
    const j = async (r) => ({ s: r.status, b: await r.json().catch(() => null) });
    const locs = (await j(await fetch(`${API_URL}/locations/`, { headers: H }))).b;
    const loc = (locs.locations || locs).find((l) => l.is_active !== false);
    let whs = (await j(await fetch(`${API_URL}/warehouses/`, { headers: H }))).b; whs = whs.warehouses || whs;
    let wh = whs.find((w) => w.location_id === loc.id && w.is_active !== false);
    if (!wh) wh = (await j(await fetch(`${API_URL}/warehouses/`, { method: 'POST', headers: H, body: JSON.stringify({ name: 'E2E Store', location_id: loc.id }) }))).b;
    await fetch(`${API_URL}/sales/end-business-day?location_id=${loc.id}`, { method: 'POST', headers: H });
    let name = 'Offline Day Probe ' + Math.random().toString(36).slice(2, 7);
    const created = await j(await fetch(`${API_URL}/products/`, { method: 'POST', headers: H, body: JSON.stringify({ name, category: 'Test', quantity: 30, min_stock_level: 1, cost_price: 50, retail_price: 100, wholesale_price: 90, warehouse: wh.name }) }));
    if (created.s >= 400) { // e.g. the plan's product limit: reuse a stocked product
      let products = (await j(await fetch(`${API_URL}/products/?limit=500`, { headers: H }))).b; products = products.products || products;
      name = products.find((p) => p.warehouse === wh.name && p.quantity >= 5)?.name; // any product stocked in that warehouse
    }
    const current = (await j(await fetch(`${API_URL}/sales/current-day?location_id=${loc.id}`, { headers: H }))).b;
    return { locationId: loc.id, productName: name, created: created.s, openBefore: current.open, token: authToken, warehouseName: wh.name };
  });
  if (openDay) {
    const r = await fetch(`${BASE}/sales/start-business-day?location_id=${setup.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${setup.token}` } });
    setup.dayId = (await r.json()).business_day?.id;
    await page.evaluate(() => loadData());
  }
  await page.evaluate(async () => { await navigator.serviceWorker.register('/sw.js'); await navigator.serviceWorker.ready; });
  for (let i = 0; i < 4 && !(await page.evaluate(() => !!navigator.serviceWorker.controller)); i++) { await page.reload({ waitUntil: 'networkidle' }); await pause(1500); }
  if (await page.$('#offline-opt-in-dialog[open]')) await page.click('#offline-opt-in-not-now');
  await page.evaluate((pin) => window.CauldraOffline.provision({ apiUrl: API_URL, token: authToken, user: currentUserProfile, business: businessProfile, pin }), PIN);
  return setup;
}

async function unlockOffline(page) {
  await page.goto(BASE + '/').catch(() => {});
  await page.waitForSelector('#offline-unlock-dialog[open]', { timeout: 30000 });
  await page.fill('#offline-pin', PIN);
  await page.click('#offline-pin-unlock');
  await page.waitForFunction(() => typeof offlineWorkspaceUnlocked !== 'undefined' && offlineWorkspaceUnlocked && !document.getElementById('offline-unlock-dialog')?.open, null, { timeout: 30000 });
  await pause(1000);
}

const header = (page, locationId) => page.evaluate(async (id) => {
  selectedBusinessDayLocationId = id;
  await loadBusinessDayControl();
  const btn = document.getElementById('business-day-header-action-btn');
  return { label: document.getElementById('business-day-header-label').textContent, sub: document.getElementById('business-day-header-sub').textContent,
    button: btn.classList.contains('hidden') ? null : btn.textContent };
}, locationId);

const outbox = (page) => page.evaluate(async () => (await window.CauldraOffline.listOutbox()).map((r) => ({
  op_id: r.op_id, type: r.type, status: r.status, depends_on_op_ids: r.depends_on_op_ids, business_day_id: r.payload?.business_day_id,
  local_id: r.meta?.local_id, conflict_code: r.conflict_code, last_error: r.last_error })));

async function sellOffline(page, productName) {
  await page.evaluate(async (name) => {
    openSaleModal(); await new Promise((r) => setTimeout(r, 400));
    addSpecificProductToPOSCart(globalProducts.find((p) => p.name === name)); await new Promise((r) => setTimeout(r, 300));
    await completePOSCheckout();
  }, productName);
  await pause(800);
}

async function closeFromDashboard(page, locationId) {
  await page.evaluate(async (id) => { selectedBusinessDayLocationId = id; showCustomConfirm = async () => true; await loadBusinessDayControl(); await openBusinessDayHeaderClose(); }, locationId);
  await pause(800);
}

const q = (v) => `'${String(v).replace(/'/g, "''")}'`;
const dayRow = (id) => sql(`select is_open, to_char(opened_at,'YYYY-MM-DD"T"HH24:MI:SS"Z"'), coalesce(to_char(closed_at,'YYYY-MM-DD"T"HH24:MI:SS"Z"'),'') from business_days where id=${Number(id)}`).split('|');

async function sellOnlineAsOtherDevice(s) {
  const productId = sql(`select id from products where name=${q(s.productName)} order by id desc limit 1`);
  const r = await fetch(`${BASE}/sales/checkout`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: [{ product_id: Number(productId), quantity: 1, price_mode: 'retail' }], location_id: s.locationId, client_ref: `e2e-b-${Date.now()}` }) });
  return r.status;
}

async function reconnect(ctx, page) {
  await ctx.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await pause(4000);
  if (!(await page.evaluate(() => !!authToken))) await signIn(page);
  for (let i = 0; i < 40; i++) {
    await page.evaluate(() => runSync());
    const rows = await outbox(page);
    if (!rows.some((r) => ['pending', 'syncing', 'failed_retryable', 'blocked_dependency'].includes(r.status))) return rows;
    await pause(1500);
  }
  return outbox(page);
}

const salesFor = (refs) => refs.length ? sql(`select distinct business_day_id from sales where client_ref in (${refs.map((r) => `'${r}'`).join(',')})`).split('\n').filter(Boolean) : [];
const openDays = (locationId) => sql(`select id from business_days where location_id=${Number(locationId)} and is_open`).split('\n').filter(Boolean);

async function scenario1() {
  console.log('\n# 1. open offline, sell, restart offline, sell again, reconnect');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-bd1-'));
  let { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page);
  check('setup: no Business Day open at the location before going offline', s.openBefore === false, JSON.stringify(s));
  await ctx.setOffline(true);
  await unlockOffline(page);
  const closed = await header(page, s.locationId);
  check('offline, no day: Dashboard offers Open Business Day', closed.label === 'Business Day Closed' && closed.button === 'Open Business Day', JSON.stringify(closed));
  await page.evaluate(() => openBusinessDayHeaderOpen());
  await pause(800);
  const opened = await header(page, s.locationId);
  check('opened offline: shown open, waiting to sync, and it can be closed offline', opened.label === 'Business Day Open' && /^Opened offline at .+ · waiting to sync$/.test(opened.sub) && opened.button === 'Close Business Day', JSON.stringify(opened));
  await sellOffline(page, s.productName);
  let rows = await outbox(page);
  const day = rows.find((r) => r.type === 'business_day_open');
  const sale1 = rows.find((r) => r.type === 'sale_checkout');
  check('offline sale is queued against the offline day and waits for it', !!day && day.local_id < 0 && sale1?.business_day_id === day.local_id && sale1.depends_on_op_ids.includes(day.op_id), JSON.stringify(rows));

  const raw = await page.evaluate(() => new Promise((resolve) => { const req = indexedDB.open('cauldra_offline'); req.onsuccess = () => { const all = req.result.transaction('secure_outbox').objectStore('secure_outbox').getAll(); all.onsuccess = () => resolve(JSON.stringify(all.result)); }; }));
  check('queued day and sale are sealed at rest (no location, product or payload in clear)', raw.includes('"sealed"') && !raw.includes('location_id') && !raw.includes(s.productName) && !raw.includes('"payload"'));

  await ctx.close();
  ({ ctx, page } = await launch(dir, true));
  await unlockOffline(page);
  const afterRestart = await header(page, s.locationId);
  check('after restarting offline and unlocking, the offline day is still open', afterRestart.label === 'Business Day Open' && /waiting to sync/.test(afterRestart.sub), JSON.stringify(afterRestart));
  await sellOffline(page, s.productName);
  rows = await outbox(page);
  const sales = rows.filter((r) => r.type === 'sale_checkout');
  check('second sale reuses the same offline day (one day change queued)', rows.filter((r) => r.type === 'business_day_open').length === 1 && sales.length === 2 && sales.every((r) => r.business_day_id === day.local_id), JSON.stringify(rows));

  await closeFromDashboard(page, s.locationId);
  const closedLocally = await header(page, s.locationId);
  check('closed offline: shown closed, waiting to sync, Open offered again', closedLocally.label === 'Business Day Closed' && /^Closed offline at .+ · waiting to sync$/.test(closedLocally.sub) && closedLocally.button === 'Open Business Day', JSON.stringify(closedLocally));
  rows = await outbox(page);
  const close = rows.find((r) => r.type === 'business_day_close');
  check('the close waits for the day\'s open and both sales', !!close && close.business_day_id === day.local_id && [day.op_id, ...sales.map((r) => r.op_id)].every((id) => close.depends_on_op_ids.includes(id)), JSON.stringify(close));

  await ctx.close();
  ({ ctx, page } = await launch(dir, true));
  await unlockOffline(page);
  const closedAfterRestart = await header(page, s.locationId);
  check('after another offline restart the day is still closed', closedAfterRestart.label === 'Business Day Closed' && /^Closed offline at/.test(closedAfterRestart.sub), JSON.stringify(closedAfterRestart));
  const raw2 = await page.evaluate(() => new Promise((resolve) => { const req = indexedDB.open('cauldra_offline'); req.onsuccess = () => { const all = req.result.transaction('secure_outbox').objectStore('secure_outbox').getAll(); all.onsuccess = () => resolve(all.result); }; }));
  check('open, sales and close are all still queued and sealed at rest', raw2.length === 4 && raw2.every((r) => r.sealed && !('payload' in r)) && !JSON.stringify(raw2).includes('own_refs') && !JSON.stringify(raw2).includes('location_id'));
  await sellOffline(page, s.productName);
  rows = await outbox(page);
  const day2 = rows.find((r) => r.type === 'business_day_open' && r.op_id !== day.op_id);
  const sale3 = rows.find((r) => r.type === 'sale_checkout' && !sales.some((x) => x.op_id === r.op_id));
  check('no new sale attaches to the closed day: it opens a new day that waits for the close', !!day2 && day2.depends_on_op_ids.includes(close.op_id) && sale3?.business_day_id === day2.local_id && sale3.business_day_id !== day.local_id, JSON.stringify(rows));

  const reconnectAt = Date.now();
  const synced = await reconnect(ctx, page);
  check('reconnect: open, sales, close and the next day all synchronized', synced.length === 0, JSON.stringify(synced));
  const [firstDay] = salesFor(sales.map((r) => r.op_id));
  check('both earlier sales belong to one day', salesFor(sales.map((r) => r.op_id)).length === 1, salesFor(sales.map((r) => r.op_id)).join(','));
  const [isOpen1, opened1, closed1] = dayRow(firstDay);
  check('that day is closed, keeping the offline open and close times', isOpen1 === 'f' && Date.parse(opened1) < Date.parse(closed1) && Date.parse(closed1) < reconnectAt - 1000, `${opened1} ${closed1}`);
  const [secondDay] = salesFor([sale3.op_id]);
  const [isOpen2, opened2] = dayRow(secondDay);
  check('the later sale is in a second day, opened after the first closed', secondDay !== firstDay && isOpen2 === 't' && Date.parse(opened2) >= Date.parse(closed1), `${closed1} -> ${opened2}`);
  check('exactly one open day at the location', JSON.stringify(openDays(s.locationId)) === JSON.stringify([secondDay]), openDays(s.locationId).join(','));
  const closeAudit = sql(`select metadata_json from audit_logs where business_day_id=${firstDay} and action='BUSINESS_DAY_CLOSED'`);
  check('audit: deliberate open, offline close, auto-opened second day', /"offline": true/.test(closeAudit)
    && /"auto": false/.test(sql(`select metadata_json from audit_logs where business_day_id=${firstDay} and action='BUSINESS_DAY_STARTED'`))
    && sql(`select count(*) from audit_logs where business_day_id=${secondDay} and action='BUSINESS_DAY_AUTO_OPENED'`) === '1', closeAudit.slice(0, 200));
  const online = await header(page, s.locationId);
  check('after sync the Dashboard shows the second day from the server', online.label === 'Business Day Open' && online.button === 'Close Business Day' && !/waiting to sync/.test(online.sub), JSON.stringify(online));
  await fetch(`${BASE}/sales/end-business-day?location_id=${s.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } });
  await ctx.close();
}

async function scenarioSyncedDayClose() {
  console.log('\n# 5. a synchronized day is closed offline');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-bd5-'));
  const { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page, { openDay: true });
  await ctx.setOffline(true);
  await unlockOffline(page);
  const shown = await header(page, s.locationId);
  check('offline, the synchronized day is open and can be closed', shown.label === 'Business Day Open' && shown.button === 'Close Business Day', JSON.stringify(shown));
  await sellOffline(page, s.productName);
  await closeFromDashboard(page, s.locationId);
  const rows = await outbox(page);
  const sale = rows.find((r) => r.type === 'sale_checkout'), close = rows.find((r) => r.type === 'business_day_close');
  check('sale and close go to the synchronized day; the close waits for the sale', sale?.business_day_id === s.dayId && close?.business_day_id === s.dayId && close.depends_on_op_ids.includes(sale.op_id), JSON.stringify(rows));
  const reconnectAt = Date.now();
  const synced = await reconnect(ctx, page);
  const [isOpen, , closedAt] = dayRow(s.dayId);
  check('reconnect: the day is closed at the offline close time, with its sale', synced.length === 0 && isOpen === 'f' && Date.parse(closedAt) < reconnectAt - 1000 && JSON.stringify(salesFor([sale.op_id])) === JSON.stringify([String(s.dayId)]), `${JSON.stringify(synced)} ${closedAt}`);
  await ctx.close();
}

async function scenarioServerClosedFirst() {
  console.log('\n# 6. the day is closed on the server before this device reconnects');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-bd6-'));
  const { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page, { openDay: true });
  await ctx.setOffline(true);
  await unlockOffline(page);
  await sellOffline(page, s.productName);
  await closeFromDashboard(page, s.locationId);
  await fetch(`${BASE}/sales/end-business-day?location_id=${s.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } });
  const [, , serverClosed] = dayRow(s.dayId);
  const rows = await reconnect(ctx, page);
  const sale = rows.find((r) => r.type === 'sale_checkout'), close = rows.find((r) => r.type === 'business_day_close');
  check('the pending sale is kept with its own reason (day closed)', sale?.status === 'conflict' && sale.conflict_code === 'BUSINESS_DAY_CLOSED', JSON.stringify(sale));
  check('the offline close is held, not sent over unresolved work', close?.status === 'conflict' && close.conflict_code === 'BUSINESS_DAY_CONFLICT' && /needs review first/.test(close.last_error), JSON.stringify(close));
  const [isOpen, , closedNow] = dayRow(s.dayId);
  check('the server day is untouched: closed once, at the other device\'s time', isOpen === 'f' && closedNow === serverClosed && salesFor([sale.op_id]).length === 0, `${serverClosed} ${closedNow}`);
  for (const r of rows) await page.evaluate((id) => window.CauldraOffline.removeOutbox(id), r.op_id);
  await ctx.close();
}

async function scenarioJoinedAndClosed(otherDeviceKeepsSelling) {
  console.log(`\n# 7. another device opened today's day; this one closes it offline${otherDeviceKeepsSelling ? ', but the other device keeps selling' : ''}`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-bd7-'));
  const { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page);
  await ctx.setOffline(true);
  await unlockOffline(page);
  await header(page, s.locationId);
  await sellOffline(page, s.productName);
  await closeFromDashboard(page, s.locationId);
  const queued = await outbox(page);
  const other = await (await fetch(`${BASE}/sales/start-business-day?location_id=${s.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } })).json();
  const otherDay = String(other.business_day.id);
  if (otherDeviceKeepsSelling) check('device B sells in its day after A closed offline', (await sellOnlineAsOtherDevice(s)) === 200);
  const rows = await reconnect(ctx, page);
  const mySales = queued.filter((r) => r.type === 'sale_checkout').map((r) => r.op_id);
  check('A\'s sale joined B\'s day (never a second day)', JSON.stringify(salesFor(mySales)) === JSON.stringify([otherDay]) && sql(`select count(*) from business_days where location_id=${s.locationId} and id > ${Number(otherDay)}`) === '0');
  if (otherDeviceKeepsSelling) {
    const close = rows.find((r) => r.type === 'business_day_close');
    check('A\'s close is refused and held; the day stays open for B', close?.status === 'conflict' && close.conflict_code === 'BUSINESS_DAY_CONFLICT' && /after it was closed offline/.test(close.last_error) && dayRow(otherDay)[0] === 't', JSON.stringify(close));
    for (const r of rows) await page.evaluate((id) => window.CauldraOffline.removeOutbox(id), r.op_id);
    await fetch(`${BASE}/sales/end-business-day?location_id=${s.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } });
  } else {
    check('with no later work by B, A\'s offline close closes that day', rows.length === 0 && dayRow(otherDay)[0] === 'f' && openDays(s.locationId).length === 0, JSON.stringify(rows));
  }
  await ctx.close();
}

async function scenario2() {
  console.log('\n# 2. another device opens today\'s day while this one is offline');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-bd2-'));
  const { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page);
  await ctx.setOffline(true);
  await unlockOffline(page);
  await header(page, s.locationId);
  await sellOffline(page, s.productName);
  const rows = await outbox(page);
  const day = rows.find((r) => r.type === 'business_day_open');
  check('a sale with no open day opens one offline for a permitted user', !!day && rows.some((r) => r.type === 'sale_checkout' && r.depends_on_op_ids.includes(day.op_id)), JSON.stringify(rows));
  const other = await fetch(`${BASE}/sales/start-business-day?location_id=${s.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } });
  const otherDay = (await other.json()).business_day?.id;
  check('device B opens the location\'s day online meanwhile', other.status === 200 && !!otherDay, String(other.status));
  const synced = await reconnect(ctx, page);
  check('reconnect: nothing left waiting or refused', synced.length === 0, JSON.stringify(synced));
  const days = openDays(s.locationId);
  check('still exactly one open day: device B\'s', JSON.stringify(days) === JSON.stringify([String(otherDay)]), days.join(','));
  check('the offline sale was kept and joined that day', JSON.stringify(salesFor(rows.filter((r) => r.type === 'sale_checkout').map((r) => r.op_id))) === JSON.stringify([String(otherDay)]));
  check('the join is audited', sql(`select count(*) from audit_logs where business_day_id=${otherDay} and action='BUSINESS_DAY_OFFLINE_OPEN_JOINED'`) === '1');
  await ctx.close();
}

async function scenario3() {
  console.log('\n# 3. open day from another date; then a later permission change (row 78)');
  if (!DB) { console.log('SKIP scenario 3 (CAULDRA_E2E_LOCAL_DB not set)'); return; }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-bd3-'));
  const { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page);
  await ctx.setOffline(true);
  await unlockOffline(page);
  await header(page, s.locationId);
  await sellOffline(page, s.productName);
  const queued = await outbox(page);
  const other = await (await fetch(`${BASE}/sales/start-business-day?location_id=${s.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } })).json();
  sql(`update business_days set date = (date::date - 1)::text where id=${other.business_day.id}`);
  let rows = await reconnect(ctx, page);
  const day = rows.find((r) => r.type === 'business_day_open');
  const sale = rows.find((r) => r.type === 'sale_checkout');
  check('offline day from another date is refused with its reason', day?.status === 'conflict' && day.conflict_code === 'BUSINESS_DAY_CONFLICT' && /different date/.test(day.last_error), JSON.stringify(day));
  check('its sale is kept on the device with its own reason, not dropped', sale?.status === 'conflict' && sale.conflict_code === 'BUSINESS_DAY_CONFLICT' && /could not be synchronized: A Business Day from a different date/.test(sale.last_error), JSON.stringify(sale));
  check('nothing was added to the other day', salesFor(queued.filter((r) => r.type === 'sale_checkout').map((r) => r.op_id)).length === 0);

  const userId = sql(`select id from users where username='${process.env.CAULDRA_E2E_USER.replace(/'/g, "''")}' and business_id=(select business_id from locations where id=${Number(s.locationId)})`);
  const before = sql(`select coalesce(permission_overrides,'') from users where id=${userId}`);
  try {
    await page.evaluate(() => window.CauldraOffline.enqueue({ op_id: crypto.randomUUID(), type: 'supplier_create', payload: { name: 'Later change' }, created_at: Date.now() }));
    sql(`update users set permission_overrides='{"reports.sales": false}' where id=${userId}`);
    await page.evaluate(() => runSync());
    await pause(2500);
    rows = await page.evaluate(async () => (await window.CauldraOffline.listOutbox().catch(() => [])).length);
    await page.reload(); await pause(2500);
  } finally {
    sql(`update users set permission_overrides=${before ? `'${before.replace(/'/g, "''")}'` : 'NULL'} where id=${userId}`);
  }
  if (await page.$('#offline-unlock-dialog[open]')) await unlockOffline(page);
  else {
    if (!(await page.evaluate(() => !!authToken))) await signIn(page);
    await page.evaluate(async (pin) => { try { await window.CauldraOffline.refreshAccess({ apiUrl: API_URL, token: authToken, user: currentUserProfile, business: businessProfile }); } catch (_) {} }, PIN);
  }
  const final = await outbox(page);
  const d2 = final.find((r) => r.op_id === day.op_id), s2 = final.find((r) => r.op_id === sale.op_id), later = final.find((r) => r.type === 'supplier_create');
  check('row 78: the later permission change took the waiting change', later?.status === 'conflict' && ['AUTH_EXPIRED', 'PERMISSION_CHANGED'].includes(later.conflict_code), JSON.stringify(later));
  check('row 78: earlier refusal reasons are not overwritten', d2?.conflict_code === 'BUSINESS_DAY_CONFLICT' && s2?.conflict_code === 'BUSINESS_DAY_CONFLICT' && /different date/.test(d2.last_error) && /could not be synchronized/.test(s2.last_error), JSON.stringify([d2, s2]));
  await page.evaluate(() => window.CauldraOffline.openSyncDetails());
  const details = await page.evaluate(() => document.getElementById('offline-conflict-list').textContent);
  check('Sync Details shows each separate reason', /different date/.test(details) && /could not be synchronized/.test(details) && (later ? details.includes(later.last_error) : false), details.slice(0, 400));
  for (const r of final) await page.evaluate((id) => window.CauldraOffline.removeOutbox(id), r.op_id);
  await fetch(`${BASE}/sales/end-business-day?location_id=${s.locationId}`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } });
  await ctx.close();
}

async function scenarioDenied() {
  console.log('\n# permission: no business_day.manage, offline, no day');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-bd4-'));
  const { ctx, page } = await launch(dir, false);
  const s = await prepareOnline(page);
  await ctx.setOffline(true);
  await unlockOffline(page);
  const result = await page.evaluate(async (id) => {
    currentEffectivePermissions = { ...currentEffectivePermissions, 'business_day.manage': false };
    let error = null;
    try { await openBusinessDayOffline(id); } catch (e) { error = e.message; }
    return { error, queued: (await window.CauldraOffline.listOutbox()).length };
  }, s.locationId);
  check('a user without business_day.manage cannot open a day offline', /Ask someone who can open the Business Day/.test(result.error || '') && result.queued === 0, JSON.stringify(result));
  const hidden = await header(page, s.locationId);
  check('and the Dashboard does not offer the button', hidden.button === null, JSON.stringify(hidden));
  await sellOffline(page, s.productName);
  const rows = await outbox(page);
  check('matches online: a sale still opens a day with the sale permission', rows.some((r) => r.type === 'business_day_open') && rows.some((r) => r.type === 'sale_checkout'), JSON.stringify(rows));
  const closeResult = await page.evaluate(async (id) => { try { await closeBusinessDayOffline(id); return null; } catch (e) { return e.message; } }, s.locationId);
  const afterSale = await header(page, s.locationId);
  check('without business_day.manage the day cannot be closed offline, and no Close is offered', /do not have permission to close/.test(closeResult || '') && afterSale.button === null && (await outbox(page)).every((r) => r.type !== 'business_day_close'), `${closeResult} ${JSON.stringify(afterSale)}`);
  for (const r of rows) await page.evaluate((id) => window.CauldraOffline.removeOutbox(id), r.op_id);
  await ctx.close();
}

(async () => {
  const only = process.env.CAULDRA_E2E_ONLY;
  const runs = [scenario1, scenario2, scenario3, scenarioSyncedDayClose, scenarioServerClosedFirst,
    function scenarioJoinedClosed() { return scenarioJoinedAndClosed(false); }, function scenarioJoinedOtherKeepsSelling() { return scenarioJoinedAndClosed(true); }, scenarioDenied];
  for (const run of runs.filter((r) => !only || only.split(',').includes(r.name))) {
    try { await run(); } catch (e) { check(`${run.name} completed`, false, e.stack); }
  }
  console.log(failures ? `\n${failures} FAILED` : '\nALL PASS');
  process.exit(failures ? 1 : 0);
})();
