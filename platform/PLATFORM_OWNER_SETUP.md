# Cauldra Platform Owner Control Panel — setup & recovery

This is internal documentation for the person(s) operating Cauldra itself,
not for a customer business. It is not linked from anywhere in the app —
find it by opening this file directly.

## What a Platform Owner is (and isn't)

A **Platform Owner** is Cauldra-level access: platform-wide analytics across
every business, revenue, AI provider spend, alerts. It is a completely
separate account type from a **Customer Admin** (a business's own owner).

- Platform Owner accounts live in their own `platform_owners` table, with no
  `business_id` at all and no relationship to the `users` table.
- There is **no public sign-up**. The Control Panel's login screen has
  exactly two steps — email/password, then a 6‑digit MFA code — and nothing
  else. No "Sign Up", "Register", or "Create Platform Owner" link exists
  anywhere in it.
- The only way a Platform Owner account is ever created is by running
  `scripts/create_platform_owner.py` directly on the server, which requires
  the same access as reading the production database.

---

## 1. How to establish the first Platform Owner account

From the project root, with the real `.env` already in place:

```bash
venv/Scripts/python.exe scripts/create_platform_owner.py
```

It will ask for an email, a password, and then attempt to verify that email
(see §4). Run it once per person who needs Platform Owner access — each
person gets their own account and their own MFA device.

## 2. Which email address becomes the Platform Owner login

Whatever address you type in when the script asks `Platform Owner email:`.
Use an address **you personally control** and check regularly — it is not
tied to any customer business, any `users` row, or any existing Cauldra
account. Nothing prevents reusing an email that also happens to be a
Customer Admin's login elsewhere — the two systems don't overlap — but a
dedicated address is simplest to reason about.

## 3. How to set the initial password securely

The script prompts for it with `getpass` (nothing is echoed to the
terminal, and it is never written to any log file), asks you to confirm it,
and validates it against Cauldra's existing password-strength rule (the same
one every customer account uses — at least 8 characters, upper case, lower
case, and a number). The password is stored only as a bcrypt hash
(`hash_password()`, the same hashing used everywhere else in Cauldra) — the
plaintext is never written to the database, a file, or a log.

**The password is never hardcoded anywhere.** It only ever exists in your
terminal session while you type it.

## 4. How to verify the email

The script automatically attempts real, backend-verified email ownership
through Cauldra's existing Supabase Auth integration — the exact same
mechanism the customer email-verification and email-change flows already
use:

1. It sends a verification link to the address via Supabase.
2. It asks you to open that email and click the link.
3. Once you press Enter, it re-checks with Supabase (`_supabase_email_confirmed`)
   and only proceeds once Supabase itself reports the address confirmed.

If Supabase is **not configured** on this server at all (no `SUPABASE_URL` /
key set), the script tells you so plainly and asks you to explicitly type
`YES` to proceed without it — creating the account already required direct
server + database access, which is itself a strong control, but real
verification is recommended: configure Supabase and re-run for future
accounts.

Email verification is recorded (`platform_owners.email_verified_at`) for
audit purposes only. **It is never required to sign in** — password + MFA
already fully secure login, and requiring a Supabase-dependent flag at
login time would be able to lock out an owner on a server where Supabase
becomes unreachable.

## 5. How to enroll MFA

MFA is **mandatory** — there is no password-only Platform Owner login at
all (see `get_platform_owner()` / `platform_login()` in `backend/main.py`).
The script always provisions a TOTP secret (RFC 6238 — the same standard
Google Authenticator, Authy, 1Password, and Microsoft Authenticator all
support) and prints, at the end:

- The **manual entry key** (a short base32 string) — every authenticator
  app has an "Enter setup key manually" option that accepts this directly.
- The full `otpauth://` URI, if you'd rather generate a QR code from it
  (paste it into any QR generator, or some apps accept the URI directly).

Add it to your authenticator app before you try to sign in — the account
cannot complete login without a valid 6‑digit code from it.

## 6. How to access the private Platform Owner login route

