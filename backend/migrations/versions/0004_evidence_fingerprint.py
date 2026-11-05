"""add evidence fingerprint for evidence-keyed report reuse

A brief is derived from its evidence, so a later request whose facts match is the
same brief whatever the meeting date. Without this column the cache could only
key on the request and missed whenever a meeting moved by a day.

Revision ID: 0004_evidence_fingerprint
Revises: 0003_url_fingerprints
Create Date: 2026-11-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_evidence_fingerprint"
down_revision: Union[str, Sequence[str], None] = "0003_url_fingerprints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "briefings",
        sa.Column("evidence_fingerprint", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_briefings_evidence_fingerprint",
        "briefings",
        ["evidence_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_briefings_evidence_fingerprint", table_name="briefings")
    op.drop_column("briefings", "evidence_fingerprint")
