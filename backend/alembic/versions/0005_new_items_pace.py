"""learner-chosen new items per day

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-07 11:34:59.892520
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("daily_new_items_target", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "daily_new_items_target")
