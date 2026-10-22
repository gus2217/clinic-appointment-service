"""idempotency: per-endpoint scope and in-progress state

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_keys",
        sa.Column("endpoint", sa.String(128), nullable=False,
                  server_default="POST /appointments"),
    )
    op.add_column(
        "idempotency_keys",
        sa.Column("state", sa.String(16), nullable=False, server_default="completed"),
    )
    op.alter_column("idempotency_keys", "status_code", nullable=True)
    op.alter_column(
        "idempotency_keys", "response_body",
        type_=postgresql.JSONB(), nullable=True,
    )
    op.drop_constraint("uq_idempotency_actor_key", "idempotency_keys", type_="unique")
    op.create_unique_constraint(
        "uq_idempotency_scope", "idempotency_keys", ["actor_id", "endpoint", "key"]
    )
    op.alter_column("idempotency_keys", "endpoint", server_default=None)
    op.alter_column("idempotency_keys", "state", server_default=None)


def downgrade() -> None:
    op.execute("DELETE FROM idempotency_keys WHERE state <> 'completed'")
    op.alter_column("idempotency_keys", "response_body", nullable=False)
    op.alter_column("idempotency_keys", "status_code", nullable=False)
    op.drop_constraint("uq_idempotency_scope", "idempotency_keys", type_="unique")
    op.create_unique_constraint(
        "uq_idempotency_actor_key", "idempotency_keys", ["actor_id", "key"]
    )
    op.drop_column("idempotency_keys", "state")
    op.drop_column("idempotency_keys", "endpoint")
