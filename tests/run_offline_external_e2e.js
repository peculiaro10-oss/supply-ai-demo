// OFFLINE-EXTERNAL-001 end to end: the real app in Chromium against a real
// local backend whose email provider is SIMULATED (tests/fake_email_provider;
// no email ever leaves the machine). Not for QA or production: it edits the
// local database to play "another device".
//
//   email:   a purchase-order draft saved while online is readable offline
//            ("Using saved data from ...") -> Send by email offline queues it,
//            sealed, "Waiting to send" (nothing reaches the provider, the order
//            stays DRAFT) -> no second queue for the same order -> restart
//            offline, still waiting -> reconnect during a provider outage: not
//            sent, retried, never called sent -> provider back: emailed once,
//            with the provider idempotency key, order SENT.
//   cancel:  a queued email is cancelled in Sync Details before reconnecting:
//            nothing is ever sent and the draft can be sent again.
//   changed: the draft is edited elsewhere before reconnecting: the email is
//            held ("Needs attention", not sent) and can be removed.
//   wait:    offline, the AI assistant keeps the question and says it was not
//            answered; the local answers still work; barcode look-up checks
//            the business's own products and skips the provider; nothing is
//            requested from /ai/* or /catalog/barcode-lookup.
//
// Same environment as tests/run_business_day_offline_e2e.js, plus the
// simulated provider's files (see tests/fake_email_provider/sitecustomize.py):
//   CAULDRA_TEST_FAKE_EMAIL_LOG=... CAULDRA_TEST_FAKE_EMAIL_STATUS_FILE=...
//   node tests/run_offline_external_e2e.js
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { launch, prepareOnline, unlockOffline, outbox, signIn, sql, q, check, pause, BASE, failures } = require('./run_business_day_offline_e2e.js');

