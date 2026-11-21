"""Store the chatter's username alongside their Twitch user ID.

Revision ID: 0003
Revises: 0002
Create Date: 2026-01-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("username", sa.String(length=64), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "username")
