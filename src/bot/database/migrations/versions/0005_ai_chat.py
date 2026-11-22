"""AI chat settings, allowlists and thread context.

Revision ID: 0005
Revises: 0004
Create Date: 2026-01-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_channels",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("persona", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("channel_id"),
    )

    op.create_table(
        "ai_chat_allowlist",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("login", sa.String(length=64), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("channel_id", "user_id"),
    )
    op.create_index(
        "ix_ai_chat_allowlist_channel", "ai_chat_allowlist", ["channel_id"], unique=False
    )

    op.create_table(
        "ai_chat_threads",
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("persona", sa.String(length=128), server_default="", nullable=False),
        sa.Column(
            "messages",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index(
        "ix_ai_chat_threads_channel", "ai_chat_threads", ["channel_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ai_chat_threads_channel", table_name="ai_chat_threads")
    op.drop_table("ai_chat_threads")
    op.drop_index("ix_ai_chat_allowlist_channel", table_name="ai_chat_allowlist")
    op.drop_table("ai_chat_allowlist")
    op.drop_table("ai_chat_channels")
