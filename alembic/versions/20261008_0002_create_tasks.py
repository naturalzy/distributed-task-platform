"""create tasks and outbox tables

Revision ID: 20261008_0002
Revises: 20260924_0001
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261008_0002"
down_revision: str | None = "20260924_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_uuid", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_uuid", name="uq_tasks_task_uuid"),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("task_uuid", sa.String(length=36), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_outbox_events_task_id"),
    )
    op.create_index(
        "ix_outbox_events_due", "outbox_events", ["sent_at", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_due", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")
