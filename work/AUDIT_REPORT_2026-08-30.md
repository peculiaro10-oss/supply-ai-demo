# Cauldra complete audit, reconciliation, and safe test report

**Audit date:** 2026-08-30 (Africa/Lagos)  
**Scope:** current workspace at `C:\Users\DELL\Desktop\supply-ai`  
**Mode:** audit only; no production code/schema/data change, no migration application, and no real Paystack/AI/email/SMS operation  
**Final status:** **NOT SAFE**

## Executive decision

The current release is **not safe to deploy or treat as financially verified**. The strongest blockers are: a real database credential copied into `.env.example`; a configured PostgreSQL URL that cannot load with the installed driver; non-atomic inventory checkout and idempotency; non-atomic registration; a webhook event being marked processed before its effects succeed; first-time subscription activation without authoritative amount/currency/status verification; and 12 current Python dependency advisories across four packages.

This is the required audit-before-fix checkpoint. No fixes were applied. The findings below are proposed work only and require approval before implementation.

## A. System architecture map

| Layer | Runtime source | Responsibilities | Persistence/external edges |
|---|---|---|---|
| Web/API | `main.py`, FastAPI/SQLAlchemy | auth, business modules, financial aggregation, subscriptions, Business Brain, uploads | PostgreSQL, filesystem uploads, Paystack, email/SMS, configured AI provider |
| Browser UI | root `index.html` plus `assets/` and `sw.js` | single-page UI, access token in memory, refresh cookie, IndexedDB business cache/offline outbox | 107 parsed request call sites; service worker caches shell/assets, not API responses |
| Native wrapper | `capacitor.config.json`, `package.json`, `www/` | Capacitor packaging | `webDir=www`; its frontend is not byte-identical to the served root frontend |
| Schema | 38 SQLAlchemy models; Alembic `0001` through `0006` | tenant/business data and indexes | configured live PostgreSQL revision `0006_performance_indexes` |
| Deployment | `Dockerfile`, `DEPLOYMENT.md`, `alembic.ini` | Python 3.12 image; explicit release migration; Uvicorn | `uvicorn main:app --host ${HOST} --port ${PORT}` |

## B. Runtime truth

- **Working directory:** `C:\Users\DELL\Desktop\supply-ai`.
- **Active app process at audit start:** none listening on port 8000. Process command-line enumeration was denied by the host, so no claim is made about unrelated processes.
- **Host Python:** `python` was not on PATH. Tests used a disposable Python 3.12 virtual environment created from Codex's bundled runtime, then removed.
- **Deployment entrypoint:** `Dockerfile` runs `uvicorn main:app`; the FastAPI object is `main.py`'s `app`.
- **Served frontend:** `main.py:61` resolves the root `index.html`; `main.py:7403` serves it. The root UI, not `www/index.html`, is the web runtime.
- **Static assets:** root `assets/`; service worker root `sw.js`.
- **Database source:** `.env` `DATABASE_URL`, effective redacted form `postgresql://[redacted]@aws-1-eu-west-1.pooler.supabase.com:5432/postgres`.
- **Configured database defect:** the scheme is `postgresql://`, which SQLAlchemy resolves through `psycopg2`; requirements install Psycopg 3 (`psycopg[binary]`) and not psycopg2. Normal configured startup/Alembic failed with `ModuleNotFoundError: psycopg2`.
- **Read-only database verification:** after normalizing only the driver prefix in the audit process to `postgresql+psycopg://`, connection succeeded as database/user `postgres`, schema `public`, search path `"$user", public, extensions`.
- **Alembic:** source head and live DB revision are both `0006_performance_indexes`; no pending source revision was identified.
- **Duplicate/stale runtime sources:** no second `main.py` or second migration tree found. Root `index.html` and `www/index.html` have different SHA-256 hashes; `www/` is stale relative to the served UI. Three old `work/audit_smoke*.db` files predated this audit and were not modified.
- **Git/checkpoint:** this directory is not a Git worktree. Because this phase made no production changes, no schema/data checkpoint was created. Existing backup material was only inventoried.

