"""one daily session per learner-day

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07 10:17:16.600029
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A database that ran the stop-button bug can already hold several daily sessions for one
    # learner-day: tapping Stop with nothing in progress opened a lesson and abandoned it at once.
    # Keep the first one for each day — the one that actually issued the new items — and demote the
    # rest to practice, which is what they were. Nothing is deleted: their steps and any grades stay
    # attached and readable. Without this the index below cannot be created.
    op.execute("""
        UPDATE learning_sessions AS s
           SET kind = 'practice'
         WHERE s.kind = 'daily'
           AND s.id <> (
                 SELECT keep.id
                   FROM learning_sessions AS keep
                  WHERE keep.user_id = s.user_id
                    AND keep.local_date = s.local_date
                    AND keep.kind = 'daily'
                  ORDER BY keep.started_at, keep.id
                  LIMIT 1
               )
        """)
    op.create_index(
        "uq_learning_sessions_daily_per_day",
        "learning_sessions",
        ["user_id", "local_date"],
        unique=True,
        postgresql_where=sa.text("kind = 'daily'"),
    )


def downgrade() -> None:
    # The demotion above is not reversed: which sittings were once mislabelled is not recoverable,
    # and practice is the truthful label for a lesson that issued nothing.
    op.drop_index(
        "uq_learning_sessions_daily_per_day",
        table_name="learning_sessions",
        postgresql_where=sa.text("kind = 'daily'"),
    )
