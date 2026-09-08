# Fresh onboarding email verification

New-business onboarding creates a 256-bit random challenge. Its server record
binds the normalized email, selected plan, billing interval, platform, HTTPS web
return or registered native return, timestamps and status. Pending proof lasts
20 minutes; verified proof can start payment for 30 minutes. Changing any bound
value requires a new challenge. Resend rotates pending challenges and retains
provider cooldown plus Cauldra's existing rate limiter.

Supabase receives an S256 PKCE challenge with each OTP/magic-link request. The
verifier is derived using the backend secret and the unique challenge ID. The
HTTPS callback submits the returned authorization code to FastAPI, which
exchanges it with Supabase using that challenge's verifier and checks the
returned confirmed email. Global confirmation, an old access token, URL flags,
and polling without a code cannot verify a pending challenge. Provider access
and refresh tokens are discarded, never returned to the frontend.

The callback page routes using the persisted platform and return target only.
Web returns to the configured HTTPS frontend. Android uses the registered
`cauldra://auth/email-verified` scheme after confirmation, with an Open Cauldra
button and readable browser fallback. The App plugin handles warm `appUrlOpen`
and cold `getLaunchUrl` events. It validates the scheme, host, path, purpose and
challenge syntax, then obtains status from the backend. A deep link cannot
grant verification. No claim of operating-system dispatch is made without a
device test. No iOS project exists; see installation instructions before adding
the iOS target.

Payment initialization locks the verified challenge, checks its bound values,
then atomically consumes it and creates one linked authorization. A repeated
idempotency key returns the existing reference for status recovery; a different
payment attempt cannot reuse the proof. Registration requires that consumed,
verified evidence and checks challenge email = authorization email = owner
email. Existing unbound authorizations must restart onboarding; they are not
backfilled with fabricated evidence. Already-created businesses are unaffected.
Paystack's transaction, currency, amount, customer and reusable-card checks stay
in place. A consumed proof is retained as evidence; registration remains bounded
by the existing authorization consumption window.

Authenticated profile self-verification and email change keep their existing
endpoints and Supabase checks, with explicit `self_verify` and `email_change`
return markers. A new onboarding callback cannot complete a profile change.

Native refresh cookies use Secure, HttpOnly and SameSite=None only for the
exact native origins that are also configured in the CORS allowlist. Other
requests keep the existing cookie policy. Refresh rejects an untrusted Origin.
The existing Android CookieManager acceptance and lifecycle flush remain in
place. No refresh credential is placed in JavaScript storage. Real WebView
restart persistence still requires the device checklist.

## Protocol references

- [Supabase PKCE](https://supabase.com/docs/guides/auth/sessions/pkce-flow)
- [Supabase OTP request fields](https://github.com/supabase/auth/blob/master/internal/api/otp.go)
- [Supabase magic-link handling for new and confirmed users](https://github.com/supabase/auth/blob/master/internal/api/magic_link.go)
- [Supabase code exchange](https://supabase.com/docs/reference/python/auth-exchangecodeforsession)
- [Capacitor App events](https://capacitorjs.com/docs/apis/app)
- [Android CookieManager](https://developer.android.com/reference/android/webkit/CookieManager)

The pinned Python Auth SDK's OTP method does not forward PKCE fields. The
onboarding-only adapter therefore calls the same official Supabase Auth REST
endpoints with explicit PKCE fields, using the existing server-only settings.
Profile flows continue using the existing SDK client.
