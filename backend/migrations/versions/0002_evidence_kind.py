"""add evidence provenance column and opinion kind

Adds the provenance payload the UI reads beside the report, and the kind
discriminator that lets commentary live in the same table as hard facts.

Revision ID: 0002_evidence_kind
Revises: 0001_baseline
Create Date: 2026-11-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_evidence_kind"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_column = sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), "postgresql"
    )
    op.add_column("briefings", sa.Column("evidence", json_column, nullable=True))

    # server_default backfills existing rows, which is what makes adding a
    # NOT NULL column to a populated table safe.
    op.add_column(
        "evidence_facts",
        sa.Column("kind", sa.String(16), nullable=False, server_default="hard"),
    )

    # The old uniqueness did not distinguish facts from commentary.
    op.drop_constraint(
        "uq_evidence_company_content", "evidence_facts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_evidence_company_kind_content",
        "evidence_facts",
        ["company_id", "kind", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_evidence_company_kind_content", "evidence_facts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_evidence_company_content",
        "evidence_facts",
        ["company_id", "content_hash"],
    )
    op.drop_column("evidence_facts", "kind")
    op.drop_column("briefings", "evidence")
