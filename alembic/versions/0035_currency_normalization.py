"""Currency canonicalization (FINAL 3-DEFECT CLOSURE PASS, defect #3).

currency_snapshot (migration 0034) and Location.currency are documented as
holding a canonical ISO 4217 CODE ("NGN", "USD", "GBP") — never a
decorated display string. That contract was not actually enforced at the
time 0034 ran: its own backfill copied `locations.currency` /
`business_profile.currency` verbatim, and those columns can contain
COUNTRY_CONTEXTS-style decorated values like "NGN (₦)" (registration and
Location create/update stored whatever the client sent, unvalidated,
before this pass). This migration is intentionally a NEW migration rather
than an edit to 0034 itself — 0034's deployment state elsewhere is not
known with certainty, so its already-shipped behavior is left alone and
corrected forward here instead.

Canonicalization rule (general, not a lookup table of specific
currencies — this app supports 190+ countries/currencies via
COUNTRY_CONTEXTS): take the leading run of letters, uppercase it, and
accept the result ONLY if it is exactly 3 letters (not immediately
followed by a 4th) — the one shape every real ISO 4217 code shares.
"NGN (₦)" -> "NGN", "usd ($)" -> "USD", "GBP" -> "GBP" (unchanged),
"Naira" -> left as-is (5 letters, does not resemble a code — never
invented). This exact rule is what backend/main.py's
normalize_currency_code() implements in Python for every write path
going forward; this migration applies the equivalent as a one-time SQL
backfill so already-stored data matches what new writes will produce.

Scope, per this pass's own instructions (section 25/31 — do not widen
beyond the 3 verified defects):
- locations.currency: canonicalized in place (this is the Location
  record itself, not a historical financial snapshot — correcting it is
  safe and is what makes every future currency_snapshot correct at the
  source, since every write path reads FROM Location.currency).
- sales.currency_snapshot, expenses.currency_snapshot,
  refund_transactions.currency_snapshot, purchase_orders.currency_snapshot:
  re-canonicalized. This does NOT change what currency any historical
  record was actually denominated in — it only cleans the STRING
  representation of that same currency (a decorated "NGN (₦)" and a bare
  "NGN" name the exact same currency), so this is a formatting
  correction, never a reinterpretation of financial history.
- business_profile.currency is intentionally left untouched here — it is
  a separate, pre-Location legacy field still read directly by other
  flows (registration, Business Profile settings) that this pass's three
  named defects do not cover; touching it is exactly the kind of
  unrelated-system change section 31 says to leave alone.

Where a stored value does NOT resemble a 3-letter code at all (garbage,
blank-but-not-null, or already something unresolvable), it is left
UNCHANGED — never invented, never NULLed destructively by this migration
(NULLing a value a business may still be relying on for display, even if
imperfectly formatted, would be a worse regression than leaving it as
found; the write paths going forward already guarantee no NEW garbage
can enter).

Revision ID: 0035_currency_normalization
Revises: 0034_currency_snapshots
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_currency_normalization"
down_revision = "0034_currency_snapshots"
branch_labels = None
depends_on = None

# The canonical-3-letter-prefix pattern, expressed once and reused for
# every column — PostgreSQL's ARE regex engine supports the lookahead
# (?!...) used here (never match a 4th consecutive letter as part of the
# code), mirroring normalize_currency_code()'s Python regex exactly.
_CANONICAL_PREFIX = r"^[A-Za-z]{3}(?![A-Za-z])"

# The same known-currency allowlist backend/main.py's normalize_currency_code()
# checks against, derived once from COUNTRY_CONTEXTS (190+ countries) and
# materialized here since a migration cannot import the running app.
# Shape alone is not enough (an unrelated 3-letter word can coincidentally
# match), so this migration only ever rewrites a value to a code that is
# ALSO a real currency this app recognizes — anything else is left
# unchanged rather than risking a wrong "correction".
_KNOWN_CURRENCY_CODES = [
    'AED', 'AFN', 'ALL', 'AMD', 'AOA', 'ARS', 'AUD', 'AWG', 'AZN', 'BAM', 'BBD', 'BDT', 'BGN', 'BHD', 'BIF',
    'BMD', 'BND', 'BOB', 'BRL', 'BSD', 'BWP', 'BYN', 'BZD', 'CAD', 'CDF', 'CHF', 'CLP', 'CNY', 'COP', 'CRC',
    'CUP', 'CVE', 'CZK', 'DJF', 'DKK', 'DOP', 'DZD', 'EGP', 'ERN', 'ETB', 'EUR', 'FJD', 'FKP', 'GBP', 'GEL',
    'GHS', 'GIP', 'GMD', 'GNF', 'GTQ', 'GYD', 'HKD', 'HNL', 'HTG', 'HUF', 'IDR', 'ILS', 'INR', 'IQD', 'IRR',
    'ISK', 'JMD', 'JOD', 'JPY', 'KES', 'KGS', 'KHR', 'KMF', 'KPW', 'KRW', 'KWD', 'KYD', 'KZT', 'LAK', 'LBP',
    'LKR', 'LRD', 'LYD', 'MAD', 'MDL', 'MGA', 'MKD', 'MMK', 'MNT', 'MOP', 'MRU', 'MUR', 'MVR', 'MWK', 'MXN',
    'MYR', 'MZN', 'NGN', 'NIO', 'NOK', 'NPR', 'NZD', 'OMR', 'PAB', 'PEN', 'PGK', 'PHP', 'PKR', 'PLN', 'PYG',
    'QAR', 'RON', 'RSD', 'RUB', 'RWF', 'SAR', 'SBD', 'SCR', 'SDG', 'SEK', 'SGD', 'SHP', 'SLE', 'SOS', 'SRD',
    'SSP', 'STN', 'SYP', 'SZL', 'THB', 'TJS', 'TMT', 'TND', 'TOP', 'TRY', 'TTD', 'TWD', 'TZS', 'UAH', 'UGX',
    'USD', 'UYU', 'UZS', 'VES', 'VND', 'VUV', 'WST', 'XAF', 'XCD', 'XOF', 'XPF', 'YER', 'ZAR', 'ZMW',
]


def _canonicalize_column(bind, table: str, column: str) -> None:
    # Naturally idempotent: after the first run every matching row's value
    # already equals its own canonicalized form, so the WHERE clause's
    # inequality check no longer matches it on a second run.
    bind.execute(sa.text(f"""
        UPDATE {table}
        SET {column} = upper(substring({column} from :pattern))
        WHERE {column} IS NOT NULL
          AND substring({column} from :pattern) IS NOT NULL
          AND upper(substring({column} from :pattern)) = ANY(:known_codes)
          AND {column} <> upper(substring({column} from :pattern))
    """), {"pattern": _CANONICAL_PREFIX, "known_codes": _KNOWN_CURRENCY_CODES})


def upgrade() -> None:
    bind = op.get_bind()
    _canonicalize_column(bind, "locations", "currency")
    for table in ("sales", "expenses", "refund_transactions", "purchase_orders"):
        _canonicalize_column(bind, table, "currency_snapshot")


def downgrade() -> None:
    # Canonicalization is not meaningfully reversible (the original
    # decorated string is not recoverable from the canonical code alone,
    # and re-decorating would be inventing formatting that may not match
    # what was originally stored) — a no-op, matching how this repo
    # treats other one-way data-cleanup migrations. The columns
    # themselves are untouched (no schema change here to revert).
    pass
