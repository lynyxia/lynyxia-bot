"""Per-channel LLM endpoint and model selection.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_channels", sa.Column("endpoint", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "ai_chat_channels", sa.Column("model", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_chat_channels", "model")
    op.drop_column("ai_chat_channels", "endpoint")
