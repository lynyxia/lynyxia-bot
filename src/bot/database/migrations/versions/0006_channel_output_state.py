"""Per-channel output toggle state.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_states",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column(
            "output_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("channel_id"),
    )


def downgrade() -> None:
    op.drop_table("channel_states")
