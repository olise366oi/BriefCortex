"""add own company market scan to briefings

The caller's own company is scanned for market position so the analysis can say
how our footing bears on the deal. Stored with the request so a re-run reproduces
the same context, and so "internal material instead of retrieval" is a recorded
choice rather than an implicit one.

Revision ID: 0007_own_company_scan
Revises: 0006_own_profile
Create Date: 2026-11-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_own_company_scan"
down_revision: Union[str, Sequence[str], None] = "0006_own_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("briefings", sa.Column("own_company_name", sa.String(255), nullable=True))
    op.add_column("briefings", sa.Column("own_company_ticker", sa.String(32), nullable=True))
    op.add_column("briefings", sa.Column("own_company_source", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("briefings", "own_company_source")
    op.drop_column("briefings", "own_company_ticker")
    op.drop_column("briefings", "own_company_name")