## C. Complete backend route inventory

The exhaustive 122-operation inventory is in `work/backend_route_inventory_2026-08-30.csv`. Each row includes method, path, source line/function, auth, role literals, request model/parameters, response declaration, statically detected tables, external integration, frontend callers, and one of the required coverage statuses.

Coverage distribution: **2 TESTED**, **20 PARTIALLY TESTED**, **100 NOT TESTED**. “PARTIALLY TESTED” means the route was exercised/referenced by a focused test or safe audit probe, not that every branch is proven.

## D. Complete frontend API inventory

`work/frontend_api_inventory_2026-08-30.csv` contains **107 request call sites / 96 unique method-endpoint templates** with file, line, enclosing function, method, query/body signals, response/error/auth behavior, backend match, and coverage status.

- 16 call sites: **PARTIALLY TESTED**.
- 91 call sites: **NOT TESTED**.
- Definite mismatch: `index.html:21196` calls `POST /subscription/checkout/confirm`; no backend route implements it.
- Dynamic `PATCH /users/${userId}/${action}` reconciles to the concrete `/enable` and `/disable` routes.

## E. Test coverage ledger

### Executed automated suites

| Suite | Passed | Failed | Skipped | Interpretation |
|---|---:|---:|---:|---|
| root `test_expenses.py` discovery | 14 | 1 | 0 | failure was Windows Application Control blocking `_sqlite3.dll`; not an app assertion pass/fail |
| `tests.test_infrastructure` | 6 | 2 | 1 | two isolation failures inherited `.env` `SUPPLY_AI_AUTO_CREATE_SCHEMA=false`; live PostgreSQL test skipped without `TEST_POSTGRES_URL` |
| `tests.test_auth_refresh_rotation` | 10 | 0 | 0 | refresh rotation/replay/revocation scenarios passed in isolated SQLite |
| `tests.test_business_day` | 32 | 0 | 0 | business-day lifecycle and uniqueness scenarios passed in isolated SQLite |
| **Total** | **62** | **3** | **1** | **66 executed** |

Additional checks:

- Python AST parse: 18 first-party `.py` files passed.
- JavaScript syntax: all 6 inline script blocks across root and `www` passed `node --check`.
- Asset reference resolution: 10 checked references in each frontend, zero missing.
- Current Python advisory scan: 12 advisories across `python-multipart 0.0.29`, `requests 2.32.5`, `starlette 0.50.0`, and `ecdsa 0.19.2`. This identifies dependency advisories, not proven exploitability in every application path.
- Safe browser: root load, guest start/register path, plan rendering, guest protected-action gate, refresh-without-cookie, and 390×844 horizontal-overflow check.
- Targeted isolated API: failed empty cart, arbitrary price, multi-line transaction count, and legacy NULL cost behavior.
- Live PostgreSQL: read-only connection/schema inspection only; no write or concurrent test.

### Major flow status

| Flow | Status | Evidence/limit |
|---|---|---|
| initial/public load and plan catalog | TESTED | in-app browser loaded UI; `/plans` returned 200 and rendered plan cards |
| guest protected-action behavior | TESTED | Add Product opened auth/register gate |
| registration/payment onboarding | PARTIALLY TESTED | static and unit inspection; no real/mocked complete Paystack registration transaction |
| admin/manager/staff login, refresh, logout | PARTIALLY TESTED | auth suites passed focused lifecycle; browser signed-in flows not repeated here |
| dashboard load | PARTIALLY TESTED | guest shell only; authenticated dashboard NOT TESTED in browser |
| products/inventory | PARTIALLY TESTED | existing API tests/static audit; no full browser CRUD |
| warehouses/suppliers/purchase orders | NOT TESTED | static contract/query inspection only |
| sale/business day | PARTIALLY TESTED | isolated API + business-day suite; no PostgreSQL concurrent sale |
| expenses/financial summaries | PARTIALLY TESTED | expense tests plus targeted legacy-cost probe; one test blocked by host control |
| alerts/audit/account actions | NOT TESTED | static only |
| billing/subscriptions | PARTIALLY TESTED | static audit found blockers; no real provider call |
| Business Brain/AI | NOT TESTED | no live provider call or current end-to-end role/browser test |
| uploads/download isolation | NOT TESTED | static only |
| password recovery | NOT TESTED | external email/SMS intentionally not called |
| team management | PARTIALLY TESTED | auth/user route static reconciliation; no full browser workflow |
| responsive UI | PARTIALLY TESTED | desktop and 390×844 shell; tablet and authenticated module layouts not tested |

