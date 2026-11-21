"""Initial schema: channels, users and chat messages.

Revision ID: 0001
Revises:
Create Date: 2026-01-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_channels_login"), "channels", ["login"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_login"), "users", ["login"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("twitch_message_id", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chat_messages_channel_sent"),
        "chat_messages",
        ["channel_id", "sent_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chat_messages_sent_at"), "chat_messages", ["sent_at"], unique=False
    )
    op.create_index(
        op.f("ix_chat_messages_twitch_message_id"),
        "chat_messages",
        ["twitch_message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_chat_messages_user_sent"),
        "chat_messages",
        ["user_id", "sent_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_messages_user_sent"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_twitch_message_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_sent_at"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_channel_sent"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_users_login"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_channels_login"), table_name="channels")
    op.drop_table("channels")
