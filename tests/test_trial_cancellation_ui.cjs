// SUB-001 — the trial-cancel dialog must tell the customer what happens to ACCESS,
// and the billing panel must reflect a cancelled trial.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/js/app.js'), 'utf8');

// --- the dialog says access continues, and until when
const dialog = appSource.match(/Your trial will not convert into a paid subscription[^`"]*/);
assert.ok(dialog, 'the trial dialog states that the trial will not convert');
assert.match(dialog[0], /keep access until \$\{trialUntil\}/, 'it names the date access runs to');
assert.match(dialog[0], /business data will be kept/, 'it keeps the data-retention promise');
assert.ok(/const trialUntil = billingUsageCache\?\.trial_ends_at/.test(appSource),
  'the date comes from the server payload, not a guess');

// the old copy, which mentioned only money and data, must be gone
assert.ok(!/Cancelling now will prevent your trial from converting into a paid subscription\. You won't be charged, and your business data will be kept\./.test(appSource),
  'the old access-silent copy is gone');

// --- the paid dialog is untouched
assert.ok(/Your access continues until \$\{until\}\. No further payment will be charged after that\./.test(appSource),
  'the paid-cancellation copy is unchanged');

// --- the Cancel Trial button disappears once the trial is already cancelled
assert.ok(/isAdmin && usage\.status === 'trialing' && !usage\.cancel_at_period_end/.test(appSource),
  'a trial already set to end cannot be cancelled twice from the panel');

// --- the trial dates line says the trial will not convert
assert.ok(/cancelled — will not convert to a paid plan/.test(appSource),
  'the billing panel marks a cancelled trial');

// --- nothing here grants access: the gate stays server-side
assert.ok(!/status === 'cancelled'\s*\?\s*true/.test(appSource), 'no client-side entitlement shortcut');

console.log('TRIAL_CANCELLATION_UI_PASS dialog_states_access date_from_server panel_marks_cancelled paid_copy_unchanged');
