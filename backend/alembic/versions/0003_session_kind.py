"""session kind: daily vs practice

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07 01:24:22.079553
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    session_kind = postgresql.ENUM("daily", "practice", name="session_kind")
    session_kind.create(op.get_bind(), checkfirst=True)
    # Backfill with a server default so the column can be NOT NULL on a table that already has rows,
    # then drop the default: from here on the ORM decides, and a session must say why it exists.
    op.add_column(
        "learning_sessions",
        sa.Column("kind", session_kind, nullable=False, server_default="daily"),
    )
    op.alter_column("learning_sessions", "kind", server_default=None)


def downgrade() -> None:
    op.drop_column("learning_sessions", "kind")
    op.execute("DROP TYPE IF EXISTS session_kind")
