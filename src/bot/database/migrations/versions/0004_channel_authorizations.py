"""Per-channel broadcaster authorizations for app-token chat sends.

Revision ID: 0004
Revises: 0003
Create Date: 2026-01-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_authorizations",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column(
            "broadcaster_login",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("channel_id"),
    )


def downgrade() -> None:
    op.drop_table("channel_authorizations")
