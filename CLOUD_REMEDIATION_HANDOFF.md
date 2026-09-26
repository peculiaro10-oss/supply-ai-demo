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

- **LEGAL-001, row 1:** Terms of Service and Privacy Policy are still drafts with 14 bracketed placeholders. Needs legal review and owner-supplied text; the text swap afterwards is small.
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

---

*Handoff prepared 2026-09-26 from `remediation/batch-e-ai-forecast-brain` @ `2aeec3f`. Documentation only — no product code, QA, `main` or production change.*
