"""deleting a card must not delete the steps that showed it

``session_steps.card_id`` was ``ON DELETE CASCADE``. ``placement_service.unmark_known`` deleted
untested cards to put a syllable back in the teaching queue, so un-claiming one mid-lesson
destroyed that lesson's pending steps: production holds a sitting recorded as 40 planned steps of
which only 10 rows survive, all of them the ``intro_item`` steps that carry no ``card_id``. With
nothing left pending, ``next_step`` returned ``None`` and the sitting closed itself as "completed".

A step is the record of what the learner was shown. SET NULL keeps that record and lets the
application decide what a card-less step means, instead of the database quietly removing evidence.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14 01:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "fk_session_steps_card_id_cards"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "session_steps", type_="foreignkey")
    op.create_foreign_key(CONSTRAINT, "session_steps", "cards", ["card_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "session_steps", type_="foreignkey")
    op.create_foreign_key(CONSTRAINT, "session_steps", "cards", ["card_id"], ["id"], ondelete="CASCADE")