## F. Findings table

| ID | Severity | Finding and evidence | Risk / root cause | Proposed smallest safe fix (not applied) |
|---|---|---|---|---|
| F-01 | Critical | `.env.example:17` contains an embedded DB username/password and is byte-for-byte the same URL as active `.env` | live credential disclosure through a template | rotate the DB credential immediately; replace example with an inert placeholder; search history/artifacts |
| F-02 | Critical | `main.py:5441-5463` reads stock, checks it, then decrements Python objects without row lock or conditional update | concurrent checkouts can both pass, oversell, or lose updates | PostgreSQL transaction with row locking/atomic conditional decrement; rollback all lines on conflict; concurrency test |
| F-03 | Critical | `main.py:7145-7148` commits `PaystackWebhookEvent` before applying the event | crash/error poisons idempotency; provider retry is discarded as already processed | process effect + inbox state atomically; store processing/failed/completed states; retry test |
| F-04 | Critical | first-time subscription branch `main.py:7231-7249` activates from webhook payload without comparing amount/currency/status to `PaymentRecord`, and no server transaction verification occurs | forged/misrouted/incorrect charge can activate access | fetch transaction server-side, compare reference/status/currency/amount/business/purpose, then atomically activate |
| F-05 | Critical | registration commits authorization consumption (`3066-3071`), business (`3094`), user (`3101`), and subscription (`3121`) separately | failure leaves consumed authorization or orphan/partial tenant | one DB transaction with rollback; defer external scheduling; recovery/idempotency tests |
| F-06 | High | `.env` uses `postgresql://`; Psycopg 3 is installed but psycopg2 is not | configured startup and Alembic fail | use documented `postgresql+psycopg://` form and add a configuration regression test |
| F-07 | High | frontend `index.html:21196` calls missing `POST /subscription/checkout/confirm` | paid callback UX/state cannot reconcile deterministically | implement authenticated server verification endpoint or remove call in favor of a proven webhook/status polling contract |
| F-08 | High | `main.py:2945-2958` only checks price is finite/positive; `5457` trusts submitted price. Isolated API accepted 0.01 against catalog retail 10/cost 5 | any sale-capable user can arbitrarily set financial price with no permission/audit rule | server-side price mode/discount policy and audit; explicit negotiated-price permission if required |
| F-09 | High | checkout/product/expense idempotency does “check then insert” without a DB uniqueness/claim constraint (`4089`, `5076`, `5418-5463`) | concurrent retries can duplicate mutations | tenant-scoped idempotency record or unique aggregate checkout key; transactional claim |
| F-10 | High | `ensure_open_business_day` runs at `5435` before empty cart validation at `5437`; isolated 400 response still created one day | rejected checkout mutates durable state | validate cart before any committed side effect; keep day creation in sale transaction |
| F-11 | High | `compute_financial_summary` counts `SaleModel.id` (`2262-2267`); each cart line is a row | multi-item checkout inflated transaction count (isolated two-line cart reported 2) | introduce checkout/transaction entity or count stable checkout reference with legacy handling |
| F-12 | High | legacy NULL cost uses current product cost (`2269`); isolated COGS changed 14 to 59 after product cost edit | historical profit is mutable; deleted product NULL cost becomes zero | one-time evidence-based cost backfill plus explicit unknown-cost handling; never silently use current price |
| F-13 | High | refund helper swallows all errors (`6455-6462`), but `6607-6609` and `6798-6801` always mark/announce refund | financial records and customer messaging can be false | persist refund pending/succeeded/failed from provider response; retry/reconcile |
| F-14 | High | offline `runSync` (`index.html:2944+`) replays with the current token and does not enforce queued `user_id`/auth version | same-business later user can execute and be attributed operations queued by another user | bind outbox to user/auth version; require explicit reassignment; server idempotency/audit identity |
| F-15 | High | root and `www/index.html` hashes differ; Capacitor serves `www`; package uses unpinned `latest` and placeholder app id | native app behavior/contracts can differ from web and builds are non-reproducible | deterministic sync/build step, lockfile/pinned versions, production app id, parity test |
| F-16 | High | current advisory scan found 12 advisories in four packages | known dependency attack surface; exploitability needs route-level triage | upgrade compatible dependency set, rerun tests/audit; evaluate unpatched `ecdsa` advisory or remove dependency path |
| F-17 | Medium | security middleware exists (`188-203`) but registration is commented (`205`); isolated GET lacked CSP, XFO, nosniff, referrer, permissions, HSTS | browser hardening absent | enable tested headers; remove CSP unsafe-inline by nonce/hash migration before strict enforcement |
| F-18 | Medium | `0001` and `0002` import current `main.Base` and run `create_all`; startup retains runtime schema mutation at `main.py:961+` | old migrations are non-reproducible and application startup can become a migration engine | freeze migration DDL; keep production auto-create false; move all evolution to Alembic |
| F-19 | Medium | test infrastructure inherits real `.env` auto-create flag | two tests fail before assertions and coverage is misleading | force isolated test configuration before importing app; assert test DB URL/schema setup |
| F-20 | Medium | deterministic N+1 queries: warehouses `1+N` (`3925-3932`); purchase orders `1+N` (`5307+`); price monitor `1+3N` (`6081+`); sales analytics `1+N` (`5689+`) | latency/query growth with tenant data | aggregate/join/select-in loading and pagination; add query-count tests |
| F-21 | Medium | business and user auth lookups use `.all()` then Python normalization (`2329+`, login paths) | full tenant/business scans on auth path | normalized indexed columns and direct DB lookup with tenant-scoped uniqueness |
| F-22 | Medium | sale commits before audit (`5472-5473`) | successful sale can remain without audit if second commit fails | write sale and audit in one transaction/outbox |
| F-23 | Low | several secondary lookups omit an explicit business predicate after a scoped parent (deletion request product, PO supplier, price-monitor relations, Brain product relations) | invariant drift could weaken defense-in-depth | add tenant predicate/join invariant and cross-tenant corruption tests |

