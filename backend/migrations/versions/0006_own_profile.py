"""add own company profile selection to briefings

A profile shapes the recommendation, so it must be stored with the request that
used it. Without it, a re-run cannot reproduce whose position the advice was
written for.

Revision ID: 0006_own_profile
Revises: 0005_ticker_and_description
Create Date: 2026-11-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_own_profile"
down_revision: Union[str, Sequence[str], None] = "0005_ticker_and_description"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "briefings", sa.Column("own_profile_name", sa.String(255), nullable=True)
    )
    op.add_column(
        "briefings", sa.Column("own_profile_inline", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("briefings", "own_profile_inline")
    op.drop_column("briefings", "own_profile_name")