const LOG = process.env.CAULDRA_TEST_FAKE_EMAIL_LOG;
const STATUS = process.env.CAULDRA_TEST_FAKE_EMAIL_STATUS_FILE;
if (!LOG || !STATUS) { console.error('CAULDRA_TEST_FAKE_EMAIL_LOG and CAULDRA_TEST_FAKE_EMAIL_STATUS_FILE are required'); process.exit(2); }
const providerCalls = () => fs.readFileSync(LOG, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
const setProvider = (status) => fs.writeFileSync(STATUS, String(status));

const sealedRows = (page) => page.evaluate(() => new Promise((resolve) => {
  const req = indexedDB.open('cauldra_offline');
  req.onsuccess = () => { const all = req.result.transaction('secure_outbox').objectStore('secure_outbox').getAll(); all.onsuccess = () => resolve(all.result); };
}));
const syncDetails = (page) => page.evaluate(async () => {
  await window.CauldraOffline.openSyncDetails();
  const text = { pending: document.getElementById('offline-pending-list').textContent, conflicts: document.getElementById('offline-conflict-list').textContent,
    cancels: [...document.querySelectorAll('#offline-sync-dialog [data-cancel-op]')].map((b) => b.textContent) };
  document.getElementById('offline-sync-dialog').close();
  return text;
});
const draftCard = (page, poId) => page.evaluate(async (id) => {
  await loadPurchaseOrders();
  const box = document.getElementById('po-draft-container');
  const card = [...box.children].find((el) => el.textContent.includes(`#${id}`)) || null;
  return { note: box.querySelector('.po-offline-note')?.textContent || '', text: card?.textContent || '',
    waiting: !!card?.querySelector('.po-waiting-to-send'), sendButton: !!card?.querySelector(`[onclick="sendPurchaseOrderEmail(${id})"]`) };
}, poId);
// Every toast the app shows, as the app itself worded it.
const recordToasts = (page) => page.evaluate(() => { window.__toasts = []; const show = showToast; showToast = (m, t) => { window.__toasts.push(friendlyErrorMessage(m)); return show(m, t); }; });
const lastToast = (page) => page.evaluate(() => (window.__toasts || []).slice(-1)[0] || '');

function makeDraft(label) {
  const biz = sql(`select id from business_profile where business_code=${q(process.env.CAULDRA_E2E_BIZ_CODE)}`);
  const supplier = sql(`insert into suppliers (name, contact_email, phone, business_id) values (${q('E2E Supplier ' + label)}, 'orders@supplier.example', '08011111111', ${biz}) returning id`).split('\n')[0];
  const loc = sql(`select id from locations where business_id=${biz} and is_active order by id limit 1`);
  const po = sql(`insert into purchase_orders (supplier_id, status, total_estimated_cost, email_draft, business_id, location_id, created_at) values (${supplier}, 'DRAFT', 500, ${q('Please supply 12 x E2E item ' + label)}, ${biz}, ${loc}, now() at time zone 'utc') returning id`).split('\n')[0];
  return Number(po);
}
const poStatus = (id) => sql(`select status from purchase_orders where id=${Number(id)}`);

async function queueEmail(page, poId) {
  await page.evaluate(async (id) => { showCustomConfirm = async () => true; await sendPurchaseOrderEmail(id); }, poId);
  await pause(600);
}

async function goOnline(ctx, page) {
  await ctx.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await pause(4000);
  if (!(await page.evaluate(() => !!authToken))) await signIn(page);
}
async function syncOnce(page) { await page.evaluate(() => runSync()); await pause(1500); return outbox(page); }

async function scenarioEmail() {
  console.log('\n# Purchase-order email saved offline');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-ext-'));
  const poId = makeDraft('A');
  const cancelId = makeDraft('B');
  const changedId = makeDraft('C');
  let { ctx, page } = await launch(dir, false);
  await prepareOnline(page);
  const before = providerCalls().length;
  await ctx.close();
  ({ ctx, page } = await launch(dir, true));
  await unlockOffline(page);

  let card = await draftCard(page, poId);
  check('saved purchase orders are readable offline, labelled with when they were saved', /Using saved data from /.test(card.note) && card.text.includes(`#${poId}`), card.note);
  check('offline: Send by email is offered, WhatsApp/edit are not', card.sendButton && !/WhatsApp/.test(card.text), card.text.slice(0, 200));

  await queueEmail(page, poId);
  let rows = await outbox(page);
  const emailOp = rows.find((r) => r.type === 'po_email_send');
  check('queued as a pending email, not sent', !!emailOp && emailOp.status === 'pending', JSON.stringify(rows));
  check('nothing reached the provider and the order is still a draft', providerCalls().length === before && poStatus(poId) === 'DRAFT');
  card = await draftCard(page, poId);
  check('the draft says "Waiting to send" and no longer offers Send', card.waiting && !card.sendButton && /not sent yet/.test(card.text), card.text.slice(0, 300));
  const sealed = await sealedRows(page);
  const raw = JSON.stringify(sealed);
  check('the queued email is sealed at rest (no supplier, draft text or address in clear)', sealed.length >= 1 && !raw.includes('E2E Supplier') && !raw.includes('Please supply') && !raw.includes('orders@supplier.example'));

  await queueEmail(page, poId);
  check('the same order cannot be queued twice', (await outbox(page)).filter((r) => r.type === 'po_email_send').length === 1);

  let details = await syncDetails(page);
  check('Sync Details: "Waiting to send" with a Cancel action', /Waiting to send/.test(details.pending) && details.cancels.includes('Cancel'), JSON.stringify(details));

  await queueEmail(page, cancelId);
  await queueEmail(page, changedId);
  await ctx.close();
  ({ ctx, page } = await launch(dir, true));
  await unlockOffline(page);
  rows = await outbox(page);
  check('after a restart offline all three emails are still waiting', rows.filter((r) => r.type === 'po_email_send' && r.status === 'pending').length === 3, JSON.stringify(rows));
  check('after the restart the draft still says "Waiting to send"', (await draftCard(page, poId)).waiting);

  // Cancel one before it is ever sent.
  page.once('dialog', (d) => d.accept());
  const cancelOpId = (await page.evaluate(async (id) => (await window.CauldraOffline.listOutbox()).find((r) => r.payload?.po_id === id)?.op_id, cancelId));
  await page.evaluate(async () => { await window.CauldraOffline.openSyncDetails(); });
  await page.click(`#offline-sync-dialog [data-cancel-op="${cancelOpId}"]`);
  await pause(800);
  const notice = await page.evaluate(() => document.getElementById('offline-sync-notice').textContent);
  await page.evaluate(() => document.getElementById('offline-sync-dialog').close());
  rows = await outbox(page);
  check('cancel: removed from the queue and says it was not sent', !rows.some((r) => r.op_id === cancelOpId) && /not sent/.test(notice), notice);
  card = await draftCard(page, cancelId);
  check('cancel: the draft can be sent again', !card.waiting && card.sendButton);

  // Another device edits one draft before this device reconnects.
  sql(`update purchase_orders set email_draft = email_draft || ' (edited elsewhere)' where id=${changedId}`);

  // Reconnect while the provider is down.
  setProvider(500);
  await goOnline(ctx, page);
  rows = await syncOnce(page);
  const afterOutage = rows.find((r) => r.op_id === emailOp.op_id);
  check('provider outage: kept for retry, not marked sent', afterOutage?.status === 'failed_retryable' && poStatus(poId) === 'DRAFT', JSON.stringify(afterOutage));
  check('provider outage: the attempt reached the provider but nothing was delivered', providerCalls().slice(before).filter((c) => c.delivered).length === 0);
  const changedRow = rows.find((r) => r.type === 'po_email_send' && r.op_id !== emailOp.op_id);
  check('edited elsewhere: held with a reason, not sent', changedRow?.status === 'conflict' && changedRow?.conflict_code === 'PO_CHANGED' && poStatus(changedId) === 'DRAFT', JSON.stringify(changedRow));
  details = await syncDetails(page);
  check('Sync Details: the held email "Needs attention" and can be removed', /Needs attention/.test(details.conflicts) && /not sent/.test(details.conflicts) && details.cancels.includes('Remove'), JSON.stringify(details));
  check('still waiting while the provider is down', /Waiting to send/.test(details.pending));

  // Provider recovers.
  setProvider(200);
  await page.evaluate(async (id) => window.CauldraOffline.updateOutbox(id, { next_retry_at: 0 }), emailOp.op_id);
  rows = await syncOnce(page);
  const calls = providerCalls().slice(before);
  const delivered = calls.filter((c) => c.delivered);
  check('provider back: emailed exactly once, order SENT', !rows.some((r) => r.op_id === emailOp.op_id) && delivered.length === 1 && poStatus(poId) === 'SENT', JSON.stringify(calls));
  check('every attempt used the provider idempotency key of the saved change', calls.length >= 2 && calls.filter((c) => c.subject === `Purchase Order #${poId}`).every((c) => c.idempotency_key === `cauldra-offline-${emailOp.op_id}`), JSON.stringify(calls));
  check('the cancelled email was never sent', poStatus(cancelId) === 'DRAFT');
  check('the app shows it as sent only now', (await page.evaluate((id) => globalPurchaseOrders.find((p) => p.id === id)?.status, poId)) === 'SENT');

  const audits = sql(`select count(*) from audit_logs where action='PURCHASE_ORDER_DISPATCHED' and resource_id=${poId}`);
  check('one dispatch recorded for the order', audits === '1', audits);

  await ctx.close();
}

async function scenarioMustWait() {
  console.log('\n# AI and barcode look-up offline');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cauldra-ext-wait-'));
  let { ctx, page } = await launch(dir, false);
  await prepareOnline(page);
  await ctx.close();
  ({ ctx, page } = await launch(dir, true));
  await unlockOffline(page);
  await recordToasts(page);
  const requested = [];
  page.on('request', (r) => { if (/\/ai\/|\/catalog\/barcode-lookup/.test(r.url())) requested.push(r.url()); });

  const chat = await page.evaluate(async () => {
    toggleAIChatWindow(); await new Promise((r) => setTimeout(r, 300));
    const input = document.getElementById('chat-input');
    input.value = 'What should I reorder before the holidays?';
    await sendAIChat({ preventDefault() {} });
    await new Promise((r) => setTimeout(r, 400));
    const offlineReply = document.querySelector('#chat-messages .ai-offline-reply')?.textContent || '';
    const kept = input.value;
    input.value = 'any low stock?';
    await sendAIChat({ preventDefault() {} });
    await new Promise((r) => setTimeout(r, 700));
    const last = [...document.querySelectorAll('#chat-messages > div')].pop()?.textContent || '';
    return { offlineReply, kept, last };
  });
  check('AI chat opens offline and says the question was not answered', /has not been answered/.test(chat.offlineReply), chat.offlineReply);
  check('the question is kept in the box', chat.kept === 'What should I reorder before the holidays?', chat.kept);
  check('local answers still work offline', /low-stock|low on stock|no low-stock/i.test(chat.last), chat.last.slice(0, 160));

  await page.evaluate(async () => { await fetchAIInsights(); });
  await pause(400);
  check('AI insights do not run offline and say so', /AI has not run/.test(await lastToast(page)), await lastToast(page));

  const barcode = await page.evaluate(async () => {
    openAddProductModal?.(); await new Promise((r) => setTimeout(r, 400));
    document.getElementById('barcode-input').value = '0036000291452';
    document.getElementById('p-name').value = '';
    await lookupBarcode();
    return { barcode: document.getElementById('barcode-input').value, name: document.getElementById('p-name').value, disabled: document.getElementById('p-name').disabled };
  });
  await pause(300);
  check('barcode offline: provider skipped, barcode kept, manual entry open', barcode.barcode === '0036000291452' && barcode.name === '' && !barcode.disabled, JSON.stringify(barcode));
  check('barcode offline: says it could not be looked up', /could not be looked up/.test(await lastToast(page)), await lastToast(page));
  check('nothing was requested from AI or the barcode provider', requested.length === 0, requested.join(','));
  await ctx.close();
}

(async () => {
  setProvider(200);
  for (const run of [scenarioEmail, scenarioMustWait]) {
    try { await run(); } catch (e) { check(`${run.name} completed`, false, e.stack); }
  }
  console.log(failures() ? `\n${failures()} FAILED` : '\nALL PASS');
  process.exit(failures() ? 1 : 0);
})();