## G. Schema / ORM / Alembic comparison

- **Models/tables:** all 38 ORM tables exist in live `public`; no extra application table beyond `alembic_version`.
- **Columns/nullability:** inspected live columns matched ORM names and nullability. PostgreSQL `TIMESTAMP` versus SQLAlchemy `DateTime` was normalized as compatible, not reported as a mismatch.
- **PK/FK/unique/check:** no mismatch found by the inspector comparison.
- **Defaults/type lengths/cascades:** inspected but not exhaustively normalized for every dialect expression; therefore PARTIALLY TESTED, not a universal equivalence claim.
- **Indexes:** live has Alembic-owned indexes not declared on ORM columns, including business/status/date indexes for requests, alerts, audit logs, expenses and sales, plus the partial unique active-business-day index. These correspond to revisions `0004`/`0006`; ORM metadata alone is not full schema truth.
- **Migration ancestry:** `0001_baseline_schema -> 0002_business_day_integrity -> 0003_business_brain_invalidation -> 0004_business_day_multi_session -> 0005_sale_cost_snapshot -> 0006_performance_indexes`.
- **Discipline defect:** `0001:16-17` and `0002:75-76` depend on current application metadata; they do not freeze historical schema.
- **Migration application:** none performed. A fresh dedicated PostgreSQL upgrade/downgrade smoke test was NOT TESTED because no dedicated test DB was supplied.

## H. API contract matrix

