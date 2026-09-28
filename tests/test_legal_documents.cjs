// LEGAL-001: the Terms of Service and Privacy Policy ship as final launch copy.
// Renders both documents from frontend/js/app.js and checks that no placeholder
// or draft notice comes back, that the operator, address, governing law and
// contact are present, that the billing wording still matches the renewal
// engine in backend/main.py, and that every screen where a customer agrees to a
// trial or subscription links to both documents.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${!ok && detail ? ' — ' + detail : ''}`); if (!ok) failures++; };

const app = read('frontend/js/app.js');
const html = read('frontend/index.html');
const main = read('backend/main.py');

// ---- Render both documents -------------------------------------------------
const start = app.indexOf('        // LEGAL-001 — final launch copy');
const end = app.indexOf('        function openThirdPartyNoticesModal()');
check('legal documents block found', start > 0 && end > start);
const opened = {};
const sandbox = {
  escapeHtml: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  openLegalDocModal: (title, body) => { opened[title] = body; },
};
vm.createContext(sandbox);
vm.runInContext(app.slice(start, end) + '\nopenTermsOfServiceModal(); openPrivacyPolicyModal();', sandbox);
const terms = opened['Terms of Service'] || '';
const privacy = opened['Privacy Policy'] || '';
check('Terms of Service renders', terms.length > 5000);
check('Privacy Policy renders', privacy.length > 5000);
const text = h => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const termsText = text(terms), privacyText = text(privacy);

for (const [name, doc] of [['Terms', termsText], ['Privacy', privacyText]]) {
  check(`${name}: no bracketed placeholder`, !/\[[^\]]*\]/.test(doc), (doc.match(/\[[^\]]*\]/) || [])[0]);
  check(`${name}: no placeholder wording`, !/TO BE CONFIRMED|TO BE REVIEWED|PLACEHOLDER|TBD|TODO|lorem ipsum/i.test(doc));
  check(`${name}: no draft notice`, !/\bDRAFT\b|legal review before public launch/i.test(doc));
  check(`${name}: operator is Cohren Limited`, /operated by Cohren Limited/.test(doc));
  check(`${name}: exact business address`, doc.includes('No. 21, Amadi Close, Oyigbo, Rivers State, Nigeria'));
  check(`${name}: contact address`, doc.includes('contact@cohren.com'));
  check(`${name}: effective date shown`, /Effective date: \d{1,2} [A-Z][a-z]+ \d{4}/.test(doc));
  const other = doc.match(/\b[A-Z][A-Za-z]+ (Limited|Ltd\.?|LLC|Inc\.?|PLC)\b/g) || [];
  check(`${name}: no other legal entity named`, other.every(e => e === 'Cohren Limited'), other.join(', '));
  const emails = doc.match(/[\w.+-]+@[\w-]+\.[\w.-]+/g) || [];
  check(`${name}: only the contact address is given`, emails.every(e => e === 'contact@cohren.com'), emails.join(', '));
}
check('Terms: governing law is Nigeria', /governed by the laws of the Federal Republic of Nigeria/.test(termsText));
check('Terms: disputes go to a Nigerian court', /court of competent jurisdiction in Nigeria/.test(termsText));
check('contact is a working mailto link', (terms + privacy).includes('href="mailto:contact@cohren.com"')
  && !/href="mailto:(?!contact@cohren\.com")/.test(terms + privacy));
check('Terms has all 23 sections', /23\. Contact/.test(termsText) && /1\. Who provides Cauldra/.test(termsText));
check('Privacy Policy has all 20 sections', /20\. Contact/.test(privacyText) && /1\. Who operates Cauldra/.test(privacyText));

// ---- No draft banner or placeholder anywhere in the shipped UI -------------------
check('draft banner helper is gone', !/legalDraftBanner|REQUIRES LEGAL REVIEW/.test(app) && !/REQUIRES LEGAL REVIEW/.test(html));
check('no legal placeholder anywhere in the app', !/\[(LEGAL ENTITY|REGISTERED ADDRESS|JURISDICTION|RETENTION|TRIAL LENGTH|CANCELLATION POLICY|REFUND POLICY|LIMITATION OF LIABILITY|GOVERNING LAW|MINIMUM ACCOUNT AGE|AUTO-CHARGE)[^\]]*\]/.test(app + html));

