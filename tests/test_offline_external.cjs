// OFFLINE-EXTERNAL-001: static regression checks for external-provider
// workflows offline. The queued purchase-order email is exercised end to end
// by tests/run_offline_external_e2e.js and on the server by
// tests/test_offline_external_postgres.py.
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
const sw = read('frontend/sw.js');
const catalog = JSON.parse(read('i18n/launch_catalog.json'));

// ---- Purchase-order email (queueable) ----------------------------------------
const sender = between(main, 'def send_resend_email(', '\ndef ');
check('provider idempotency key is sent when given', /"Idempotency-Key": idempotency_key/.test(sender) && /idempotency_key: Optional\[str\] = None/.test(sender));
const helper = between(main, 'def email_purchase_order(', '\n# ---');
check('online and queued sends share one helper', /return email_purchase_order\(db, user, po_id\)/.test(main) && /g\["email_purchase_order"\]/.test(server));
check('the purchase order row is locked before sending', /PurchaseOrder\.business_id == user\.business_id\)\.with_for_update\(\)\.first\(\)/.test(helper));
check('SENT is written only after the provider call', helper.indexOf('send_resend_email(') > 0 && helper.indexOf('send_resend_email(') < helper.indexOf('po.status = "SENT"'));
const replay = between(server, 'def send_po_email(', '\n    @app.post');
check('queued send requires po.send', /"po_email_send": "po.send"/.test(server));
check('stale queued email is not sent automatically', /PO_EMAIL_MAX_AGE = timedelta\(hours=72\)/.test(server) && /STALE_ACTION/.test(replay));
check('changed draft or supplier is not sent', /PO_CHANGED/.test(replay) && /draft_sha256/.test(replay) && /supplier_id/.test(replay));
check('already-sent order is not sent again', /PO_ALREADY_SENT/.test(replay));
check('provider key is tied to the saved change', /idempotency_key=f"cauldra-offline-\{ref\}"/.test(replay));
check('provider outcomes are distinguished', /PROVIDER_UNAVAILABLE/.test(replay) && /PROVIDER_REJECTED/.test(replay) && /"PROVIDER_RETRY".*503/.test(replay));

