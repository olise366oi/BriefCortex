"""add ticker and company description to briefings

Both are caller inputs that change the output: the ticker enables the quote lane,
and the description separates companies that share a name. They were accepted on
the request but never stored, so a re-run could not reproduce its own attribution.

Revision ID: 0005_ticker_and_description
Revises: 0004_evidence_fingerprint
Create Date: 2026-11-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ticker_and_description"
down_revision: Union[str, Sequence[str], None] = "0004_evidence_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("briefings", sa.Column("ticker", sa.String(32), nullable=True))
    op.add_column("briefings", sa.Column("company_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("briefings", "company_description")
    op.drop_column("briefings", "ticker")
