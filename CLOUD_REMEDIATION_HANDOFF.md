# Cauldra — Cloud Remediation Handoff

**Checkpoint:** 2026-09-26, after the consolidated Android A–E pass.
**Purpose:** let a fresh Claude cloud session continue launch remediation from the current state without the old local chat history, without re-auditing the product and without guessing past owner decisions.
**This is not a second remediation track.** From this handoff on, the cloud session is the primary coding session. The local session stops.

Everything below was checked against the permanent remediation records (see §11) on 2026-09-26. Where the records disagree or a fact is unknown, this file says so. **It contains no secrets, credentials, tokens, passwords, sign-in identifiers of test accounts or private URLs.**

---

## 0. Read this first

1. Read this whole file.
2. Then read `PROJECT_STRUCTURE.md`, `DEPLOYMENT.md`, `DATABASE_MIGRATION.md` and `MOBILE_PACKAGING.md` in the repository root.
3. Do **not** start the next batch until the owner confirms its scope (§13).
4. When in doubt, stop and ask the owner. Do not infer permission from this file.

---

## 1. Starting state

| Item | Value |
|---|---|
| **Canonical remediation branch** | `remediation/batch-e-ai-forecast-brain` |
| **Canonical source commit** | `2aeec3fd8b120f0e0bf4a587657cc15a4fc46152` (`2aeec3f`), pushed to `origin` |
| **Handoff commit** | the commit that adds this file (documentation only), directly on top of `2aeec3f` |
| **Line of work** | one stacked line: … → Batch A → Batch B → Batch C → QA-STORAGE-001 → Batch D → Batch E. Each batch branch is on `origin` (§3) |
| **QA service** | Railway service `cauldra-qa`, public address `https://cauldra-qa.up.railway.app` |
| **QA deployment (current)** | `74db9267` — built from the working tree at `2aeec3f` with `railway up --service cauldra-qa` |
| **QA database** | Alembic head **`0042_business_brain_forecast_recompute`** |
| **Latest QA APK** | built from `2aeec3f`, QA target, SHA-256 `92a5fa505ff155b6e2d96feb8034ba8a0d78bea0d8aea51529d3a1350616ca0c`. **Stored on the owner's machine only; not in git** |
| **`main`** | `origin/main` @ `4195d98`. **Untouched by remediation.** It holds two owner edits to `frontend/index.html` from 2026-09-22 (`a8a35a7`, `4195d98`) that are **not** on the remediation line; promotion must bring them in |
| **Production** | Railway service `supply-ai-demo`, deploys from `main`, canonical address `https://cauldra.cohren.com`. **No remediation product fix is in production.** Only the PRODURL-001 / BUILD-001 web infrastructure is live. The last production deployment the records name is `228a2aed` (commit `ad83951`); later automatic deploys from `main` are not recorded. **Production's migration level has never been read — UNKNOWN** |

**Note:** the rollout manifest says QA "deploys from branch `claude-e2e-audit`". That branch is stale (`2c28101`); since Batch A, QA is deployed from the working tree with `railway up`.

---

## 2. Hard rules (non-negotiable)

1. **Never touch `main` or production** without explicit owner authorization for that exact action. This covers merging, pushing to `main`, Railway production variables or deploys, the production database, production Supabase, Paystack LIVE and production APK builds.
2. **QA only.** Committing and pushing to remediation branches, and QA deploys, are authorized per batch by the owner. Ask when not stated.
3. **Never print, commit or record** passwords, tokens, one-time codes, service-role keys, API keys, connection strings or the Ops private path. Environment variables are recorded by **name only**.
4. **`PRODUCTION_ROLLOUT_MANIFEST.md` is the authoritative record** of every code, config, dashboard and migration change needed for eventual promotion. Every remediation must update it (§10).
5. **Never move real money. Never use Paystack LIVE.** Paystack TEST only.
6. **Do not reopen FIXED + VERIFIED findings without evidence.**
7. **Do not reconstruct the missing frozen audit records** (§11.3).
8. **QA Tenant B is naturally expired.** Do not reactivate or manipulate it for testing. Do not cancel QA Tenant A. Use disposable data, and restore anything you change.
9. **Nothing is FIXED + VERIFIED** because related code changed. It needs QA evidence.
10. **Change control:** no merge, rebase of shared branches, PR to `main`, production change or APK publication without explicit per-action approval.

---

## 3. Completed remediation

Commits are on the canonical line, oldest first. "QA" = verified on the QA service; the channel is named where verification was partial.

### Earlier tickets (before the launch triage, 2026-09-17 → 09-20)

| Ticket | Commits | Verified behaviour (QA) |
|---|---|---|
| PLAN-009 | `deb59c1` | AI plan gate no longer fails open for roles that can't load billing (Web + Android) |
| BUILD-001 / PRODURL-001 parity | `be95b63`, `8a26b04`, `9768c3b` | Fail-closed build targets (`web` / `qa` / `production`); the APK verifier. The production address is `https://cauldra.cohren.com` |
| PERM-001 Phase A | `f9c871c`, `4c2bbc6` + test commits | Declared permissions enforced on the audited read paths (Web + Android + offline) |
| MIGR-001 | `38738e3` | Migrations 0037–0040 idempotent; an empty database provisions |
| AUTH-RECOVERY-001 | `4f58a61` | Forgot-password email delivered; requires `RESEND_FROM` (Web + Android) |
| CB-001 | `aaa38f4`, `820e846`, `4711773`, `6ff1cdf` | Sign-up verification link valid for Cauldra's full 20 minutes (Web + Android timing). The **Android Chrome → app return** is not yet observed |
| ERR-002 | `b7eaf9a` | A malformed body returns 422, never an unhandled 500 |
| REFUND-002/003, REFUND-001 | `4001f30`, `d07a4b4` | Refunds in Profit and the export; presented as their own line (Web) |
| SUB-001 | `d398afa` | Cancelling a trial no longer locks the business immediately (Web) |
| B9: AI-004, PM-004, OCR-001 | `fed383d`, `c278e3b`, `c05b8da`, `612e2f1`, `8f5ac7c`, `fa48bd1`, `3ece631`, `3117619` | Margin Advisor works; price-list CSV works; OCR failure handling correct. **OCR success is unverified: the QA OpenAI account has no credit** |
| BARCODE-HARDENING-001 | `733294e`, `f859977`, `2c28101`, `82d63ff` | Editable barcode; a per-business share of the free lookup quota; provider cooldown; copy in 35 locales. **A live provider hit is unverified** (shared QA egress quota) |
| Infra `.railwayignore` | `a33999c` | Source upload cut from 85.8 MB to 8.3 MB |

### Launch-triage batches (2026-09-24 → 09-26)

| Batch | Commits | Migration | QA deploy | Verified behaviour |
|---|---|---|---|---|
| **A** — Android/offline unblockers | `7431299`, `ac2abbf` | none | `301ff48e` | See the Batch A detail below |
| **B** — security and backend correctness (triage 10–21) | `c0174e3`, `c6e7423` | none | `cb2f222e` | See the Batch B detail below |
| **C** — billing, subscriptions, staff (triage 3–5, 25–36) + NATIVE-PAY-001 | `8c1d3f6`, `56aef95` | none | `99edc23a` | See the Batch C detail below |
| **QA-STORAGE-001** — durable uploads | `70cf6b8` | none | `504cfcf8` → redeploy `55a3171a` | Uploads go to the **private** Supabase bucket through `backend/storage.py`. Bytes, rows and authorization survived a redeploy. Deployed environments refuse local storage or a public bucket |
| **D** — inventory, purchasing, supplier, Price Monitor (triage 6, 41–50) | `fda211c` | **0041** `general_catalog_category_retired` (relaxes NOT NULL; applied before the code) | `9d6249f0` | See the Batch D detail below |
| **E** — AI, forecasting, Business Brain (triage 7, 51–60) | `581f0ba`, `2aeec3f` | **0042** `business_brain_forecast_recompute` (data-only flag; idempotent) | `6c148863` → **`74db9267`** | See the Batch E detail below |

**Batch A.**
- **Fixed and verified on Web and Android:**
  - NAT-002: the slow-start "Internet required" trap. "Can't reach Cauldra right now" plus a real-server-check "Try again"; the app self-recovers after a timeout.
  - AND-003: Android Back closes ordinary popups, while protected flows (the mandatory gate, forced password change, one-time credential, payment) are kept.
  - AND-001: no error on launch.
- **Fixed, Web verified:**
  - UX-007: offline opt-in "Not now" = 24 h snooze, once per session, never over another popup.
  - NAT-001: ₦ fallback font, Web pixel-identical.
- **Investigated, not fixed:** AND-002, which is inside Capacitor `SystemBars` (see §5.4).
- Also found and fixed: **NATIVE-PLUGIN-001**, native plugin access without `registerPlugin`.

**Batch B.** All **FIXED + VERIFIED IN QA** unless noted:
- SEC-001: rate limit on unknown Business IDs.
- SEC-002: debug prints removed.
- SEC-003 + AUTH-OBS-1: Forgot Password answers identically for every combination.
- SEC-004: `/health/database` is liveness only.
- SEC-005: self-disable needs the password.
- X1: card details and payment history are Admin-only.
- X2: `inventory.view` enforced.
- X3: `/uploads` scoped.
- OBS-7: Ops console served only to a platform-owner session.
- OBS-13: presence over-sharing removed.
- SEC-NOTIFY-001: security email after a password change — dispatch verified, **real-inbox arrival pending**.
- SEC-NOTIFY-002: notice to the old address after an email change — tests only, **needs a second inbox**.

**Batch C.**
- **Verified:**
  - PLAN-005: downgrades keep data and block new growth.
  - PLAN-007: Price Monitor actions plan-gated server-side, records still readable.
  - PLAN-008: AI usage priced by the plan in force at the time.
  - PLAN-001, PLAN-002.
  - UX-001: Manager billing is read-only with no dead controls.
  - UX-005/006: plan-change confirmation names the plan and price; buttons say what they do.
  - Audit OBS-1: `aria-pressed`.
  - X4: the refund entry follows `sales.refund`.
  - COPY-002, ACCT-001.
  - `supplier.edit`: `PATCH /suppliers/{id}`.
- **Fixed, awaiting a runtime check:**
  - PAY-001 and NATIVE-PAY-001 (Paystack TEST);
  - SUB-002/003/004 and new SUB-005 (cancelled business screens; the "will not renew" webhook);
  - PRESET-001/002 (apply-and-save in the UI).

**Batch D.**
- **Verified:**
  - DATA-001/002: negative product values rejected; negative stock = Out of Stock.
  - PM-001: supplier price must be > 0.
  - PM-002: Remove = deactivate with history; one active count.
  - PM-003: duplicate source → 409.
  - PROC-001: reorder = 2 × min − on hand, min 1.
  - SUP-001: supplier email validated.
  - COPY-001: "1 unit".
  - GC-008: 50cl = 500ml = 0.5L.
  - GC-F1: retired field no longer written.
  - OCR-UI-001: picker JPEG/PNG/WebP only (Web).
- **RESP-001:** not reproducible on Web, and no code change was made. **It reproduces on Android** (§6).

**Batch E.**
- **Verified:**
  - BRAIN-001: forecast = rate × the business days actually in 7 calendar days, measured per weekday; open-day sales excluded; no correction factor.
  - UX-008: whole units.
  - UX-009: confidence needs measured accuracy.
  - BRAIN-003: truthful `recommendation_state`.
  - UX-010/011: plain labels; invalid inputs excluded; a materiality floor of 1% of stock cost.
  - AI-002: ₦ amounts.
  - AI-003: a scoped server-built sales summary, only with `reports.sales`.
  - X6: `ai.use` enforced on `/business-brain` and `/business-brain/history`. Business Brain has no plan gate.
- **Verified on Web:**
  - AI-001: an escape-first Markdown renderer; hostile text inert.
  - UX-013: AI Center cards are real buttons; focus enters and returns; one insights request at a time.
  - UX-002: one name, "Inventory Financial Analysis".
- **Verified in English:** UX-012, business wording. The other 34 languages still translate the old loading strings; that belongs to the translation batch.

---

## 4. Migration state

- Alembic chain `0001`…**`0042`** in `alembic/versions/`. Head: **`0042_business_brain_forecast_recompute`**.
- **QA:** at `0042`.
- **Production:** **UNKNOWN**; it has never been read. Two older records claim "production at 0040"; that claim is **unevidenced**. Read it only with owner authorization before any promotion.
- **Promotion order** (from the manifest):
  1. `0041` **before** the Batch D code;
  2. the code;
  3. `0042` **right after** the Batch E code is live (it is safe either side).
- `0037`–`0040` are idempotent (MIGR-001).
- Migrations are written MIGR-001-style: idempotent, and guarded against objects that already exist.

---

## 5. Current triage state

**Source:** `LAUNCH_TRIAGE_2026-09-24.md`, which groups the audit's 74 confirmed defects plus post-audit findings into 80 customer-behaviour rows. It was updated after every batch.

**Count now: "Still needs a fix" = 32 rows.** The count ran 80 → 68 (Batch B) → 53 (Batch C) → 42 (Batch D) → 31 (Batch E) → **32**, because row 41 (RESP-001) returned as Android-only after the Android pass.

**Caveat on the 32:**
- The triage deliberately did **not** subtract the Batch A rows 2, 61, 62, 63, 64 and 66. Their true state is in §5.1, §5.2 and §5.4.
- Rows 1, 72, 78, 79 and 80 are also not pure code fixes.
- A row is a customer behaviour, and several finding IDs can share one row.

### 5.1 FIXED + VERIFIED (QA)

Everything marked verified in §3, including:
- the Batch A rows **2 (NAT-002), 61 (AND-003) and 63 (AND-001)**. They are still inside the triage's 32 only because Batch A was not recounted;
- the pre-triage "already finished" list: PERM-001, PLAN-009, AUTH-RECOVERY-001, ERR-002, REFUND-001/002/003, SUB-001, AI-004, PM-004, BARCODE-HARDENING-001 (except the live hit), MIGR-001, ENV-001, ENV-OFFLINE-001 and BUILD-001/PRODURL-001.

### 5.2 Fixed, still needing final Android or manual verification

These are **not** code work unless the check fails.

**Batch A**
- UX-007 (row 66): Android check with a signed-in session.
- NAT-001 (row 62): one check on a device with an **older** Android System WebView.

**CB-001**
- A successful **Android Chrome → `cauldra://` → app** return, with **one** new disposable QA email alias.
- No further expiry or timing test.

**Batch B (owner actions)**
- **#12 (SEC-003):** real-inbox arrival after the change to background delivery.
- **#20 (SEC-NOTIFY-001):** notice arrives in a real inbox.
- **#21 (SEC-NOTIFY-002):** needs a second inbox the owner controls.
- **#18 (OBS-7):** the owner signs in to the Ops console with MFA on QA.
- **#15 (X1):** Manager billing on Android shows no card last-4, expiry or history.

These owner checks are **not launch blockers** but stay on the owner list.

