"""baseline: users, invites, items, cards, review_logs, audio_assets, ai_usage_ledger

Revision ID: 0001
Revises:
Create Date: 2026-09-05 13:52:04.235355
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audio_assets",
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("voice", sa.String(length=64), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("text", sa.String(length=2048), nullable=False),
        sa.Column("ogg_path", sa.String(length=255), nullable=True),
        sa.Column("mp3_path", sa.String(length=255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("timepoints", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tg_file_id", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audio_assets")),
        sa.UniqueConstraint("hash", name=op.f("uq_audio_assets_hash")),
    )
    op.create_table(
        "items",
        sa.Column(
            "type", sa.Enum("kana", "vocab", "grammar", "sentence", "dialogue", name="item_type"), nullable=False
        ),
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("curriculum_order", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Enum("kana_hira", "kana_kata", "core", name="item_stage"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
        sa.UniqueConstraint("slug", name=op.f("uq_items_slug")),
        sa.UniqueConstraint("type", "ref_id", name=op.f("uq_items_type_ref_id")),
    )
    op.create_index(op.f("ix_items_curriculum_order"), "items", ["curriculum_order"], unique=False)
    op.create_table(
        "users",
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("role", sa.Enum("learner", "admin", name="user_role"), nullable=False),
        sa.Column("status", sa.Enum("active", "paused", "blocked", name="user_status"), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("reminder_time", sa.Time(), nullable=True),
        sa.Column("daily_minutes_target", sa.Integer(), nullable=False),
        sa.Column("furigana_mode", sa.Enum("always", "auto", "off", name="furigana_mode"), nullable=False),
        sa.Column("daily_budget_usd", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("desired_retention", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("fsrs_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name=op.f("fk_users_invited_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("tg_user_id", name=op.f("uq_users_tg_user_id")),
    )
    op.create_table(
        "ai_usage_ledger",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("task", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("audio_seconds", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("chars", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ai_usage_ledger_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_usage_ledger")),
    )
    op.create_index("ix_ai_usage_ledger_user_created", "ai_usage_ledger", ["user_id", "created_at"], unique=False)
    op.create_table(
        "cards",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column(
            "direction", sa.Enum("recognition", "production", "listening", name="card_direction"), nullable=False
        ),
        sa.Column("state", sa.Enum("new", "learning", "review", "relearning", name="card_state"), nullable=False),
        sa.Column("stability", sa.Float(), nullable=True),
        sa.Column("difficulty", sa.Float(), nullable=True),
        sa.Column("due", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_review", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=False),
        sa.Column("lapses", sa.Integer(), nullable=False),
        sa.Column("elapsed_days", sa.Integer(), nullable=False),
        sa.Column("scheduled_days", sa.Integer(), nullable=False),
        sa.Column("suspended", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], name=op.f("fk_cards_item_id_items"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_cards_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cards")),
        sa.UniqueConstraint("user_id", "item_id", "direction", name=op.f("uq_cards_user_id_item_id_direction")),
    )
    op.create_index("ix_cards_user_due", "cards", ["user_id", "due"], unique=False)
    op.create_table(
        "invites",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_invites_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("code", name=op.f("uq_invites_code")),
    )
    op.create_table(
        "invite_redemptions",
        sa.Column("invite_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invite_id"], ["invites.id"], name=op.f("fk_invite_redemptions_invite_id_invites"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_invite_redemptions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite_redemptions")),
        sa.UniqueConstraint("invite_id", "user_id", name=op.f("uq_invite_redemptions_invite_id_user_id")),
    )
    op.create_table(
        "review_logs",
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("rating BETWEEN 1 AND 4", name=op.f("ck_review_logs_rating_range")),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elapsed_days", sa.Integer(), nullable=False),
        sa.Column("scheduled_days", sa.Integer(), nullable=False),
        sa.Column(
            "state_before",
            postgresql.ENUM("new", "learning", "review", "relearning", name="card_state", create_type=False),
            nullable=False,
        ),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("auto_graded", sa.Boolean(), nullable=False),
        sa.Column("intra_session", sa.Boolean(), nullable=False),
        sa.Column("answer_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_review_logs_card_id_cards"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_logs")),
    )
    op.create_index("ix_review_logs_card_review_at", "review_logs", ["card_id", "review_at"], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index("ix_review_logs_card_review_at", table_name="review_logs")
    op.drop_table("review_logs")
    op.drop_table("invite_redemptions")
    op.drop_table("invites")
    op.drop_index("ix_cards_user_due", table_name="cards")
    op.drop_table("cards")
    op.drop_index("ix_ai_usage_ledger_user_created", table_name="ai_usage_ledger")
    op.drop_table("ai_usage_ledger")
    op.drop_table("users")
    op.drop_index(op.f("ix_items_curriculum_order"), table_name="items")
    op.drop_table("items")
    op.drop_table("audio_assets")
    for enum_name in (
        "card_state",
        "card_direction",
        "furigana_mode",
        "user_status",
        "user_role",
        "item_stage",
        "item_type",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    # ### end Alembic commands ###