| Area | Backend/frontend status | Runtime status |
|---|---|---|
| public shell/plans | aligned | TESTED |
| auth/refresh/logout | aligned statically | PARTIALLY TESTED by focused suites and no-cookie browser refresh |
| products/stock/warehouses/suppliers/PO | aligned for parsed calls | PARTIALLY/NOT TESTED by route ledger |
| sales/business days/financials | aligned for parsed calls | PARTIALLY TESTED; semantic defects F-02, F-08, F-10-F-12 |
| team/account actions | dynamic enable/disable calls resolve to concrete routes | PARTIALLY TESTED |
| billing | one missing confirm operation | NOT coherent; F-03, F-04, F-07, F-13 |
| Business Brain/AI | parsed paths matched | NOT TESTED end-to-end |
| mobile wrapper | API source is stale `www/index.html` | NOT TESTED as a native build |

## I. Role permission matrix

| Actor/state | Frontend visibility | Backend enforcement | Status |
|---|---|---|---|
| guest | public hub/plans/auth; protected Add Product triggers auth gate | public routes only without token | PARTIALLY TESTED |
| admin | broad operational, team, billing, settings access | route-local admin checks plus authenticated tenant | PARTIALLY TESTED |
| manager | operational modules and some approvals; billing/account-owner actions restricted | mixed admin/manager checks | PARTIALLY TESTED; complete route matrix is in backend CSV |
| staff | operational sale/stock attention; elevated settings expected hidden | route-local staff/manager/admin checks | PARTIALLY TESTED |
| disabled user | no valid ongoing access expected | auth checks disabled/auth_version | PARTIALLY TESTED by auth suite, not browser |
| expired/past_due/cancelled | UI and API should gate by subscription state | subscription access helper/static checks | NOT TESTED across all states |

No role-escalation exploit was confirmed. Because authorization is distributed across route-local conditionals rather than a central declarative policy, the matrix remains partially verified.

## J. Tenant isolation results

No direct cross-tenant read/write exploit was confirmed. Static review found business predicates on primary path-ID mutations. Existing tests partially cover products, expenses, and business days. Warehouses, suppliers, purchase orders, sales, users, alerts, audit logs, subscriptions/payments, uploads, AI usage, and all Business Brain entity types were **NOT TESTED dynamically across two tenants**. F-23 records secondary lookup defense gaps.

## K. Auth / session results

- Refresh rotation/replay/revocation suite: 10/10 passed.
- Business-scoped admin/manager/staff focused infrastructure coverage exists, but two current runs failed at schema setup due test configuration, not auth assertions.
- Refresh cookie is HttpOnly/Secure/SameSite; access token is memory-based in the current frontend.
- Browser no-cookie refresh returned 204; logout/reload/multiple-tab/visibilitychange were not comprehensively repeated in this audit.
- Auth full-table normalization scans remain a performance/uniqueness concern (F-21).

## L. Sales / inventory results

- Single and multi-line isolated checkout succeeded; stock changed and cost snapshot was written.
- Arbitrary positive price was accepted (F-08).
- Empty cart returned 400 but opened a business day (F-10).
- Stock decrement has a PostgreSQL race (F-02); no concurrent live write test was authorized.
- Idempotency is not atomic (F-09).
- Warehouse stock decrement lookup lacks `business_id` in the immediate query and relies on product/warehouse invariants; add explicit tenant scoping.

## M. Financial consistency results

- Server aggregation is centralized and period/timezone helpers are present.
- Two-line checkout is counted as two transactions (F-11).
- Legacy NULL cost history changes when current product cost changes; isolated COGS 14 became 59 (F-12).
- New sales snapshot current cost and corrections are included in authoritative aggregation.
- Deleted-product/NULL-snapshot and all custom timezone boundaries were not exhaustively tested.

## N. Business day results

32/32 focused tests passed, including repeated calls, close/reopen/multiple-session and active uniqueness behaviors in SQLite. Live PostgreSQL contains the partial unique active-day index. PostgreSQL simultaneous-open behavior was **NOT TESTED**. Empty-checkout creation is a confirmed cross-flow defect.