**Batch C**
- **#25 PAY-001 + NATIVE-PAY-001:** open a Paystack **TEST** checkout from the Android app, cancel, confirm the pending state is dismissable and doesn't return; optionally one TEST card payment.
- **#28 SUB-002/003/004/005:** cancelled-business screens on Web + Android. They need a **disposable** cancelled business: not Tenant A, and not a reactivated Tenant B. **Not done — a recorded gap.**
- **#32 PRESET-001/002:** apply and save one disposable preset in the UI.
- **#36:** the supplier Edit dialog, with a permitted role.
- **#33:** refund visibility follows the permission, on Android.
- Billing usable at phone and tablet sizes on Android.

**Batch D Android UI**
- PM-002 Remove and active count;
- the invoice picker restricted to JPEG/PNG/WebP;
- `min="0"` inputs and negative-value errors;
- Out of Stock bucket;
- "1 unit / 1 SKU / 1 item".

**Batch E Android UI**
- Markdown rendering, hostile text escaped;
- AI Center keyboard and focus;
- a single Generate Insights request;
- "Inventory Financial Analysis" naming;
- Brain wording, confidence, whole units and loading text.

**Older Web-only fixes never checked on Android**
- ERR-002, REFUND ×3, SUB-001, AI-004, PM-004, the OCR failure message, barcode, translations.

**Manual or external checks**
- **Invoice OCR success:** blocked by the QA OpenAI account having no credit. Do not debug it.
- **Barcode live lookup:** needs QA egress with free quota.
- **Purchase-order email arrival:** needs a supplier inbox the owner can read.
- **Real-phone system bars:** a final check on a real device. Do not replace Capacitor inset handling.

### 5.3 Still needing a code fix

Rows are from the triage (§3 of that file); "Where" says which surfaces are affected. **These are the remaining launch-scope code findings.**

| Row | Finding | Customer behaviour | Where |
|---|---|---|---|
| 41 | **RESP-001** (Android) | At 800 px with the sidebar shown, Add Product is clipped to "Add Pro" (§6) | Web layout / Android tablet |
| 8 | ERR-001 | Error messages sometimes show raw JavaScript parser text (`Unexpected token 'I'…`); the frontend parses non-JSON error bodies as JSON | Web/Android |
| 9 | ERR-003 | Server failures reach Android without CORS/security headers, so Android says "check your connection". The error middleware sits outside the CORS/security middleware | Backend |
| 22 | SESS-001, UX-004, PLAN-003 | When a session expires, the screen still looks signed in and destructive actions fail silently; then the app silently becomes the guest app; the billing screen's Retry is unusable | Web/Android |
| 23 | UI-001 | Right after sign-in the sidebar briefly says "Guest Hub" (role badge race) | Web/Android |
| 24 | PLAN-006 | After a successful password change the dialog stays open and shows an error | Web/Android |
| 37 | AUD-001 | Two false "profile updated" activity entries every time the app loads | Backend+Web |
| 38 | AUDIT-UI-001 | "Clear filters" leaves Location set, so an export after clearing held 13 of 501 records | Web/Android |
| 39 | SALE-001 | The sale confirmation labels one sale's amount "Today's total" | Web/Android |
| 40 | EXP-001 | Guest CSV/Excel export buttons do nothing | Web/Android |
| 65 | UX-014 | Opening a module from the menu stacks panels instead of replacing the current one | Web/Android |
| 67 | UX-015 | One endpoint gives a poor "permission denied" message | Web/Android |
| 68 | audit OBS-6 | Close buttons have small tap areas | Web/Android |
| 69 | audit OBS-2 | Error messages presented inconsistently | Web/Android |
| 70 | audit OBS-5 | Onboarding copy issues | Web/Android |
| 71 | audit OBS-8 | Rate-limit messages don't say when to retry | Web/Android |
| 72 | I18N-001 | 35 languages offered, most of the dashboard untranslated. **Owner decision made** (§8): 5 launch languages, hide the rest | Web/Android |
| 73 | audit OBS-11 | Production styling uses Tailwind's development "play" CDN (in-browser CSS compile); needs a build step | Web/Android |
| 74 | audit OBS-10 | The service worker precaches files twice | Web/Android |
| 75 | audit OBS-12 | Build-stamp messages print in the console | Web/Android |
| 76 | URL-OPS-001 | The private Ops route still uses the legacy production origin (the private path itself is never recorded) | Backend / config |
| 77 | REC-001 | If the database falls behind the code, the app fails with confusing errors; it should fail with a clear operator error | Backend |

**Also inside the 32, but not simple code fixes:**
- **row 1**, LEGAL-001: external (§5.5);
- **row 64**, AND-002: decision (§5.4);
- **row 78**, OFFLINE-QUEUE-001: investigate first;
- **row 79**, QA-AUTH-EMAIL-001: configuration;
- **row 80**, X5: decision;
- the Batch A rows 2, 61, 62, 63 and 66 (§5.1, §5.2).

**App-wide UI/UX polishing is launch scope** (§8). It is a sweep that sits on top of rows 65–71, not a replacement for them.

### 5.4 Investigation and owner-decision items (not confirmed bugs)

- **AND-002, row 64:** Capacitor 8.5.1 `SystemBars` logs a caught pre-load error, and on Android 15 content does not sit under the bars. Accept it, or replace the inset handling. The owner has said **not** to replace Capacitor's handling now. Recheck on a real notch or gesture-nav phone.
- **OFFLINE-QUEUE-001, row 78:** a refused offline change stores a generic reason. Investigate before fixing.
- **OFFLINE-UI-001:** does the Android WebView's "online" signal stay on while offline, so the banner never shows? Uninvestigated.
- **SUSP-001 / ANOM-006:** `/auth/refresh` returns 204, but the session isn't restored and the app falls back to Guest. The cause is unknown; investigate with row 22.
- **SUSP-002/003:** does the Brain score its own forecasts, and why are there no product relationships? Suspected only.
- **Audit OBS-4:** cold start on real phones. Unconfirmed; the emulator showed 9.8 s against the old 6 s limit.
- **PROC-002:** goods receipt against purchase orders. A capability gap and a product decision.
- **Audit OBS-9:** deep links into specific screens. A decision.
- **X5, row 80:** should ignored privileged sale values give a visible signal? A decision.
- **Business Brain recommendation scope** beyond stock, demand and seasonal checks. New product scope; the owner decides.
- **Batch E tunable rules:**
  - the materiality floor for "recoverable" recommendations, 1% of stock cost;
  - confidence thresholds: High needs ≥3 checked forecasts at about ⅔ accuracy.
  - The owner **keeps both for launch** as tunable rules.
- **The 42 historical QA upload rows** whose bytes were lost before QA-STORAGE-001: unrecoverable; an owner decision. **Do not touch them.**
- **Storage backup retention:** `scripts/backup_storage.py` never deletes from R2. An owner decision.

### 5.5 External or manual items

- **LEGAL-001, row 1:** FIXED + VERIFIED IN QA (Web) — final copy `7b422c6`, list fix `7763a26`, QA web legal smoke 64/64. The Galaxy A23 native legal smoke on the frozen APK is open and non-blocking for the RC freeze (owner decision; §14.8). Professional legal review may improve the copy later.
- **Paystack B10:** success, declined, 3-D Secure, cancel and duplicate were never exercised. Needs a person, using **TEST** cards, about 45–60 minutes.
- **QA OpenAI credit:** blocks invoice OCR success. The owner adds credit; do not debug it.
- **Barcode live lookup:** QA's Railway egress IP has spent the free UPCitemdb quota.
- **Production barcode quota check:** the owner runs one lookup from the production shell.
- **Owner inbox checks** #12, #20, #21 and the Ops MFA sign-in #18 (§5.2).
- **QA-AUTH-EMAIL-001, row 79:** QA sign-up emails come from Supabase's default mailer, capped at about 2 per hour. Custom SMTP is a configuration item.
- **Real-phone checks:** system bars, and an older WebView for NAT-001.
- **Storage backup scheduling** for `scripts/backup_storage.py`.

### 5.6 Production-rollout-only actions

These are recorded in `PRODUCTION_ROLLOUT_MANIFEST.md` and done only with owner authorization, after scope freeze. Summary in §10.

---

## 6. RESP-001 — evidence from the Android pass and the recommended fix

**Record status:** OPEN (Android). Row 41 is back in "Still needs a fix". It is **not** an A–E regression: Batch D found it not reproducible on Web and made no code change.

**Evidence** (QA APK from `2aeec3f`, emulator `Tablet_QA` at 1600×2560 px, density 320 → **800×1224 CSS px**, DPR 2, WebView Chrome 152; Guest dashboard, 2026-09-26):
- The screenshot shows the Stock Inventory card with the button cut at the right edge, reading **"+ Add Pro"**. The sidebar is visible at 800 px.
- Measured in the live WebView:
  - `Add Product`: left 704, **right 815**, width 111; viewport 800;
  - clipped by `#inventory-section` (`overflow-x: hidden`), whose right edge is **776**, `clientWidth` **530**, `scrollWidth` **570**;
  - the button centre is still hit-testable (tappable); page overflow is 0.
- Layout cause:
  - `.inventory-header-stack` is `display: grid` with columns **`86.97px 445.41px`**, from `1fr auto` in the 768–1100 px rule;
  - the `auto` column is `.inventory-filter-row` (`display: flex`, **`flex-wrap: nowrap`**) at its minimum content width: `#warehouse-filter` **`min-width: 220px`** + the Export menu **98 px** (`shrink-0`) + Add Product **111 px** (`shrink-0`) + gaps = **445 px**;
  - the title column keeps about 87 px, so 87 + 445 + gap > 530, and the row overflows the card by about 40 px.
- **Web at 800 px passed in Batch D** (button right edge 775 of 800, signed in as Admin and as Staff). Why the web layout fits and the Android tablet layout doesn't was **not measured**. The likely difference is the card width available beside the sidebar; the Android tablet shows the sidebar at 800 px. Confirm this while fixing.
- 768 and 819 px were not measured on the device, which is fixed at 800 CSS px.
- The raw measurements and screenshots are on the owner's machine (§11).

**Recommended smallest fix (Batch F; not implemented):**
1. In the 768–1100 px range, let `.inventory-filter-row` wrap (`flex-wrap: wrap`, the select taking the full row when needed), **or** lower the `#warehouse-filter` minimum below 220 px. The target is the filter row's minimum content width fitting the card's content box with the sidebar visible.
2. Keep the Batch D behaviour at 767/768/800/819/820 and the four standard layouts.
3. Add a width test for the **sidebar-visible** case. Reproduce it in a headless browser with the sidebar forced visible (or an 800 px viewport with the tablet layout), asserting the Add Product right edge ≤ the `#inventory-section` right edge and no clipping ancestor. The Batch D script measured exactly these values.
4. Verify on Web at the four layouts plus 768/800/819, then on Android in the final release-candidate pass (§7).

---

## 7. Android status and plan

**Consolidated A–E pass (2026-09-26), from `REMEDIATION_ANDROID-CONSOLIDATED-A-E_REPORT.md`:**
- **PASS:**
  - APK built from `2aeec3f`, QA target;
  - static verification: packaged `build-target.js` = QA; no packaged file forces production; bundle byte-identical to the repository; Batch A/C/D/E and Paystack markers present;
  - installed APK hash matches;
  - on device, the runtime target is QA;
  - **0 production requests** (a fail-closed proxy blocked the production hosts);
  - NAT-002 startup gate on Android: truthful wording, Back leaves the gate open, Try again recovers to the online flow.