const queue = between(app, 'async function queuePurchaseOrderEmailOffline(', '\n        function rememberSentPurchaseOrder');
check('queued email is sealed in the outbox with a draft fingerprint', /type: "po_email_send"/.test(queue) && /draft_sha256: await sha256Hex/.test(queue) && /addOutboxOp\(/.test(queue));
check('queue checks permission, status and duplicates first', /hasPermission\("po\.send"\)/.test(queue) && /po\.status !== "DRAFT"/.test(queue) && /pendingPoEmailIds\.has/.test(queue));
check('offline send never claims it was sent', /It has not been sent yet\./.test(queue) && !/was emailed/.test(queue));
check('"was emailed" only after the server confirms', /op\.type === "po_email_send"\) \{\s*rememberSentPurchaseOrder\(op\)/.test(app));
check('send button queues only in an offline workspace', /if \(offlineWorkspaceActive\(\)\) return queuePurchaseOrderEmailOffline\(po, supplier\);/.test(app));
check('email send is no longer blocked outright offline', !/"sendPurchaseOrder", "sendPO"/.test(app) && /"sendPurchaseOrderWhatsApp", "Invoice", "WhatsApp"/.test(app));
check('draft card says Waiting to send and hides send/edit/delete', /Waiting to send/.test(between(app, 'function renderPurchaseOrderDrafts(', 'function renderPurchaseOrderHistory')) && /const canSend = hasPermission\('po\.send'\) && !waitingToSend;/.test(app));
check('saved purchase orders are labelled with their saved time offline', /Using saved data from \$\{when\}\./.test(app) && /cache\?\.\["\/purchase-orders\/"\]\?\.verified_at/.test(app));
check('provider retries are capped, then Needs attention', /EXTERNAL_ACTION_MAX_ATTEMPTS = 8/.test(app) && /conflict_code: "PROVIDER_UNAVAILABLE"/.test(app));
check('a change cancelled just before sending is skipped', /Cancelled from Sync Details just before this pass reached it/.test(app));

// ---- Cancellation ------------------------------------------------------------
const cancel = between(offline, 'async function cancelOutbox(', '\n    }\n');
check('only external requests can be cancelled', /const CANCELLABLE_TYPES = new Set\(\["po_email_send"\]\)/.test(offline) && /CANCELLABLE_TYPES\.has\(row\.type\)/.test(cancel));
check('an in-flight send cannot be cancelled', /row\.status === "syncing"/.test(cancel));
check('cancel is one atomic read-and-delete', /transaction\("secure_outbox", "readwrite"/.test(cancel) && /store\.delete\(opId\)/.test(cancel));
check('an update never re-creates a cancelled change', /if \(await requestToPromise\(store\.get\(opId\)\)\) await requestToPromise\(store\.put\(row\)\)/.test(offline));
check('Sync Details says Waiting to send', /po_email_send: "Waiting to send"/.test(offline));
check('Sync Details explains provider outcomes', ['PO_ALREADY_SENT', 'PO_CHANGED', 'STALE_ACTION', 'PROVIDER_UNAVAILABLE', 'PROVIDER_REJECTED'].every(code => new RegExp(`${code}: "`).test(offline)));

// ---- AI / barcode / recovery (must wait, context kept) ------------------------
check('AI chat can open offline', !/"toggleAIChat"/.test(app));
const chat = between(app, 'async function sendAIChat(', 'function playSuccessBeep');
check('AI chat offline keeps the question and says it was not answered', /input\.value = query;/.test(chat) && /has not been answered/.test(chat)
  && chat.indexOf('externalServiceUnreachable()') < chat.indexOf('/ai/chat') && chat.indexOf('externalServiceUnreachable()') < chat.indexOf('if (!authToken)'));
check('AI chat local answers still come first', chat.indexOf('low stock') < chat.indexOf('externalServiceUnreachable()'));
check('AI insights and margin advice do not run offline', /async function runAIInsights\(\) \{\s*if \(aiUnavailableOffline\(\)\) return;/.test(app)
  && /async function runProductAiMarginAdvice\(\) \{\s*if \(aiUnavailableOffline\(\)\) return;/.test(app) && /async function runEditAiMarginAdvice\(\) \{\s*if \(aiUnavailableOffline\(\)\) return;/.test(app));
check('invoice scan says nothing was uploaded offline', (app.match(/Scanning an invoice needs the internet\. Nothing was uploaded or scanned\./g) || []).length === 2);
const barcode = between(app, 'async function lookupBarcode(', 'const clearProvisional');
check('barcode: own catalogue first, provider skipped offline', barcode.indexOf('ownMatch') < barcode.indexOf('externalServiceUnreachable()') && /the barcode is kept/.test(barcode));
check('a request that cannot reach Cauldra counts as offline (navigator.onLine is unreliable on Android)',
  /if \(err instanceof TypeError\) \{ answerOffline\(\); return; \}/.test(chat) && /const msg = err instanceof TypeError\s*\?\s*"No internet, so the barcode could not be looked up/.test(app));
check('recovery code / reset are never queued and say so offline', /function recoveryOfflineMessage\(/.test(app) && (app.match(/recoveryOfflineMessage\(err(, true)?\) \|\| friendlyErrorMessage/g) || []).length === 3);

// ---- Cache / translations ---------------------------------------------------
check('service-worker cache bumped', !/cauldra-shell-v1[0-6]-/.test(sw) && /const SHELL_CACHE = "cauldra-shell-v(1[7-9]|[2-9][0-9])-/.test(sw));
for (const text of ['Waiting to send', 'Send when back online?', 'This purchase order is already waiting to send.',
  'Waiting to send: this purchase order will be emailed when you reconnect. It has not been sent yet.',
  "This needs the internet. Cauldra's AI has not run, and nothing was sent.",
  "You're offline, so this question has not been answered. It is still in the box; send it again when you're back online.",
  'Scanning an invoice needs the internet. Nothing was uploaded or scanned.', 'Using saved data from {when}.',
  'Cancelled. It was not sent.', 'This purchase order was already sent, so it was not sent again.'])
  check(`translated in all launch languages: ${text.slice(0, 50)}`, Array.isArray(catalog[text]) && catalog[text].length === 4 && catalog[text].every(Boolean));

console.log(failures ? `${failures} FAILED` : 'ALL PASS');
process.exit(failures ? 1 : 0);