// ---- Billing wording matches the renewal engine --------------------------------
const trialDays = [...main.matchAll(/"trial_days": (\d+)/g)].map(m => Number(m[1]));
check('every plan has the trial length the Terms state (14 days)', trialDays.length >= 4 && trialDays.every(d => d === 14) && /current trial period is 14 calendar days/.test(termsText));
const interval = (main.match(/RENEWAL_RETRY_INTERVAL = timedelta\(hours=(\d+)\)/) || [])[1];
const windowDays = (main.match(/PAYSTACK_GRACE_PERIOD_DAYS = int\(os\.getenv\("PAYSTACK_GRACE_PERIOD_DAYS", "(\d+)"\)\)/) || [])[1];
check('retry interval and window match the Terms', /RENEWAL_RETRY_WINDOW = timedelta\(days=PAYSTACK_GRACE_PERIOD_DAYS\)/.test(main)
  && termsText.includes(`every ${interval} hours for up to ${windowDays} days`), `engine: ${interval}h / ${windowDays}d`);
const preStages = (main.match(/SUBSCRIPTION_EMAIL_PRE_STAGES = frozenset\(\{([^}]*)\}\)/) || [])[1] || '';
check('pre-expiry emails match the Terms (24 h and 5 h)', preStages.replace(/[\s"]/g, '').split(',').sort().join() === '24h,5h'
  && /approximately 24 hours before expiry/.test(termsText) && /approximately 5 hours before expiry/.test(termsText));
const stageEmails = main.slice(main.indexOf('def subscription_stage_emails('), main.indexOf('def subscription_reminder_email_mode('));
check('failed-first-attempt, final and renewed emails match the Terms', /"renewal_failed", "renewed"/.test(stageEmails) && /stage == "window_end"/.test(stageEmails)
  && /first failed automatic renewal attempt/.test(termsText) && /final email if the automatic retry window ends/.test(termsText) && /successful-renewal confirmation/.test(termsText));
check('early same-plan renewal is refused, as the Terms say', /"code": "RENEWAL_NOT_DUE"/.test(main) && /cannot manually purchase the next same-plan subscription period early/.test(termsText));
check('card verification is described as separate from the subscription charge', /card-verification transaction, that transaction is separate from the actual subscription charge/.test(termsText)
  && /This is not your subscription payment/.test(html));
check('trial screens state the same 14 days', /Start 14-Day Free Trial/.test(html) && /after the 14-day trial/.test(html));
check('providers named in the Privacy Policy', ['Supabase', 'Paystack', 'Sentry', 'OpenAI', 'Google Gemini'].every(p => privacyText.includes(p)));

// ---- Agreement points link to both documents -------------------------------------
const agreements = html.match(/<p class="legal-agreement[\s\S]*?<\/p>/g) || [];
check('four agreement points link both documents', agreements.length === 4 && agreements.every(a => /openTermsOfServiceModal\(\)/.test(a) && /openPrivacyPolicyModal\(\)/.test(a) && /type="button"/.test(a)));
const near = (anchor) => { const i = html.indexOf(anchor); return i > 0 && html.slice(i, i + 2500).includes('legal-agreement'); };
check('new business: before Continue to Paystack', near('id="ev-continue-paystack-btn"'));
check('new business: at Create Business & Admin Account', near('Create Business & Admin Account</button>'));
check('Billing: at Start 14-Day Free Trial', near('id="trial-consent-checkbox"'));
check('Billing: at plan purchase / change', near('id="plan-change-confirm-impact"'));
check('agreement sentence translated', (() => { const c = JSON.parse(read('i18n/launch_catalog.json'))['By continuing, you agree to these documents:']; return Array.isArray(c) && c.length === 4 && c.every(Boolean); })());
check('closing a document returns to About only when opened from About', /legalDocReturnsToAbout = !!about && !about\.classList\.contains\("hidden"\)/.test(app)
  && /if \(legalDocReturnsToAbout\) document\.getElementById\("about-modal"\)/.test(app));
check('legal modal sits above the sign-up and billing dialogs', /id="legal-doc-modal" class="[^"]*z-\[151\]/.test(html));

console.log(failures ? `${failures} FAILED` : 'ALL PASS');
process.exit(failures ? 1 : 0);
