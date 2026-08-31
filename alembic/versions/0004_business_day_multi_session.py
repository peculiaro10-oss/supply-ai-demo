"""Business Day multi-session support.

A business may now have MULTIPLE Business Day sessions on the same
business-local calendar date (e.g. opened 8:00 AM, closed 1:30 PM, then a
brand-new session opened 2:15 PM the same day) — each is its own row with
its own id, never merged or reused. See main.py's BusinessDay model
docstring for the full rationale.

This makes the old UNIQUE(business_id, date) constraint (added in
0002_business_day_integrity) actively wrong — it would reject the second
session opened on the same date. It is replaced with the real invariant the
system actually needs: at most one ACTIVE session per business at a time.
That is enforced with a PARTIAL unique index on (business_id) that only
applies WHERE is_open is true — any number of CLOSED rows for the same
business/date are fine (each is a distinct historical session), but two
rows with is_open=true for the same business can never coexist.

Pre-flight data repair (discovered by live audit before writing this
migration, not guessed): production has at least one business with SEVERAL
BusinessDay rows simultaneously marked is_open=true (created before the
Business Day audit-log/lifecycle feature existed, so there is no audit
trail for how they were opened). The old UNIQUE(business_id, date)
constraint never prevented this — it only blocked duplicate DATES, not
duplicate ACTIVE rows. Creating the new partial unique index below would
fail outright against that pre-existing bad state, so upgrade() first
closes every such row except the most-recently-opened one per business —
this is a REPAIR of an already-invalid state (more than one "active"
Business Day was never a valid state under any version of this app's
business rules), not a new destructive action:
  - No row is deleted. No sales/expenses/audit data is touched.
  - Only is_open/status/closed_at are set on the superseded rows.
  - closed_at is set to the next chronologically-opened row's opened_at
    for that business (the most defensible inference available — "open
    until the next one started" — never a fabricated exact timestamp).
  - closed_by_id/name/role are left NULL: no actor performed this closure,
    and inventing one would misrepresent the audit trail worse than
    leaving it blank.
A business with only one is_open=true row (the normal, correct case) is
completely unaffected by this step.

This is otherwise purely a constraint change: no column is added or
removed, and no sale/expense/audit data is touched. Existing CLOSED rows
are completely unaffected other than which index polices future
inserts/updates.

Revision ID: 0004_business_day_multi_session
Revises: 0003_business_brain_invalidation
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_business_day_multi_session"
down_revision = "0003_business_brain_invalidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Repair pre-existing invalid multi-active-day state (see docstring)
    # BEFORE the new unique index is created, or its creation would fail.
    repaired = bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id, business_id, opened_at,
                       ROW_NUMBER() OVER (PARTITION BY business_id ORDER BY opened_at DESC) AS rn,
                       LEAD(opened_at) OVER (PARTITION BY business_id ORDER BY opened_at ASC) AS next_opened_at
                FROM business_days
                WHERE is_open = true
            )
            UPDATE business_days
            SET is_open = false, status = 'CLOSED', closed_at = ranked.next_opened_at
            FROM ranked
            WHERE business_days.id = ranked.id AND ranked.rn > 1
            RETURNING business_days.id, business_days.business_id
            """
        )
    ).fetchall()
    if repaired:
        print(f"[0004 migration] Repaired {len(repaired)} pre-existing duplicate-active Business Day row(s) "
              f"(closed, never deleted) to satisfy the new one-active-session-per-business invariant: {repaired}")

    # Drop the old "one row per business per date" constraint — it is
    # incompatible with multiple sessions per date and must go first so the
    # new index below can be created without a name/definition clash.
    op.execute("DROP INDEX IF EXISTS ux_business_days_business_date")
    # The real invariant: at most one ACTIVE (is_open=true) session per
    # business at any moment. A partial unique index only constrains rows
    # matching its WHERE clause, so any number of closed sessions for the
    # same business (same date or different) are unaffected.
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_business_days_one_active ON business_days (business_id) WHERE is_open = true")


def downgrade() -> None:
    # Refusing to automatically restore UNIQUE(business_id, date): by the
    # time anyone downgrades, a business may legitimately have multiple
    # sessions on the same date (exactly the feature this migration adds),
    # and blindly recreating that constraint would fail outright against
    # real, valid data — or silently reintroduce the bug this migration
    # fixes if it were somehow made non-strict. Matches the baseline/
    # integrity migrations' own policy of refusing an automatic downgrade
    # that could not be done safely.
    raise RuntimeError(
        "Refusing to automatically restore the one-Business-Day-per-date "
        "constraint: businesses may now have legitimate multi-session days "
        "that would violate it. Resolve manually if a downgrade is truly "
        "required."
    )
