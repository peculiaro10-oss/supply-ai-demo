"""Per-user profile fields: personal language, avatar, verified-email mirror.

Purely additive to `users`. No existing column, table, or row is modified, and
every existing user keeps working immediately because all four columns are
nullable with no backfill required:

- preferred_language: the INDIVIDUAL's chosen UI language. Deliberately
  separate from business_profile.language, which stays exactly as it is for
  business-document/default purposes — an Admin, a Manager and two Staff in the
  same business can now each use Cauldra in a different language at the same
  time. NULL means "this person has not chosen one yet", and the application
  falls back to the business language (and then the browser language, and then
  English) exactly as it did before this migration — which is why no backfill
  is performed: writing every existing user's language to the business value
  would turn an inherited default into a permanent personal choice.

- avatar_upload_id: id of a row in stored_uploads holding the person's profile
  photo. Intentionally a PLAIN INTEGER, not a foreign key: stored_uploads
  already carries an FK back to users (uploaded_by_id), so adding an FK in the
  other direction makes the two tables mutually dependent and breaks the
  metadata table-sort used by 0001's Base.metadata.create_all(). Ownership is
  enforced in the application on every read instead — the avatar lookup is
  always filtered by business_id (see GET /users/me/avatar).

- email_verified_at: a MIRROR of Supabase Auth's own verification state, which
  remains the single source of truth. Written only by trusted backend logic
  after Supabase itself confirms the address; never settable from a request
  body. NULL = not known to be verified.

- pending_email: an email change the user has requested but not yet verified.
  The trusted `email` column is left untouched until Supabase confirms the new
  address, so account recovery is never weakened by an unverified address.

Revision ID: 0017_user_profile_fields
Revises: 0016_sale_pricing_type
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_user_profile_fields"
down_revision = "0016_sale_pricing_type"
branch_labels = None
depends_on = None

# (name, type factory) — the factory is called per use so a fresh, unbound
# Column object is handed to op.add_column each time.
_COLUMNS = (
    ("preferred_language", sa.String),
    ("avatar_upload_id", sa.Integer),
    ("email_verified_at", sa.DateTime),
    ("pending_email", sa.String),
)


def _existing_columns(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns("users")}


def upgrade() -> None:
    # Idempotent, matching 0013's _add_column_if_missing pattern: a database
    # that already has one of these columns (e.g. created by an earlier
    # Base.metadata.create_all() run against a newer model) is left alone
    # instead of failing the whole release step.
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for name, coltype in _COLUMNS:
        if name not in existing:
            op.add_column("users", sa.Column(name, coltype(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    # Reverse order of creation; only drop what is actually there.
    for name, _coltype in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("users", name)
