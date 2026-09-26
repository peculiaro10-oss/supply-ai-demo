"""Recompute every Business Brain once with the corrected forecast (BRAIN-001).

Stored 7-day forecasts, "Prepare for demand" recommendations and confidence
scores were produced by the old formula (a per-business-day rate times 7
calendar days, and a confidence score that reached "High" on history alone).
This marks every business's Business Brain as needing a refresh, so the next
read recomputes it with the corrected code. It changes no figures itself and
deletes nothing; forecasts already evaluated keep their recorded outcomes.

Safe to run before or after the code deploy, and more than once (MIGR-001
style): setting the flag again is harmless.

Revision ID: 0042_business_brain_forecast_recompute
Revises: 0041_general_catalog_category_retired
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_business_brain_forecast_recompute"
down_revision = "0041_general_catalog_category_retired"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("business_profile")}
    if "business_brain_dirty" in columns:
        op.execute("UPDATE business_profile SET business_brain_dirty = TRUE")


def downgrade():
    # Nothing to undo: the flag only asks for a recompute.
    pass
