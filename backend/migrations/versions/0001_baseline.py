"""create baseline tables

Establishes the schema as it existed before versioned migrations were
introduced, so a fresh database can be built from empty with
``alembic upgrade head``.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JsonColumn = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "briefings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("meeting_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meeting_purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("report", JsonColumn, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "evidence_facts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("publish_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "content_hash", name="uq_evidence_company_content"),
    )
    op.create_index("ix_evidence_facts_company_id", "evidence_facts", ["company_id"])

    op.create_table(
        "evidence_coverage",
        sa.Column("company_id", sa.String(255), primary_key=True, nullable=False),
        sa.Column("searched_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("evidence_coverage")
    op.drop_index("ix_evidence_facts_company_id", table_name="evidence_facts")
    op.drop_table("evidence_facts")
    op.drop_table("briefings")