- **FAIL:** RESP-001 at 800 px (§6).
- **NOT RUN:**
  - CB-001 Chrome return;
  - Batch B Manager billing;
  - Paystack TEST open, cancel and clear;
  - Batch C staff items (#32, #36, #33);
  - Batch D and E UI;
  - offline snooze and Back on popups while signed in;
  - four layouts.
- **Why not run:** the host machine's emulator was starved (guest load 17–41, launcher ANRs; QA `/health` 5.7–10.7 s from the guest against 0.7–1.1 s from the host). Also, **signing in to QA on the device is an owner action**.
- **We are intentionally NOT rerunning the overloaded emulator now.**

**Plan:**
1. **Accumulate all remaining frontend changes** (Batch F onward, including RESP-001 and the UI/UX sweep) on the remediation line.
2. Verify each batch on **Web** at the four layouts: 375×812, 768×1024, 1024×768, 1366×768+. Add 768/800/819 for layout work.
3. Build **one final release-candidate QA APK** when frontend scope is complete (`build-apk.bat qa`, QA target only; never a production APK without authorization).
4. Run **one final Android pass** on a responsive device: a physical phone or tablet, or an emulator on a host with enough free RAM. The owner signs in on the device. Cover every §5.2 Android item, plus RESP-001 and a smoke pass.
5. Fix only genuine regressions from that pass (smallest fix + test), then rebuild **once**. No repeated build loops for cosmetic issues.

**A proven approach for the final pass** (debug APKs only):
- `adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>`;
- drive the packaged WebView with raw Chrome DevTools Protocol (`Runtime.evaluate` over WebSocket);
- use `adb exec-out screencap` for screenshots;
- run a fail-closed proxy that blocks the production hosts, to prove "no production requests";
- prove offline state with airplane mode plus `dumpsys connectivity`, **not** `navigator.onLine`, which is unreliable in this WebView.

---

## 8. Owner scope decisions for the remaining remediation

Recorded decisions: some from the records (with dates), and the rest stated by the owner in the 2026-09-26 handoff instruction.

**Languages (2026-09-24)**
- Launch languages: **English, French, Arabic, Spanish, Portuguese**.
- **Hide** the other, incomplete languages from the selector, but **preserve** their files and infrastructure.
- For the five launch languages:
  - full customer-facing translation;
  - no raw keys;
  - no unintended English fallback where practical;
  - one standard for Web and Native.
- **Arabic needs RTL layout verification.**

**UI/UX polishing**
- UI/UX polishing is **launch scope**. It is not automatically post-launch.
- It must be an **app-wide sweep of every Cauldra-controlled surface**, not only the named examples.
- Examples:
  - connectivity Retry;
  - Enable Offline Access;
  - email-verification callback and return;
  - the Cauldra-controlled payment transition before Paystack;
  - dialogs and overlays;
  - loading, error, empty and success states;
  - auth transitions;
  - responsive layouts;
  - overflow and clipping;
  - touch targets;
  - focus and keyboard states;
  - phone, tablet and desktop breakpoints;
  - a consistent Cauldra logo, colours and design language.
- **Do not attempt to brand third-party UI** (Paystack's page, Chrome custom tabs) **or Android system UI**.

**Plans and billing**
- **Downgrade** preserves existing data and blocks new creation beyond the lower plan's limits. Nothing is deleted, disabled or hidden. (Batch C, 2026-09-24.)
- **Business Brain remains available on Starter**, per the current product specification: deterministic and non-AI. `ai.use` is enforced as a permission, not as a plan gate. (Batch C/E.)
- **Price Monitor** existing data stays **readable** after a downgrade, while paid actions stay **server-blocked**. (Batch C.)

**Architecture**
- **No multi-business implementation** during launch remediation. Today one account belongs to exactly one business.
- **No Warehouse or Stock Area architecture redesign** during launch remediation.

**Barcode**
- **Free barcode lookup may launch if reliable.** Paid provider expansion is later.

**Batch E rules**
- Keep the 1% materiality floor and the confidence thresholds for launch, as tunable rules.

**QA tenants**
- QA Tenant B stays naturally expired. Do not reactivate it merely for testing.

**Scope discipline**
- **Do not silently defer findings based on severity.** The owner decides deferrals.
- **Do not broaden the finish line with another general audit.**
- Only genuine **regression, security or data-loss discoveries**, or explicit owner scope changes, may add launch work. Anything else is recorded, not fixed.

---

## 9. QA infrastructure and configuration (names and non-secret identifiers only)

**Railway**
- QA service `cauldra-qa`: https://cauldra-qa.up.railway.app
- Production service `supply-ai-demo`: https://cauldra.cohren.com. Its Railway service URL `https://cauldra.up.railway.app` is infrastructure only and never a build target.
- Always pass `--service cauldra-qa`.
- `railway up` source uploads sometimes time out. It is a known infrastructure pattern; retry once.

**Database**
- QA uses its own Supabase Postgres project; production uses a separate one.

**QA tenants**
- **Tenant A:** Premium, active; Admin, Manager and Staff audit accounts. Use it for disposable data, and clean up afterwards.
- **Tenant B:** Premium trial, cancelled; paid period ended 2026-09-25 12:00 UTC, so **every request returns 402, even GETs**. Its data can be read read-only through the database, never by reactivating it.
- Sign-in identifiers and passwords are **not** in git. They live in the owner's local secrets file (git-excluded). A cloud session must ask the owner for any test sign-in it needs, and must not print it.

**QA variables set by the owner or earlier sessions (names only)**
- `RESEND_FROM`
- `CAULDRA_OFFLINE_SIGNING_KEY` (QA-only key; keys differ per environment)
- `SUPABASE_STORAGE_BUCKET` (the private bucket `cauldra-private`)
- `SUPPLY_AI_STORAGE_BACKEND=supabase`
- `SUPABASE_URL`, `SUPABASE_SECRET_KEY` / `SUPABASE_SERVICE_ROLE_KEY`
- `GEMINI_API_KEY` (QA key replaced 2026-09-20; QA Gemini sometimes returns 503 "high demand", which is not a regression)
- `OPENAI_API_KEY` (**QA account has no credit**)
- `DATABASE_URL`

**Other variables the backend reads (names only)**
- App and auth: `SUPPLY_AI_ENV`, `SUPPLY_AI_SECRET_KEY`, `SUPPLY_AI_CORS_ORIGINS`, `SUPPLY_AI_TRUSTED_HOSTS`, `SUPPLY_AI_FRONTEND_URL`, `SUPPLY_AI_REFRESH_COOKIE_NAME` / `_SAMESITE` / `_SECURE`.
- Email callbacks: `ONBOARDING_EMAIL_CALLBACK_BASE_URL`, `SUPABASE_EMAIL_REDIRECT_URL`.
- Paystack: `PAYSTACK_PUBLIC_KEY`, `PAYSTACK_SECRET_KEY`, `PAYSTACK_CALLBACK_URL`. The records don't state QA's Paystack mode, so **confirm with the owner that QA uses TEST keys before opening any checkout**. Never LIVE.
- Email: `RESEND_API_KEY`.
- Ops console: `PLATFORM_OWNER_SECRET_KEY`, `PLATFORM_PANEL_PATH` (**the value is private and must never be recorded**).
- Barcode: `SUPPLY_AI_BARCODE_EXTERNAL_DAILY_LIMIT`, `SUPPLY_AI_BARCODE_EXTERNAL_WINDOW_SECONDS`, `UPCITEMDB_COOLDOWN_FALLBACK_SECONDS`, `UPCITEMDB_PLAN`.
- Monitoring: `SENTRY_BACKEND_DSN`, `SENTRY_FRONTEND_DSN`.
- The full list is in `backend/main.py` (`os.getenv`).

**Plan ids are frozen billing keys, not labels** (`backend/main.py`):

| Plan id | Label | Price per month |
|---|---|---|
| `core` | Starter | ₦5,000 |
| `starter` | Business | ₦20,000 |
| `business` | Premium | ₦50,000 |
| `enterprise` | Enterprise | ₦200,000 |

**Android**
- App id `com.example.cauldra`, shared by QA and production today (manifest G7).
- Build target registry `scripts/build-targets.json`: `web`, `qa`, `production`.
- `build-apk.bat qa` builds and verifies; `node scripts/verify-apk-target.js --apk=<path> --target=qa` checks a built APK.
- The Windows builds used `cmd.exe //c ".\\build-apk.bat qa"`. The cloud environment is unlikely to have the Android SDK, so APK builds may need the owner's machine.

**Tests**
- Python: `PYTHONPATH="backend:tests" python -m unittest tests.<module>` (Windows uses `;`). Node: `node tests/<name>.cjs`.
- **Pre-existing failures at `2aeec3f`** (not regressions):
  - `test_inapp_payments`;
  - `test_infrastructure`;
  - `test_location_authority` (load error or timeout);
  - `test_paystack_webhook_atomicity`;
  - `test_sentry_monitoring` (hangs or fails);
  - PostgreSQL-only suites without `TEST_POSTGRES_ADMIN_URL`;
  - `tests/test_native_bundle.cjs` (needs a freshly built `www/` bundle);
  - `tests/test_localization_barcode.cjs` (on the Windows checkout its regex expects LF but files were CRLF; on a Linux checkout it may pass).
- Compare against this baseline before calling anything a regression.

**Line endings**
- The owner's Windows checkout uses `core.autocrlf=true`, so `frontend/js/app.js`, `frontend/index.html` and `backend/main.py` are CRLF there. Git stores LF, and a Linux clone sees LF.
- `frontend/js/build-target.js` is pinned to LF (`.gitattributes`).
- Don't mass-convert line endings.

---

## 10. Production rollout prerequisites (already recorded; names only)

**Authoritative file:** `PRODUCTION_ROLLOUT_MANIFEST.md` in the owner's records.
- Every change that production will need must be added there: code commits, migrations, variables (**names only**), dashboard settings (Supabase, Paystack, Resend, Railway) and Android release steps.
- Statuses move only on evidence: `NOT READY` → `READY FOR PROD` → `APPLIED TO PROD` → `VERIFIED IN PROD`, per channel (server / Native).
- **Current status of every remediation item: production `NOT READY` or `READY FOR PROD`; nothing `VERIFIED IN PROD`.**

Because the cloud session can't reach the owner's manifest, **record each new production requirement in §14 of this file** and in the batch's commit message. The owner copies it into the manifest; see §11.

**Recorded prerequisites (summary):**

**Merge and migrations**
1. **Merge plan.** Bring the remediation line to `main` only with authorization. `main` has 6 commits the line lacks (`a4a3d3d`, `dcf8c01`, `73de8cb`, `ad83951`, `a8a35a7`, `4195d98`), including the two 2026-09-22 `index.html` edits. Reconcile these, re-check on QA, then promote.
   - **The promotion method needs an owner decision.** The line's early history includes `f339d33`, which **added a 15.8 MB QA APK** (`qa_environment/APK/cauldra-qa.apk`), removed again in `34cee0e`.
   - A plain merge would carry that blob into `main`'s history. The manifest's G3 warns against merging wholesale for this reason.
2. **Migrations.** Read production's revision first. Then `0041` before the Batch D code, and `0042` right after the Batch E code.

**Variables and service configuration**
3. **`RESEND_FROM`** must be set on production **before** code containing `4f58a61` lands. Production refuses to start without it, and production Forgot Password is broken until then.
4. **Durable storage** (QA-STORAGE-001), before or with that code:
   - production's **own private** Supabase bucket;
   - `SUPABASE_STORAGE_BUCKET`, `SUPPLY_AI_STORAGE_BACKEND=supabase`, and the existing `SUPABASE_URL` + `SUPABASE_SECRET_KEY`/`SUPABASE_SERVICE_ROLE_KEY` for the production project.
   - The code refuses to start without these.
   - After deploy: confirm the startup log shows `Upload storage provider: supabase`, then run a disposable durability check.
   - Also needed: a storage backup schedule, a retention decision, and a read-only assessment of any historical production rows with missing bytes (owner decision).
5. **`CAULDRA_OFFLINE_SIGNING_KEY`** on production: presence unknown; confirm it. Keys differ per environment.
6. **Supabase Auth on production:**
   - email-link lifetime ≥ 20 min;
   - Confirm email ON;
   - redirect URLs covering `https://cauldra.cohren.com/**`;
   - custom SMTP and branded templates (QA-AUTH-EMAIL-001).
7. **Canonical-address settings** to confirm: `ONBOARDING_EMAIL_CALLBACK_BASE_URL`, `SUPABASE_EMAIL_REDIRECT_URL`, `SUPPLY_AI_FRONTEND_URL`, `SUPPLY_AI_CORS_ORIGINS`, `SUPPLY_AI_TRUSTED_HOSTS`, `PAYSTACK_CALLBACK_URL` and the `SUPPLY_AI_REFRESH_COOKIE_*` settings. Keep the Railway host served for legacy APKs (G8).
8. **Provider keys:**
   - `GEMINI_API_KEY` on production was replaced by the owner on 2026-09-20, but it has not been proven in production;
   - `OPENAI_API_KEY` on production needs credit for invoice OCR.
9. **Barcode** (optional; defaults are safe; no key needed for the free plan): `SUPPLY_AI_BARCODE_EXTERNAL_DAILY_LIMIT`, `SUPPLY_AI_BARCODE_EXTERNAL_WINDOW_SECONDS`, `UPCITEMDB_COOLDOWN_FALLBACK_SECONDS`, `UPCITEMDB_PLAN`.
10. **Ops route** on the canonical address (URL-OPS-001).

**Android, payments and launch**
11. **Android release:**
    - a release signing key;
    - the release fingerprint in `assetlinks.json` (it currently carries the **debug** key, G5);
    - App Links path scope (G6);
    - a separate QA application id (G7);
    - the refresh cookie verified cross-site from the native origin (G1c);
    - build with target `production` only when authorized, and check it with the APK verifier.
12. **Paystack LIVE:** callback and webhook on the canonical address; one real low-value payment, **by the owner**, after the TEST flows.
13. **Legal pages** published with final text; Play Store listing and privacy declarations.
14. **Production smoke test** after promotion: sign-up email, password recovery, one payment, AI, invoice scan, barcode, Android install.
15. **Housekeeping:** delete the local secrets file when remediation closes.

**Promotion order recorded in the manifest:** the A → B → C → QA-STORAGE-001 → D → E line, together with the pre-triage tickets it already contains.

---

## 11. Permanent records — where they are and what this file summarises

### 11.1 Location (owner's local machine; NOT accessible from the cloud)

`C:\Users\DELL\Documents\Cauldra-Records\`
- `qa_environment\`:
  - `MASTER_REMEDIATION_TRACKER.md`
  - `LAUNCH_TRIAGE_2026-09-24.md`
  - `PRODUCTION_ROLLOUT_MANIFEST.md`
  - `REMEDIATION_AND_RETEST_LOG.md`
  - `FINDINGS_REGISTER.md`
  - every `REMEDIATION_*_REPORT.md` (Batches B–E, QA-STORAGE-001, CB-001, the Android A–E report, and others)
  - `BARCODE_UPCITEMDB_INSPECTION.md`
  - `INVESTIGATION_QA-STORAGE-001_REPORT.md`
  - `PERM-001_REMEDIATION_PLAN.md`
- `session_evidence_2026-09-24\`, `…-25\`, `…-26\`: test scripts, outputs, screenshots and APK copies. The RESP-001 device evidence is `session_evidence_2026-09-26\android\a6_resp001_device.txt`, `a7_resp001_rootcause.txt` and `shots\`.
- `MISSING_RECORDS.md`.

**The cloud session cannot read that directory.** This handoff summarises the parts needed to continue:
- identities and commits of every batch;
- verified behaviour;
- migrations;
- the triage buckets and exact remaining findings;
- owner decisions;
- QA state;
- production prerequisites;
- the Android pass results;
- the RESP-001 evidence.

**Not copied:** per-test evidence, raw logs, screenshots, the full 74-row tracker tables, and the owner's record-keeping notes. When needed, ask the owner for a specific record.

### 11.2 Keeping the records current from the cloud

- The owner's records stay authoritative. Each cloud batch should end with a **records update block** in its report, and in §14 of this file.
- The block covers: triage rows changed, new tracker statuses, the retest-log entry, and manifest additions (names only). The owner, or a local session, applies them to the permanent records.
- Never create a competing tracker in the repository. §14 is a transfer buffer only.

### 11.3 Missing historical audit records

- On 2026-09-24, the original records folder (in Windows Temp) was rewritten by something outside the Claude session.
- **23 files and 3 folders are missing**, including the frozen **`FINAL_AUDIT_REPORT.md`** (last verified SHA-256 prefix `c726619df5287200`), `PERM_MATRIX_STAFF.md`, `MASTER_TEST_INVENTORY.md`, `UX_PROFESSIONALISM_REGISTER.md` and others. `MISSING_RECORDS.md` lists them.
- The triage and tracker were built from the frozen audit **before** the loss, and remain the working source of findings.
- **Do not reconstruct, summarise or replace the missing canonical audit** from unverified copies, chat history or memory. Recover it only from a trusted copy, and only the owner decides that.
- Separately: **42 historical QA upload rows** have no bytes (QA-STORAGE-001). They are unrecoverable. **Do not touch them.**

---

## 12. How to work in this repository

**Branching**
- Branch each batch from the current tip of the line: `remediation/batch-<x>-<topic>` from `remediation/batch-e-ai-forecast-brain`, **after** this handoff commit.
- Commit with clear messages, push the batch branch, and never merge.

**Verification standard**
- Each finding needs:
  - root cause confirmed in code;
  - the smallest fix;
  - tests that fail on the unfixed code;
  - a QA deploy (when authorized);
  - API and Web evidence at four layouts (375×812, 768×1024, 1024×768, 1366×768+);
  - a regression sweep against the §9 baseline.
- Frontend changes: every change is checked at the four layouts.
- Android verification is batched into the final release-candidate pass (§7).

**Data handling**
- Prefer disposable data. Restore permissions and settings and **verify** the restoration.
- Use the minimum number of paid AI calls. A provider 503 is not a regression.

**Security**
- Server-side enforcement is authoritative; UI hiding is secondary.
- Keep tenant isolation: every query is filtered by `business_id`.
- Never give the AI model unrestricted database access.
- Never inject raw HTML (see `renderAIMarkdown`).

**Scope**
- Regressions caused by a batch get the smallest fix plus a test.
- Unrelated issues are recorded, not fixed.

---

## 13. Recommended next coding batch (Batch F) — NOT started

**"Batch F" is not defined in the permanent records.** This is a **recommendation** for the owner to confirm or adjust before any code is written.

**Recommended Batch F — layout, sessions and UI-state correctness (web bundle):**
1. **RESP-001 (row 41):** the fix in §6 plus a sidebar-visible width test.
2. **Rows 22, 23, 24 (SESS-001, UX-004, PLAN-003, UI-001, PLAN-006):** an honest session-expiry notice instead of silent failure; no silent drop to guest; a usable billing Retry; no "Guest Hub" flash; the password-change dialog closes on success. Investigate SUSP-001 alongside.
3. **Rows 8, 9 (ERR-001, ERR-003):** readable errors when the body isn't JSON; server errors carry CORS/security headers so Android doesn't blame the connection.
4. **Rows 37–40 (AUD-001, AUDIT-UI-001, SALE-001, EXP-001):** no false activity entries; Clear filters resets Location; the correct sale label; guest export buttons work or are removed.
5. **Rows 65, 67–71 (UX-014, UX-015, audit OBS-6/2/5/8):** panel navigation replaces rather than stacks; permission and rate-limit messages; tap targets; consistent error presentation; onboarding copy.

**Then, in later batches:**
- the **app-wide UI/UX polishing sweep** (§8);
- **I18N-001** (5 launch languages, hide the rest, Arabic RTL);
- **platform rows 73–77** (Tailwind build step, service worker, console noise, Ops route, schema-drift error);
- the **final release-candidate APK and Android pass** (§7).

Production promotion stays separate and owner-authorized.

---

## 14. Transfer buffer — records updates pending for the owner's permanent records

*(Each cloud batch appends:)*
- date and batch;
- branch and commits;
- QA deploy;
- migrations;
- triage rows changed, with new status;
- tracker lines;
- retest-log summary;
- manifest additions (**names only**);
- owner actions.

### 14.1 Batch F — layout, sessions and UI-state correctness (2026-09-26, cloud session)

**Branch and commits.** Pushed to `remediation/batch-e-ai-forecast-brain`, on top of `2e15735`. This cloud session may push only to that branch, so there is no separate `batch-f` branch. The owner may create `remediation/batch-f-layout-sessions` at `130f3a4` locally if the per-batch convention (§12) should be kept.
`6d1a8a9` RESP-001 · `046acb7` ERR-001/ERR-003 · `8397ccc` SESS-001/UX-004/PLAN-003/UI-001/PLAN-006 · `7cc7eae` AUD-001/AUDIT-UI-001/SALE-001/EXP-001 · `130f3a4` UX-014/UX-015/OBS-6/OBS-2/OBS-8 · plus this documentation commit.

**QA deploy: NONE.** The cloud environment has no Railway CLI or credentials, and its network policy denies `cauldra-qa.up.railway.app`. All verification ran against a **local** backend (disposable Postgres 16, Alembic head `0042`, disposable business `LOCALF01`) in headless Chromium, plus unit/API tests. Current QA deployment stays `74db9267` (`2aeec3f`). **Owner/local action:** `railway up --service cauldra-qa` from `130f3a4` (or later), then the QA checks below.

**Migrations: none.** Batch F is code only. Head stays `0042`.

**Triage rows (Still needs a fix = 32 before Batch F):**

| Row | Finding | New status |
|---|---|---|
| 41 | RESP-001 | FIXED — Web verified locally (768–1440, sidebar visible); QA + Android pending |
| 8 | ERR-001 | FIXED — tests + local; QA pending |
| 9 | ERR-003 | FIXED — tests (CORS + security headers on 500); QA + Android pending |
| 22 | SESS-001, UX-004, PLAN-003 | FIXED — 9 local browser scenarios + billing retry; QA + Android pending. **SUSP-001 root cause still open** (the symptom is now announced, not silent) |
| 23 | UI-001 | FIXED — local sign-in/reload trace; QA + Android pending |
| 24 | PLAN-006 | FIXED — mandatory + voluntary change, local; QA pending |
| 37 | AUD-001 | FIXED — Postgres test + local (0 false entries over sign-in + 3 reloads); QA pending |
| 38 | AUDIT-UI-001 | FIXED — local browser; QA pending |
| 39 | SALE-001 | FIXED — label; en/fr/es/pt/ar wording; the 30 non-launch languages fall back to English for this one sentence |
| 40 | EXP-001 | FIXED — guest Export opens Sign In; QA pending |
| 65 | UX-014 | FIXED — local browser (menu replaces module); QA + Android pending |
| 67 | UX-015 | FIXED — all 7 bare "Access denied" 403s now specific (the audit's single endpoint is not identifiable without the missing audit) |
| 68 | audit OBS-6 | FIXED — 44 px hit area on all close buttons; Android pending |
| 69 | audit OBS-2 | FIXED (objective part) — inline errors escaped/filtered; owner to confirm against the tracker's original wording |
| 71 | audit OBS-8 | FIXED — every 429 names the wait |
| 70 | audit OBS-5 | **NOT FIXED — blocked**: the specific onboarding copy issues were in the missing audit; owner to supply the tracker text |

If the owner accepts local Web verification pending QA, the count becomes **32 → 17**. Otherwise the 15 rows stay open until the QA pass.

**Tracker lines (new):** RESP-001, ERR-001, ERR-003, SESS-001, UX-004, PLAN-003, UI-001, PLAN-006, AUD-001, AUDIT-UI-001, SALE-001, EXP-001, UX-014, UX-015, OBS-6, OBS-2, OBS-8 → `FIXED — awaiting QA deploy verification` (plus Android where noted). OBS-5 → `OPEN — needs owner detail`. SUSP-001 → `OPEN — investigation (symptom now visible)`.

**Retest-log summary.** New tests: `tests/test_resp001_inventory_header_width.cjs` (needs Playwright; skips its browser part otherwise), `tests/test_batch_f_frontend.cjs` (16 checks), `tests/test_batch_f_error_responses.py` (8), `tests/test_batch_f_backend_postgres.py` (needs `TEST_POSTGRES_ADMIN_URL`). Each fails on the pre-fix code. **Sweep:** every Python and Node suite was run at `2e15735` and at `130f3a4` in the same environment, with a local `TEST_POSTGRES_ADMIN_URL`. The exit codes are identical for every pre-existing suite, and the four new suites pass. **Recorded, not fixed (pre-existing, not Batch F):** with a Postgres admin URL present, 9 Postgres-only suites also fail at baseline — `test_business_day`, `test_historical_cogs_postgres` (timeout), `test_mutation_idempotency_postgres`, `test_refund_state_postgres`, `test_registration_atomicity_postgres`, `test_rejected_checkout_state_postgres`, `test_sale_pricing_policy_postgres`, `test_sales_checkout_atomicity_postgres`, `test_transaction_count_postgres`. The sampled cause is test fixtures whose Location has no currency ("This Location has no authoritative currency…"); the fixtures predate location-currency enforcement. They were previously hidden because the baseline ran without Postgres.

**Manifest additions (names only).** Code only; no variables, dashboard settings or migrations. Add the five Batch F commits to the promotion line after Batch E. Behaviour notes for promotion: unhandled 500s now return JSON `{"detail": ...}` with CORS headers (new `UnhandledErrorResponseMiddleware`); `PATCH /users/me/profile` records only real changes; the browser keeps a non-sensitive `localStorage` key `cauldra.signedInSession` (a "was signed in" marker, cleared on sign-out).

**Owner actions.**
1. Deploy `130f3a4`+ to QA; run the QA checks for the rows above (a Manager sign-in for billing Retry, a disposable second location for Clear filters).
2. Supply the OBS-5 onboarding-copy detail from the tracker.
3. Android final pass (§7): add RESP-001 at 800 px, close tap areas, session-expiry notice, UX-014, the ERR-003 error text.


### 14.2 UI/i18n result + platform stage, rows 73–80 (2026-09-27, overnight cloud session)

**UI/i18n stage (local session, recorded here).** Commits `d551d89` + `25b8e4c` (triage 70, 72; I18N-001, OBS-5). QA deployment **`75cb2f59`**. **UI/i18n signed-in QA: 34 PASS, 0 FAIL.** The stage is QA/browser verified; not rerun here.

**Branch and commits.** `remediation/batch-e-ai-forecast-brain`, on top of `25b8e4c`:
`d38965e` rows 73–75 · `80c2736` row 77 · `ec955e4` row 78 · `63fc49b` row 80 · plus this documentation commit. **No QA deploy** (no Railway access from the cloud). **No migrations, no new variables.**

| Row | Finding | Status | Evidence |
|---|---|---|---|
| 73 | OBS-11 | **FIXED — QA + Android pending.** The app compiled Tailwind in every browser (vendored 3.4.17 play compiler, with a "not for production" console warning). It now ships the prebuilt `frontend/css/tailwind.css` (`node scripts/build-tailwind-css.js`, theme in `scripts/tailwind/`), linked last, like the injected styles. | All 472 runtime-generated classes present. Computed styles match the runtime version in 150 states (27 modules; 375/768/800/1024/1366 px; en/fr/es/ar/pt), except one real class conflict (expense custom-category row: two text colours), fixed to its previous colour. Remaining differences are data or animation timing only. |
| 74 | OBS-10 | **FIXED — QA pending** (the service worker is web-only; native skips it). Cause: `base.css` precached twice (`/css/base.css` and an unused `/frontend/css/base.css`, same file). | Cache listing before and after; `run_service_worker_offline.js`: no duplicate entries, `tailwind.css` cached, styled cold offline. Cache version `cauldra-shell-v13-prebuilt-css`. |
| 75 | OBS-12 | **FIXED — QA pending.** Three load-time `app.js build …` console lines removed. The Tailwind warning went with row 73. Scanner traces that run only on a user's scan are kept (diagnostics). | Console capture before and after. |
| 76 | URL-OPS-001 | **PRODUCTION-ROLLOUT ACTION — no code defect found.** The Ops route is built from the request's own host plus `PLATFORM_PANEL_PATH`. The backend, Ops bundle (`location.origin`) and `scripts/create_platform_owner.py` contain no origin. The legacy address must be in configuration or records: the owner's Ops bookmark or records, and/or production Supabase Auth **Site URL** (the setup script's verification email passes no redirect, so the Site URL decides where it lands). | Code search. |
| 77 | REC-001 | **FIXED (diagnostics) — owner decision on fail-closed.** Startup now logs `[startup] Database migration: …` (matches / DATABASE BEHIND CODE + pending list / not shipped by this code / no Alembic history table). A request hitting a missing table or column logs `[schema-behind-code] …` naming it; customers keep the ERR-003 text. It never refuses to start (see decision 1). | `test_rec001_migration_status.py` (5), `…_postgres.py` (current → behind → missing-column request → untracked). |
| 78 | OFFLINE-QUEUE-001 | **FIXED — confirmed first.** (a) Client: a later account/permission refusal overwrote **every** queued change's reason, including changes already refused for their own reason, and could throw on a tamper-quarantined row. Now only waiting changes take the account reason. (b) Server: a replay failing validation said "The original operation contains invalid values." It now names the field and problem, never the value. | Offline harness check (fails before, passes after; 29 others unchanged); `test_offline_queue001.py`, `…_postgres.py` via `/offline/replay`. |
| 79 | QA-AUTH-EMAIL-001 | **EXTERNAL / QA CONFIGURATION — no code change.** App behaviour is correct: a Supabase 429 is surfaced as its own reason with the provider's wait (OBS-8), never bypassed. The cap and default branding are Supabase's default mailer. Custom SMTP plus templates is Supabase dashboard configuration (production: §10 item 6). | Code read. |
| 80 | X5 | **FIXED — QA + Android pending** (signal per this stage's instruction; see decision 2). In retail/wholesale mode a stale cart price was silently replaced by the catalog price (reproduced: cart ₦100, recorded ₦120). The "Sale completed" message was also invisible because it rendered inside the closing dialog. Checkout now returns `price_adjustments` (also in the audit metadata). The till shows the sale message on the page, as a warning naming each adjusted line (en/fr/es/ar/pt), and refreshes its local price. Sale security, permissions and negotiated pricing are unchanged. | Browser reproduction before and after; `test_x5_price_signal_postgres.py` (fails before). |

**Tests and sweep.** New tests:
- `tests/test_platform_rows_73_80.cjs` (static, 33 checks);
- `tests/test_rec001_migration_status.py` and `tests/test_rec001_migration_status_postgres.py`;
- `tests/test_offline_queue001.py` and `tests/test_offline_queue001_postgres.py`;
- `tests/test_x5_price_signal_postgres.py`.

Extended:
- `tests/offline-browser-harness.html`;
- `tests/run_service_worker_offline.js`;
- `tests/test_batch_a_native_offline.cjs` (the mono-stack guard follows the theme into its new file).

The full Python + Node suite ran at `25b8e4c` and at `63fc49b` with a local Postgres: identical exit codes **and** identical failure messages for every pre-existing suite, and all new suites pass.

**Pre-existing, not this stage:**
- the same 9 Postgres fixture suites as §14.1, plus `test_inapp_payments`, `test_infrastructure`, `test_location_authority`, `test_paystack_webhook_atomicity`, `test_sentry_monitoring` and `test_native_bundle`;
- `run_service_worker_offline.js` asserts an old "internet connection … required … first sign-in" unlock wording and fails at `25b8e4c` too.

**QA verification still required** (after `railway up --service cauldra-qa` from `63fc49b`+):
1. The startup log shows `Database migration: 0042_business_brain_forecast_recompute (matches this code)`.
2. Console on load: no Tailwind warning and no `app.js build` lines. Network: `/css/tailwind.css` 200; `tailwindcss-3.4.17.js` is never requested.
3. Spot-check dashboard, sale, expense and settings at 375, 800 and 1366 px in en and ar. The expense "Enter a custom category" row is blue.
4. DevTools → Cache Storage `cauldra-shell-v13-prebuilt-css`: `base.css` appears once, and an offline reload is styled.
5. X5:
   - with the sale screen loaded on device A, change a product's retail price on device B;
   - sell it on A without reloading;
   - A shows a warning naming the product, both prices and the recorded total.
6. A normal sale shows "Sale completed. Sale total: …" on the page.

**Android final-checklist additions:**
- The bundled `css/tailwind.css` styles every screen. Check phone and 800 px tablet, en and ar, including the offline unlock and sale screens, with no unstyled flash at cold start (note on OBS-4).
- `android:prepare:qa` bundle verification passes. It now requires `css/tailwind.css`.
- The X5 warning and "Sale completed" message are readable on a phone, including Arabic RTL.
- Offline: after a permission change, a previously refused queued sale still shows its own reason in Sync details.
- Logcat shows no `app.js build` lines.
- Row 74: nothing to check (the service worker is not used in the native shell).

These are added to all outstanding A–F and UI/i18n Android checks.

**Manifest additions (names only).** Add `d38965e`, `80c2736`, `ec955e4`, `63fc49b` after `25b8e4c`. No migrations or variables. Behaviour notes:
- the frontend loads the static `/css/tailwind.css`, and the vendored Tailwind compiler is removed;
- the service-worker cache version moves to `v13`, and clients drop older caches on activation;
- a startup migration-status log line, plus `[schema-behind-code]` lines. A production schema without `alembic_version` logs "no Alembic history table", which is expected and non-fatal;
- the `/sales/checkout` response and `SALE_COMPLETED` audit metadata gain `price_adjustments`;
- the offline replay `VALIDATION_ERROR` message is specific and adds `details.fields`.

Rollout actions:
- **Row 76:** open Ops only on the canonical address. Set production Supabase Auth Site URL to the canonical address. Replace any stored Ops link that uses the Railway service URL, and keep that host served for legacy APKs (G8).
- **Row 79:** custom SMTP and branded templates (already §10 item 6).

**Owner decisions / manual actions.**
1. **REC-001:** keep the log-only diagnostics, or make startup refuse to run when the database is behind? Refusing would require every migration to land before its code (the recorded order runs Batch E before `0042`). It would also require confirming that production is Alembic-tracked (its level is unread, §4).
2. **X5:** confirm the non-blocking signal (the sale completes at the catalog price, with a warning). The alternative is refusing the sale until the cart is refreshed, as offline replay already does.
3. **Row 76:** check the stored Ops link and production Supabase Site URL; nothing in the repository to change.
4. **Row 79:** decide whether QA gets custom SMTP now or only production.

**Resulting count.** `Still needs a fix` was 32 before Batch F, then 17 after Batch F, then 15 after UI/i18n (rows 70, 72 QA-verified). Rows 73, 74, 75, 78 and 80, plus 77 if the owner accepts diagnostics, move to **fixed, awaiting QA**. Once QA-verified, **15 → 9 (10 if 77 stays open)**.

The remaining 9 are none of them code fixes:
- rows 1 (legal), 64 (decision/Android), 76 (production rollout) and 79 (provider configuration);
- Batch A rows 2, 61, 62, 63 and 66 (Android/manual verification).

This assumes the 15 Batch F rows are confirmed on QA. `75cb2f59` contains their code, and this session has no record of their QA checks.

**Tracker lines (new):**
- OBS-11, OBS-10, OBS-12, OFFLINE-QUEUE-001, X5 → `FIXED — awaiting QA verification`;
- REC-001 → `FIXED (diagnostics) — awaiting QA + owner decision on fail-closed`;
- URL-OPS-001 → `PRODUCTION-ROLLOUT ACTION`;
- QA-AUTH-EMAIL-001 → `EXTERNAL — QA/provider configuration`.


### 14.3 BUSINESS-DAY-OFFLINE-001 — open a Business Day while offline (2026-09-27, cloud session)

**Finding (new, launch-scoped, owner requirement).** Offline sales needed an open Business Day that had synchronized *before* the outage. With none, the till refused: "Internet required: no previously synchronized open Business Day…". This blocked the final Android row 78 run on APK `cauldra-qa-rc-704b843.apk`.

**Cause.**
- `completePOSCheckoutOffline()` and `handleRecordExpenseOffline()` in `app.js` only accepted days from the offline snapshot's `days`.
- `/offline/replay` had no change type for opening a day.

**Branch and commits** (`remediation/batch-e-ai-forecast-brain`, from `704b843`):
- `a3609a6` server: replay type `business_day_open`;
- `95b8470` client: offline open, dependency ordering, id swap, conflict hold, Dashboard state, translations, tests.

**Behaviour now.**
- **A synchronized open day already exists:** unchanged; offline sales use it.
- **No open day, user has `business_day.manage`:**
  - the Dashboard **Open Business Day** control works offline;
  - a sale or expense also opens one, as online checkout does.
- **No open day, user lacks `business_day.manage`:** refused with "Ask someone who can open the Business Day to start it". The button is hidden, as online.
- **Dashboard state:** the offline day shows **Business Day Open — "Opened offline at … · waiting to sync"**, with no Close control until it syncs.
- **Storage:** the day is its own queued change (`business_day_open`, negative local id in `meta.local_id`). It is sealed like every other queued change, so it survives an offline restart and disappears once synced. No new store.
- **Sync order:** sales and expenses recorded in the day list it in `depends_on_op_ids`. `runSync()` sends the day first, then swaps the local id for the server's (`resolveOutboxDependenciesOnBusinessDaySynced`), then sends the sales. No new sync framework.
- **Server:**
  - requires `business_day.manage` plus the usual device, identity and permission-hash checks;
  - uses the same idempotency receipt;
  - keeps the real offline `opened_at` and its location-local `date`;
  - audits `BUSINESS_DAY_STARTED` / `BUSINESS_DAY_AUTO_OPENED` with `offline: true`.
- **Two devices:**
  - the location already has an open day from the **same location-local date** → the offline day **joins** it. It is audited `BUSINESS_DAY_OFFLINE_OPEN_JOINED` and never produces a second open day; the sales land in that day.
  - the open day is from a **different date** → refused as `BUSINESS_DAY_CONFLICT`, and nothing is written. Its sales are held on the device as `BUSINESS_DAY_CONFLICT` with their own reason, never dropped or re-pointed.
- **Row 78:** a later account or permission change still does not overwrite earlier reasons.
- **Service worker:** cache is now `cauldra-shell-v14-offline-business-day`, so §14.2 QA step 4 now uses the v14 cache name.

**Migrations / variables:** none. `CAULDRA_OFFLINE_SIGNING_KEY` is already required for Offline Access.

**Tests.**
- **New:**
  - `tests/test_business_day_offline_postgres.py` — 5 tests, pass;
  - `tests/test_business_day_offline.cjs` — static checks, pass; 21 of them fail on the old client;
  - `tests/run_business_day_offline_e2e.js` — real app, local backend, Chromium; **28/28 pass**.
- **The e2e covers:**
  - open offline, sell, restart offline, unlock, sell again, reconnect;
  - one open day, both sales in it, offline `opened_at` kept;
  - the queue is sealed at rest;
  - the two-device join;
  - the other-date refusal;
  - row 78 reasons kept after a permission change;
  - Sync Details showing each reason separately;
  - a user without permission denied.
- **Regression:**
  - offline harness 30/30, including the row 78 check;
  - offline access, OFFLINE-QUEUE-001, REC-001 and X5 suites pass;
  - all `tests/*.cjs` pass;
  - QA bundle verification passes.
- **Pre-existing failures:** `test_business_day`, `test_mutation_idempotency_postgres`, `test_rejected_checkout_state_postgres`, `test_sales_checkout_atomicity_postgres` and `test_location_authority` fail **identically** at `704b843` (same test names). The service-worker runner's old "first sign-in" wording check also fails at baseline.

**Local follow-up checklist (cloud cannot do these).**
1. Pull `remediation/batch-e-ai-forecast-brain` at `95b8470` or later. There are no migrations and no new variables.
2. Deploy to QA: `railway up --service cauldra-qa`. Confirm the startup line reports the database matches the code (`0043` is expected at head).
3. Rebuild the QA APK. It is **required**: `app.js`, `offline.js` and the i18n catalog changed. Record its SHA-256; `cauldra-qa-rc-704b843.apk` is superseded.
4. Optional, before Android: run `tests/run_business_day_offline_e2e.js` against a **local** backend (see its header). It refuses non-localhost targets.
5. Android: run the row 78 sequence below, plus the checklist additions.
6. Clean-up: close the test Business Day, and remove QA test products and sales per the usual QA hygiene. Do not touch the 42 lost upload rows or Tenant B.

**Row 78 Android sequence (replaces the "pre-open a day online" workaround).**
1. Online, as a permitted user (Staff with `business_day.manage`, or Admin), confirm **no Business Day is open** for the location. Close it if one is. Offline Access must be enabled.
2. Turn the device offline (airplane mode) and unlock the workspace with the PIN.
3. The Dashboard shows **Business Day Closed / Open Business Day**. Tap it: "Business Day opened offline…", then the header reads *Opened offline at … · waiting to sync*.
4. Make an offline sale. It is saved locally, and Sync Details shows 2 waiting.
5. Force-stop the app, reopen it still offline, and unlock. The day is still open, and a second sale works.
6. Set up the refusal on the server: another device lowers stock below the queued quantity (`STOCK_CHANGED`).
7. Reconnect. The day syncs first; the sale is refused with "Server stock is lower…".
8. Change the user's permissions on the other device, then trigger sync with a new queued change. The new change takes the permission reason, and the earlier sale **keeps** "Server stock is lower…".
9. Sync Details lists the separate reasons.
10. On QA, confirm exactly one open day for the location, with its opened time from step 3.

**Android checklist additions.**
- Offline open with no prior day:
  - via the Dashboard, and via a sale;
  - a user without `business_day.manage` is refused;
  - English and Arabic.
- Force-stop while offline: the day persists and the header text is correct.
- Two devices: B opens the day online while A is offline with its own day. A reconnects → one open day, A's sales in it.
- Stated honestly: browser e2e is not Android evidence; this cloud session ran no Android build or device.

**Owner decisions (parked; everything around them is implemented).**
1. **Permission for a sale-triggered offline open.** Implemented: needs `business_day.manage`, as the owner's requirement says. Online, a sale can auto-open a day with only `sales.create`. Default roles all have both, so this only matters when `business_day.manage` has been revoked. Keep the stricter offline rule, or mirror online?
2. **Held sales from a different-date conflict.** Implemented: held on the device with a reason, and never dropped or joined, the same as every existing offline conflict (Sync Details has no resolve action today). Should there be a reviewed action to record them, for example into their own day for that date? That would be a later product change.

**Not in scope, unchanged:** closing a Business Day offline (the Close control still needs a connection); AI and other provider-backed features stay online-only.

**Tracker lines (new):**
- `BUSINESS-DAY-OFFLINE-001 — Offline sales required a previously synchronized open Business Day → FIXED (a3609a6, 95b8470) — awaiting QA deploy + new APK + Android row 78 rerun`;
- row 78 (OFFLINE-QUEUE-001): `Android evidence pending on the new APK with the owner-expected sequence (no online pre-open)`.

**Triage file:** add row `BUSINESS-DAY-OFFLINE-001`: **launch-scoped, owner requirement 2026-09-27**, FIXED — awaiting QA/Android.

**Retest log:**
- 2026-09-27 cloud: server Postgres 5/5; e2e 28/28; offline harness 30/30; static suites all pass;
- failing backend suites identical to `704b843`;
- no QA deploy (no Railway access from cloud); no Android.

**Manifest:** add `a3609a6`, `95b8470` after `704b843`; no migrations, no variables; the service-worker cache is v14.

**Android/native checklist:** add the row 78 sequence and the additions above; mark `cauldra-qa-rc-704b843.apk` (SHA-256 `756fedb8…a035730`) as superseded for row 78.

**Count:** one new launch-scoped finding opened and fixed in code. It stays open until QA and Android confirm it.


### 14.4 BUSINESS-DAY-OFFLINE-001 part 2 — close offline, implicit-open parity, offline capability matrix (2026-09-27, cloud session)

**Owner rule (2026-09-27).** Any workflow Cauldra itself controls should keep working offline where it can be made safe and deterministic. External-service actions stay online-only and are never faked. Two owner decisions also apply:
- **Implicit open matches online.** The explicit Open Business Day control needs `business_day.manage`; a sale or expense that finds no open day opens one with its own permission.
- **Different-date conflicts keep the safe hold.** No manual-resolution feature for launch.

**Branch and commits** (`remediation/batch-e-ai-forecast-brain`, from `bc585ab`):
- `7c84a82` server: `business_day_close` replay; implicit-open permission;
- `45e5f54` client: offline close, lifecycle ordering, closed state, holds, tests.

**Offline close behaviour.**
- **Who and where:** a user with `business_day.manage` (as online) can close a synced day or an offline-opened day from the same Dashboard control. Without the permission, the control is hidden and the action is refused.
- **What is queued:** a `business_day_close` change (sealed like the rest of the queue) with the real close time. It names the day's own work in `own_refs`.
- **What the user sees:** "Business Day Closed — Closed offline at … · waiting to sync", with Open offered again. This state survives an offline restart.
- **New work after the close:** a closed day never takes new work. A later sale opens a **new** day, exactly as online checkout does after a close, and that open waits for the close.

**Ordering** (existing `depends_on_op_ids`, no new framework): open → the day's sales/expenses → close → the next day's open → its work. The local-to-server id swap covers the close too. Once the close syncs, the device remembers the day as closed in the sealed snapshot, so an offline restart before the next full refresh cannot resurrect it.

**Conflicts.**
- **A — B opened the same-date day while A was offline:** A's open joins B's day, and A's sales land there. With no later work by B, A's close closes that day (the same result as A pressing Close online).
- **B kept selling after A's offline close time:** closing at A's time would put B's sales after the close, so the server refuses (`BUSINESS_DAY_CONFLICT`, "Other work was recorded … after it was closed offline") and the day stays open. A's close is held with that reason.
- **B closed the day before A reconnected, with no pending A work:** answered, and audited as `BUSINESS_DAY_OFFLINE_CLOSE_ALREADY_CLOSED`. The day is never re-closed or re-timed.
- **C — A has pending work for a day the server already closed:** the work keeps its own reason (`BUSINESS_DAY_CLOSED`). A's close is **held** ("a change recorded in it needs review first, so the day was not closed on the server"). It is never sent over unresolved work.
- **D — different date:** the safe hold, as decided. The open is refused; its work and its close are held with reasons; nothing is moved to another accounting day.
- **A refused close:** holds the next day's open, and in turn that day's work.
- **Row 78:** a later permission/account change still never overwrites these reasons.

**Permissions.** Explicit Open or Close needs `business_day.manage`. An implicit open needs `sales.create` for a sale or `expenses.record` for an expense; this matches online `ensure_open_business_day`. The server re-checks on sync through the single replay rule map, and the permission-coverage guard still passes. A permission change before reconnect is refused as `PERMISSION_CHANGED`, the day is untouched and the queue is quarantined.

**Migrations / variables:** none. The service-worker cache is now `cauldra-shell-v15-offline-business-day-close`.

**Tests.**
- **Server:** `tests/test_business_day_offline_postgres.py`, **12/12**:
  - synced-day close keeps the offline time;
  - open → sale → close;
  - already closed on the server;
  - others' later work keeps the day open;
  - close without permission;
  - permission changed;
  - implicit open with the sale permission; an explicit or trigger-less open without `business_day.manage` is refused.
- **Static:** `tests/test_business_day_offline.cjs`, all pass.
- **End to end:** `tests/run_business_day_offline_e2e.js`, 8 scenarios and 48 checks, all passing, on the real app with a local backend:
  - open → 2 sales → close offline → offline restart (still closed, queue intact and sealed) → new sale opens day 2 waiting for the close → reconnect: day 1 closed at the offline time with both sales, day 2 open, one open day, audits correct;
  - synced day closed offline;
  - server closed first (sale and close held);
  - joined and closed;
  - other device keeps selling (close refused, day open);
  - different date plus the row 78 reasons;
  - permission denied (Open/Close refused, a sale still opens a day).
- **Regression:**
  - offline harness 30/30;
  - offline access, OFFLINE-QUEUE-001, X5, REC-001 and staff product permissions pass;
  - `test_business_day`, `test_mutation_idempotency_postgres`, `test_rejected_checkout_state_postgres` and `test_sales_checkout_atomicity_postgres` fail with **identical** failure sets to `704b843`;
  - `test_native_bundle.cjs` needs a prepared source bundle and fails identically without these changes;
  - all other `tests/*.cjs` pass; QA bundle verification passes.

**Local follow-up.**
1. Pull `45e5f54` or later. No migrations, no new variables.
2. Deploy QA (`railway up --service cauldra-qa`) and check the startup database line.
3. **A new APK is required** (`app.js`, i18n catalog). It supersedes both `704b843` and any build of `95b8470`.
4. Optional: run the e2e runner against a **local** backend (see its header).
5. Android:
   - row 78 sequence (§14.3) — it now ends by closing the day offline before reconnecting;
   - offline close of a synced day and of an offline-opened day;
   - force-stop while closed offline;
   - a sale after the offline close opens a new day;
   - two devices: B keeps selling after A's offline close → A's close held with its reason, day open;
   - a user without `business_day.manage`: no Open/Close control, but a sale still opens a day;
   - English and Arabic.
6. Clean-up: close any QA test day and remove test products per QA hygiene. Do not touch the 42 lost uploads or Tenant B.

**First-party offline capability matrix** (one focused audit: every mutating API call in `app.js`, the offline replay rule map and the offline action policy).

| Workflow | Status | Notes |
|---|---|---|
| Offline sign-in (unlock with PIN/biometric), restart | WORKS OFFLINE | First-ever sign-in on a device needs the server, by design (identity). |
| Business Day open / close | WORKS OFFLINE WITH SYNC | This task. |
| Sales (POS checkout) | WORKS OFFLINE WITH SYNC | Stock/price re-validated on sync. |
| Expenses (record) | WORKS OFFLINE WITH SYNC | |
| Products: add / edit (non-stock fields) | WORKS OFFLINE WITH SYNC | Admin delete also queues. Staff deletion *requests* are online. |
| Suppliers: add | WORKS OFFLINE WITH SYNC | |
| Dashboard, Today's Sales, 90-day sales/expense history, inventory, suppliers, POs, Business Brain (read) | WORKS OFFLINE | Reads the sealed snapshot. |
| Barcode scan → own catalogue match | WORKS OFFLINE | The UPCitemdb lookup is external. |
| CSV exports built in the browser (inventory, suppliers, etc.) | WORKS OFFLINE | |
| AI chat, invoice scan, margin advice | INTENTIONALLY ONLINE — EXTERNAL | |
| Subscription, plan changes, Paystack | INTENTIONALLY ONLINE — EXTERNAL | |
| Email verify/change, forgot/reset password | INTENTIONALLY ONLINE — EXTERNAL | Email provider. |
| PO dispatch (email/WhatsApp), push subscribe, price-source *checks* | INTENTIONALLY ONLINE — EXTERNAL | |
| Employees: create, permissions, disable, reset password, delete, account-action approvals | INTENTIONALLY ONLINE — AUTHORITY | Changes who may act on *other* devices; cannot be made safe while this device is offline. Recommend keeping online. |
| Business registration, business deletion, full reset | INTENTIONALLY ONLINE — AUTHORITY | |
| **Stock adjustment (quick stock) and warehouse transfer** | FIRST-PARTY OFFLINE GAP | See G1. |
| **Refunds** | FIRST-PARTY OFFLINE GAP | See G2. |
| **Supplier edit / delete** | FIRST-PARTY OFFLINE GAP | See G3. |
| **Purchase-order drafts: generate / edit / delete** | FIRST-PARTY OFFLINE GAP | See G4. |
| **Historical Business Day: reopen, reopen request/approval, close by id** | FIRST-PARTY OFFLINE GAP | See G5. |
| **Excel exports; sales/expense exports** | FIRST-PARTY OFFLINE GAP | See G6. |
| **Settings: company profile, own profile/avatar, notification preferences; language preference persistence** | FIRST-PARTY OFFLINE GAP | See G7. |
| **Notifications: mark read** | FIRST-PARTY OFFLINE GAP | See G7. |
| **Price Monitor: manual price entry, add/remove source, upload price list** | FIRST-PARTY OFFLINE GAP | See G8. Source *checks* are external. |
| **Warehouses/locations: create, delete, activate/deactivate** | FIRST-PARTY OFFLINE GAP | See G9. |
| Business Brain: act on a recommendation | FIRST-PARTY OFFLINE GAP (minor) | See G7. |
| Product deletion approval (admin resolving a staff request) | FIRST-PARTY OFFLINE GAP (minor) | Approval authority; recommend keeping online. |

Common current behaviour for every gap: the action tries the network, then shows a generic "could not…/failed" message (or the button is marked online-only). The existing queue already supports ordering, dependencies, idempotent replay, permission re-checks and held conflicts.

**Gap details** (current behaviour / why online / queue fit / risk / scope / launch-critical?):
- **G1 Stock adjustment and transfer.**
  - *Current / why online:* a generic failure; the replay explicitly refuses stock changes on `product_update`.
  - *Queue fit:* yes, as delta operations (like sales), re-validated on sync.
  - *Risk:* medium (concurrent stock; a transfer must never go negative).
  - *Scope:* **medium.**
  - *Launch-critical: likely YES*: receiving or correcting stock during an outage is ordinary counter work.
- **G2 Refunds.**
  - *Current / why online:* online-only in the offline policy; money and stock plus a link to the original sale.
  - *Queue fit:* yes, with a dependency on the original sale's sync. Refunds of offline sales need the sale's server id, and remap works.
  - *Risk:* high (money, restock, double refund; server idempotency via `client_ref` exists).
  - *Scope:* **medium–architectural.**
  - *Launch-critical: owner call; probably YES* for retail counters under the stated rule.
- **G3 Supplier edit/delete.**
  - *Current / why online:* not implemented in replay.
  - *Queue fit:* yes (mirror product update/delete with `base_updated_at`).
  - *Risk:* low. *Scope:* **small.** *Launch-critical:* no.
- **G4 Purchase-order drafts.**
  - *Current / why online:* generation runs server logic (reorder calculation).
  - *Queue fit:* edit/delete of existing drafts queues easily; offline *generation* would need the calculation on the client.
  - *Risk:* low. *Scope:* **small** (edit/delete) / **medium** (generate). *Launch-critical:* no.
- **G5 Historical day reopen/close by id.**
  - *Current / why online:* an approval workflow over closed accounting periods.
  - *Queue fit:* possible, but it reopens finalised periods with multi-user approval.
  - *Risk:* high (accounting). *Scope:* **architectural.**
  - *Launch-critical:* no; recommend keeping online.
- **G6 Excel / sales / expense exports.**
  - *Current / why online:* files are built on the server.
  - *Queue fit:* not a queue item; build locally from the cached 90-day data.
  - *Risk:* low (the export must say it excludes unsynced/unsnapshotted data). *Scope:* **small–medium.** *Launch-critical:* no.
- **G7 Settings/profile/preferences, notifications read, Brain actions.**
  - *Queue fit:* simple last-writer updates; company profile edits touch currency/tax display, so conflict rules are needed.
  - *Risk:* low (profile) to medium (company currency). *Scope:* **small** each. *Launch-critical:* no.
- **G8 Price Monitor first-party actions.**
  - *Queue fit:* manual price entry and source add/remove queue easily; the price-list upload parses a file on the server.
  - *Risk:* low. *Scope:* **small** (manual/sources) / **medium** (upload). *Launch-critical:* no.
- **G9 Warehouses/locations structure.**
  - *Current / why online:* stock, currency and Business Day scoping hang off them.
  - *Queue fit:* possible, with local-id remap chains (products → warehouse).
  - *Risk:* medium–high (location currency and day scoping). *Scope:* **medium–architectural.**
  - *Launch-critical:* no.

Also a small UX gap across all of the above: offline failures show generic "failed" wording instead of a clear "needs internet — nothing was saved" message. It is **small** and recommended alongside any G1/G2 work.

**Launch-critical under the owner rule (recommendation, for the owner to confirm):** **G1** (stock adjustment/transfer) and **G2** (refunds). Everything else can follow launch.

**Tracker lines:**
- `BUSINESS-DAY-OFFLINE-001 — part 2: offline close + implicit-open parity → FIXED (7c84a82, 45e5f54) — awaiting QA + new APK + Android`;
- new: `OFFLINE-CAP-G1 stock adjust/transfer — FIRST-PARTY OFFLINE GAP (medium) — owner: launch-critical?`;
- new: `OFFLINE-CAP-G2 refunds — FIRST-PARTY OFFLINE GAP (medium–architectural) — owner: launch-critical?`;
- new: `OFFLINE-CAP-G3…G9 — FIRST-PARTY OFFLINE GAP — post-launch candidates (see handoff §14.4)`.

**Triage file:**
- update `BUSINESS-DAY-OFFLINE-001` (part 2 fixed);
- add `OFFLINE-CAP-G1` and `-G2` as **owner decision: launch scope**;
- add `OFFLINE-CAP-G3…G9` as post-launch.

**Retest log:**
- 2026-09-27 cloud: server 12/12; e2e 48/48 checks (8 scenarios); offline harness 30/30; static suites pass;
- failing backend suites identical to `704b843`;
- no QA deploy, no Android.

**Manifest:** add `7c84a82`, `45e5f54`; no migrations, no variables; service-worker cache v15.

**Android/native checklist:** add the step-5 checks above. Mark any APK built before `45e5f54` as superseded.


### 14.5 OFFLINE-STOCK-REFUND-001 — G1 stock adjustments/transfers and G2 refunds offline (2026-09-27, cloud session)

Owner decision: G1 and G2 are launch scope; G3–G9 stay later work, not started. Starting head `be2504e`. No migrations, no new variables; service-worker cache `cauldra-shell-v16-offline-stock-refunds`.

**G1, stock adjustments.**
- The existing +/- actions work offline for users with `inventory.adjust_stock`.
- Each is a sealed `stock_adjust` change: product, the product's warehouse, a signed **delta**, reason, the quantity the device saw, and the real local time.
- The online endpoint was already delta-based, so nothing is ever overwritten with an absolute count.
- On sync the server rechecks the permission and device grant, the product (RESOURCE_DELETED) and the warehouse/location (LOCATION_CHANGED). It then applies the delta on top of other devices' changes, exactly once (op-id idempotency).
- If the result would be negative, it refuses (STOCK_CHANGED) with the current figure, and nothing changes.
- Online and offline share one core, `_apply_stock_adjustment`.

**G1, transfers.**
- The warehouse-modal transfer works offline for users with `inventory.transfer_stock`, between warehouses already on the device. Local source stock is checked first.
- `stock_transfer` carries warehouse **ids**. The server locks both stock rows in a fixed order and moves both in one transaction, or neither.
- Too little source stock is refused whole with the figures; there is no partial move and no negative stock.
- Online and offline share `_apply_stock_transfer`.
- The device's cached stock reflects its own pending changes, like an offline sale, so the same units cannot be moved twice on the device.

**G2, refunds: what was found.** Sale refunds are **internal to Cauldra**: accounting rows plus optional restock. `create_refund` calls no payment provider. Paystack refund fields exist only on subscription and card-verification payment records. So an offline refund is a pending request that the server completes in full on sync, and nothing claims money moved.

**G2, which sales can be refunded offline.** With `sales.refund`:
- (A) a sale recorded offline on this device, synced or not;
- (B) a synchronized sale in the new sealed snapshot list `refundable_sales`: the last 90 days, bounded at 500 lines, carrying what the server says is still refundable;
- (C) anything else needs the internet, and the modal says so;
- a legacy sale with no known location is not refunded offline.

**G2, how an offline refund is recorded.**
- The device takes off refunds it has already queued, so the same units cannot be refunded twice on it.
- The refund lands in the open day at the original sale's location, auto-opening one with `sales.refund` as online does.
- A refund of a not-yet-synced sale depends on that sale and addresses its line by cart position; the server stores one row per cart item, in order, and checks the product id.
- If the sale is refused, the refund is held with "The sale this refund is for could not be synchronized…".
- Nothing is restocked on the device; the server restocks exactly once.
- A Business Day close waits for refunds recorded in it.

**G2, on sync.**
- The server rechecks permission and grant, and the day and location.
- It locks the sale rows and rechecks the remaining refundable quantity. If another till refunded some or all of it, the whole offline refund is refused (REFUND_CONFLICT) with sold, already-refunded and remaining figures. Nothing is partly applied, over-refunded or restocked twice.
- Otherwise it runs the online refund with the op id as its idempotency key.
- `create_refund` now also locks the sale rows, so two concurrent online refunds cannot over-refund either.

**UX.**
- Sync Details now lists waiting changes as "Refund pending", "Transfer pending", "Adjustment pending" or "Waiting to sync", and conflicts as "Needs attention".
- Transactions shows "Refund pending" on a sale.
- Toasts say pending, never done.
- New strings are translated (fr/es/ar/pt).

**Tests.**
- Server `tests/test_offline_stock_refund_postgres.py`: 16/16.
- E2E `tests/run_offline_stock_refund_e2e.js`: 35/35 checks, 3 scenarios, stable over repeated runs.
- Static `tests/test_offline_stock_refund.cjs`: pass.
- Regressions:
  - Business Day e2e 46/46 (the part-2 report's "48" was a miscount; the suite has 46 checks);
  - Business Day offline server tests 12/12;
  - offline harness 30/30 (row 78);
  - batch D inventory, staff permissions, PERM-001, offline access/queue, X5, REC-001, refund reporting and batch E: all pass;
  - suites failing at `be2504e` (business_day, mutation idempotency, rejected checkout, checkout atomicity, sale pricing, refund state (Paystack verification charges), REC-001 postgres, location authority) fail on exactly the same tests.
- Two test-only fixes: a whole-second timing gap in the Business Day e2e, and per-run audit scoping in the new e2e.

**APK:** a new APK is required (client code and service-worker change). It supersedes builds before this batch.

**Android checks to run** (English and Arabic):
1. Offline +/- stock, then force-stop and reopen: still pending. Reconnect: applied once.
2. Offline transfer: "Transfer pending". A transfer larger than the device's stock is refused.
3. Another device empties the source first: the transfer is held, and nothing moves.
4. Offline refund of a synced sale and of an offline sale: "Refund pending"; no stock comes back until sync; force-stop keeps both.
5. Another till refunds the same sale first: the offline refund is held with its figures.
6. The offline sale is refused: its refund is held.
7. A user without `sales.refund`: no refund offline.

**Tracker:**
- `OFFLINE-CAP-G1 → FIXED (commits below) — awaiting QA + APK + Android`;
- `OFFLINE-CAP-G2 → FIXED — awaiting QA + APK + Android`;
- `OFFLINE-CAP-G3…G9 → later offline work (unchanged)`.

**Triage:** the same rows.

**Retest log:** 2026-09-27 cloud results above; no QA deploy, no Android.

**Manifest:** add this batch's commits; no migrations, no variables; service-worker cache v16.

### 14.6 OFFLINE-EXTERNAL-001 — external-provider workflows offline-friendly, and ONE combined local handoff (2026-09-27, cloud session)

Owner principle: first-party workflows work offline where safe; external-provider workflows are **offline-friendly** (work preserved, the external step deferred or resumed, PENDING never shown as COMPLETED). Starting head `904f904`. No migrations, no new variables; service-worker cache `cauldra-shell-v17-offline-external`.

**Shared architecture (no new framework).** A deferred external step is one more change type in the existing sealed outbox: AES-GCM at rest; in clear only `op_id`, `type`, `status`, dependencies and time; replayed by the existing `/offline/replay` (device grant, identity and permission rechecked, `claim_idempotent_mutation` on the op id). Added:
- `EXTERNAL_ACTION_TYPES` (today `po_email_send`) with a retry cap: backoff 10 s → 5 min, at most 8 attempts, then **Needs attention** (never retried forever);
- `cancelOutbox()` in `offline.js`: one atomic read-and-delete, only for external types, never while the send is in flight; `updateOutbox()` can no longer re-create a cancelled row; the sync loop re-reads after marking a change "syncing" and skips a cancelled one;
- Sync Details: "Waiting to send", a **Cancel** action for a waiting email and **Remove** for a held one, and plain reasons for the new codes.

**Implemented.**
1. **Purchase-order email while offline (QUEUEABLE).** Send by email on a synchronized DRAFT queues a sealed `po_email_send` (order id, supplier id, SHA-256 of the draft text). The card says **Waiting to send** ("Saved on this device and not sent yet…"); edit, delete and Send are hidden for it; the same order cannot be queued twice. On reconnect it is sent automatically through the server, which rechecks `po.send`, that the order still exists and is DRAFT (`PO_ALREADY_SENT`), that the draft and supplier are unchanged (`PO_CHANGED`) and that it is at most 72 hours old (`STALE_ACTION`), then calls the provider with `Idempotency-Key: cauldra-offline-<op id>`. The order becomes SENT, audited and notified (the server sends the push) only after the provider accepted it; "Purchase order #N was emailed." appears only then. Provider outcomes: unconfigured → `PROVIDER_UNAVAILABLE` (Needs attention), refused → `PROVIDER_REJECTED` (Needs attention), outage → `PROVIDER_RETRY` (retried). Reasons never echo the address, key or draft.
   - Online send unchanged in behaviour; it now shares `email_purchase_order()` and locks the order row, so two concurrent sends of one draft can no longer both email.
2. **Purchase orders readable offline.** The Purchase Orders screen renders the sealed snapshot's orders with "Using saved data from <time>." (it said "load failed" before). WhatsApp, edit and delete stay online-only and are hidden offline.
3. **AI (MUST WAIT, context kept; local fallback).** The assistant window now opens offline; its local answers (low stock, product and unit counts, out of stock) still work. Any other question is **not queued** (it spends AI credits and answers from the data at that moment) and **not answered**: the question goes back in the box with "You're offline, so this question has not been answered…". AI insights, margin advice and invoice scan say "Cauldra's AI has not run, and nothing was sent" / "Nothing was uploaded or scanned". No on-device model.
4. **Barcode.** The business's own products are matched offline as before; the provider look-up is skipped and says "No internet, so the barcode could not be looked up… the barcode is kept, so enter the details yourself." Manual product creation offline is unchanged.
5. **Security email/SMS (recovery code, password reset).** Never queued (codes expire). Offline they now say so and keep what was typed.
6. Because `navigator.onLine` is unreliable in the Android WebView, a request that cannot reach Cauldra at all is treated as offline for the chat, barcode and recovery messages too.

**Deliberately unchanged / online-only:** Paystack payment (resumable by the existing PAY-001 design: server payment record, webhook, "check status / resume", "do not pay again"; never queued or simulated; no card data on the device); email verification and email change; WhatsApp PO hand-off; Price Monitor checks and price-list upload; profile-photo upload; invoice scan; push subscribe; Sentry (loaded only online in production; offline events are dropped by the SDK; Cauldra stores no diagnostics); FX (platform-panel only, cached server-side with its fetch time).

**Reconnect policy (po_email_send):** auto-send on reconnect; retry with backoff (8 attempts max) then Needs attention; op-id receipt plus provider idempotency key; Cancel until the send starts, Remove once held; not sent after 72 hours (STALE_ACTION, user re-sends); `po.send`, device grant, identity and auth version rechecked; no dependencies (the order must already exist on the server — offline-created drafts are G4, not built). Sign-out/removing offline data: the existing warning counts it as an unsynced change; a permission or account change quarantines the queue as before.
- Residual risk, stated: Resend honours an idempotency key for 24 hours. A duplicate is only possible if the provider accepted a send, the Cauldra transaction then failed, and the retry came more than 24 hours later.

**Tests.**
- Server `tests/test_offline_external_postgres.py` (simulated provider): **10/10**.
- End to end `tests/run_offline_external_e2e.js` with the test-only simulated provider `tests/fake_email_provider/` (loaded only when put on `PYTHONPATH` with `CAULDRA_TEST_FAKE_EMAIL_PROVIDER=1`; never deployed): **29/29**, repeated.
- Static `tests/test_offline_external.cjs`: all pass.
- Regressions: G1/G2 e2e 35/35; Business Day e2e 46/46; offline harness 30/30 (row 78); full Python/cjs sweep. The sweep caught one real regression (the offline check broke the UX-013 single-run test for AI insights); fixed in `c6c4bfd`. Every other failing suite fails on exactly the same tests at `904f904`: business_day, infrastructure, mutation idempotency, Paystack webhook atomicity, REC-001 postgres, refund state, registration atomicity, rejected checkout, sale pricing, checkout atomicity, Sentry monitoring, transaction count; in-app payments and location authority need `DATABASE_URL` (environment); historical COGS times out (as in the earlier baseline); `test_native_bundle` needs a prepared `www` bundle.

**External offline capability matrix** (Class: A queueable, B cacheable, C resumable, D local fallback, E must wait).

| Workflow | Provider | Before | Class | Now? | Offline UX after | Reconnect | Security | Config | Launch |
|---|---|---|---|---|---|---|---|---|---|
| Purchase-order email | Resend | failed offline | A | Yes | Waiting to send; Cancel; sent only after acceptance | Auto ≤72 h; 8 tries then Needs attention | po.send, draft fingerprint, row lock, op id + provider key, sealed | existing `RESEND_*` | Critical — done |
| Purchase orders list | first-party | "load failed" | B | Yes | Using saved data from … | refresh | sealed snapshot | none | done |
| PO via WhatsApp | wa.me / WhatsApp app | online only | E | No | hidden offline | manual | user attests send | none | later |
| AI assistant chat | Gemini/OpenAI | window blocked | D + E | Yes | local answers; others not answered, question kept | manual resend | no queue (credits, stale data) | server keys | done |
| AI insights / margin advice | Gemini | error | E | Yes | "AI has not run" | manual | — | server keys | done |
| Invoice scan (upload + AI) | storage + Gemini | blocked/error | E (C later) | Yes | "nothing uploaded or scanned" | manual | would need sealed file store | storage + AI | later |
| Price-list upload, Price Monitor checks | storage / websites | blocked | E (A later) | No | internet required | manual | — | — | later (G8) |
| Profile photo | storage | error | E (A later) | No | error, nothing uploaded | manual | — | storage | later |
| Barcode: own products | first-party | worked | D | unchanged | matched offline | — | — | — | done |
| Barcode: provider look-up | General Catalog + UPCitemdb | "lookup failed" | E + D | Yes | not looked up; barcode kept; manual entry | manual | — | UPCitemdb | done |
| Recovery code, password reset | Resend / Termii / Twilio | generic error | E | Yes (message) | offline, nothing sent, input kept | manual | never queued (expiry) | existing | done |
| Email verify / change, onboarding verify | Resend | blocked | E | No | internet required | manual | security email | existing | as is |
| Paystack checkout / upgrade / trial / card | Paystack | blocked (Billing) | C | No (existing PAY-001) | internet required; resume or check status online | manual | never queued; no card data; new intent if expired | Paystack keys | as is |
| Paystack webhook | incoming | server | n/a | — | not a device concern | server | server idempotency | — | — |
| Push delivery | web push (VAPID) | server | server-owned | — | notification created when the change syncs; pushed once | server | — | VAPID keys | as is |
| Push subscribe | browser push service | error | E | No | — | manual | — | — | later |
| Sentry (frontend) | Sentry CDN | not loaded offline | SDK | No | never blocks; offline events dropped | — | no stored diagnostics | DSN | as is |
| FX USD→NGN | Frankfurter | server-side cache | B (server) | — | platform panel only | server | — | — | — |
| Business Brain | first-party (no AI provider) | not shown offline | B | No | — | — | — | — | later (first-party) |

No analytics SDK and no outgoing webhooks exist in the code.

**Launch-critical external gaps still open:** none in code. Open verification: QA deploy, one APK, the Android pass below, and one owner-approved real-provider email check (parked: cloud may not send real email).

**ONE combined local handoff — all offline work since `704b843`.**
- Final head: the docs commit that adds this section, directly after `c6c4bfd` on `remediation/batch-e-ai-forecast-brain`.
- Commits: `a3609a6`, `95b8470`, `bc585ab` (docs), `7c84a82`, `45e5f54`, `be2504e` (docs), `3ee9e8d`, `dfb74fd`, `904f904` (docs), `0dd4092` (server), `c8b7674` (client), `c6c4bfd` (fix), and this section's docs commit.
- Migrations: none since `704b843` (latest remains `0043_ops_accuracy_telemetry`). Variables: none new (`CAULDRA_OFFLINE_SIGNING_KEY`, `RESEND_API_KEY`, `RESEND_FROM` already exist on QA; do not set any `CAULDRA_TEST_*` variable on QA).
- Cache: `cauldra-shell-v17-offline-external`.
- Order: pull the final head once → `railway up --service cauldra-qa` once → check the startup database line (at head, no pending migration) and `/health` → build **one** QA APK (`build-apk.bat qa`) → one Android pass.
- New APK: **required**; supersedes `cauldra-qa-rc-704b843.apk` and every build in between.
- Android pass (English and Arabic; prove offline with airplane mode plus `dumpsys connectivity`):
  1. Row 78 sequence (§14.3), ending with the day closed offline before reconnecting.
  2. Business Day part 2 (§14.4 step 5): offline close of a synced and of an offline-opened day; force-stop while closed; a sale after the close opens a new day; B keeps selling after A's offline close → A's close held; no `business_day.manage` → no Open/Close, but a sale opens a day.
  3. G1/G2 (§14.5, seven checks).
  4. External: a synced PO draft offline → Send by email → "Waiting to send"; force-stop and reopen → still waiting; Cancel on a second one → never sent; reconnect → the first becomes Sent once. AI chat offline keeps the question, local answers work; AI insights and invoice scan say nothing ran; barcode offline keeps the barcode for manual entry; forgot-password offline says nothing was sent.
- Earlier Android results: the offline, Business Day and row 78 parts of the `704b843` run are superseded. Areas whose code did not change (native Paystack bridge, build-target and 0-production-request checks, NAT-002 gate wording, RESP-001 layout) keep their recorded status; smoke them on the new APK.
- External checks that stay manual (owner): one real Resend delivery of a queued PO email to an owner-controlled address on QA; Paystack TEST resume after a dropped connection; AI provider answers online.
- Clean-up: close the QA test day; delete test suppliers, purchase orders, products and sales per QA hygiene. Do not touch the 42 lost uploads or Tenant B.

**Records.**
- **Tracker:** `OFFLINE-EXTERNAL-001 — external-provider workflows offline-friendly (PO email queue; AI/barcode/recovery truthful offline; PO list readable) → FIXED (0dd4092, c8b7674, c6c4bfd) — awaiting QA + APK + Android`; G1/G2 and BUSINESS-DAY-OFFLINE-001 unchanged (awaiting the same combined pass); G3–G9 later offline work.
- **Triage:** add `OFFLINE-EXTERNAL-001`, launch-scoped (owner principle 2026-09-27), FIXED — awaiting QA/Android; later items: invoice-scan/price-list/photo queued upload, Business Brain offline, WhatsApp hand-off, push subscribe.
- **Retest log:** 2026-09-27 cloud: server 10/10, e2e 29/29, static pass, regressions as above; no QA deploy, no Android, no real provider call.
- **Manifest:** add all commits listed above after `704b843`; no migrations; no new variables; cache v17; test-only `tests/fake_email_provider/` must never be on a deployed `PYTHONPATH`.
- **Android checklist:** replace the separate offline lists with the combined pass above; mark every APK before this batch superseded.

### 14.7 LEGAL-001 final integration and owner decisions before RC freeze (2026-09-28, cloud session)

Starting head `99f0128`; QA deployment before this task `2cc30dc2-50b2-4015-81fa-fd4a744efb9b`. Code commit `7b422c6`. No migrations, no new variables, no subscription/payment/business logic change. Service-worker cache `cauldra-shell-v22-legal-final`.

**What changed.**
- `frontend/js/app.js`: the Terms of Service (23 sections) and Privacy Policy (20 sections) are the founder-approved launch copy of 2026-09-28. The draft banner (`legalDraftBanner`) and all 14 bracketed placeholders are removed. Operator `Cohren Limited`, address `No. 21, Amadi Close, Oyigbo, Rivers State, Nigeria`, governing law the Federal Republic of Nigeria, contact `contact@cohren.com` and the effective date (28 September 2026, the commit date; the app has no launch-date mechanism) are defined once and used by both documents. Documents stay English with the existing "available in English only" notice.
- `frontend/index.html`: "By continuing, you agree to these documents: Terms of Service · Privacy Policy" at every agreement point: before **Continue to Paystack** (new business, card verification and trial), at **Create Business & Admin Account**, in **Start 14-Day Free Trial**, and in the plan purchase/change confirmation. About → Legal is unchanged.
- Closing a document returns to About only when it was opened from About (before, it always opened About, which would cover the sign-up screen).
- Sentence catalogued for fr/es/ar/pt.

**Wording checked against the code.** 14-day trial (`PLAN_CONFIG` trial_days, all plans); renewal attempts every 12 h for 3 days (`RENEWAL_RETRY_INTERVAL`, `PAYSTACK_GRACE_PERIOD_DAYS` default 3); emails at 24 h and 5 h before expiry (`SUBSCRIPTION_EMAIL_PRE_STAGES`), after the first failed charge, at the end of the window and on renewal (`subscription_stage_emails`); no early same-plan purchase (`RENEWAL_NOT_DUE`); ₦50 refundable card verification separate from the subscription charge; card brand and last four stored (`card_type`, `card_last4`); Supabase, Paystack, Sentry, OpenAI and Google Gemini named. `tests/test_legal_documents.cjs` ties these to the code so a later billing change that contradicts the Terms fails.

**Tests (cloud).**
- `test_legal_documents.cjs`: ALL PASS; a planted `[ADDRESS TBC]` is caught.
- Every `tests/*.cjs`: pass, except `test_native_bundle.cjs` (needs Capacitor's synced `android/.../assets/public`; environmental, as before).
- Browser, local static server: both documents from the sign-up line and from About at 375, 768, 1024 and 1366 px: visible, inside the viewport, body scrolls, no horizontal overflow, address present, only `mailto:contact@cohren.com`; close returns to the right screen (32/32). Sign-up agreement line in English and Arabic at 375 px, no overflow.
- `build-www.js --target=qa`: parity PASS; the bundle contains the final text and the four agreement lines, no draft banner.
- Python: batch B 42, C 44, D 34, E 45, ops accuracy 32, pre-freeze policy 19: all OK.

**Not done from the cloud (no Railway CLI or token, no Android SDK or Capacitor here).**
1. QA deploy: pull `7b422c6` or later → `railway up --service cauldra-qa` once → startup database line and `/health`.
2. On QA (web, guest and signed in): open Terms and Privacy from About and from the sign-up screen; check no draft banner, the operator, address, law and contact; the mailto link opens a mail app.
3. Build one QA APK (`build-apk.bat qa`); record path, SHA-256 and size; on it, open both documents from About and from sign-up. This APK supersedes every earlier one.
4. Then mark LEGAL-001 closed.

**Owner decisions to record (already locked).**
- Row 62 (NAT-001, old-WebView naira display): accepted for launch.
- Row 64 (AND-002, system bars): accepted for launch; Capacitor's handling is not replaced.
- The ~1-second paused/restored subscription notice: accepted as non-blocking polish.
- Resend open/click tracking: must be confirmed **OFF** in the Resend dashboard before freeze. Not verified from this session (no authorized dashboard access); not claimed OFF.

**Records text.**
- **MASTER_REMEDIATION_TRACKER.md:** `LEGAL-001 (row 1) — final founder-approved Terms of Service and Privacy Policy (Cohren Limited; Nigeria; contact@cohren.com) → FIXED (7b422c6) — CLOSED once verified on QA and in the new QA APK. Professional legal review may improve the copy later.` Row 62 → accepted for launch (owner). Row 64 → accepted for launch (owner). Add: paused/restored ~1 s notice → accepted, non-blocking polish. Add: Resend open/click tracking OFF → open, owner dashboard check before freeze.
- **LAUNCH_TRIAGE_2026-09-24.md:** LEGAL-001 moves from "external" to "fixed, awaiting QA verification + APK"; rows 62 and 64 → owner-accepted; add the Resend tracking check as a freeze prerequisite.
- **REMEDIATION_AND_RETEST_LOG.md:** 2026-09-28 cloud: LEGAL-001 `7b422c6`; test results above; no QA deploy, no APK, no Android, no production.
- **PRODUCTION_ROLLOUT_MANIFEST.md:** add `7b422c6`; no migrations; no new variables; cache v22; legal copy effective 28 September 2026 (if the production launch date differs, the owner decides whether to change the effective date in a follow-up commit before rollout); Resend open/click tracking OFF on the production sender.

### 14.8 RC FREEZE — release candidate `7763a26` frozen (2026-09-28, cloud session, records only)

The owner's four permanent records were attached to this session and updated directly (MASTER_REMEDIATION_TRACKER, LAUNCH_TRIAGE_2026-09-24, REMEDIATION_AND_RETEST_LOG, PRODUCTION_ROLLOUT_MANIFEST); this section mirrors them.

- **Frozen commit:** `7763a26` (LEGAL-UI-001), the last intended RC commit. Cloud check: branch fetched; HEAD = origin = `7763a26`; working tree clean; no commit or uncommitted change after it. This section is a documentation-only commit on top; it changes no application code, CSS or test, so the frozen code is still `7763a26`.
- **Frozen QA deployment:** `5e9f973a-cf90-4969-97b4-074849e9101c` — SUCCESS; `/health` 200.
- **Migration head:** `0044_subscription_renewal_engine`, matching the code.
- **Frozen QA APK:** `cauldra-qa-rc-7763a26.apk` — SHA-256 `8421310071c4b82e2fb33936340208cabcd1d6f8cec9b8d0e74d121c1027684d` — 16,012,408 bytes; supersedes every earlier QA APK.
- **Verification (recorded local evidence; not rerun here):** QA web legal smoke 64/64; 34/34 JS test files on a clean export; `test_native_bundle` PASS after the build; 0 legal placeholders, no draft banner; LEGAL-UI-001 PASS; Resend Open Tracking OFF and Click Tracking OFF (owner-verified).
- **Closures preserved:**
  - Android/offline closure;
  - subscription Android closure;
  - subscription reminder email;
  - Paystack TEST renewal;
  - subscription pre-freeze policy;
  - Resend tracking closed;
  - legal web verification and LEGAL-UI-001.
  - Owner decisions: rows 62 and 64 accepted for launch; the ~1 s paused → restored notice accepted as non-blocking polish.
- **Open, non-blocking (owner decision), not PASS:** the Galaxy A23 native legal smoke on `cauldra-qa-rc-7763a26.apk`. Check: install; Terms and Privacy open and scroll; bullets visible; no draft banner or placeholders; operator, address and contact correct; governing law correct in the Terms; no clipping or horizontal scrolling. It is a pre-production / native-release item.
- **Freeze rule:** no new features, refactors or unrelated polish. Any code change after `7763a26` breaks the RC and needs explicit re-verification.
- **Production and `main`:** untouched (`origin/main` `4195d98`). No production preflight yet. No PR, no tag.
- Superseded by local evidence (do not reopen): the §14.6 / §14.7 "awaiting QA + APK + Android" and "Resend tracking to confirm" items.

### 14.9 PRICE-UPGRADE-001 — upgrade pricing and the change-plan bypass (2026-09-29, cloud session) — RC `7763a26` freeze deliberately broken

**Discovery.** Found in the read-only production preflight of 2026-09-29, against the frozen RC `7763a26`. Evidence: `PRODUCTION_PREFLIGHT_2026-09-29.md` and `PRICING_MATRIX_7763a26.md` (120 rows, 108 SUSPECT; kept unchanged as historical evidence).

**Root cause.**
- The quote charged `destination full-term price − unused credit`, but the paid upgrade kept the old paid-through time. The next renewal then billed the full destination price again.
- So the customer bought a full destination term but received only the rest of the old period. Example: Business Monthly → Enterprise Annual with hours left charged ≈ ₦2,099,919 for those hours.
- The ₦1 floor sold months of a higher tier from annual → monthly moves.
- Separately, `POST /subscription/change-plan` compared **list prices**, not plan rank. A paying Admin could therefore take a higher tier without paying whenever its price was not higher (Business Annual ₦200,000 → Enterprise Monthly ₦200,000; Starter Annual → Business Monthly; Premium Annual → Enterprise Monthly). It also allowed same-plan annual → monthly during the paid period.

**Model chosen (one): the upgrade payment starts a fresh destination period at Paystack confirmation.**
- **Formula**, in `upgrade_quote_amounts()`, the only implementation:
  - `fraction = remaining ÷ paid-period seconds` (clamped 0–1);
  - `credit = round(source price kobo × fraction)`;
  - `due = destination one-term price kobo − credit`.
- Kobo = naira × 100, applied once. No tax or fee is added.
- **Refused** (409 `UPGRADE_CREDIT_EXCEEDS_TERM`) when `credit > destination term − 100 kobo`. This is only possible annual → higher-tier monthly early in the year. The customer is offered the annual option, or a change at renewal.
- **On confirmed payment:**
  - `current_period_start` = Paystack `paid_at`;
  - `current_period_end` = `next_billing_at` = start + one destination term;
  - the renewal engine anchors there.
- **Why this model:**
  - It matches the locked rule that a late renewal starts at provider confirmation.
  - The quote already priced a full destination term, so only the period handling was wrong.
  - Credit and price are both plain naira values of service, so it is sound across monthly and annual.
  - The alternative (charge only the remaining fraction, keep the old date) would bill up to a year of monthly-rate service upfront for annual → monthly moves.

**Safety added.**
- **One checkout per quote.** A repeat request resumes the same Paystack checkout; the access code is now stored.
- **One open upgrade at a time.** A new quote first settles any earlier open upgrade checkout with Paystack. If it was paid, it is applied and the new quote is refused. Otherwise it is closed.
- **Reconciliation also requires:**
  - the record still open;
  - the record to be the quote's own checkout;
  - record amount = quote amount;
  - provider amount = record amount (a fee added on top ⇒ flagged).
- **Payment window `UPGRADE_PAYMENT_WINDOW` = 1 h** from the quote:
  - an open upgrade checkout is closed after it (as well as at the paid-through time);
  - a payment confirmed after it, or after the old paid-through time, is **flagged for review**, never applied with a stale credit.
- The quote API now also returns `amount_due_kobo`, the current period start and end, `unused_fraction`, and `new_paid_through_if_paid_now`.

**`change-plan` for a paying business.**
- Any higher rank → 402 (use the paid upgrade).
- Lower rank → 409 (schedule the downgrade), as before.
- Same plan, pricier interval → 402, as before.
- Same plan, other interval while the paid period runs → 409 `INTERVAL_CHANGE_AT_RENEWAL`.
- Trials are unchanged (free switch).

**Frontend.**
- An upgrade opens the confirmation dialog, which fetches the server quote and shows:
  - the destination price;
  - the credit;
  - "You pay now" = `amount_due_kobo`, exactly;
  - the new paid-through if paid now.
- Confirm starts `CauldraPayments.start('upgrade', {quote_reference})` for that quote only. A refused quote shows the server's reason and offers no payment.
- Six new sentences plus the reworded upgrade body, in fr/es/ar/pt. Service-worker cache `v24-upgrade-quote`.

**Files.**
- `backend/main.py`;
- `frontend/js/app.js`, `frontend/index.html`, `frontend/sw.js`;
- `i18n/launch_catalog.json` and the regenerated `frontend/js/i18n-catalog.js`;
- new `tests/test_price_upgrade_001.py`, `tests/test_price_upgrade_ui.cjs`, `scripts/price_upgrade_matrix.py`.
- No migration (head stays `0044_subscription_renewal_engine`). No new environment variable. No Paystack plan or subscription object.

**Verification in this cloud session.**
- `tests/test_price_upgrade_001.py`: 24 tests + 103 subtests PASS. Against `7763a26` the same file fails: 17 tests and 102 subtests, including the three `change-plan` bypass subcases.
- `tests/test_price_upgrade_ui.cjs`: 15 checks PASS, browser included; it fails on `7763a26`.
- Post-fix matrix (`PRICING_MATRIX_PRICE-UPGRADE-001_POSTFIX.md`): 120 rows, **120 PASS, 0 SUSPECT**, including 12 expected refusals.
- **Regression.** All 57 Python test files were run one process per file, on this commit and on `7763a26` side by side (local PostgreSQL 16).
  - The only difference is the new PRICE-UPGRADE-001 file.
  - Subscription/billing suites all PASS: lifecycle 21, pre-freeze policy 19, renewal engine 32, trial cancellation 8, trial-expiry calendar 10, batch C billing/staff 44, Paystack verification (Postgres) 3, webhook atomicity (Postgres) 2, email/Paystack closure 4.
  - With a migrated database, `test_inapp_payments` passes 22/23; the one failure (onboarding email-verification setup) fails identically on `7763a26`.
  - The failures that remain are the same on `7763a26` and are this sandbox's environment:
    - Location currency reference data is missing ("no authoritative currency": business-day and sales Postgres suites);
    - one subprocess test runs with an empty environment;
    - Sentry and infrastructure checks.
- **JS.** All 35 JS test files PASS: 34 existing plus the new UI test, with the browser tests on Chromium via Playwright 1.56. That includes `test_native_bundle` after `build:www:qa` + `cap sync android`, and `test_legal_documents`.

**Not done here (sandbox limits: `cauldra-qa.up.railway.app`, `api.paystack.co` and `checkout.paystack.com` are blocked by the network policy; there is no Android SDK; the Railway connector cannot deploy local code or change the QA source branch):**
- the QA deploy;
- live QA and Paystack TEST verification;
- the QA APK and native checks.

**Status: FIXED IN CODE — NOT YET VERIFIED IN QA.** Do not freeze a new RC until the owner's QA run passes (runbook below).

**QA runbook (owner, local):**
1. Deploy the new commit to `cauldra-qa` (`railway up`, as for earlier RCs). Confirm the deployment is SUCCESS, `/health` returns 200, and the startup line reads "Database migration: 0044_subscription_renewal_engine (matches this code)".
2. On a QA business, Active **Business Monthly**:
   - Billing → Enterprise **Annual**. The dialog shows destination ₦2,100,000, the credit, and "You pay now". Paystack TEST shows the **same** amount.
   - Pay with the TEST card. The business becomes Enterprise Annual, with period start = the payment time and paid-through = one year later. Billing shows the next renewal there.
3. Repeat for:
   - Starter Monthly → Business Monthly (≈50% through);
   - one annual source → annual destination.
4. **Bypass.** As a paying Admin, call `POST /subscription/change-plan` with `{"plan":"enterprise","billing_interval":"monthly"}` from Business Annual. Expect **402**, with the plan unchanged. Same-plan annual → monthly: expect **409**.
5. **Refusal.** Premium Annual early in the year → Enterprise Monthly. The dialog shows the refusal; no checkout opens.
6. **Abandon / duplicate.**
   - Open an upgrade checkout and close it; request a new quote. The first is closed, and only one payment is possible.
   - After paying, press Check status or replay the webhook. The paid-through time does not move again.
7. **APK.** Build `cauldra-qa-rc-<commit>.apk` (the frontend changed) and record the SHA-256 and size.
   - Native: the upgrade dialog shows the quote; Paystack opens with the same amount; returning shows the new plan and period.
   - The A23 legal smoke can be done on this APK.

**Previous RC.** `7763a26` / QA `5e9f973a-…` / `cauldra-qa-rc-7763a26.apk` is **superseded as production candidate (PRICE-UPGRADE-001)** — kept as history, not deleted. The new RC is named only after the QA runbook passes.

**Unchanged and still open (not in scope):**
- production migrations 0038–0044;
- production storage variables;
- the main promotion shape;
- Paystack LIVE;
- Supabase Auth;
- production AI smoke tests;
- Android release signing and the production APK;
- the A23 legal smoke;
- the queued PO inbox receipt.

**Production and `main`:** untouched (`origin/main` `4195d98`, production deployment `44a12849`). No PR, no tag.

---

*Handoff prepared 2026-09-26 from `remediation/batch-e-ai-forecast-brain` @ `2aeec3f`. Documentation only — no product code, QA, `main` or production change.*
