"""Per-channel live pass lists.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_passes",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("login", sa.String(length=64), server_default="", nullable=False),
        sa.Column("added_by", sa.String(length=32), server_default="", nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("channel_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("live_passes")
