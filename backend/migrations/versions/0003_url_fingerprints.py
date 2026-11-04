"""add url fingerprints for evidence retention

URL identity must outlive the fact bodies it describes, otherwise a run pays to
re-read articles whose text was already purged.

Revision ID: 0003_url_fingerprints
Revises: 0002_evidence_kind
Create Date: 2026-11-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_url_fingerprints"
down_revision: Union[str, Sequence[str], None] = "0002_evidence_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "url_fingerprints",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(255), nullable=False),
        sa.Column("url_key", sa.String(512), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "url_key", name="uq_url_fingerprint"),
    )
    op.create_index(
        "ix_url_fingerprints_company_id", "url_fingerprints", ["company_id"]
    )
    op.create_index(
        "ix_url_fingerprints_last_seen", "url_fingerprints", ["last_seen"]
    )


def downgrade() -> None:
    op.drop_index("ix_url_fingerprints_last_seen", table_name="url_fingerprints")
    op.drop_index("ix_url_fingerprints_company_id", table_name="url_fingerprints")
    op.drop_table("url_fingerprints")