The Control Panel is served at an **unlisted** path — never linked from the
public Cauldra app, never shown to a customer, never mentioned anywhere a
regular user could find it:

```
https://<your-domain>/cauldra-ops-9182
```

(The exact path is printed by the setup script every time it runs, and is
controlled by the `PLATFORM_PANEL_PATH` environment variable — change it at
any time, no code change or redeploy of anything else required, and no
regeneration of any account needed.)

**This URL is convenience, not security.** Every `/api/platform/*` request
independently re-verifies a Platform-Owner-scoped bearer token — reaching
this page does nothing for someone without valid Platform Owner
credentials and a working MFA device; they'd see the same login screen a
customer's Admin, Manager, or Staff account would (and their token, even if
they had one, is cryptographically rejected — see `get_platform_owner()`'s
docstring in `backend/main.py`).

Bookmark it once you have it. Never publish it, put it in a support macro,
or paste it anywhere a customer or their staff could see it.

## 7. How to sign in after setup

1. Open the private URL from §6.
2. Enter the Platform Owner email and password.
3. Enter the current 6‑digit code from your authenticator app.
4. You land on the Control Panel's Overview page.

Sessions last 60 minutes by default (`PLATFORM_OWNER_ACCESS_TOKEN_MINUTES`)
and are held only in the browser tab's `sessionStorage` — closing the tab,
or the 60 minutes elapsing, means signing in again. "Sign out" (the icon
next to your email in the sidebar) invalidates that session immediately,
server-side, even if the token hasn't expired yet.

## 8. How to recover access securely if the password or MFA device is lost

There is **no self-service "forgot password" email flow** for Platform
Owner accounts — for something this sensitive, recovery deliberately
requires the same trust level as creating the account in the first place:
direct access to the server and its database. From the project root:

```bash
venv/Scripts/python.exe scripts/create_platform_owner.py --recover
```

It will:

1. Ask for the account's email and show you its id / creation date / last
   login so you can confirm it's the right one.
2. Require you to type `RESET` to continue (an explicit, hard-to-fumble
   confirmation).
3. Let you reset the password, the MFA device (a fresh TOTP secret — you'll
   enroll it in your authenticator app exactly as in §5), or both.
4. **Immediately invalidate every existing session** for that account
   (`platform_auth_version` is bumped, so any token issued before the
   reset — lost, stolen, or otherwise — stops working the instant recovery
   completes), and re-enable the account if it had been disabled.
5. Write a permanent record to the Control Panel's own internal audit log
   (`platform_audit_logs`) noting what was reset and that it happened via
   the server CLI — so a later review of that account's history shows the
   recovery event, not a silent gap.

If you no longer have server access at all, that is the same situation as
losing access to the database itself — restore server/infrastructure
access first through your normal hosting-provider recovery process, then
run `--recover`.

---

## Reference: what each Control Panel section shows

| Section | Answers |
|---|---|
| Overview | Headline numbers only — businesses, users, active users, revenue this month, AI spend this month, unresolved alerts. |
| Businesses | Every registered business, searchable/filterable, with plan, status, users, last activity, lifetime revenue, AI credits and cost. Click through for full detail + its user roster. |
| Users | Every individual account across every business, with activity/growth summary and filters by role/business/active-now. |
| Subscriptions | Real subscription counts by status and by plan — no invented statuses. |
| Revenue | Real collected revenue only (successful, non-verification payments) — This Month / 6 Months / 1 Year / All Time / Custom, by plan, by business, and true all-time total. |
| AI & Costs | Cauldra AI credits (a product allowance) shown **separately** from real provider ($) cost, per provider and per feature — plus editable provider pricing and internal monitoring budgets. |
| Alerts | Platform-level alerts (e.g. an AI provider crossing 75/85/95% of its configured budget) — separate from customer notifications. |
| System Health | Database status, failed AI/payment counts, webhook activity, unresolved critical alerts — a summary, not a raw log viewer. |
| Infrastructure | Only what Cauldra's own database can verify (storage used, configured AI providers) — never a scraped or invented Railway/Supabase metric. |