## O. Expense results

14 of 15 discovered expense tests passed; one could not import SQLite because of Windows Application Control. Tenant scoping and financial effects are partially covered. Concurrent idempotency, browser CRUD, and PostgreSQL constraint behavior are NOT TESTED.

## P. Subscription / billing results

The plan catalog rendered, and static pricing sources reconcile to `PLAN_CONFIG`. Billing cannot be approved because webhook completion, first-time activation verification, registration atomicity, refund status, and callback confirmation are defective (F-03-F-05, F-07, F-13). No real provider call or customer write was attempted.

## Q. Business Brain / AI results

Models/routes cover predictions, recommendations, memories, seasonal patterns, relationships, evidence/confidence and financial intelligence. Static tenant/role filters are present. Current live-data correctness, insufficient-data behavior, usage accounting, failure accounting, provider response grounding, and browser presentation are **NOT TESTED** in this run.

## R. Concurrency results

| Scenario | Result |
|---|---|
| simultaneous sales checkout | NOT TESTED dynamically; static failure F-02 |
| simultaneous active-day creation | PARTIALLY TESTED via uniqueness logic/SQLite; PostgreSQL race NOT TESTED |
| refresh rotation collision | PARTIALLY TESTED; focused suite passed |
| repeated logout | PARTIALLY TESTED |
| repeated/double-click checkout | NOT TESTED; non-atomic idempotency found |
| concurrent product edits / stock updates | NOT TESTED; lost-update risk |
| duplicate webhook delivery | NOT TESTED dynamically; poisoned-event design found |
| repeated payment callback | NOT TESTED; missing confirm endpoint |
| duplicate upgrade attempt | NOT TESTED dynamically |
| duplicate onboarding verification | PARTIALLY TESTED statically; CAS exists but full registration is non-atomic |

## S. Performance results

Evidence-based query-growth findings are F-20. These are handler query counts from source: warehouse list `1+N`, PO list `1+N`, price monitor `1+3N` plus history, and sales analytics `1+N` day queries. Product list is capped at 500; sales export at 5000 and expense export at 2000, while several history/list paths are unbounded.

Isolated development logs observed root GET at 10-45 ms, no-cookie refresh at 6-358 ms, and service worker GET at 7-9 ms. These are not production benchmarks and no latency/SLA claim is made. No production EXPLAIN or write workload was run.

## T. Dead / duplicate code results

- No duplicate Python class/function/route definitions were found by AST.
- JS names such as `cleanup`, `onConfirm`, and `onCancel` were nested scopes, not confirmed global duplicates.
- Root and mobile frontends are conflicting copies, confirmed by hash (F-15).
- Runtime schema-compatibility code and mutable baseline migrations are legacy/conflicting migration mechanisms (F-18).
- “Dead code” was not declared solely from missing search hits; complete runtime reachability is NOT TESTED.

## U. SQLite / production legacy results

- Test suites use isolated SQLite and prove application behavior only for those paths.
- Production configuration targets PostgreSQL; production mode rejects SQLite.
- SQLite cannot prove partial-index/locking/concurrent-update semantics required by checkout and active-day invariants.
- Three old audit SQLite files already existed in `work/`; no evidence shows they are production data, and they were left untouched.
- Runtime `create_all`/manual migration compatibility code remains; production `.env` currently disables it, while `.env.example` enables it.

## V. End-to-end test matrix

