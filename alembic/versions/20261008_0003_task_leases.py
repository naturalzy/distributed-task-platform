"""add worker leases and execution attempt fencing

Revision ID: 20261008_0003
Revises: 20261008_0002
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261008_0003"
down_revision: str | None = "20261008_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("execution_attempts", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("tasks", sa.Column("lease_token", sa.String(length=36), nullable=True))
    op.add_column("tasks", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_status_lease", "tasks", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_status_lease", table_name="tasks")
    op.drop_column("tasks", "lease_expires_at")
    op.drop_column("tasks", "lease_token")
    op.drop_column("tasks", "execution_attempts")