| Test | Role | Frontend action | API | DB change | Expected | Actual | Result |
|---|---|---|---|---|---|---|---|
| guest initial load | guest | open `/` | GET `/`, refresh | none | shell loads | loaded; refresh 204 | TESTED |
| plan selection | guest | Get Started > Register | GET `/plans` | none | plans/intervals render | Core and pricing cards rendered | TESTED |
| protected Add Product | guest | click Add Product | none before auth | none | auth gate | register/auth gate shown | TESTED |
| mobile shell | guest | 390×844 load | GET `/` | none | no horizontal overflow | scroll width 385 <= 390 | PARTIALLY TESTED |
| empty checkout | authenticated test user | API probe | POST `/sales/checkout` | should be none | 400, no mutation | 400 and BusinessDay +1 | FAILED |
| negotiated price | authenticated test user | API probe | POST `/sales/checkout` | sale/stock | authorized server rule | 0.01 accepted without special permission/audit | FAILED |
| multi-line sale count | authenticated test user | API probe | checkout + summary | two line rows | one transaction | transaction_count 2 | FAILED |
| historical NULL cost | authenticated test user | API probe | summary/read after cost edit | product cost only | old COGS stable | 14 -> 59 | FAILED |
| auth refresh rotation | admin/manager/staff fixtures | test client | auth routes | refresh sessions/revocations | rotate/reject replay | focused suite passed | PARTIALLY TESTED |
| business-day lifecycle | authenticated fixtures | test client | business-day routes | day/request/audit rows | coherent lifecycle | 32 tests passed | PARTIALLY TESTED |
| live schema reconcile | auditor/read-only | none | direct DB inspection | none | ORM/migrations compatible | tables/columns/constraints compatible; indexes Alembic-owned | PARTIALLY TESTED |
| Paystack onboarding/activation | admin/guest | not invoked | billing routes/webhook | none in audit | verified atomic lifecycle | static blockers found | NOT TESTED |
| Business Brain | roles | not invoked | Brain routes | none in audit | grounded tenant output | not executed | NOT TESTED |

## W. Every file changed

No production/application file was changed. Audit artifacts created:

- `work/AUDIT_REPORT_2026-08-30.md`
- `work/backend_route_inventory_2026-08-30.csv`
- `work/frontend_api_inventory_2026-08-30.csv`
- `work/orm_inventory_2026-08-30.csv`
- `work/inventory_summary_2026-08-30.md`
- `work/build_audit_inventory.py`

The disposable venv, isolated browser DB, temporary test DB, server, and browser tab were removed/closed; port 8765 was confirmed not listening.

## X. Every migration created/applied

**None.** No schema write, migration creation, Alembic upgrade, or downgrade was performed. Live migration state was read-only inspected.

## Y. Every test created/updated

**None.** Existing tests were executed; no test source was changed. The inventory builder is an audit artifact, not an application test.

## Z. Untested items

- Dedicated PostgreSQL migration from base, downgrade, concurrent checkout, locks, isolation, and constraints: no dedicated writable PostgreSQL test database was authorized/provided.
- Real Paystack verification, webhook delivery, subscription scheduling, refunds, upgrades, renewal/grace/cancel states: real financial side effects prohibited; mocks do not yet cover the full contract.
- AI provider, email, SMS, PO dispatch, password recovery: external calls intentionally avoided.
- Full two-tenant dynamic suite for every module: absent from current tests and too risky to run against live data.
- Complete authenticated browser journeys for admin/manager/staff, multiple tabs, offline/reconnect, tablet/native builds, uploads, alerts, audit log, team/account actions, price monitor, PO and Brain: no safe seeded browser fixture currently exists.
- npm advisory scan/native package reproducibility: dependencies are `latest` and there is no committed lock/install state to audit exactly.
- Production timings/EXPLAIN: no production load or expensive query plan was executed.

## AA. Remaining risks

Until fixes are approved and verified, assume possible database credential compromise, duplicate/oversold inventory under concurrency, inconsistent tenant creation, incorrect payment activation/refund state, financially mutable history, incorrect transaction KPIs, stale native behavior, and exploitable dependency surface. The breadth of NOT TESTED routes and provider flows prevents a safe release claim even if the known defects were ignored.

## AB. Final system status

# NOT SAFE

This exact status is required by the confirmed Critical/High findings and the meaningful untested PostgreSQL concurrency, payment, tenant, external-provider, and authenticated-browser areas. The next phase should begin only after approval of a prioritized minimal-fix plan, starting with credential rotation, configured DB-driver correction, payment atomicity/verification, and transactional stock/idempotency.
